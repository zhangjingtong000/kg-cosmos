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
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import argparse
import glob
import hashlib
import json
import os
import sys
import time

ROOT = str(ROOT)

# `_rel` 的实现抽到 `tools/_paths.py`（见 N1 的说明）。
sys.path.insert(0, os.path.join(ROOT, 'tools'))
from _paths import rel as _shared_rel  # noqa: E402

Q = os.path.join(ROOT, 'data', 'quality')
# 当前文件路径可用 `KG_CURRENT_FILE` 重定向（测试用），否则写仓库里的真实文件。
# 不加这个的话，`--write` 的回归测试每跑一次都会改写被 git 跟踪的
# `data/quality/metric-provenance.json`（时间戳变），跑完就必须手动还原。
OUT = os.environ.get('KG_CURRENT_FILE') or os.path.join(Q, 'metric-provenance.json')

# ── 档案目录（2026-09-16 加，N1）──────────────────────────────────────────
#
# 【为什么必须加】这个脚本原先**覆盖写** `metric-provenance.json`：
# 一旦数据变了、指标重算、再 `--write`，**上一版就永久没了**。
# 于是无法回答"改之前那套指标是多少" —— 而 `docs/ARCHIVE_POLICY.md` 明确要求
# **被替代的东西要留档**。
#
# 【同一个病根】`tools/protocol_ladder.py` 也写固定路径，结果 P3 第二篇
# **把第一篇的产物覆盖了**（见 TODO P13）。两处是同一个毛病：
# **「覆盖写固定路径」+「产物在 gitignore 目录里」= 数据必然丢。**
#
# 【K1 为什么依赖它】改图会改变图规模 ⇒ 必须能拿出 **before / after 两套指标**。
# 没有归档，就只能"改完再记一遍"，而 before 已经没了。
#
# 【为什么要可配置】`tests/test_metric_archive.py` 会真的跑一次 `--write` 来验证
# 归档行为 —— 若它写进真实归档目录，**每跑一次 pytest 就多一份文件**（实测确实如此：
# 跑两次测试后归档就变成了 3 份）。那不是 bug，但会让归档目录被测试噪声填满。
# ⇒ 用 `KG_ARCHIVE_DIR` 让测试把它指到临时目录，**真实归档只由人手动产生**。
ARCHIVE_DIR = os.environ.get('KG_ARCHIVE_DIR') or os.path.join(
    Q, 'archive', 'metric-provenance')


def _rel(path):
    """尽量给相对路径；**跨盘时退回绝对路径**。

    实现已抽到 `tools/_paths.py` —— **因为这是第二次踩同一个坑**：
    `tools/apply_edit_plan.py` 也栽在 `Path.relative_to` 的跨盘
    `ValueError: path is on mount 'C:', start on mount 'D:'` 上。
    （第一次是本文件的 `KG_ARCHIVE_DIR` 被测试指到 C 盘。）
    这里保留一个薄包装，是为了不动本文件其它调用点。
    """
    return _shared_rel(path)


def _fname_ts(rec):
    """把一条记录的 `generated` 转成可用于文件名的紧凑时间戳。"""
    g = (rec or {}).get('generated') or ''
    return g.replace('-', '').replace(':', '').replace(' ', '-') or 'unknown'


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
    # L4 谓词**选择**层（2026-09-12 新增）。旧 L4 只判"谓词是否落在这 15 类内"，
    # 一条本该 USES 的边标成 PART_OF 照样通过 ⇒ 99.5% 接近自证。
    # 本项用同一条 premise 与 15 个假设句模板各算 entail_prob、取 argmax 作预测谓词，
    # 报"与标注谓词的一致率"。它是**两个模型的一致率**，不是与人工金标的准确率。
    'L4_predicate_agreement': ('l4-predicate.json', ['metrics', 'agreement_top1'],
                          'tools/l4_predicate_report.py'),
    'L4_predicate_agreement_excl_ties': ('l4-predicate.json',
                                    ['metrics', 'agreement_top1_excluding_ties'],
                                    'tools/l4_predicate_report.py'),
    'L4_predicate_macro_f1': ('l4-predicate.json', ['metrics', 'macro_f1'],
                              'tools/l4_predicate_report.py'),
    'L4_predicate_baseline_related_to': ('l4-predicate.json',
                                         ['metrics', 'baseline_related_to'],
                                         'tools/l4_predicate_report.py'),
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
    # L9 是本项目**唯一非自洽型**的指标：它从原文段落出发回答
    # "原文里的东西抽到没有"，而不是只检查图内部是否一致。
    'L9_endpoint_recall': ('l9-recall.json', ['endpoint_recall'], 'tools/l9_recall.py'),
    'L9_gold_entities': ('l9-recall.json', ['gold_entities'], 'tools/l9_recall.py'),
    # 反建精度抽检（噪声率上界）
    'l3_backfill_noise_share': ('l3-backfill-precision-sample.json',
                                ['heuristic_noise_share'], 'tools/l3_backfill_precision.py'),
    # ── L5 域内效度（2026-09-12）：本项目最重要的一个发现 ──
    # 63.7% 只是通过率，真实支持率 40.0%、PPV 49.7%。
    # 这几个数依赖 l5-validity-labels.jsonl（人工盲判标签），换标签就要重算。
    'L5_true_support_rate': ('l5-validity-report.json', ['true_support_rate'],
                             'tools/l5_validity_report.py'),
    'L5_ppv': ('l5-validity-report.json', ['ppv'], 'tools/l5_validity_report.py'),
    'L5_domain_auc': ('l5-validity-report.json', ['in_domain_auc'],
                      'tools/l5_validity_report.py'),
    'L5_vs_human_kappa': ('l5-validity-report.json', ['cohens_kappa'],
                          'tools/l5_validity_report.py'),
    'L5_annotator_kappa': ('l5-annotator-agreement.json', ['cohen_kappa'],
                           'tools/check_l5_agreement.py'),
    # ── L7 角色违规归因：决定论文里能不能写成"端点类型违规率" ──
    'L7_role_false_positive_rate': ('l7-role-violation-audit.json',
                                    ['false_positive_rate'],
                                    'tools/check_l7_role_violation.py'),
    'L7_role_predicate_wrong_rate': ('l7-role-violation-audit.json',
                                     ['predicate_wrong_rate'],
                                     'tools/check_l7_role_violation.py'),
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


def _list_archives():
    """归档文件按时间升序返回（最早的在前）。"""
    if not os.path.isdir(ARCHIVE_DIR):
        return []
    return sorted(glob.glob(os.path.join(ARCHIVE_DIR, '*.json')))


def _resolve_selector(sel):
    """把 A/B 选择子解析成一个文件路径。

    支持三种写法：
      · `current`          → 当前的 `metric-provenance.json`
      · 索引号（`0` 最早） → 第 N 份归档，`-1` = 最近一份归档
      · 文件名（可省 .json）→ 归档目录里匹配名字的唯一一份
    """
    if sel == 'current':
        return OUT
    if sel.lstrip('-').isdigit():
        arch = _list_archives()
        if not arch:
            raise SystemExit('还没有任何归档 —— 先跑一次 --write')
        i = int(sel)
        try:
            return arch[i]
        except IndexError:
            raise SystemExit(f'索引 {i} 越界（现有 {len(arch)} 份归档，'
                             f'可用的：0..{len(arch) - 1} 或 -1）')
    cand = os.path.join(ARCHIVE_DIR, sel)
    if os.path.exists(cand):
        return cand
    if os.path.exists(cand + '.json'):
        return cand + '.json'
    hits = glob.glob(os.path.join(ARCHIVE_DIR, f'*{sel}*')) if os.path.isdir(ARCHIVE_DIR) else []
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise SystemExit(f'{sel!r} 匹配到 {len(hits)} 份，请写得更具体')
    raise SystemExit(f'找不到 {sel!r}（归档目录：{_rel(ARCHIVE_DIR)}）')


def _load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def cmd_history():
    """列出历次归档 + 当前，并给出关键指标的走向。"""
    arch = _list_archives()
    print('=== 指标档案历史 ===\n')
    if not arch and not os.path.exists(OUT):
        print('  还没有任何记录。先跑 python tools/metric_provenance.py --write')
        return 1

    # 挑几个"大多数人最关心"的指标看走向（不是全部，全部太多）
    KEY = ['L1_entity_presence', 'L3_endpoints_all', 'L4_predicate_all',
           'L5_triplet_all', 'L6_evidence_all', 'edges_total',
           'L8_clustering_vs_random']
    rows = []
    for p in arch:
        rows.append((p, _load(p)))
    if os.path.exists(OUT):
        rows.append((OUT, _load(OUT)))

    print(f'  {len(arch)} 份归档 + ' + ('1 份当前' if os.path.exists(OUT) else '无当前'))
    print()
    hdr = f'{"#":<4}{"时间":<21}{"类型":<8}' + ''.join(f'{k.replace("_", " ")[:11]:>13}' for k in KEY)
    print(hdr)
    print('-' * len(hdr))
    for i, (p, rec) in enumerate(rows):
        kind = '当前' if p == OUT else '归档'
        vals = []
        for k in KEY:
            v = (rec.get('metrics') or {}).get(k, {}).get('value')
            if v is None:
                vals.append('—')
            elif isinstance(v, float):
                vals.append(f'{v:.4g}')
            else:
                vals.append(str(v))
        print(f'{i:<4}{rec.get("generated", "?"):<21}{kind:<8}'
              + ''.join(f'{x:>13}' for x in vals))
    print()
    print('  用 `--diff A B` 比较两份（`0` 最早、`-1` 最近一份归档、`current` 当前）。')
    return 0


def cmd_diff(sel_a, sel_b):
    """比较两份记录：数据指纹 + 逐指标。"""
    pa, pb = _resolve_selector(sel_a), _resolve_selector(sel_b)
    a, b = _load(pa), _load(pb)
    print('=== 指标档案对比 ===\n')
    print(f'  A = {_rel(pa)}   （{a.get("generated")}）')
    print(f'  B = {_rel(pb)}   （{b.get("generated")}）')
    print()

    print('[数据源指纹]')
    sa, sb = a.get('sources') or {}, b.get('sources') or {}
    for k in sorted(set(sa) | set(sb)):
        x, y = sa.get(k, {}), sb.get(k, {})
        same = (x.get('sha1_16') == y.get('sha1_16') and x.get('files') == y.get('files'))
        print(f'  {"=" if same else "≠"} {k:22s} '
              f'A {x.get("files")}/{x.get("sha1_16")}  '
              f'B {y.get("files")}/{y.get("sha1_16")}')
    print()

    print('[指标]')
    ma, mb = a.get('metrics') or {}, b.get('metrics') or {}
    changed = same = 0
    for k in sorted(set(ma) | set(mb)):
        va = ma.get(k, {}).get('value')
        vb = mb.get(k, {}).get('value')
        if va == vb:
            same += 1
            continue
        changed += 1
        fa = f'{va:.6g}' if isinstance(va, float) else str(va)
        fb = f'{vb:.6g}' if isinstance(vb, float) else str(vb)
        delta = ''
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)) and va not in (0, None):
            try:
                delta = f'  ({(vb - va) / abs(va) * 100:+.1f}%)'
            except Exception:
                delta = ''
        print(f'  ≠ {k:34s} {fa:>14} → {fb:>14}{delta}')
    print()
    print(f'  变化 {changed} 项 · 未变 {same} 项')
    if changed == 0:
        print('  （两份记录完全一致 —— 期间没有重算过任何指标）')
    return 0


def main():
    ap = argparse.ArgumentParser(description='指标与数据快照的版本绑定')
    ap.add_argument('--write', action='store_true', help='记录当前指纹与指标读数')
    # N1（2026-09-16）：归档相关
    ap.add_argument('--history', action='store_true',
                    help='列出历次归档（含关键指标），看"指标是怎么变的"')
    ap.add_argument('--diff', nargs=2, metavar=('A', 'B'),
                    help='比较两份记录。A/B 可以是归档文件名（可省略 .json）、'
                         '索引号（0=最早）、或字面量 current')
    args = ap.parse_args()

    now_fp = {k: digest(v) for k, v in SOURCES.items()}

    if args.history:
        return cmd_history()
    if args.diff:
        return cmd_diff(args.diff[0], args.diff[1])

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

        # ── 先归档旧的那份（**在覆盖之前**）────────────────────────────
        archived = None
        if os.path.exists(OUT):
            try:
                old = json.load(open(OUT, encoding='utf-8'))
                os.makedirs(ARCHIVE_DIR, exist_ok=True)
                archived = os.path.join(ARCHIVE_DIR, _fname_ts(old) + '.json')
                if os.path.exists(archived):
                    # 同一秒内重复 --write：加后缀，**不覆盖已归档的**
                    n = 2
                    while os.path.exists(archived.replace('.json', f'-{n}.json')):
                        n += 1
                    archived = archived.replace('.json', f'-{n}.json')
                json.dump(old, open(archived, 'w', encoding='utf-8'),
                          ensure_ascii=False, indent=2)
            except Exception as e:
                print(f'  ⚠️ 归档旧记录失败（不影响本次写入）：{type(e).__name__}: {e}')
                archived = None

        json.dump(rec, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        if archived:
            print(f'  📦 旧记录已归档 → {_rel(archived)}')
        else:
            print('  （没有旧记录可归档 —— 这是第一次写入）')
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
