# -*- coding: utf-8 -*-
r"""B5 的验收：**产物到底变了没有**（不看出代码，看数）。

## 为什么单独写这个

B5 的实现里连踩三处「假成功」，而**三处的外表都是"成功"**：

```
① ingest_corpus 硬编码 backend="neo4j"  ⇒ rc=1（被当成"环境问题"）
② OUT.exists() 让 --apply 早退          ⇒ "重启"其实是又打印了一遍旧结果
③ skip (done)                            ⇒ **rc=0 · 0.0 分钟 · 看起来成功** —— 一次都没抽
```

**⇒ 教训是**：`rc=0` 不等于"做了事"。**验收要看"产物变了没有"。**

⇒ 本脚本就做那一件事：**把每次跑的图与"改动前那份"逐项比**：

| 比什么 | 为什么 |
|---|---|
| **文件哈希** | **完全相同 ⇒ 那次跑等于没跑**（skip 或缓存命中） |
| **节点数 / 边数** | 变化 ⇒ 真重抽了 |
| **关系类型分布** | A/B 比的正是这个（`APPLIES_TO` 28→44 之类） |
| **长标题碎片数** | A/B 的第三个指标（24 → 10） |

**⚠️ 而"变了"本身还不构成 A/B 的结论** —— 那要走 `l5_report` 的金标口径算 F1。
本脚本只回答一个**更前置**的问题：**这些产物是不是真的不同。**

用法：
    python tools/check_prompt_ab.py
    python tools/check_prompt_ab.py --runs data/quality/_prompt-ab-runs
"""
import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / 'data' / 'quality' / '_prompt-ab-runs'


def sha8(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()[:8]


def stats(p):
    """一份图的规模与关系分布。"""
    d = json.loads(p.read_text(encoding='utf-8'))
    nodes = d.get('nodes') or []
    edges = d.get('edges') or []
    rels = Counter(str(e.get('type') or '') for e in edges)
    # **长标题碎片**：被切开的长标题（`OPTIMIZATION_PLAN.md` 用的那个指标）
    frag = 0
    for n in nodes:
        nm = str(n.get('name') or '')
        # 长（>40 字符）且以连字符/冒号断开的名字，视为碎片候选
        if len(nm) > 40 and re.search(r'[:\-–—]\s*$', nm):
            frag += 1
    return {'nodes': len(nodes), 'edges': len(edges),
            'rels': dict(rels.most_common(8)), 'fragments': frag}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', default=str(RUNS))
    a = ap.parse_args()
    runs = Path(a.runs)
    print('=' * 92)
    print('B5 验收：**产物到底变了没有**（`rc=0` 不算证据）')
    print('=' * 92)
    if not runs.exists():
        print(f'  ❌ 缺 {runs}（还没跑过）')
        return 1
    files = sorted(p for p in runs.glob('*.json'))
    if not files:
        print(f'  ⚠️ {runs.name}/ 里没有 json ⇒ **还没产出任何一次的结果**')
        print('     （若进程还在跑，那是正常的；**跑完才会有 `<tag>.json`**）')
        return 0

    base = runs / '_before.json'
    print(f'  找到 {len(files)} 份产物'
          + (f' · 基线 {base.name}' if base.exists() else ' · **没有基线**'))
    print()
    b = stats(base) if base.exists() else None
    bh = sha8(base) if base.exists() else None
    if b:
        print(f'  基线（改动前）：{b["nodes"]} 节点 / {b["edges"]} 边'
              f' · 碎片 {b["fragments"]} · sha {bh}')
        print(f'      关系分布：{json.dumps(b["rels"], ensure_ascii=False)[:110]}')
    print()
    ok_any = False
    for f in files:
        if f.name.startswith('_'):
            continue
        try:
            s = stats(f)
        except Exception as e:
            print(f'  ✗ {f.name}：读失败 {type(e).__name__}')
            continue
        h = sha8(f)
        same = (b is not None and h == bh)
        mark = '❗**与基线完全相同 ⇒ 那次等于没跑**' if same else '✓ 与基线不同（真跑了）'
        if not same:
            ok_any = True
        print(f'  {f.name:<16}{s["nodes"]:>6} 节点 {s["edges"]:>6} 边'
              f' · 碎片 {s["fragments"]:>3} · sha {h}  {mark}')
        if b and not same:
            dn, de = s['nodes'] - b['nodes'], s['edges'] - b['edges']
            df = s['fragments'] - b['fragments']
            print(f'      相对基线：节点 {dn:+d} · 边 {de:+d} · 碎片 {df:+d}')
            print(f'      关系分布：{json.dumps(s["rels"], ensure_ascii=False)[:110]}')
            # ⚠️⚠️ **规模差得太多时，两者可能不是同一口径** —— 实测踩到：
            #   基线 **681 节点 / 1391 边**，而新抽的 `old-s0` 是 **344 / 272**。
            #   那不是"抽坏了"，很可能**基线来自多次抽取的累积**（或含上传的那份），
            #   而新的是**一次干净的抽取** ⇒ **拿它们直接比 F1 是错的**。
            #   ⇒ 这里主动报警，而不是让那个数悄悄进结论。
            if b['nodes'] and abs(dn) / b['nodes'] > 0.25:
                print(f'      ⚠️ **规模相差 {abs(dn) / b["nodes"]:.0%}（>25%）** ⇒')
                print('         这两份**很可能不是同一口径**'
                      '（基线可能是多次累积 / 含上传版本）')
                print('         ⇒ **不要直接拿它们比 F1**；'
                      '先确认两者的抽取配置是否一致。')
    print()
    print('=' * 92)
    if not ok_any:
        print('  ❌ **没有任何一份产物与基线不同** ⇒')
        print('     那意味着**这些"跑"都没有真正重新抽取** ——')
        print('     正是 `skip (done)` / `OUT.exists()` 那三处假成功的形态。')
        print('     ⇒ **不要把它们当 A/B 的数据用。**')
        return 1
    print('  ✅ 至少有一份产物与基线不同 ⇒ **真抽过**')
    print('  ★ 而"变了"还不是结论：那要走 `l5_report` 的金标口径算 F1，')
    print('     再看**新旧两个区间是否重叠** —— 那才是 B5 的结论。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
