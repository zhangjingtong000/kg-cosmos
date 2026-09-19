"""数据新鲜度总览：一眼看出「哪些数据需要重跑」。

【为什么单独做这个】
  这个项目有 40+ 个 JSON 产物，分布在 data/quality、data/corpus/graphs、
  web/kg-data 三处，彼此有依赖顺序。人（包括我自己）很难凭记忆判断
  "改了 A 之后哪些下游要重跑"。

  本项目已有两件相关工具，但它们答的不是同一个问题：
    · `metric_provenance.py` —— 指标读数 vs 数据指纹，答"数据变了没重算"
    · `check_doc_numbers.py`  —— 文档/页面里的数字 vs 产物，答"写错了没"
  缺的是「**产物之间的先后依赖**」这一维：某个产物是不是在它依赖的上游
  之后生成的？本脚本补这一维。

用法：
  python tools/freshness_report.py
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def mt(p):
    try:
        return os.stat(ROOT / p).st_mtime
    except Exception:
        return None


def fmt(t):
    return time.strftime('%m-%d %H:%M', time.localtime(t)) if t else '缺失'


# (产物, 依赖, 重建命令)
# 重建命令写在声明里，`--fix` 才能自动跑——否则"记得重跑下游"仍然靠人。
CHAIN = [
    ('data/quality/relation-verify-all.json', [
        ('data/corpus/graphs', 'L5 打分依赖快照'),
    ], [sys.executable, 'tools/relation_verifier.py', '--all']),
    ('data/quality/l5-six-layer.json', [
        ('data/quality/relation-verify-all.json', '六层依赖 L5 打分'),
    ], [sys.executable, 'tools/l5_report.py', '--json']),
    ('data/quality/subgraph-supported.json', [
        ('data/quality/relation-verify-all.json', 'L5 子图依赖打分'),
    ], [sys.executable, 'tools/l5_subgraph.py']),
    ('data/quality/l7-direction-verification.json', [
        ('data/corpus/graphs', 'L7 依赖快照 + 类型表'),
    ], [sys.executable, 'tools/l7_direction_verify.py']),
    ('data/quality/l7-relation-constraints.json', [
        ('data/corpus/graphs', 'L7 全类审计依赖快照'),
    ], [sys.executable, 'tools/l7_relation_constraints_audit.py']),
    ('data/quality/l8-structure-audit.json', [
        ('web/kg-data/nebula.json', 'L8 结构审计读概览层导出'),
    ], [sys.executable, 'tools/l8_structure_audit.py']),
    ('data/quality/l9-recall.json', [
        ('data/corpus/graphs', 'L9 召回依赖快照'),
        # ⚠️⚠️ 原来这里是 `None`，注释写着「**需要人工标注，不能自动重建**」——
        #   **那是错的**（2026-09-18 逐条核验，见 `docs/早先的审计记录.md`）。
        #
        #   依据：`data/quality/l9-gold-spans.json` 的 `annotator` 字段自己写着
        #     「独立于抽取管线的逐段人工标注」，而 `note` 说的是
        #     「**操作性**金标……由不经过抽取管线的**人/模型**给出」，
        #     并且**只有 8 个 span**。
        #   ⇒ 它要求的不是"**真人**"，而是"**一个独立于抽取管线的标注者**"。
        #   ⇒ 那个标注者**可以是模型**。
        #
        #   **⇒ 而 `None` 的代价是具体的**：这条链**永远报红**、`--fix` **修不掉**，
        #     而一个"标注为不可自动重建"的条目，会**掩盖它其实可以重建**这件事。
        #     （这与 `L19` / `P4` 那两处"那只有用户本人能做"是同一个毛病 ——
        #       **没解释为什么不能替代，就写上了"只能人做"。**）
        ('data/quality/l9-gold-spans.json',
         'L9 的标注单元（**独立标注者**给出；`l9_recall.py --sample` 导出待标、'
         '`--score` 读回）'),
    ], [sys.executable, 'tools/l9_recall.py', '--score']),
    ('data/quality/l5-validity-report.json', [
        ('data/quality/l5-validity-labels.jsonl', '效度报告依赖人工盲判标签'),
        ('data/quality/relation-verify-all.json', '也依赖打分（分层键）'),
    ], [sys.executable, 'tools/l5_validity_report.py']),
    ('data/quality/l7-role-violation-audit.json', [
        ('data/quality/l7-constraint-samples.json', '归因依赖抽查样本'),
    ], [sys.executable, 'tools/check_l7_role_violation.py']),
    # ── 前端数据面 ──
    ('web/kg-data/nebula.json', [
        ('data/corpus/graphs', 'nebula 从快照/Neo4j 导出'),
    ], [sys.executable, 'tools/build_nebula_data.py']),
    ('web/kg-data/filaments.json', [
        ('web/kg-data/nebula.json', 'filaments 依赖 nebula'),
    ], [sys.executable, 'tools/build_filament_data.py']),
    ('web/kg-data/force-full.json', [
        ('web/kg-data/filaments.json', 'force 依赖社区划分'),
    ], [sys.executable, 'tools/build_force_data.py', '--full']),
    # ⚠️ 2026-09-19 补两条：**社区内部视图的两份数据**。
    #
    # 实测踩到（加中文语料那轮）：`filaments.json` 重建 ⇒ 社区划分重排，
    # 而 `comm-*.json`（还是 09-16 的旧划分）**不在本链里** ⇒ 没有任何东西提醒要重跑。
    # 症状很隐蔽：页面能开、社区能进，但
    #   · `?n=<实体名>` 恢复不了（`node_missing=true`）；
    #   · `?hl=` 一个都命中不了（`__hlHit = 0`）；
    # 因为页面按**新** filaments 的社区号去读**旧**的 `comm-<i>.json`
    # （实测：新 c0 叫 "AAAI"，旧 comm-0.json 叫 "Triple" —— 整个错位）。
    # ⇒ 两份都挂上。`summaries` 实际读 `comm-*.json`，这里用 `community-index.json`
    #    作**代理依赖**表达"必须在 details 之后"（两者由同一个脚本一次写出）。
    ('web/kg-data/community-index.json', [
        ('web/kg-data/filaments.json', '社区内部视图依赖社区划分'),
    ], [sys.executable, 'tools/build_community_details.py']),
    ('web/kg-data/community-summaries.json', [
        ('web/kg-data/community-index.json', '摘要依赖 comm-*.json（由上一个脚本写出）'),
    ], [sys.executable, 'tools/build_community_summaries.py']),
    ('web/kg-data/analytics.json', [
        ('data/quality/l5-six-layer.json', '面板汇总六层'),
        ('data/quality/l5-validity-report.json', '面板新增 L5 效度段'),
        ('data/quality/l7-role-violation-audit.json', '面板新增 L7 归因段'),
        ('web/kg-data/nebula.json', '面板的规模数字'),
    ], [sys.executable, 'tools/build_analytics_data.py']),
    # ⚠️ 2026-09-19 补两条：**证据索引一直在 freshness 链之外**（已知遗留）。
    #
    # 实测症状（加中文语料那轮）：`evidence-*.json` 停在 09-12，而中文图是
    # 09-19 入库的 ⇒
    #   · `corpus-index.json` 里中文论文的 `communities` 为**空** ⇒
    #     证据面板与"按论文筛图"**看不到中文图**；
    #   · 更隐蔽：`force-full.json` 重建后社区已重排，旧 evidence 仍按**旧社区号**
    #     落文件 ⇒ 实体与社区错位。
    # **两者都不报错** —— 只是"点进去什么都没有"，所以只能靠链来盯。
    ('web/kg-data/evidence-0.json', [
        ('web/kg-data/force-full.json',
         '证据索引按社区的实体表从 force-full 的社区划分出发'),
    ], [sys.executable, 'tools/build_evidence_index.py']),
    # 论文索引的 `communities` 是**从 `evidence-*.json` 反查**出来的
    # （社区 → 实体 → 证据 src），同时读图快照算每篇规模。
    ('web/kg-data/corpus-index.json', [
        ('web/kg-data/evidence-0.json', 'communities 从 evidence-*.json 反查'),
        ('data/corpus/graphs', '每篇的节点/边规模'),
        # ⚠️ 2026-09-19 补：**清单一直是这条链的漏登记的依赖**。
        #   清单是 id/title/year/query/`archived` 的第一来源（本脚本逐条读它组装 papers）。
        #   实测症状（归档标记贴错对象那轮）：改了清单的 `archived`，
        #   本脚本**一条都不报** —— 因为这条依赖没登记。
        ('data/corpus/_manifest.json', '清单条目（id/title/year/query/archived）'),
    ], [sys.executable, 'tools/build_corpus_index.py']),
    # ⚠️⚠️ 2026-09-19 补两条：**注册表与 PRISMA 也一直在 freshness 链之外**（已知遗留）。
    #
    # 症状（同一轮）：`_manifest.json` 的归档标记改了，`paper-registry.json`
    # **根本不在链里** ⇒ 没有任何东西提醒重跑；而论文页（`web/papers.html`）的
    # 三组「N 篇 · 已出图 M 篇 · 共 K 节点」与「已归档」标签**全从它来**
    # ⇒ 页面停在旧数字，且**不报错**。`prisma.json` 同理（它从 corpus-index 派生）。
    # ⇒ 两条都挂上，`--fix` 才会自动重建。
    ('web/kg-data/prisma.json', [
        ('web/kg-data/corpus-index.json', 'PRISMA 的阶段数与排除数从语料索引来'),
    ], [sys.executable, 'tools/build_prisma_data.py']),
    ('web/kg-data/paper-registry.json', [
        ('data/corpus/_manifest.json', '清单是 id/title/archived 的第一来源'),
        ('web/kg-data/corpus-index.json', '每篇的 in_graph 与节点/边规模'),
        ('data/corpus/graphs', '图落位（in_graph / graph_file）'),
    ], [sys.executable, 'tools/build_paper_registry.py']),
]


def newest(d):
    """目录下最新文件的 mtime。"""
    dd = ROOT / d
    if not dd.exists():
        return None
    ts = [os.stat(f).st_mtime for f in dd.glob('*.json')]
    if not ts:
        ts = [os.stat(f).st_mtime for f in dd.rglob('*') if f.is_file()]
    return max(ts) if ts else None


# 源目录（用 newest 而不是单文件 mtime）
SRC_DIRS = {'data/corpus/graphs'}


def resolve(p):
    """按路径形态取 mtime：目录用 newest，文件用 os.stat。"""
    p = p.replace('/*.json 之最新', '').strip()
    if p in SRC_DIRS or p.endswith('/'):
        return newest(p.rstrip('/'))
    return mt(p)


def main():
    ap = argparse.ArgumentParser(description='数据新鲜度：产物是否晚于其依赖的上游')
    ap.add_argument('--fix', action='store_true',
                    help='发现 stale 就自动跑重建命令（声明在 CHAIN 里）')
    ap.add_argument('--dry-run', action='store_true', help='--fix 时只打印不执行')
    args = ap.parse_args()

    print('=== 数据新鲜度总览（产物是否晚于它依赖的上游）===\n')
    stale = []
    for target, deps, rebuild in CHAIN:
        tt = resolve(target)
        row = []
        for dep, why in deps:
            dt = resolve(dep)
            if dt is None:
                row.append(('上游缺失', dep, why))
                continue
            if tt is None:
                row.append(('产物缺失', dep, why))
                continue
            if tt < dt - 1:      # 容 1 秒的文件系统粒度
                row.append(('STALE', dep, why))
                stale.append((target, dep, why, rebuild))
            else:
                row.append(('ok', dep, why))
        mark = '❌' if any(r[0] == 'STALE' for r in row) else (
            '·' if any(r[0] not in ('ok',) for r in row) else '✅')
        print(f'  {mark} {target}  ({fmt(tt)})')
        for status, dep, why in row:
            if status != 'ok':
                print(f'      └─ {status}: {dep} —— {why}')

    print()
    if not stale:
        print('✅ 所有产物都不早于它们依赖的上游 —— 没有需要重跑的数据。')
        print()
        print('（注：本脚本只看**产物之间的先后**；"数据变了但指标没重算"由')
        print('  tools/metric_provenance.py 负责，"文档写错了没"由')
        print('  tools/check_doc_numbers.py 负责。三者互补。）')
        return 0

    # 去重：同一个产物可能因多个上游 stale 而出现多次
    todo, seen = [], set()
    for t, d, w, rb in stale:
        if t in seen:
            continue
        seen.add(t)
        todo.append((t, d, rb))

    print(f'❌ 有 {len(todo)} 个产物 STALE（早于它依赖的上游）：')
    for t, d, rb in todo:
        cmd = ' '.join(rb) if rb else '（需人工，无法自动重建）'
        print(f'   · {t}')
        print(f'       上游: {d}')
        print(f'       重建: {cmd}')

    if not args.fix:
        print('\n重跑：E:\\Python313\\python.exe tools/freshness_report.py --fix')
        print('⚠️ `--fix` **是本脚本的参数** —— `verify_all.py` 不接受它。')
        print('   本脚本被 `verify_all.py` 当子检查调用时，这行提示会原样出现在父输出里，')
        print('   照着敲 `verify_all.py --fix` 会**静默无效**（参数被忽略，什么都不重算）。')
        print('（重算的完整顺序见 RUNBOOK.md §6 —— **在项目根目录**，不在 docs/ 下）')
        return 1

    import subprocess
    print('\n=== --fix：自动重建 ===')
    failed = []
    for t, d, rb in todo:
        if not rb:
            print(f'  ⏭  {t} —— 需人工，跳过')
            continue
        print(f'  ▶  {t}')
        r = subprocess.run(rb, cwd=str(ROOT), capture_output=True, text=True)
        ok = r.returncode == 0
        print(f'     {"✅" if ok else "❌"} exit={r.returncode}')
        if not ok:
            failed.append((t, (r.stderr or '')[-200:]))
    print()
    if failed:
        print('以下重建失败：')
        for t, e in failed:
            print(f'  · {t}: {e}')
        return 1
    print('重建完成；建议再跑一次本脚本确认（有些产物有链式依赖）。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
