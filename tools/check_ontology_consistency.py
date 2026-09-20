"""本体口径自检：把散落各处的常量抓出来与 src/ontology.py 相减。

【为什么需要】
  `docs/ERROR_CASES.md` 案例 12 的结论是「同一条规则只写一遍；写不了两遍的地方，
  就加一个把两遍结果相减的自检」。本体常量正是"写了两遍"的重灾区：
    · `sync_graph.py` 的入库白名单一度含 `CONTRIBUTED_TO`（16 类），
      而 `l5_ontology_audit.py` 按 15 类审计；
    · 刚把 `Unknown` 加进 `sync_graph.ONTOLOGY_TYPES` 时，
      `l8_structure_audit.py` 还硬编码着 15 —— 同一个错又犯了一次。
  本脚本不做业务，只做一件事：**把各处的取值与唯一来源对比，不一致就报错并给出非零退出码**，
  以便挂进回归。

【怎么做的】
  这些脚本顶层就 `parse_args()`，不能被 import，所以用**文本解析**读它们的常量。
  集合字面量的写法在项目里很规整，正则足够稳；解析不到也算失败（说明定义方式变了，
  需要人来更新本脚本）。
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import json
import re
import sys
from pathlib import Path

ROOT = Path(str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))

from ontology import (  # noqa: E402
    ALLOWED_ENTITY_LABELS, CREATED_BY_AUTHOR_TYPES, CREATED_BY_CREATABLE_TYPES,
    DERIVED_ENTITY_LABELS, ENTITY_TYPES, L5_THRESHOLD, RELATION_TYPES,
    RELATION_ENDPOINT_CONSTRAINTS, REL_HYPOTHESIS_TEMPLATE, REL_PHRASE_TEMPLATE,
    SYMMETRIC_RELATIONS, validate_constraints,
)


def strip_comments(text):
    """去掉 Python 源码里的注释，避免"注释里提到某类型"被当成"白名单里有它"。

    **为什么必须剥**（2026-09-14 实测）：`parse_set` 的正则 `"([^"]+)"` 不区分
    代码与注释。当时把 `PUBLISHED_IN` 从 `graph_store.ALLOWED_RELATION_TYPES`
    注释掉做防复发测试，自检**仍报"全部一致"** —— 因为紧挨着的注释里写了
    「把 PUBLISHED_IN 加进 kg_extractor…」，正则把这句里的类型名也算进了集合。

    这是**假阴性**方向：真漏了白名单但注释提过，自检就查不出来。
    比假阳性危险，所以先剥注释再解析。

    只处理整行注释（`#` 开头的行）与行尾注释（引号外的 `#`）——
    不做完整词法分析，够用且不会误伤字符串里的 `#`。
    """
    out = []
    for line in text.splitlines():
        in_str = None
        cut = len(line)
        for i, ch in enumerate(line):
            if in_str:
                if ch == in_str and line[i - 1] != '\\':
                    in_str = None
            elif ch in '"\'':
                in_str = ch
            elif ch == '#':
                cut = i
                break
        out.append(line[:cut])
    return '\n'.join(out)


def parse_set(text, name):
    """从源码里解析 `name = { "a", "b", ... }`（跨行）。**会先剥掉注释。**"""
    m = re.search(re.escape(name) + r'\s*=\s*\{(.*?)\}', strip_comments(text), re.S)
    if not m:
        return None
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def parse_int(text, name):
    m = re.search(re.escape(name) + r'\s*=\s*(\d+)', text)
    return int(m.group(1)) if m else None


def parse_float(text, name):
    m = re.search(re.escape(name) + r'\s*=\s*([0-9.]+)', text)
    return float(m.group(1)) if m else None


def parse_str_dict(text, name):
    """从源码里解析 `name = { "KEY": "value", ... }`（跨行，value 内无转义引号）。

    用于 L5/L4 的假设句模板表——它是 dict 而非 set，`parse_set` 抓不到。
    模板值里含 `{s}` / `{t}` 占位符与句点，正则 `[^"]*` 足够稳。
    """
    m = re.search(re.escape(name) + r'\s*=\s*\{(.*?)\n\}', text, re.S)
    if not m:
        return None
    got = dict(re.findall(r'"([A-Z_]+)"\s*:\s*"([^"]*)"', m.group(1)))
    return got or None


def main():
    fails, checks = [], []

    _IMP_RE = re.compile(
        r'^[ \t]*(?:from\s+[\w.]*ontology\s+import\b|import\s+[\w.]*ontology\b)', re.M)

    def uses_ontology(text):
        """该脚本是否从唯一来源取值（而不是就地抄一份）。

        ★ **必须按"真正的 import 行"判，不能按"全文出现过某个名字"判**
        （2026-09-20 修，起因是一次假绿）：

        假设句模板那一条原先写的是 `if 'REL_HYPOTHESIS_TEMPLATE' in txt:`
        —— 看的是**文件全文**。而 `tools/verify_direction.py:57-58` 有两行注释：

            唯一来源仍是 src/ontology.py 的 REL_HYPOTHESIS_TEMPLATE，这里只是
            同步一份供本脚本离线使用；check_ontology_consistency.py 会逐字校验

        **这两行注释本身就满足了那个判据** ⇒ 该文件被判「ok(引用 ontology)」
        ⇒ **它另存的那 12 条 REL_TEMPLATE 从来没有被逐字比过**。
        （注释里写着"会被逐字校验"，而它正是让逐字校验失效的原因。）

        ⇒ 现在统一按**行首的真实 import** 判（注释行不算），
        并且四类检查**共用这一个判据**（原先只有其中三处用了它）。
        """
        return bool(_IMP_RE.search(text))

    def check_relations(rel, name):
        p = ROOT / rel
        if not p.exists():
            checks.append((f'关系类型 {rel}', 'missing-file', rel))
            fails.append(f'{rel} 不存在 —— 被检查的文件缺失时「一致」无从谈起，'
                         f'算失败（原实现只记一行「· 」，不影响退出码 ⇒ '
                         f'删掉文件就能让本守门变绿）')
            return
        txt = p.read_text(encoding='utf-8')
        if uses_ontology(txt):
            checks.append((f'关系类型 {rel}', 'ok(引用 ontology)', rel))
            return
        got = parse_set(txt, name)
        if got is None:
            checks.append((f'关系类型 {rel}', 'PARSE-FAIL', rel))
            fails.append(f'关系类型未能从 {rel} 解析到，且它没有引用 ontology.py')
        elif got != set(RELATION_TYPES):
            checks.append((f'关系类型 {rel}', 'MISMATCH', rel))
            fails.append(f'关系类型不一致：{rel} 独有 {sorted(got - set(RELATION_TYPES))}、'
                         f'缺 {sorted(set(RELATION_TYPES) - got)}')
        else:
            checks.append((f'关系类型 {rel}', 'ok(字面量一致)', rel))

    def check_entities(rel, name):
        p = ROOT / rel
        if not p.exists():
            checks.append((f'实体类型 {rel}', 'missing-file', rel))
            fails.append(f'{rel} 不存在 —— 被检查的文件缺失时「一致」无从谈起，'
                         f'算失败（原实现只记一行「· 」，不影响退出码）')
            return
        txt = p.read_text(encoding='utf-8')
        if uses_ontology(txt) or 'len(ENTITY_TYPES)' in txt or \
                'len(ALLOWED_ENTITY_LABELS)' in txt:
            checks.append((f'实体类型 {rel}', 'ok(引用 ontology)', rel))
            return
        got = parse_set(txt, name)
        if got is None:
            checks.append((f'实体类型 {rel}', 'PARSE-FAIL', rel))
            fails.append(f'实体类型未能从 {rel} 解析到，且它没有引用 ontology.py')
        elif got != set(ENTITY_TYPES):
            checks.append((f'实体类型 {rel}', 'MISMATCH', rel))
            fails.append(f'实体类型不一致：{rel} 独有 {sorted(got - set(ENTITY_TYPES))}、'
                         f'缺 {sorted(set(ENTITY_TYPES) - got)}')
        else:
            checks.append((f'实体类型 {rel}', 'ok(字面量一致)', rel))

    def check_derived(rel, name):
        p = ROOT / rel
        if not p.exists():
            checks.append((f'派生标签 {rel}', 'missing-file', rel))
            fails.append(f'{rel} 不存在 —— 被检查的文件缺失时「一致」无从谈起，'
                         f'算失败（原实现只记一行「· 」，不影响退出码）')
            return
        got = parse_set(p.read_text(encoding='utf-8'), name)
        if got is None:
            checks.append((f'派生标签 {rel}', 'PARSE-FAIL', rel))
            fails.append(f'派生标签未能从 {rel} 解析到')
        elif got != set(DERIVED_ENTITY_LABELS):
            checks.append((f'派生标签 {rel}', 'MISMATCH', rel))
            fails.append(f'派生标签不一致：{rel} 是 {sorted(got)}，'
                         f'应为 {sorted(DERIVED_ENTITY_LABELS)}')
        else:
            checks.append((f'派生标签 {rel}', 'ok', rel))

    # ── 关系类型 ──
    # 注意 `ONTOLOGY` 这个朴素名字在 l5_report / l5_ontology_audit 里装的是
    # **关系**集合（初版把它们当成实体类型，误报了两条）。
    check_relations('tools/sync_graph.py', 'ONTOLOGY_RELS')
    check_relations('tools/l5_ontology_audit.py', 'ONTOLOGY')
    check_relations('tools/l5_report.py', 'ONTOLOGY')
    # 2026-09-14 补：**存储层的关系白名单此前没被检查**。
    # 后果是实测发生过的：把 PUBLISHED_IN 加进 kg_extractor 与 ontology 之后，
    # 忘了同步这里，而 `safe_relation_type` 会把非白名单谓词静默改写成
    # RELATED_TO —— 本体扩展的数据收益恒为 0，且不报错。
    # （本文件原来**只**检查了 graph_store 的实体类型 ALLOWED_LABELS，漏了关系。）
    check_relations('src/graph_store.py', 'ALLOWED_RELATION_TYPES')

    # ── 实体类型（本体 15 类，不含派生）──
    check_entities('src/graph_store.py', 'ALLOWED_LABELS')
    check_entities('tools/sync_graph.py', 'ONTOLOGY_TYPES')
    check_entities('tools/l8_structure_audit.py', 'ONTOLOGY_ENTITIES')

    # ── 派生标签必须单独声明，不能混进本体 ──
    check_derived('src/graph_store.py', 'DERIVED_LABELS')
    check_derived('tools/sync_graph.py', 'DERIVED_TYPES')

    # ── L7 的端点类型约束 ──
    l7 = ROOT / 'tools/l7_direction_verify.py'
    if l7.exists():
        t = l7.read_text(encoding='utf-8')
        if uses_ontology(t):
            checks.append(('L7 端点约束 tools/l7_direction_verify.py',
                           'ok(引用 ontology)', 'tools/l7_direction_verify.py'))
        else:
            got_a = parse_set(t, 'AUTHOR')
            got_c = parse_set(t, 'CREATABLE')
            for label, got, want in [
                ('L7 AUTHOR', got_a, CREATED_BY_AUTHOR_TYPES),
                ('L7 CREATABLE', got_c, CREATED_BY_CREATABLE_TYPES),
            ]:
                if got is None:
                    checks.append((label, 'PARSE-FAIL', 'tools/l7_direction_verify.py'))
                    fails.append(f'{label} 未能解析且没有引用 ontology.py')
                elif got != set(want):
                    checks.append((label, 'MISMATCH', 'tools/l7_direction_verify.py'))
                    fails.append(f'{label} 与 ontology.py 不一致')
                else:
                    checks.append((label, 'ok(字面量一致)', 'tools/l7_direction_verify.py'))

    # ── L5 阈值：只要求"没有硬编码一个与 ontology 不同的值" ──
    # relation_verifier.py 是运行时自动校准（--threshold 默认 0 = 自动），
    # 所以它**本来就不该有字面量**；有字面量才需要比对。
    for rel, name in [('tools/relation_verifier.py', 'THRESHOLD'),
                      ('tools/l5_report.py', 'THRESHOLD')]:
        p = ROOT / rel
        if not p.exists():
            checks.append((name, 'missing-file', rel))
            fails.append(f'{rel} 不存在 —— 被检查的文件缺失时「一致」无从谈起，'
                         f'算失败（原实现只记一行「· 」，不影响退出码）')
            continue
        v = parse_float(p.read_text(encoding='utf-8'), name)
        if v is None:
            checks.append((name, 'ok(无硬编码)', rel))
        elif abs(v - L5_THRESHOLD) > 1e-9:
            checks.append((name, 'MISMATCH', rel))
            fails.append(f'L5 阈值不一致：{rel} 是 {v}，ontology.py 是 {L5_THRESHOLD}')
        else:
            checks.append((name, 'ok', rel))

    # ── 语义对称关系：verify_direction.py 里仍是一份字面量 ──
    # 这份划分决定"哪些关系不做方向判定"，写两处同样会漂移。
    vd = ROOT / 'tools/verify_direction.py'
    if vd.exists():
        got = parse_set(vd.read_text(encoding='utf-8'), 'SYMMETRIC')
        if got is None:
            checks.append(('对称关系 tools/verify_direction.py', 'PARSE-FAIL',
                           'tools/verify_direction.py'))
            fails.append('SYMMETRIC 未能从 tools/verify_direction.py 解析到')
        elif got != set(SYMMETRIC_RELATIONS):
            checks.append(('对称关系 tools/verify_direction.py', 'MISMATCH',
                           'tools/verify_direction.py'))
            fails.append(
                f'对称关系不一致：verify_direction.py 独有 {sorted(got - set(SYMMETRIC_RELATIONS))}、'
                f'缺 {sorted(set(SYMMETRIC_RELATIONS) - got)}'
                f'（唯一来源是 src/ontology.py 的 SYMMETRIC_RELATIONS）')
        else:
            checks.append(('对称关系 tools/verify_direction.py', 'ok(字面量一致)',
                           'tools/verify_direction.py'))

    # ── L5/L4 的假设句模板表：唯一来源是 src/ontology.py ──
    # `relation_verifier.py` 与 `verify_direction.py` 各存了一份 REL_TEMPLATE，
    # 注释里写着"改这里必须同步改那边"——正是案例 12 说的"写两处必漂移"。
    # 本检查把两处与 ontology.REL_HYPOTHESIS_TEMPLATE **逐字**相减；
    # L4 谓词选择评测（tools/l4_predicate_report.py）直接用 ontology 的那一份，
    # 所以这三处一旦错开，L5 与 L4 就不再问同一个问题，必须报错。
    for rel, name in (('tools/relation_verifier.py', 'REL_TEMPLATE'),
                      ('tools/verify_direction.py', 'REL_TEMPLATE')):
        p = ROOT / rel
        if not p.exists():
            checks.append((f'假设句模板 {rel}', 'missing-file', rel))
            fails.append(f'{rel} 不存在 —— 被检查的文件缺失时「一致」无从谈起，'
                         f'算失败（原实现只记一行「· 」，不影响退出码）')
            continue
        txt = p.read_text(encoding='utf-8')
        # ★ 用 `uses_ontology()`（**按真实 import 行判**），不用
        #   `'REL_HYPOTHESIS_TEMPLATE' in txt`（**按全文出现判**）——
        #   后者会被 `verify_direction.py` 自己的一行注释满足，从而旁路掉逐字校验。
        if uses_ontology(txt):
            checks.append((f'假设句模板 {rel}', 'ok(引用 ontology)', rel))
            continue
        got = parse_str_dict(txt, name)
        if got is None:
            checks.append((f'假设句模板 {rel}', 'PARSE-FAIL', rel))
            fails.append(f'{name} 未能从 {rel} 解析到，且它没有引用 ontology.py')
        elif got != REL_HYPOTHESIS_TEMPLATE:
            only_a = sorted(set(got) - set(REL_HYPOTHESIS_TEMPLATE))
            only_b = sorted(set(REL_HYPOTHESIS_TEMPLATE) - set(got))
            diff = sorted(k for k in set(got) & set(REL_HYPOTHESIS_TEMPLATE)
                          if got[k] != REL_HYPOTHESIS_TEMPLATE[k])
            checks.append((f'假设句模板 {rel}', 'MISMATCH', rel))
            fails.append(
                f'假设句模板不一致：{rel} 独有 {only_a}、缺 {only_b}、'
                f'取值不同 {diff}（唯一来源是 src/ontology.py 的 REL_HYPOTHESIS_TEMPLATE）')
        else:
            checks.append((f'假设句模板 {rel}', 'ok(字面量一致)', rel))

    # ── L7 端点类型约束表自身的完整性 ──
    problems = validate_constraints()
    if problems:
        checks.append(('L7 端点类型约束表', 'MISMATCH', 'tools/../src/ontology.py'))
        fails.extend(f'L7 约束表自检失败：{p}' for p in problems)
    else:
        checks.append((f'L7 端点类型约束表（{len(RELATION_ENDPOINT_CONSTRAINTS)} 类）',
                       'ok(自检通过)', 'src/ontology.py'))

    print('=== 本体口径自检 ===\n')
    for label, status, path in checks:
        if status.startswith('ok'):
            mark = '✅'
        else:
            mark = {'MISMATCH': '❌', 'PARSE-FAIL': '⚠️ ',
                    'missing-file': '· '}.get(status, '?')
        print(f'  {mark} {label:44s} {path}')

    print(f'\n唯一来源：src/ontology.py')
    print(f'  实体 {len(ENTITY_TYPES)} 类 · 关系 {len(RELATION_TYPES)} 类 · '
          f'派生 {sorted(DERIVED_ENTITY_LABELS)} · L5 阈值 {L5_THRESHOLD}')

    out = {
        'source': 'src/ontology.py',
        'entity_types': sorted(ENTITY_TYPES),
        'relation_types': sorted(RELATION_TYPES),
        'derived_labels': sorted(DERIVED_ENTITY_LABELS),
        'allowed_labels': sorted(ALLOWED_ENTITY_LABELS),
        'checks': [{'label': a, 'status': b, 'path': c} for a, b, c in checks],
        'failures': fails,
    }
    (ROOT / 'data' / 'quality' / 'ontology-consistency.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')

    print('\n' + '=' * 60)
    if fails:
        print(f'发现 {len(fails)} 处不一致：')
        for f in fails:
            print('  · ' + f)
        print('\n这就是案例 12 说的「同一规则写两处」——请改回从 src/ontology.py 取值。')
        return 1
    print('全部一致')
    return 0


if __name__ == '__main__':
    sys.exit(main())
