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
import json
import os
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


# (产物, 依赖)
CHAIN = [
    ('data/corpus/graphs/*.json 之最新', [
        ('data/corpus/graphs', '图谱快照（一切评测的源头）'),
    ]),
    ('data/quality/relation-verify-all.json', [
        ('data/corpus/graphs', 'L5 打分依赖快照'),
    ]),
    ('data/quality/l5-six-layer.json', [
        ('data/quality/relation-verify-all.json', '六层依赖 L5 打分'),
    ]),
    ('data/quality/subgraph-supported.json', [
        ('data/quality/relation-verify-all.json', 'L5 子图依赖打分'),
    ]),
    ('data/quality/l7-direction-verification.json', [
        ('data/corpus/graphs', 'L7 依赖快照 + 类型表'),
    ]),
    ('data/quality/l7-relation-constraints.json', [
        ('data/corpus/graphs', 'L7 全类审计依赖快照'),
    ]),
    ('data/quality/l8-structure-audit.json', [
        ('web/kg-data/nebula.json', 'L8 结构审计读概览层导出'),
    ]),
    ('data/quality/l9-recall.json', [
        ('data/corpus/graphs', 'L9 召回依赖快照'),
    ]),
    ('data/quality/l5-validity-report.json', [
        ('data/quality/l5-validity-labels.jsonl', '效度报告依赖人工盲判标签'),
        ('data/quality/relation-verify-all.json', '也依赖打分（分层键）'),
    ]),
    ('data/quality/l7-role-violation-audit.json', [
        ('data/quality/l7-constraint-samples.json', '归因依赖抽查样本'),
    ]),
    # ── 前端数据面 ──
    ('web/kg-data/nebula.json', [
        ('data/corpus/graphs', 'nebula 从快照/Neo4j 导出'),
    ]),
    ('web/kg-data/filaments.json', [
        ('web/kg-data/nebula.json', 'filaments 依赖 nebula'),
    ]),
    ('web/kg-data/force-full.json', [
        ('web/kg-data/filaments.json', 'force 依赖社区划分'),
    ]),
    ('web/kg-data/analytics.json', [
        ('data/quality/l5-six-layer.json', '面板汇总六层'),
        ('data/quality/l5-validity-report.json', '面板新增 L5 效度段'),
        ('data/quality/l7-role-violation-audit.json', '面板新增 L7 归因段'),
        ('web/kg-data/nebula.json', '面板的规模数字'),
    ]),
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
    print('=== 数据新鲜度总览（产物是否晚于它依赖的上游）===\n')
    stale = []
    for target, deps in CHAIN:
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
                stale.append((target, dep, why))
            else:
                row.append(('ok', dep, why))
        mark = '❌' if any(r[0] == 'STALE' for r in row) else (
            '·' if any(r[0] not in ('ok',) for r in row) else '✅')
        print(f'  {mark} {target}  ({fmt(tt)})')
        for status, dep, why in row:
            if status != 'ok':
                print(f'      └─ {status}: {dep} —— {why}')

    print()
    if stale:
        print(f'❌ 有 {len(stale)} 处 STALE（产物早于它依赖的上游）：')
        for t, d, w in stale:
            print(f'   · {t}  依赖  {d}')
        print('\n重算顺序见 RUNBOOK.md §6。')
        return 1
    print('✅ 所有产物都不早于它们依赖的上游 —— 没有需要重跑的数据。')
    print()
    print('（注：本脚本只看**产物之间的先后**；"数据变了但指标没重算"由')
    print('  tools/metric_provenance.py 负责，"文档写错了没"由')
    print('  tools/check_doc_numbers.py 负责。三者互补。）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
