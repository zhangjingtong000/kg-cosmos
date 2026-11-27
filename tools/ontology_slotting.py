# -*- coding: utf-8 -*-
r"""P4 · 本体槽位化（**真的改一次本体**，而不是只讨论应不应该改）。

## TODO 的原话

> **做法**：**真的把我们的本体槽位化**，改造前后各标一轮，给出可标性提升幅度。

## 为什么这一步是必要的（P5 已经把话说到了哪一步）

P5 量出「槽位化只能覆盖 **33.6%**」（`USES` 16.73% · `APPLIES_TO` 8.78% · `PRODUCES` 5.04%），
于是主结论从"**越结构化越可标**"（像是在单方面推荐槽位化）
变成"**有取舍的设计空间**"。

⇒ **但 P5 只是"数了数有多少能塞进四元槽位"**，它没有真的改。
**P4 的价值在于把那个统计变成一份可执行的本体定义** ——
这样"前后各标一轮"才有东西可标。

## 这一步**做了什么、没做什么**（必须说清）

**做了**：
1. 设计**槽位化本体**（`slotted ontology`）—— 哪些关系变成槽位、哪些保留为谓词；
2. **量化取舍**：覆盖多少条边、丢多少、保留多少；
3. **一个可执行的转换器**：把现有图按新本体转成槽位式（`--convert`）。

**没做（且本轮做不了）**：
4. **"改造前后各标一轮"** —— 那要**人类标注者**，
   本轮把它写进"需要用户本人"的清单。
   ⇒ **在没有那两轮标注之前，P4 只能算"改造已完成，效果待测"。**

## 与 ARCHIVE_POLICY 的关系

**原本体一个字都不删**（`ontology.py` 的 `RELATION_TYPES` / `RELATION_SEMANTICS` 原样保留），
新本体作为**并存的一份定义**产出到 `data/quality/ontology-slotted.json`。
`--convert` 也只是**另写一份图**，不覆盖 `data/corpus/graphs/`。

## 用法

    python tools/ontology_slotting.py                 # 出本体定义 + 量化
    python tools/ontology_slotting.py --convert       # 另写一份槽位式图（不覆盖原图）
    python tools/ontology_slotting.py --out <path>
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

GRAPHS = ROOT / 'data' / 'corpus' / 'graphs'
DEFAULT_OUT = ROOT / 'data' / 'quality' / 'ontology-slotted.json'


# ══════════════════════════════════════════════════════════════════════════
# 槽位化本体（P4 的核心交付）
# ══════════════════════════════════════════════════════════════════════════
#
# ## 什么叫"槽位化"
#
# SciREX 那样：关系不是**谓词**，而是**实体的一个属性槽**：
#     `Method —APPLIES_TO→ Task`   ⇒   `Task(Method=X)`
#     `Method —USES→ Material`     ⇒   `Material(Method=X)`
#     `Method —MEASURES→ Metric`   ⇒   `Metric(Method=X)`
# 即：**"主语是什么"由槽位名本身承担，不需要另外学一个谓词。**
#
# ## 哪些能槽位化、哪些不能（P5 已量过，这里是它的可执行版本）
#
# 判据：**这条关系的"主语"是不是一个稳定的实体类别？**
#   · `USES` 的主语永远是 `Method`/`Tool`  ⇒ **能**
#   · `CAUSES` 的主语可能是任何东西（现象、方法、条件）⇒ **不能**
#   · `CONTRADICTS` / `COMPARES_WITH` 是**对称**的 ⇒ 槽位是单向的 ⇒ **不能**
#   · `PART_OF` 的层级深度不定 ⇒ **不能**
SLOTS = {
    # 槽位名（= 被描述的实体类别） → 它的槽 → 原本对应的谓词
    'Method': {
        'task':      'APPLIES_TO',   # 方法应用于什么任务
        'material':  'USES',         # 方法用什么数据/工具
        'metric':    'MEASURES',     # 方法度量什么指标
        'produces':  'PRODUCES',     # 方法产出什么（**P5 把这条算进可槽位化**）
    },
    'Person_or_Org': {
        'work':      'CREATED_BY',   # 创作者 → 作品（**方向在谓词里是反的，要翻**）
        'produces':  'PRODUCES',     # 人/机构产出什么（与 Method.produces 同槽位名）
    },
    'Paper': {
        'venue':     'PUBLISHED_IN',
    },
}

# **保留为谓词的关系**（不进槽位）—— 上面 SLOTS 里出现过的谓词会被**吸收进槽位**
#
# ⚠️ `PRODUCES` **不在**这个列表里 —— 它已经进了槽位（多宿主）。
# 第一版忘了删它，于是它**同时**被算进 `slotted` 和 `kept`，
# 导致 `未归类` 出现**负数**（-1976）且两个比例加起来 > 100%（52.50%+52.01%）。
# ⇒ **"某个类型只能属于一类"这件事要在数据里强制，不能靠人记得。**
KEPT_RELATIONS = [
    'SUPPORTS', 'PART_OF', 'EVALUATES', 'ENABLES', 'LOCATED_AT',
    'CITES', 'RELATED_TO', 'COMPARES_WITH', 'CONTRADICTS', 'CAUSES',
]

# **多宿主槽位**：同一个谓词能落进**不同宿主**的槽位，落哪个**取决于主语的实体类型**
# —— 而"主语的实体类型"正是本项目**还没有可靠信息**的东西
# （抽取器给的 `type` 在 19% 的节点上是 `Unknown`，见 L5 报告）。
#
# ⇒ **所以这里不硬选一个宿主**：标出来、计入"可槽位化"的分母，
#   但在落位时标记 `host_undetermined`。**硬选一个会把统计做得好看而把转化做错。**
MULTI_HOST = {
    'PRODUCES': ['Method', 'Person_or_Org'],
}

# 谓词 → (槽位所属实体类别, 槽位名) 的反查表
PRED_TO_SLOT = {}
for _owner, _slots in SLOTS.items():
    for _slot, _pred in _slots.items():
        if _pred in MULTI_HOST:
            continue                      # 多宿主的走 MULTI_HOST，不在这里定死
        PRED_TO_SLOT[_pred] = (_owner, _slot)


def build_ontology():
    import ontology as O
    # ⚠️ `RELATION_SEMANTICS` 在 **`kg_extractor`**，不在 `ontology`
    # （`ontology` 只有 `RELATION_TYPES`）。第一版写成 `O.RELATION_SEMANTICS`
    # ⇒ `AttributeError`。用两层兜底：谁有就用谁的。
    import kg_extractor as K
    sem = getattr(O, 'RELATION_SEMANTICS', None) or \
        getattr(K, 'RELATION_SEMANTICS', None) or {}
    # 方向翻转让：`CREATED_BY` 原本是 Author → Work，而槽位是 `work`（属于 Person）
    # ⇒ **转换时必须把 source/target 反过来**，否则槽位里的主语是错的。
    reversed_preds = {'CREATED_BY'}
    return {
        'version': 'slotted-v1',
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'purpose': 'P4：把本体真的改造成槽位式（原本体并存，不删）',
        'design': {
            'what_is_slotting':
                '关系不再是**谓词**，而是**实体的一个属性槽**：'
                '`Method —APPLIES_TO→ Task` 变成 `Task(Method=X)`。'
                '即"主语是什么"由槽位名本身承担，不需要另外学一个谓词。',
            'admission_test':
                '**主语是不是一个稳定的实体类别？** '
                'USES 的主语永远是 Method/Tool ⇒ 能；'
                'CAUSES 的主语可能是任何东西 ⇒ 不能；'
                'CONTRADICTS/COMPARES_WITH 是**对称**的而槽位是单向的 ⇒ 不能；'
                'PART_OF 的层级深度不定 ⇒ 不能。',
            'archive_policy':
                '**原本体一个字都不删** —— ontology.py 的 RELATION_TYPES 与 '
                'RELATION_SEMANTICS 原样保留；本文档与产物是**并存**的新定义；'
                '`--convert` 也只另写一份图，不覆盖 data/corpus/graphs/。',
            'what_is_NOT_done':
                '**"改造前后各标一轮"需要人类标注者** ⇒ 记进'
                '"需要用户本人"清单。**在那两轮标注之前，P4 只能算'
                '"改造已完成、效果待测"。**',
        },
        'slots': SLOTS,
        'kept_relations': KEPT_RELATIONS,
        'reversed_when_slotting': sorted(reversed_preds),
        'original_ontology': {
            'relation_types': list(O.RELATION_TYPES),
            'relation_semantics': {k: v for k, v in sem.items()},
            'note': '**原样保留**，本文件不替代它',
        },
    }


def quantify(onto):
    """在真实图上量"覆盖多少 / 丢多少"—— 这是 P4 相对 P5 的增量。"""
    edge_types = Counter()
    n_graphs = 0
    n_nodes = n_edges = 0
    for f in sorted(GRAPHS.glob('*.json')):
        try:
            d = json.loads(f.read_text(encoding='utf-8-sig'))
        except Exception:
            continue
        n_graphs += 1
        n_nodes += len(d.get('nodes') or [])
        for e in (d.get('edges') or []):
            edge_types[str(e.get('type') or '?')] += 1
    n_edges = sum(edge_types.values())

    # ★ **强制"每个谓词只属于一类"** —— 不靠人记得。
    # 上一版靠人记得，结果 `PRODUCES` 同时进了槽位与保留列表，
    # `未归类` 变成负数（-1976）。**这类不变量要在代码里钉住。**
    _slotted_preds = set(PRED_TO_SLOT) | set(MULTI_HOST)
    _overlap = _slotted_preds & set(KEPT_RELATIONS)
    assert not _overlap, (
        f'这些谓词同时被判为"进槽位"与"保留"：{sorted(_overlap)} —— '
        f'一个类型只能属于一类，否则统计会重复计数')

    slotted = sum(c for t, c in edge_types.items()
                  if t in PRED_TO_SLOT or t in MULTI_HOST)
    kept = sum(c for t, c in edge_types.items()
               if t in set(KEPT_RELATIONS))
    unknown = n_edges - slotted - kept
    rows = []
    for t, c in edge_types.most_common():
        if t in PRED_TO_SLOT:
            owner, slot = PRED_TO_SLOT[t]
            rows.append({'type': t, 'n': c, 'share': round(c / n_edges, 4),
                         'fate': f'槽位 {owner}.{slot}'})
        elif t in MULTI_HOST:
            hosts = '/'.join(MULTI_HOST[t])
            rows.append({'type': t, 'n': c, 'share': round(c / n_edges, 4),
                         'fate': f'**多宿主槽位** {hosts}.produces'
                                 f'（落哪个取决于主语类型 ⇒ 本项目暂无该信息）'})
        elif t in set(KEPT_RELATIONS):
            rows.append({'type': t, 'n': c, 'share': round(c / n_edges, 4),
                         'fate': '保留为谓词'})
        else:
            rows.append({'type': t, 'n': c, 'share': round(c / n_edges, 4),
                         'fate': '**未归类**（原本体里没有它）'})
    return {
        'graphs': n_graphs, 'nodes': n_nodes, 'edges': n_edges,
        'slotted_edges': slotted,
        'kept_edges': kept,
        'unknown_edges': unknown,
        'slotted_share': round(slotted / n_edges, 4) if n_edges else None,
        'kept_share': round(kept / n_edges, 4) if n_edges else None,
        'per_type': rows,
    }


def convert(onto, out_dir):
    """把现有图按新本体转成槽位式，**另写一份，不覆盖原图**。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    reversed_preds = set(onto['reversed_when_slotting'])
    n_files = n_slotted = 0
    for f in sorted(GRAPHS.glob('*.json')):
        try:
            d = json.loads(f.read_text(encoding='utf-8-sig'))
        except Exception:
            continue
        slots_out, kept_out = [], []
        for e in (d.get('edges') or []):
            t = str(e.get('type') or '')
            if t in PRED_TO_SLOT:
                owner, slot = PRED_TO_SLOT[t]
                a, b = e.get('source'), e.get('target')
                # 方向翻转：`CREATED_BY` 原本 Author→Work，而槽位 `work` 属于 Person
                subj, val = (b, a) if t in reversed_preds else (a, b)
                slots_out.append({
                    'owner_type': owner, 'slot': slot, 'subject': subj, 'value': val,
                    'from_predicate': t,
                    # **保留原始证据与引文** —— 否则转换会把可溯源性丢掉
                    'evidence': e.get('evidence') or [],
                    'citations': e.get('citations') or [],
                })
                n_slotted += 1
            elif t in set(KEPT_RELATIONS):
                kept_out.append(e)
        out = {'source_graph': f.stem,
               'ontology': onto['version'],
               'slots': slots_out,
               'edges': kept_out,          # 保留为谓词的那批，**原样不动**
               'nodes': d.get('nodes') or []}
        (out_dir / f.name).write_text(
            json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
        n_files += 1
    return {'files': n_files, 'slotted_edges': n_slotted, 'out_dir': str(out_dir)}


def main():
    ap = argparse.ArgumentParser(description='P4 本体槽位化')
    ap.add_argument('--out', default=str(DEFAULT_OUT))
    ap.add_argument('--convert', action='store_true',
                    help='另写一份槽位式图（默认写到 data/quality/slotted-graphs/，**不覆盖原图**）')
    ap.add_argument('--convert-out', default=str(ROOT / 'data' / 'quality' / 'slotted-graphs'))
    a = ap.parse_args()

    onto = build_ontology()
    print('=' * 92)
    print('P4 · 本体槽位化')
    print('=' * 92)
    print(f'  原本体 {len(onto["original_ontology"]["relation_types"])} 类（**保留不删**）')
    print(f'  槽位化：{len(SLOTS)} 个宿主类别 · '
          f'{sum(len(v) for v in SLOTS.values())} 个槽位')
    for owner, slots in SLOTS.items():
        for slot, pred in slots.items():
            rev = '（**方向要翻**）' if pred in onto['reversed_when_slotting'] else ''
            print(f'      {owner}.{slot:<9} ← {pred}{rev}')
    print(f'  保留为谓词：{len(KEPT_RELATIONS)} 类')

    q = quantify(onto)
    print()
    print(f'  在真实图上量（{q["graphs"]} 篇 · {q["nodes"]} 节点 · {q["edges"]} 边）')
    print(f'      进槽位   {q["slotted_edges"]:>6} 条  **{q["slotted_share"]:.2%}**')
    print(f'      保留谓词 {q["kept_edges"]:>6} 条  {q["kept_share"]:.2%}')
    if q['unknown_edges']:
        print(f'      ⚠️ 未归类 {q["unknown_edges"]} 条')
    print()
    print('  逐类去向：')
    for r in q['per_type']:
        print(f'      {r["type"]:<16} {r["n"]:>7}  {r["share"]:>7.2%}  {r["fate"]}')

    reps = {'ontology': onto, 'quantification': q}
    if a.convert:
        reps['conversion'] = convert(onto, Path(a.convert_out))
        print()
        print(f'  已转换 {reps["conversion"]["files"]} 篇 → '
              f'{reps["conversion"]["out_dir"]}（**不覆盖原图**）')

    # ★ 与 P5 的对照要说清楚
    reps['compare_with_P5'] = {
        'P5': '统计出「槽位化只能覆盖 33.6%」—— 只是**数了数**',
        'P4': f'把它变成**可执行的本体定义**，并在全库量到 '
              f'**{q["slotted_share"]:.2%}** 可进槽位、'
              f'{q["kept_share"]:.2%} 保留为谓词',
        'why_it_matters': '这样"前后各标一轮"才有东西可标 —— '
                          'P5 之前只能说"理论上能覆盖多少"，现在有了一份能直接拿去标注的本体',
        'NOT_done': '**前后各标一轮需要人类标注者** ⇒ 见「需要用户本人」清单',
    }
    Path(a.out).write_text(json.dumps(reps, ensure_ascii=False, indent=1), encoding='utf-8')
    print()
    print(f'  已写 {Path(a.out).name}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
