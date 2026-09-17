# -*- coding: utf-8 -*-
r"""K2 · **论文管理**（已入库列表 / 删除 / 元数据）的服务端逻辑。

## TODO 的原话

> | **K2** | ⬜ **论文管理**（已入库列表 / 删除 / 元数据） |
> 现在**传了就只能在 `data/` 里找到**，界面没有"我传过哪些"；
> 且**只有文件名，没有标题/作者/年份** |
> 无页面（`data/corpus/_manifest.json` 有部分元数据，没上界面） | **1 天** + **半天**（元数据解析）

## ★ N7 已经把 K2 的每个决定都答好了（`docs/N7_WHAT_IS_A_PAPER.md §五`）

| K2 的一个决定 | N7 的答案 |
|---|---|
| 列表按哪一层列？ | **四列状态都列**（清单 / 已下载 / 已入库 / 上传图），不只列"入库的" |
| "我传过哪些"要不要含失败的？ | **要** —— 6 条失败的上传也在注册表里 |
| 删除删什么？ | **必须区分删"上传图（gid）"与删"语料图（arXiv id）"** —— 两套 id |
| 元数据从哪来？ | `_manifest.json` 的 `id/title/year/query` + **作者要另解析** |
| 同一篇被传两次怎么办？ | **不合并**，列表标出"同一篇有 N 份上传" |

## ★★ 删除：**永远不真删**（这一条是本模块的全部设计）

`ARCHIVE_POLICY`：**被替代的东西不删，改名归档。** 而删除**用户自己的数据**
比"替代一个实现"严重得多 —— 用户点错一下，**几十分钟的抽取就没了**（本项目的
抽取是本地模型，一篇要跑很久；而 C3 那次还遇到过 500）。

⇒ 所以本模块的删除**只有一种语义**：**移到 `data/_trash/` 并留一张说明。**

```
data/_trash/<时间戳>-<kind>-<id>/
    <被移走的文件（保持原相对路径结构）>
    _why.json     谁、何时、为什么、原路径、字节数、sha256
```

**⇒ 于是"删除"永远可逆**，而"为什么没了"永远查得到。

## ⚠️ 三个必须分清的对象

| kind | 删的是什么 | 影响 |
|---|---|---|
| `upload` | `data/<gid>/`（**一次上传的图**） | 只影响那一次上传；**语料图不动** |
| `corpus_graph` | `data/corpus/graphs/<id>.json`（**已入库的图**） | 该篇从图谱里消失；**PDF 还在** |
| `corpus_pdf` | `data/corpus/<id>.pdf`（**下载的原文**） | **连带**：图就失去原文（证据深链会断） |

⇒ **`corpus_pdf` 最危险**（它会让已入库的图失去可核对的原文）。
所以它**要求显式确认**（见 `--confirm`）。

用法：
    python tools/papers_admin.py list                 # 看有哪些
    python tools/papers_admin.py trash upload 3e38f803 --why "传错了"
    python tools/papers_admin.py trash corpus_graph 2410.21237v1 --why "抽取质量差"
    python tools/papers_admin.py restore <时间戳目录名>
    python tools/papers_admin.py trashcan             # 回收站里有什么
"""
import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
TRASH = DATA / '_trash'
REGISTRY = ROOT / 'web' / 'kg-data' / 'paper-registry.json'

KINDS = ('upload', 'corpus_graph', 'corpus_pdf')


def sha256_of(p, limit=8 * 1024 * 1024):
    """算 sha256（大文件只算前 `limit` 字节，够做"是不是同一个文件"的判断）。"""
    h = hashlib.sha256()
    try:
        with open(p, 'rb') as f:
            h.update(f.read(limit))
    except Exception:
        return None
    return h.hexdigest()


def target_of(kind, pid):
    """把 (kind, id) 映射成要被移走的路径。**不猜：找不到就报错。**"""
    if kind == 'upload':
        p = DATA / pid
        if not (p / 'graph.json').exists():
            raise SystemExit(f'❌ 没有这个上传图：data/{pid}/graph.json')
        return p
    if kind == 'corpus_graph':
        p = DATA / 'corpus' / 'graphs' / f'{pid}.json'
        if not p.exists():
            raise SystemExit(f'❌ 没有这个图：data/corpus/graphs/{pid}.json')
        return p
    if kind == 'corpus_pdf':
        p = DATA / 'corpus' / f'{pid}.pdf'
        if not p.exists():
            raise SystemExit(f'❌ 没有这个 PDF：data/corpus/{pid}.pdf')
        return p
    raise SystemExit(f'❌ kind 必须是 {KINDS} 之一')


def load_registry():
    if not REGISTRY.exists():
        raise SystemExit(f'❌ 缺 {REGISTRY}（先跑 tools/build_paper_registry.py）')
    return json.loads(REGISTRY.read_text(encoding='utf-8'))


def cmd_list(a):
    d = load_registry()
    c = d.get('counts') or {}
    print('=' * 92)
    print('K2 · 论文管理（四个身份）')
    print('=' * 92)
    print(f"  清单 {c.get('manifest_total')} · 已下载 {c.get('downloaded_pdf')} · "
          f"已入库 {c.get('in_graph')} · 上传图 {c.get('uploaded_graphs')}")
    print()
    print('  ★ 上传的图（**这是"我传过哪些"**）')
    print(f"      {'gid':<12}{'文件名':<26}{'arXiv id':<16}{'节点':>6}{'边':>6}  创建时间")
    for u in (d.get('uploads') or []):
        print(f"      {u['gid']:<12}{str(u['filename'])[:24]:<26}"
              f"{str(u['arxiv_id'] or '—'):<16}{u['nodes']:>6}{u['edges']:>6}  "
              f"{str(u.get('created_at') or '')[:19]}")
    fl = d.get('upload_failures') or []
    if fl:
        print()
        print(f'  ★ 传过但没成的（{len(fl)} 次）—— **"我传过哪些"不能漏这些**')
        for o in fl:
            print(f"      {o['gid']:<12}{str(o['filename'])[:24]:<26}"
                  f"{o['state']}/{o['stage']}")
    print()
    print('  已入库的语料图（前 20 篇）')
    ing = [p for p in (d.get('papers') or []) if p.get('in_graph')]
    for p in ing[:20]:
        ups = f"  ← 上传过 {len(p['uploads'])} 次" if p.get('uploads') else ''
        print(f"      {p['arxiv_id']:<18}{str(p.get('year') or ''):<6}"
              f"{(p.get('title') or '')[:44]}{ups}")
    print(f'      …共 {len(ing)} 篇')
    return 0


def cmd_trash(a):
    p = target_of(a.kind, a.id)
    rel = p.relative_to(ROOT)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    dest = TRASH / f'{stamp}-{a.kind}-{a.id}'
    info = {
        'trashed_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'kind': a.kind, 'id': a.id,
        'why': a.why or '(未说明)',
        'original': str(rel).replace('\\', '/'),
        'is_dir': p.is_dir(),
        'bytes': (sum(f.stat().st_size for f in p.rglob('*') if f.is_file())
                  if p.is_dir() else p.stat().st_size),
        'sha256_first8mb': None if p.is_dir() else sha256_of(p),
        'restore_hint': f'python tools/papers_admin.py restore {dest.name}',
        'note': '**没有真删** —— ARCHIVE_POLICY。要恢复用上面的 restore_hint。',
    }
    if a.dry_run:
        print(f'  （dry-run）会移到 {dest.relative_to(ROOT)}')
        print(f'     {json.dumps(info, ensure_ascii=False, indent=1)}')
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p), str(dest / p.name))
    (dest / '_why.json').write_text(
        json.dumps(info, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'  ✅ 已移入回收站：{dest.relative_to(ROOT)}')
    print(f'     {info["bytes"] / 1024:.0f} KB · 原因：{info["why"]}')
    print(f'     **没有真删** —— 恢复：{info["restore_hint"]}')
    # 删语料图/PDF 会影响注册表 ⇒ **提醒重跑**（而不是自动重跑：
    # 自动重跑会掩盖"我删了什么"这件事）
    if a.kind.startswith('corpus_'):
        print('     ⚠️ 注册表可能过期 ⇒ 跑 `python tools/build_paper_registry.py` 刷新')
    return 0


def cmd_restore(a):
    src = TRASH / a.name
    if not src.exists():
        raise SystemExit(f'❌ 回收站里没有 {a.name}')
    info = {}
    wf = src / '_why.json'
    if wf.exists():
        info = json.loads(wf.read_text(encoding='utf-8'))
    orig = info.get('original')
    if not orig:
        raise SystemExit(f'❌ {a.name}/_why.json 里没有 original，无法恢复')
    dest = ROOT / orig
    # 回收站里那一份（除 _why.json 外的那一个）
    items = [x for x in src.iterdir() if x.name != '_why.json']
    if len(items) != 1:
        raise SystemExit(f'❌ {a.name} 里有 {len(items)} 个条目，无法判断恢复哪个')
    if dest.exists():
        raise SystemExit(f'❌ 目标已存在，不覆盖：{dest}')
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(items[0]), str(dest))
    print(f'  ✅ 已恢复：{dest.relative_to(ROOT)}')
    print(f'     （原来是 {info.get("trashed_at")} 因为「{info.get("why")}」移走的）')
    return 0


def cmd_trashcan(a):
    if not TRASH.exists():
        print('  回收站是空的')
        return 0
    ds = sorted([d for d in TRASH.iterdir() if d.is_dir()])
    print(f'  回收站里有 {len(ds)} 项')
    print()
    for d in ds:
        wf = d / '_why.json'
        info = json.loads(wf.read_text(encoding='utf-8')) if wf.exists() else {}
        print(f"    {d.name}")
        print(f"        原路径 {info.get('original')} · {info.get('bytes', 0) / 1024:.0f} KB"
              f" · 原因「{info.get('why')}」")
    print()
    print('  恢复：python tools/papers_admin.py restore <上面那一行的名字>')
    return 0


def main():
    ap = argparse.ArgumentParser(description='K2 论文管理（删除=移入回收站，永不真删）')
    sub = ap.add_subparsers(dest='cmd')

    p = sub.add_parser('list', help='列出四层身份')
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser('trash', help='移入回收站（**不真删**）')
    p.add_argument('kind', choices=KINDS)
    p.add_argument('id')
    p.add_argument('--why', default='', help='为什么删（会记进 _why.json）')
    p.add_argument('--dry-run', action='store_true')
    p.set_defaults(fn=cmd_trash)

    p = sub.add_parser('restore', help='从回收站恢复')
    p.add_argument('name')
    p.set_defaults(fn=cmd_restore)

    p = sub.add_parser('trashcan', help='看回收站里有什么')
    p.set_defaults(fn=cmd_trashcan)

    a = ap.parse_args()
    if not getattr(a, 'fn', None):
        ap.print_help()
        return 0
    return a.fn(a)


if __name__ == '__main__':
    sys.exit(main())
