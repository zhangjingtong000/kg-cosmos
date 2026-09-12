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
import json
import re
import sys
from pathlib import Path

ROOT = Path(r'D:\kg-demo')
sys.path.insert(0, str(ROOT / 'src'))

from ontology import (  # noqa: E402
    ALLOWED_ENTITY_LABELS, CREATED_BY_AUTHOR_TYPES, CREATED_BY_CREATABLE_TYPES,
    DERIVED_ENTITY_LABELS, ENTITY_TYPES, L5_THRESHOLD, RELATION_TYPES,
)


def parse_set(text, name):
    """从源码里解析 `name = { "a", "b", ... }`（跨行）。"""
    m = re.search(re.escape(name) + r'\s*=\s*\{(.*?)\}', text, re.S)
    if not m:
        return None
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def parse_int(text, name):
    m = re.search(re.escape(name) + r'\s*=\s*(\d+)', text)
    return int(m.group(1)) if m else None


def parse_float(text, name):
    m = re.search(re.escape(name) + r'\s*=\s*([0-9.]+)', text)
    return float(m.group(1)) if m else None


def main():
    fails, checks = [], []

    def uses_ontology(text):
        """该脚本是否从唯一来源取值（而不是就地抄一份）。"""
        return ('from ontology import' in text) or ('import ontology' in text)

    def check_relations(rel, name):
        p = ROOT / rel
        if not p.exists():
            checks.append((f'关系类型 {rel}', 'missing-file', rel))
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
            continue
        v = parse_float(p.read_text(encoding='utf-8'), name)
        if v is None:
            checks.append((name, 'ok(无硬编码)', rel))
        elif abs(v - L5_THRESHOLD) > 1e-9:
            checks.append((name, 'MISMATCH', rel))
            fails.append(f'L5 阈值不一致：{rel} 是 {v}，ontology.py 是 {L5_THRESHOLD}')
        else:
            checks.append((name, 'ok', rel))

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
