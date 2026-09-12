"""核实「从边反建节点」这条路可不可行 —— L3 缺口的补救选项取决于此。

上一份分析发现：15571 个缺失端点**在任何论文的节点表里都不存在**，
所以"跨论文别名映射"救不了它们。剩下的路只有两条：
  A. 重新抽取（让 LLM 把这些名字也抽成节点）—— 本地约 34 小时
  B. **从边反建节点**（边既然引用了它，就把它建成节点）—— 零成本

B 是否可行，取决于三个可查的事实：
  ① 去重后到底有多少个名字（规模）；
  ② 这些名字**是否真在原文里出现过**（用边自带的 evidence 逐字回查）——
     出现过说明是真实体、只是没被抽成节点；没出现过才是噪声；
  ③ 能否给它们定一个 type（从边另一端节点的类型推断）。
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import json
from collections import Counter, defaultdict
from pathlib import Path

VERIFY = ROOT / 'data/quality/relation-verify-all.json'
GRAPH = ROOT / 'data/corpus/graphs'


def norm(s):
    import re
    return re.sub(r'[^a-z0-9]+', '', (s or '').lower())


def main():
    d = json.loads(VERIFY.read_text(encoding='utf-8'))
    edges = d['edges']

    node_type = {}
    for f in sorted(GRAPH.glob('*.json')):
        snap = json.loads(f.read_text(encoding='utf-8'))
        for n in snap.get('nodes', []):
            nm = (n.get('name') or '').strip()
            if nm and nm not in node_type:
                node_type[nm] = n.get('type') or 'Concept'
    known = set(node_type)
    print(f'已知节点 {len(known)} 个')

    miss_occ = Counter()               # 缺失名字 -> 出现次
    evidence_hit = Counter()           # 名字在自带的 evidence 里出现过？
    neighbour_types = defaultdict(Counter)   # 缺失名字 -> 边另一端节点的类型分布

    for e in edges:
        # ⚠️ 注意 other_side 是**键名**，取值必须再 e[other_side]。
        # 初版写成 `for side, other in (...)` 然后直接用 `other in known`，
        # 那是拿字符串 'target' 去做成员判断，恒为 False ——
        # 于是"另一端是否已知"和"推断类型"两项全错（后者因此恒为 0%）。
        for side, other_side in (('source', 'target'), ('target', 'source')):
            nm = e[side]
            if nm in known:
                continue
            miss_occ[nm] += 1
            ev = norm(e.get('evidence') or '') + norm(e.get('premise') or '')
            if norm(nm) and norm(nm) in ev:
                evidence_hit[nm] += 1
            ov = e[other_side]
            if ov in known:
                neighbour_types[nm][node_type[ov]] += 1

    uniq = list(miss_occ)
    print(f'\n去重后缺失名字: {len(uniq)}')
    print(f'缺失端点次  : {sum(miss_occ.values())}')

    hit = sum(1 for n in uniq if evidence_hit[n] > 0)
    print(f'\n其中**在自带的原文证据里逐字出现过**的: {hit} / {len(uniq)} '
          f'({hit/len(uniq)*100:.1f}%)')
    occ_hit = sum(miss_occ[n] for n in uniq if evidence_hit[n] > 0)
    print(f'    按出现次加权: {occ_hit} / {sum(miss_occ.values())} '
          f'({occ_hit/sum(miss_occ.values())*100:.1f}%)')

    # 类型可推断性
    inferable = [n for n in uniq if neighbour_types[n]]
    print(f'\n能从邻居节点推断出 type 的: {len(inferable)} / {len(uniq)} '
          f'({len(inferable)/len(uniq)*100:.1f}%)')

    # 高频名字的"质量"抽样
    print('\n=== 出现 >=10 次的名字（按次数降序，看它们像不像真实体）===')
    print(f'  {"次数":>5s} {"证据命中":>8s}  名字 / 推断类型')
    for nm, c in miss_occ.most_common(30):
        nt = neighbour_types[nm].most_common(1)
        t = nt[0][0] if nt else '—'
        eh = evidence_hit[nm]
        print(f'  {c:5d} {eh:8d}  {nm[:52]!r}  → {t}')

    # 分档：可安全反建的 vs 需人工看的
    safe = [n for n in uniq if evidence_hit[n] > 0 and len(n) >= 3
            and any(ch.isalpha() for ch in n)]
    print(f'\n=== 若只在"证据命中 且 名字长度>=3"的条件下反建节点 ===')
    print(f'  可反建名字数: {len(safe)}')
    print(f'  覆盖的端点次: {sum(miss_occ[n] for n in safe)} '
          f'/ {sum(miss_occ.values())} '
          f'({sum(miss_occ[n] for n in safe)/sum(miss_occ.values())*100:.1f}%)')

    out = {
        'unique_missing_names': len(uniq),
        'missing_endpoint_slots': sum(miss_occ.values()),
        'evidence_hit_names': hit,
        'evidence_hit_slots': occ_hit,
        'inferable_type_names': len(inferable),
        'safe_to_backfill_names': len(safe),
        'safe_to_backfill_slots': sum(miss_occ[n] for n in safe),
        'top_missing': [{'name': n, 'count': c, 'evidence_hit': evidence_hit[n],
                         'guessed_type': (neighbour_types[n].most_common(1) or [('—', 0)])[0][0]}
                        for n, c in miss_occ.most_common(80)],
    }
    p = ROOT / 'data/quality/l3-backfill-feasibility.json'
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n报告已写出: {p}')


if __name__ == '__main__':
    main()
