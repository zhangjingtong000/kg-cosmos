"""诊断：为什么同样的官方代码，我的图变成一个球而官方 blocks 是多簇？

对比两个数据集的图论结构，找出决定性差异，并生成"修正版"数据用于验证。
"""
import json
import sys
from collections import Counter
from pathlib import Path

import networkx as nx

DATA = Path(r"D:\kg-demo\web\kg-data")
OFFICIAL = Path(r"D:\kg-demo\3d-force-graph\example\datasets")


def analyze(name, nodes, links):
    G = nx.Graph()
    for n in nodes:
        G.add_node(n["id"])
    for l in links:
        s = l["source"] if isinstance(l["source"], str) else l["source"].get("id")
        t = l["target"] if isinstance(l["target"], str) else l["target"].get("id")
        if s != t and s in G and t in G:
            G.add_edge(s, t)
    n, m = G.number_of_nodes(), G.number_of_edges()
    comps = sorted((len(c) for c in nx.connected_components(G)), reverse=True)
    degs = sorted((d for _, d in G.degree()), reverse=True)
    med = degs[len(degs) // 2]
    avg = 2 * m / n
    # 枢纽集中度：Top10 节点占全部度数多少
    top10 = sum(degs[:10])
    share = top10 / (2 * m) * 100
    # 直径（只看最大分量，太大则跳过）
    try:
        lcc = G.subgraph(max(nx.connected_components(G), key=len))
        diam = nx.diameter(lcc) if lcc.number_of_nodes() <= 3000 else -1
    except Exception:
        diam = -2
    print(f"\n=== {name} ===")
    print(f"  节点 {n}  边 {m}  平均度 {avg:.2f}  中位度 {med}")
    print(f"  最大度 {degs[0]}  Top10 度占比 {share:.1f}%")
    print(f"  连通分量 {len(comps)} 个；最大 {comps[0]}（占 {comps[0]/n*100:.1f}%）"
          f"  前 5 大小 {comps[:5]}")
    print(f"  最大分量直径 {'(过大跳过)' if diam == -1 else diam}")
    return {"n": n, "m": m, "avg": avg, "median": med, "maxdeg": degs[0],
            "top10share": share, "ncomp": len(comps), "maxcomp": comps[0],
            "comps": comps[:10], "diam": diam}, G


# ── 官方 blocks ──
bl = json.loads((OFFICIAL / "blocks.json").read_text(encoding="utf-8"))
s_bl, G_bl = analyze("官方 blocks.json", bl["nodes"], bl["links"])

# ── 我的 KG ──
kg = json.loads((DATA / "official-format-top1500.json").read_text(encoding="utf-8"))
s_kg, G_kg = analyze("我的 KG（official-format-top1500）", kg["nodes"], kg["links"])

print("\n" + "=" * 70)
print("决定性差异")
print("=" * 70)
print(f"  中位度     : 官方 {s_bl['median']}  vs  我的 {s_kg['median']}")
print(f"  最大度     : 官方 {s_bl['maxdeg']}  vs  我的 {s_kg['maxdeg']}")
print(f"  Top10度占比: 官方 {s_bl['top10share']:.1f}%  vs  我的 {s_kg['top10share']:.1f}%")
print(f"  连通分量   : 官方 {s_bl['ncomp']} 个  vs  我的 {s_kg['ncomp']} 个")
print(f"  最大分量   : 官方 {s_bl['maxcomp']}（{s_bl['maxcomp']/s_bl['n']*100:.0f}%）"
      f"  vs  我的 {s_kg['maxcomp']}（{s_kg['maxcomp']/s_kg['n']*100:.0f}%）")

# ── 我的 Top10 枢纽 ──
top = sorted(G_kg.degree(), key=lambda x: -x[1])[:10]
print(f"\n我的 Top10 枢纽（它们把全图拉成一个球）:")
for nm, d in top:
    print(f"   {nm[:44]:44s} 度 {d}")

# ── 生成两个"修正版"用于渲染验证 ──
# 方案 A：移除 Top 枢纽（度数最高的若干节点）
for k in (3, 8):
    hubs = {nm for nm, _ in sorted(G_kg.degree(), key=lambda x: -x[1])[:k]}
    keep_nodes = [n for n in kg["nodes"] if n["id"] not in hubs]
    keep_ids = {n["id"] for n in keep_nodes}
    keep_links = [l for l in kg["links"]
                  if l["source"] in keep_ids and l["target"] in keep_ids]
    out = {"nodes": keep_nodes, "links": keep_links}
    p = DATA / f"official-format-nohub{k}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    s2, _ = analyze(f"移除 Top{k} 枢纽后", keep_nodes, keep_links)
    print(f"   → 已写出 {p.name}")

# 方案 B：只保留度数 ≤ 阈值的节点（去掉枢纽，保留外围结构）
for thr in (30,):
    keep_nodes = [n for n in kg["nodes"] if (n.get("degree") or 0) <= thr]
    keep_ids = {n["id"] for n in keep_nodes}
    keep_links = [l for l in kg["links"]
                  if l["source"] in keep_ids and l["target"] in keep_ids]
    out = {"nodes": keep_nodes, "links": keep_links}
    p = DATA / f"official-format-lowdeg{thr}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    s3, _ = analyze(f"只保留度 ≤ {thr}", keep_nodes, keep_links)
    print(f"   → 已写出 {p.name}")
