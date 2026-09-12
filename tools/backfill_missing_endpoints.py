"""从边反建缺失端点（L3 补救）。用户 2026-09-12 决策：type 统一标 Unknown。

【问题】
  L3（关系端点层）= 68.4%：43867 条边里 13843 条至少有一个端点不在节点集内。
  已核实的事实（见 data/quality/l3-*.json）：
    · 缺失名字去重后 4754 个，**从未被任何论文抽成节点** ⇒ 不是跨论文对齐问题；
    · 93.1% 在自带的原文证据里**逐字出现过** ⇒ 是真实体，不是抽噪；
    · 缺失率在各关系类型间**均匀分布**（26%~38%）⇒ 系统性问题。

【做法】
  用「证据命中 + 形态过滤」筛出 4304 个名字（挡掉 Relations / Knowledge /
  Summary / MR / M / H. / Task / @10 这类非实体与碎片），把它们建成节点：
    · type 统一标 `Unknown`（用户决策）——不猜类型。
      实测邻居推断不可靠：会把 `Document-Level Relation Extraction` 猜成 `Person`。
    · **把引用它的边的 evidence 汇总给它**。这一步是必需的：
      模拟显示若不写证据，L1 会从 92.8% 掉到 72.2%；写了之后是 94.4%（不降反升）。
    · 打 `_backfilled: true` 标记，便于日后单独审计或整体回滚。

【预期】
  L3 68.4% → 96.2%；L1 92.8% → 94.4%；节点数 15123 → 19427。

用法：
  python tools/backfill_missing_endpoints.py            # dry-run
  python tools/backfill_missing_endpoints.py --apply
  python tools/backfill_missing_endpoints.py --rollback <备份目录>
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import argparse
import json
import re
import shutil
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

GRAPH = ROOT / 'data/corpus/graphs'
BACKUP_ROOT = ROOT / 'data/quality/backup'

JUNK = re.compile(
    r'^(summary|task|tasks|table|figure|fig\.?|section|appendix|abstract|'
    r'introduction|conclusion|results?|method|methods|approach|model|models|'
    r'example|examples|input|output|data|dataset|datasets|entity|entities|'
    r'relation|relations|knowledge|experts?|others?|misc|etc|na|none|unknown|'
    r'[a-z]|m|h|dr\.?|mr\.?|ms\.?)$', re.I)

MAX_EV = 3          # 每个新节点最多汇总几条边证据


def norm(s):
    return re.sub(r'[^a-z0-9]+', '', (s or '').lower())


def ev_text(e):
    """取一条边的证据正文。图中的 evidence 可能是 str，也可能是
    `[{text: ...}]` 这样的列表（不同批次的快照不一致），两种都要兼容。"""
    ev = e.get('evidence')
    if isinstance(ev, list):
        parts = []
        for x in ev:
            if isinstance(x, dict):
                parts.append(str(x.get('text') or ''))
            else:
                parts.append(str(x))
        return ' '.join(parts)
    return str(ev or '')


def ev_raw_list(e):
    """取可写进节点的证据原文列表。"""
    ev = e.get('evidence')
    if isinstance(ev, list):
        out = []
        for x in ev:
            if isinstance(x, dict) and x.get('text'):
                out.append(x)
            elif isinstance(x, str) and x:
                out.append({'text': x})
        return out
    if isinstance(ev, str) and ev:
        return [{'text': ev}]
    return []


def looks_like_entity(nm):
    s = nm.strip()
    if len(s) < 3 or not any(ch.isalpha() for ch in s):
        return False
    if JUNK.match(s):
        return False
    if len(s) <= 4 and s.islower():
        return False
    return True


def scan():
    """返回 (known, 候选名单, 候选->证据列表, 候选->引用它的边数)。"""
    known = set()
    files = sorted(GRAPH.glob('*.json'))
    for f in files:
        snap = json.loads(f.read_text(encoding='utf-8'))
        for n in snap.get('nodes', []):
            nm = (n.get('name') or '').strip()
            if nm:
                known.add(nm)

    occ = Counter()
    ev_of = defaultdict(list)
    for f in files:
        snap = json.loads(f.read_text(encoding='utf-8'))
        for e in snap.get('edges', []):
            ev = norm(ev_text(e))
            for side in ('source', 'target'):
                nm = (e.get(side) or '').strip()
                if not nm or nm in known:
                    continue
                occ[nm] += 1
                if norm(nm) and norm(nm) in ev and len(ev_of[nm]) < MAX_EV:
                    # 保留**未归一化**的原文证据（要写进节点里给 L1 用）
                    for item in ev_raw_list(e):
                        if item not in ev_of[nm]:
                            ev_of[nm].append(item)
                            if len(ev_of[nm]) >= MAX_EV:
                                break
    cand = {n for n in occ if ev_of[n] and looks_like_entity(n)}
    return known, cand, ev_of, occ, files


def main():
    ap = argparse.ArgumentParser(description='从边反建缺失端点')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--rollback', default='')
    args = ap.parse_args()

    if args.rollback:
        src = Path(args.rollback)
        fs = list(src.glob('*.json'))
        for b in fs:
            shutil.copy2(b, GRAPH / b.name)
        print(f'已回滚 {len(fs)} 个快照')
        return

    known, cand, ev_of, occ, files = scan()
    print(f'现有节点 {len(known)} 个；待反建 {len(cand)} 个名字'
          f'（覆盖 {sum(occ[n] for n in cand)} 个端点次）')
    per_file = Counter()
    for f in files:
        snap = json.loads(f.read_text(encoding='utf-8'))
        have = {(n.get('name') or '').strip() for n in snap.get('nodes', [])}
        hit = [n for n in cand if n not in have
               and any((e.get('source') or '').strip() == n
                       or (e.get('target') or '').strip() == n
                       for e in snap.get('edges', []))]
        if hit:
            per_file[f.name] = len(hit)
    print(f'分布到 {len(per_file)} 个快照，前 6：{per_file.most_common(6)}')

    if not args.apply:
        print('\n[dry-run] 未写文件。加 --apply 生效。')
        return

    ts = time.strftime('%Y%m%d-%H%M%S')
    bak = BACKUP_ROOT / f'graphs-{ts}-prebackfill'
    bak.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copy2(f, bak / f.name)
    print(f'\n已备份 {len(files)} 个快照到 {bak}')

    total_new = 0
    for f in files:
        snap = json.loads(f.read_text(encoding='utf-8'))
        nodes = snap.get('nodes', [])
        have = {(n.get('name') or '').strip() for n in nodes}
        # 该快照里出现、但节点表没有的名字
        in_edges = set()
        for e in snap.get('edges', []):
            for side in ('source', 'target'):
                nm = (e.get(side) or '').strip()
                if nm and nm not in have:
                    in_edges.add(nm)
        todo = sorted(n for n in cand if n in in_edges)
        if not todo:
            continue
        for nm in todo:
            nodes.append({
                'name': nm,
                'type': 'Unknown',
                'description': None,
                # ev_of 里已经是 [{'text': ...}] 形式，直接用
                'evidence': ev_of.get(nm, [])[:MAX_EV],
                '_backfilled': True,
                '_backfill_note': '从引用它的边反建；类型未知，勿当作已抽取实体',
            })
        snap['nodes'] = nodes
        if isinstance(snap.get('stats'), dict):
            snap['stats']['nodes'] = len(nodes)
        snap.setdefault('_audit', []).append({
            'op': 'backfill_endpoints', 'added': len(todo),
            'reason': 'L3 端点反建（用户决策 2026-09-12，type=Unknown）',
            'at': time.strftime('%Y-%m-%d %H:%M:%S'),
        })
        f.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding='utf-8')
        total_new += len(todo)
        print(f'  {f.name}: +{len(todo)} 节点')

    print(f'\n完成：反建 {total_new} 个节点')
    print(f'回滚：python tools/backfill_missing_endpoints.py --rollback {bak}')


if __name__ == '__main__':
    main()
