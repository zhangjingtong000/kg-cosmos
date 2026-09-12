"""指标 ↔ 数据快照 的版本绑定与 stale 检测。

【要解决的问题（对抗评审列的 blocker 之一）】
  2026-09-12 那天里，数据被改了两轮（删 CONTRIBUTED_TO、L2 判据改版、
  从边反建 4304 个端点），每次都要人工记得重跑一长串下游脚本：
      sync_graph → relation_verifier → l5_report → l5_subgraph → l2_report
      → build_nebula_data → add_pagerank → build_filament_data →
        build_community_details → build_force_data → build_evidence_index
      → l8_structure_audit → build_analytics_data
  漏跑一个，`data/quality/*.json` 里就会同时躺着新旧两套数字，而且**没有任何提示**。
  实测确实发生了：反建落盘于 14:44，而 l5-six-layer.json 还是 13:22 的旧口径
  （L1 92.8% / L3 68.4%），两者在报告里并存。

【做法】
  不引入新依赖、不改任何既有脚本的调用方式，只做两件事：
    1. `--write`：把**当前数据指纹**与**各指标读数**记进 metric-provenance.json；
    2. 默认（校验）：重算当前数据指纹，与记录比对，不一致就把受影响的指标标 stale
       并以非零码退出，便于挂进回归。
  指纹用「文件数 + 总字节 + 最新 mtime + 内容 sha1 前 16 位」，
  足以发现"数据变了但指标没重跑"。
"""
import argparse
import glob
import hashlib
import json
import os
import sys
import time

ROOT = r'D:\kg-demo'
Q = os.path.join(ROOT, 'data', 'quality')
OUT = os.path.join(Q, 'metric-provenance.json')

# 数据源：这些变了，下游指标就必须重跑
SOURCES = {
    'graphs': os.path.join(ROOT, 'data', 'corpus', 'graphs', '*.json'),
    'relation_verify_all': os.path.join(Q, 'relation-verify-all.json'),
    'nebula': os.path.join(ROOT, 'web', 'kg-data', 'nebula.json'),
    'force_full': os.path.join(ROOT, 'web', 'kg-data', 'force-full.json'),
}

# 指标 → (产物文件, 产物里的取值路径, 生成脚本)
# 路径必须与产物实际结构一致：这些键名以 data/quality/*.json 为准，
# 猜错不会报错、只会静默记成 null（初版就猜错了 5 个）。
METRICS = {
    'L1_entity_presence': ('l5-six-layer.json', ['L1_entity_presence', 'rate'], 'tools/l5_report.py'),
    'L2_merged_groups_cumulative': ('l5-six-layer.json', ['L2_entity_clustering', 'cumulative_merged_groups'], 'tools/l2_report.py'),
    'L2_merged_groups_this_run': ('l5-six-layer.json', ['L2_entity_clustering', 'merged_groups', 'total_this_run'], 'tools/l2_report.py'),
    'L2_names_after': ('l5-six-layer.json', ['L2_entity_clustering', 'names_after'], 'tools/l2_report.py'),
    'L3_endpoints_all': ('l5-six-layer.json', ['L3_endpoints', 'all'], 'tools/l5_report.py'),
    'L4_predicate_all': ('l5-six-layer.json', ['L4_predicate', 'all'], 'tools/l5_report.py'),
    'L5_triplet_all': ('l5-six-layer.json', ['L5_triplet', 'all'], 'tools/l5_report.py'),
    'L6_evidence_all': ('l5-six-layer.json', ['L6_evidence', 'all'], 'tools/l5_report.py'),
    'hit_both_all': ('l5-six-layer.json', ['hit_both', 'all'], 'tools/l5_report.py'),
    'edges_total': ('l5-six-layer.json', ['edges_total'], 'tools/l5_report.py'),
    'edges_supported': ('l5-six-layer.json', ['edges_supported'], 'tools/l5_report.py'),
    'L7_created_by_direction': ('l7-direction-verification.json', ['compliance_rate'], 'tools/l7_direction_verify.py'),
    'L7_resolvable_edges': ('l7-direction-verification.json', ['edges_resolvable'], 'tools/l7_direction_verify.py'),
    'L8_clustering_vs_random': ('l8-structure-audit.json', ['random_baseline', 'clustering_ratio_vs_random'], 'tools/l8_structure_audit.py'),
    'L8_largest_component_pct': ('l8-structure-audit.json', ['connectivity', 'largest_component_pct'], 'tools/l8_structure_audit.py'),
    'L8_isolated_nodes': ('l8-structure-audit.json', ['connectivity', 'isolated_nodes'], 'tools/l8_structure_audit.py'),
    'L8_nodes': ('l8-structure-audit.json', ['scale', 'nodes'], 'tools/l8_structure_audit.py'),
}


def digest(pattern):
    files = sorted(glob.glob(pattern))
    if not files:
        return {'files': 0, 'missing': True}
    h = hashlib.sha1()
    total = 0
    newest = 0.0
    for f in files:
        st = os.stat(f)
        total += st.st_size
        newest = max(newest, st.st_mtime)
        h.update(os.path.basename(f).encode())
        h.update(str(st.st_size).encode())
        # 大文件只哈希首尾各 64KB，够用且快
        with open(f, 'rb') as fh:
            head = fh.read(65536)
            h.update(head)
            if st.st_size > 131072:
                fh.seek(-65536, os.SEEK_END)
                h.update(fh.read(65536))
    return {
        'files': len(files),
        'bytes': total,
        'newest_mtime': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(newest)),
        'sha1_16': h.hexdigest()[:16],
    }


def read_metric(fname, path):
    p = os.path.join(Q, fname)
    if not os.path.exists(p):
        return None
    try:
        d = json.load(open(p, encoding='utf-8'))
    except Exception:
        return None
    cur = d
    for k in path:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return None
    return cur


def main():
    ap = argparse.ArgumentParser(description='指标与数据快照的版本绑定')
    ap.add_argument('--write', action='store_true', help='记录当前指纹与指标读数')
    args = ap.parse_args()

    now_fp = {k: digest(v) for k, v in SOURCES.items()}

    if args.write:
        rec = {
            'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
            'note': '数据源指纹 + 各指标读数。数据变了而指标没重跑时，'
                    'python tools/metric_provenance.py 会把它们标为 stale。',
            'sources': now_fp,
            'metrics': {},
        }
        for name, (fname, path, script) in METRICS.items():
            val = read_metric(fname, path)
            mtime = None
            p = os.path.join(Q, fname)
            if os.path.exists(p):
                mtime = time.strftime('%Y-%m-%d %H:%M:%S',
                                      time.localtime(os.stat(p).st_mtime))
            rec['metrics'][name] = {
                'value': val, 'artifact': fname, 'path': '.'.join(path),
                'produced_by': script, 'artifact_mtime': mtime,
            }
        json.dump(rec, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        print(f'已记录 {len(rec["metrics"])} 个指标与 {len(now_fp)} 组数据指纹')
        print(f'  → {OUT}')
        for name, m in rec['metrics'].items():
            print(f'     {name:32s} = {m["value"]}   ({m["artifact_mtime"]})')
        return 0

    # ── 校验模式 ──
    if not os.path.exists(OUT):
        print('尚未建立版本绑定：先跑 python tools/metric_provenance.py --write')
        return 1
    rec = json.load(open(OUT, encoding='utf-8'))

    print('=== 指标 ↔ 数据快照 一致性 ===\n')
    print(f'记录时间 {rec["generated"]}\n')
    stale = []
    for k, fp in now_fp.items():
        old = rec['sources'].get(k, {})
        same = (old.get('sha1_16') == fp.get('sha1_16')
                and old.get('files') == fp.get('files'))
        mark = '✅' if same else '❌'
        print(f'  {mark} {k:22s} 记录 {old.get("files")} 文件/{old.get("sha1_16")}  '
              f'现在 {fp.get("files")} 文件/{fp.get("sha1_16")}')
        if not same:
            stale.append(k)

    print()
    # 产物内部的完整性检查：字段是不是 null 也算一种 "stale"。
    # L2 由 l2_report.py 填，而 l5_report.py 会重写同一个文件——
    # 在 l2_report 之后重跑一次 l5_report 就会把 L2 打回 null，
    # 分析面板随即显示 "? 组"。这个坑真的踩到过，所以在这里守一道。
    holes = []
    for name, (fname, path, script) in METRICS.items():
        v = read_metric(fname, path)
        if v is None:
            holes.append((name, fname, script))
    if holes:
        print('以下指标取不到值（产物字段缺失或为 null）：')
        for n, f, s in holes:
            print(f'  · {n:32s} ← {f}（应由 {s} 写入）')
        print('  常见原因：某个脚本把同一文件的字段重写成 null，'
              '或该产物还没跑过。')

    if stale:
        print('\n以下数据源已变化，相关指标必须重跑：')
        for k in stale:
            print(f'  · {k}  ⇒ 影响 {len(METRICS)} 个指标（全部重算最省事）')
        print('\n重算命令见 docs/L5_SIX_LAYER_REPORT.md §7。')
        print('重算完成后跑：python tools/metric_provenance.py --write')
        return 1

    if holes:
        return 1

    print('数据指纹未变，指标的版本绑定有效 ✅')
    return 0


if __name__ == '__main__':
    sys.exit(main())
