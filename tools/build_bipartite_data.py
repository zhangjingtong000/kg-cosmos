# -*- coding: utf-8 -*-
r"""F4 · 「使用关系」二部图（`USES` 的**施动者 × 被用物**）。

## TODO 的原话与一处要改的命名

> **F4** | ⬜ **方法-数据集二部图** | 透镜一 backlog | —

⚠️ **"方法-数据集"这个名字不准确**。`USES` 的语义是
「source 使用了 target 这个**工具/方法/数据**」——
**target 侧是混的**，实测 top 里既有 `Embeddings`、`Convolutional Neural Networks`
（方法类），也有 `Knowledge Graphs`、`Triple`、`Entity`（概念/数据类）。

**要真的分成"方法侧"与"数据集侧"，需要实体类型** ——
而本项目的类型在相当一部分节点上是 `Unknown`
（L5 报告里就有这个口径问题）。
⇒ **所以本页按它实际测量的东西命名**：**`USES` 的左侧（施动者）× 右侧（被用物）**，
并在页面上明确写出「两侧是**施动/被用**，不是"方法/数据集"」——
**名字对不上内容是这一类页面最容易骗人的地方。**

## 数据规模（决定了必须截断）

```
USES 边 7381 条
左侧（施动者）3062 个不同实体
右侧（被用物）3623 个
```

⇒ 全量画出来是 6685 个节点的图，**看不出结构**。
按两侧度数取 top N（默认各 40），**只保留两端都在 top 里的边**。

⚠️ **截断会让"度数"失真**：一个度数很高的节点，它在图里连的边大部分被截掉了。
⇒ 所以每条边与每个节点**都带上全库真实度数**，页面上直接显示，
**不让人把"画出来的连线数"当成真实度数**。

用法：
    python tools/build_bipartite_data.py                 # 默认各取 40
    python tools/build_bipartite_data.py --top 60
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))

import graph_source  # noqa: E402

OUT = ROOT / 'web' / 'kg-data' / 'bipartite.json'


def parse_args():
    ap = argparse.ArgumentParser(description='F4 使用关系二部图')
    ap.add_argument('--top', type=int, default=40, help='两侧各取多少（按全库度数）')
    ap.add_argument('--rel', default='USES', help='用哪种关系（默认 USES）')
    return ap.parse_args()


def main():
    a = parse_args()
    t0 = time.time()
    print('=' * 92)
    print(f'F4 · 「使用关系」二部图（关系类型 {a.rel}）')
    print('=' * 92)

    nodes, edges = graph_source.load_graph(governed=False, verbose=False)
    print(f'  全库 {len(nodes)} 节点 / {len(edges)} 边')

    rel = [e for e in edges if str(e.get('type') or '').upper() == a.rel.upper()]
    print(f'  ★ {a.rel} 边 {len(rel)} 条')
    if not rel:
        print(f'  ❌ 没有 {a.rel} 边')
        return 1

    left_deg = Counter(str(e['source']) for e in rel)
    right_deg = Counter(str(e['target']) for e in rel)
    # 节点类型（有就用，没有标 Concept —— 但**页面上不会宣称它是"方法"**）
    ntype = {n['name']: (n.get('type') or 'Concept') for n in nodes}

    L = left_deg.most_common(a.top)
    R = right_deg.most_common(a.top)
    lset = {k for k, _ in L}
    rset = {k for k, _ in R}

    # **只保留两端都在 top 里的边** —— 否则会拖进上千个只出现一次的节点
    kept = [e for e in rel
            if str(e['source']) in lset and str(e['target']) in rset]
    # 同一对可能出现多次（不同论文）⇒ 去重并记下来源篇数
    pair = {}
    for e in kept:
        k = (str(e['source']), str(e['target']))
        pair.setdefault(k, 0)
        pair[k] += 1

    print(f'  取两侧各 top {a.top}：左 {len(L)} · 右 {len(R)}')
    print(f'  ⇒ 两端都在 top 里的边 {len(kept)} 条，去重后 {len(pair)} 对')
    print(f'  ⚠️ **截断会让度数失真** —— 所以每个节点的 `deg` 存的是**全库真实度数**')

    payload = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'purpose': 'F4：USES 的施动者 × 被用物 二部图',
        'naming': {
            'NOT_called': '**不叫"方法-数据集二部图"** —— USES 的 target 语义是'
                          '"工具/方法/数据"，**是混的**；要真分侧需要实体类型，'
                          '而本项目的类型在相当一部分节点上是 Unknown。',
            'actually_measured': '`USES` 的**左侧（施动者）×右侧（被用物）**。',
            'truncation': '两侧各取 top N，**只保留两端都在 top 里的边**。'
                          '截断会让"画出来的连线数"小于真实度数 ⇒ '
                          '所以每条边/每个节点都带**全库真实度数**。',
        },
        'relation': a.rel,
        'top': a.top,
        'totals': {'nodes': len(nodes), 'edges': len(edges),
                   'rel_edges': len(rel),
                   'left_entities': len(left_deg), 'right_entities': len(right_deg)},
        'left': [{'id': k, 'deg': v, 'type': ntype.get(k, 'Concept')} for k, v in L],
        'right': [{'id': k, 'deg': v, 'type': ntype.get(k, 'Concept')} for k, v in R],
        'links': [{'l': k[0], 'r': k[1], 'papers': v} for k, v in
                  sorted(pair.items(), key=lambda x: -x[1])],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                   encoding='utf-8')
    print(f'\n  已写出 {OUT}  ({OUT.stat().st_size / 1024:.0f} KB · '
          f'{time.time() - t0:.1f}s)')
    print('\n  左 Top5:')
    for k, v in L[:5]:
        print(f'      {v:>4}  {k[:56]}')
    print('  右 Top5:')
    for k, v in R[:5]:
        print(f'      {v:>4}  {k[:56]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
