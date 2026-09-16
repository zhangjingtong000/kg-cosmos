# -*- coding: utf-8 -*-
r"""执行人工改图计划 —— **K1 的执行端**。

## 设计（沿用本项目已有的 `plan → apply` 先例）

先例是 `data/quality/venue-rewrite-plan.json`：
**只出计划、不改数据**，改由单独的 `apply_*.py` 执行。它的 `method` 一栏写着
「**改写而非删除 —— 这些边携带真实信息，只是类型标错**」，这正是本项目的档案纪律。

**K1 沿用同一模式**，理由有两条（评审给的第一条，我补的第二条）：

1. **解耦**：前端"收集意图"是半天的工作量，而"改图 + 重跑全部下游"要几小时机器时间。
   合在一起做，前端就得等机器。
2. **更要紧：改图会改变图规模 ⇒ 必须能拿出 before / after 两套指标。**
   而 `tools/metric_provenance.py` 现在**有归档了**（N1，2026-09-16 加）
   —— **所以这个脚本的第一步就是归档 before**。

## 计划文件格式（前端提交的就是它）

    {
      "generated_by": "web/ask.html 或人工手写",
      "note": "自由文本，会原样存档",
      "operations": [
        {"op": "rename_entity",  "from": "TransE", "to": "TransE (Bordes 2013)", "reason": "..."},
        {"op": "merge_entities", "from": "KG",     "into": "Knowledge Graph",   "reason": "..."},
        {"op": "delete_edge",    "source": "A", "target": "B", "type": "USES",   "reason": "..."},
        {"op": "retag_edge",     "source": "A", "target": "B", "type": "PART_OF",
                                 "to_type": "PUBLISHED_IN", "reason": "..."}
      ]
    }

## 三个刻意的设计

1. **`--dry-run` 是默认**：不加 `--apply` 只报"会影响多少"。
   **改图不可逆**（会改变全库实体数），必须先看清楚。
2. **改之前先把 `data/corpus/graphs/` 快照一份**到
   `data/quality/backup/graphs-pre-edit-<时间戳>/` —— 那是**唯一的真源**。
3. **改完立刻重跑下游 + 记 after + 打 diff**：
   下游清单硬编码在 `DOWNSTREAM` 里（与 `metric_provenance.py` 的 docstring 一致），
   **漏跑一个就会新旧数字并存**（这正是 N1 要解决的问题）。

用法：
    python tools/apply_edit_plan.py plans/edit-20260916.json            # 只看影响
    python tools/apply_edit_plan.py plans/edit-20260916.json --apply    # 真的改
    python tools/apply_edit_plan.py plans/edit-20260916.json --apply --no-downstream
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))          # 为了 import _paths
from _paths import rel as _rel  # noqa: E402
GRAPHS = ROOT / 'data' / 'corpus' / 'graphs'
BACKUP = ROOT / 'data' / 'quality' / 'backup'
Q = ROOT / 'data' / 'quality'

OPS = ('rename_entity', 'merge_entities', 'delete_edge', 'retag_edge')

# 改图后必须重跑的下游（顺序固定）。
# **与 metric_provenance.py 的 docstring 列的那串一致** ——
# 漏跑一个，data/quality/*.json 里就会新旧数字并存，而且没有任何提示。
DOWNSTREAM = [
    ('tools/build_nebula_data.py', ['--from-json', '--governed']),
    ('tools/build_relations_data.py', []),
    ('tools/build_filament_data.py', []),
    ('tools/build_community_details.py', []),
    ('tools/build_force_data.py', ['--full']),
    ('tools/build_analytics_data.py', []),
]


def parse_args():
    ap = argparse.ArgumentParser(description='执行人工改图计划（K1 执行端）')
    ap.add_argument('plan', help='计划文件（JSON）')
    ap.add_argument('--apply', action='store_true',
                    help='真的改（默认只做 dry-run）')
    ap.add_argument('--no-downstream', action='store_true',
                    help='改完不重跑下游（只看图本身的变化；**会让指标变 stale**）')
    # 测试用：把"图快照目录"指到别处，**不碰真源**。
    # 这个开关是 N1 那次的教训（`tests/test_metric_archive.py` 会污染真实归档目录）：
    # **任何会写数据的脚本，都要能被重定向到临时目录。**
    ap.add_argument('--graphs-dir', default=None,
                    help='图快照目录（默认 data/corpus/graphs；测试时指到临时目录）')
    return ap.parse_args()


ARGS = parse_args()

GRAPHS = Path(ARGS.graphs_dir).resolve() if ARGS.graphs_dir else GRAPHS


def load_plan():
    p = Path(ARGS.plan)
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists():
        raise SystemExit(f'找不到计划文件：{p}')
    d = json.loads(p.read_text(encoding='utf-8-sig'))
    ops = d.get('operations') or []
    bad = [o for o in ops if (o or {}).get('op') not in OPS]
    if bad:
        raise SystemExit(f'计划里有 {len(bad)} 条未知操作：'
                         f'{[o.get("op") for o in bad[:5]]}（允许：{OPS}）')
    return p, d, ops


def load_docs():
    """读全部图快照：{论文 id: doc}。"""
    docs = {}
    for f in sorted(GRAPHS.glob('*.json')):
        docs[f.stem] = (f, json.loads(f.read_text(encoding='utf-8-sig')))
    return docs


def apply_op(op, docs, stat):
    """把一个操作施加到内存里的 docs 上，返回受影响的对象数。

    **实体名改写在所有论文里生效**（同名即同一实体，这是本项目跨篇合并的口径）；
    **删边/改类型按 (source,target,type) 匹配，可能命中多篇**。
    """
    kind = op['op']
    n = 0
    if kind == 'rename_entity':
        frm, to = op['from'], op['to']
        for _pid, (_f, d) in docs.items():
            for nd in (d.get('nodes') or []):
                if (nd.get('name') or nd.get('id')) == frm:
                    if 'name' in nd:
                        nd['name'] = to
                    else:
                        nd['id'] = to
                    n += 1
            for e in (d.get('edges') or []):
                if e.get('source') == frm:
                    e['source'] = to
                    n += 1
                if e.get('target') == frm:
                    e['target'] = to
                    n += 1
    elif kind == 'merge_entities':
        return apply_op({'op': 'rename_entity', 'from': op['from'], 'to': op['into']},
                        docs, stat)
    elif kind == 'delete_edge':
        for _pid, (_f, d) in docs.items():
            keep = []
            for e in (d.get('edges') or []):
                if (e.get('source') == op.get('source')
                        and e.get('target') == op.get('target')
                        and (op.get('type') is None or e.get('type') == op['type'])):
                    n += 1
                    continue
                keep.append(e)
            d['edges'] = keep
    elif kind == 'retag_edge':
        for _pid, (_f, d) in docs.items():
            for e in (d.get('edges') or []):
                if (e.get('source') == op.get('source')
                        and e.get('target') == op.get('target')
                        and (op.get('type') is None or e.get('type') == op['type'])):
                    e['type'] = op['to_type']
                    e['_retagged_from'] = op['type']   # 留痕
                    n += 1
    stat[kind] += n
    return n


def snapshot():
    """把 `data/corpus/graphs/` 整目录快照一份（那是唯一的真源）。"""
    ts = time.strftime('%Y%m%d-%H%M%S')
    dst = BACKUP / f'graphs-pre-edit-{ts}'
    BACKUP.mkdir(parents=True, exist_ok=True)
    shutil.copytree(GRAPHS, dst)
    return dst


def main():
    plan_path, plan, ops = load_plan()

    print('=' * 78)
    print(f'改图计划：{_rel(plan_path)}')
    print('=' * 78)
    print(f'  来源：{plan.get("generated_by") or "（未注明）"}')
    if plan.get('note'):
        print(f'  说明：{plan["note"]}')
    print(f'  操作：{len(ops)} 条 · ' + ' · '.join(
        f'{k} {v}' for k, v in sorted(Counter(o["op"] for o in ops).items())))
    print()

    docs = load_docs()
    n_nodes = sum(len(d.get('nodes') or []) for _f, d in docs.values())
    n_edges = sum(len(d.get('edges') or []) for _f, d in docs.values())
    print(f'  [改前] {len(docs)} 篇 · {n_nodes} 节点 / {n_edges} 边')

    stat = Counter()
    for i, op in enumerate(ops, 1):
        n = apply_op(op, docs, stat)
        if n == 0:
            print(f'    ⚠️ #{i} {op["op"]} 没有命中任何对象（'
                  f'{(op.get("from") or op.get("source"))!r}）—— 计划可能已过时')
    n_nodes2 = sum(len(d.get('nodes') or []) for _f, d in docs.values())
    n_edges2 = sum(len(d.get('edges') or []) for _f, d in docs.values())
    print(f'  [改后] {len(docs)} 篇 · {n_nodes2} 节点 / {n_edges2} 边'
          f'  （节点 {n_nodes2 - n_nodes:+d} · 边 {n_edges2 - n_edges:+d}）')
    print()
    for k in OPS:
        if stat[k]:
            print(f'    {k:16s} 影响 {stat[k]} 处')

    if not ARGS.apply:
        print()
        print('**这是 dry-run**（没动任何文件）。确认无误后加 `--apply`。')
        print('  注意：`--apply` 会先快照 data/corpus/graphs/，然后在覆盖前把')
        print('        当前指标归档（N1），改完重跑下游、再归档一次并打 diff。')
        return 0

    # ── 真的改 ──────────────────────────────────────────────────────────
    print()
    print('=' * 78)
    print('执行')
    print('=' * 78)

    # 测试模式（--graphs-dir）：只验证"改图逻辑"，**不碰真源、不动指标档案**
    TEST_MODE = bool(ARGS.graphs_dir)

    # ① 归档 before（N1）—— **必须在覆盖前**
    if TEST_MODE:
        print('  ① 跳过归档 before（测试模式：--graphs-dir，指标档案只该由真改动产生）')
    else:
        r = subprocess.run([sys.executable, str(ROOT / 'tools' / 'metric_provenance.py'),
                            '--write'], cwd=str(ROOT), capture_output=True, text=True)
        print('  ① 归档 before：' + ('✅' if r.returncode == 0 else '❌'))
        for line in r.stdout.splitlines():
            if '归档' in line or '已记录' in line:
                print('     ' + line.strip())

    # ② 快照真源
    if TEST_MODE:
        print('  ② 跳过快照（测试模式：改的是临时目录）')
    else:
        dst = snapshot()
        print(f'  ② 快照 data/corpus/graphs/ → {_rel(dst)}')

    # ③ 写回
    for f, d in docs.values():
        f.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'  ③ 已写回 {len(docs)} 篇图快照')

    # ④ 重跑下游
    if ARGS.no_downstream or ARGS.graphs_dir:
        # 指到临时目录时**必然**不该重跑下游（下游读的是真源，重跑没意义且会误导）
        why = '--no-downstream' if ARGS.no_downstream else '--graphs-dir 已指定（测试模式）'
        print(f'  ④ 跳过下游重跑（{why}）')
        if not ARGS.graphs_dir:
            print('     ⚠️ 指标现在是 stale 的，跑 metric_provenance.py 会报错')
    else:
        print('  ④ 重跑下游：')
        for script, extra in DOWNSTREAM:
            p = ROOT / script
            if not p.exists():
                print(f'     · 跳过 {script}（不存在）')
                continue
            t0 = time.time()
            rr = subprocess.run([sys.executable, str(p), *extra],
                                cwd=str(ROOT), capture_output=True, text=True)
            mark = '✅' if rr.returncode == 0 else '❌'
            print(f'     {mark} {script}  ({time.time() - t0:.0f}s)')
            if rr.returncode != 0:
                print(f'        {(rr.stderr or rr.stdout)[-200:].strip()}')

    # ⑤ 归档 after + diff
    if TEST_MODE:
        print('  ⑤ 跳过归档 after 与 diff（测试模式）')
        print()
        print('  ✅ 测试模式完成 —— 只改了 --graphs-dir 指定的目录，'
              '真源与指标档案都没动。')
        return 0
    r = subprocess.run([sys.executable, str(ROOT / 'tools' / 'metric_provenance.py'),
                        '--write'], cwd=str(ROOT), capture_output=True, text=True)
    print('  ⑤ 归档 after：' + ('✅' if r.returncode == 0 else '❌'))

    print()
    print('=' * 78)
    print('before → after（指标档案 diff）')
    print('=' * 78)
    r = subprocess.run([sys.executable, str(ROOT / 'tools' / 'metric_provenance.py'),
                        '--diff', '-1', 'current'], cwd=str(ROOT),
                       capture_output=True, text=True)
    print(r.stdout or r.stderr)
    print()
    print('**下一步（必须做）**：跑 `python tools/verify_all.py --fast` ——')
    print('  改图会改变图规模，回归里那些盯数字的检查会告诉你哪些文档要同步。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
