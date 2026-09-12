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
    ], None),                      # 需要人工标注，不能自动重建
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
    ('web/kg-data/analytics.json', [
        ('data/quality/l5-six-layer.json', '面板汇总六层'),
        ('data/quality/l5-validity-report.json', '面板新增 L5 效度段'),
        ('data/quality/l7-role-violation-audit.json', '面板新增 L7 归因段'),
        ('web/kg-data/nebula.json', '面板的规模数字'),
    ], [sys.executable, 'tools/build_analytics_data.py']),
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
        print('\n加 --fix 可自动重跑上面这些重建命令。')
        print('（重算的完整顺序见 RUNBOOK.md §6）')
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
