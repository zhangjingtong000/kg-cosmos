# -*- coding: utf-8 -*-
r"""N7 · **定义"什么是一篇论文"**，并把散在三处的真相合成一份注册表。

## TODO 的原话

> | **N7** | ⬜ **K2 的前置：先定义"什么是一篇论文"** |
> **K2 被严重低估** —— "我传过哪些"的真相散在**三处且无关联键**：
> `data/corpus/_manifest.json`（28 篇，字段 `id/title/year/query/status`，**无作者**）、
> `data/<gid>/graph.json`（7 个上传图，**gid 是随机 hex**）、
> `data/upload-tasks/*.json`。**没有关联键** |

## ★ 实测：**关联键是存在的，只是没人把它们连起来**

```
upload-tasks/<gid>.json  有  id（gid） + filename      ← 如 3e38f803 / 2506.09566v1.pdf
data/<gid>/graph.json    有  source.name = 文件名       ← 如 {"id":"3e38f803","name":"2506.09566v1.pdf"}
_manifest.json           有  id（= arXiv id）           ← 如 2506.09566v1
```

**⇒ 关联链**：**`gid` → `source.name` → 去掉 `.pdf` → arXiv id → `_manifest.json`**。

**TODO 说"没有关联键"是对的**（没有**显式**的外键字段），
但**文件名就是那把钥匙** —— 它在两处都存着。

## 定义：什么算"一篇论文"

**三种身份，必须分清**（这正是 TODO 说的"被严重低估"的地方）：

| 身份 | 由什么决定 | 出现在 |
|---|---|---|
| **① 清单条目** | `_manifest.json` 的一条（arXiv id） | 检索阶段（28 条） |
| **② 已下载** | `data/corpus/<id>.pdf` 存在 | 下载阶段（28 个 PDF） |
| **③ 已入库** | `data/corpus/graphs/<id>.json` 存在 | 抽取阶段（17 个图） |
| **④ 上传图** | `data/<gid>/graph.json` 存在（**gid ≠ arXiv id**） | 用户上传（3 个） |

**⇒ 所以"一篇论文"在日常对话里指的是哪一层，必须说清** ——
"我传过哪些"问的是 **④**，"语料有几篇"问的是 **③**。

**⚠️ ④ 与 ①–③ 是两套 id 体系**：
arXiv id（**语义 id**，人可读、可引用）vs gid（**随机 hex**，机器生成）。
**本注册表把它们连起来，但不合并它们** —— 合并会丢掉"这是上传的"这个事实。

用法：
    python tools/build_paper_registry.py
    python tools/build_paper_registry.py --json     # 只打 JSON
"""
import argparse
import glob
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / 'data' / 'corpus' / '_manifest.json'
CORPUS = ROOT / 'data' / 'corpus'
GRAPHS = CORPUS / 'graphs'
UPLOADS = ROOT / 'data' / 'upload-tasks'
INDEX = ROOT / 'web' / 'kg-data' / 'corpus-index.json'
OUT = ROOT / 'web' / 'kg-data' / 'paper-registry.json'

# arXiv id 长这样：2506.09566v1 / 2202.07412v1 / 1703.08098v9
RE_ARXIV = re.compile(r'^\d{4}\.\d{4,5}v\d+$')


def arxiv_from_filename(name):
    """`2506.09566v1.pdf` → `2506.09566v1`。**不猜、不模糊匹配。**"""
    if not name:
        return None
    stem = str(name).strip()
    for suf in ('.pdf', '.PDF'):
        if stem.endswith(suf):
            stem = stem[:-len(suf)]
    return stem if RE_ARXIV.match(stem) else None


def main():
    a = argparse.ArgumentParser()
    a.add_argument('--json', action='store_true')
    a.parse_args()
    t0 = time.time()

    # ── ① 清单 ────────────────────────────────────────────────────
    manifest = []
    if MANIFEST.exists():
        raw = json.loads(MANIFEST.read_text(encoding='utf-8'))
        manifest = raw if isinstance(raw, list) else (raw.get('papers') or [])
    by_id = {p['id']: dict(p) for p in manifest if p.get('id')}

    # ── ② 清单里的 in_graph（在 corpus-index.json 里，**不在 _manifest**）──
    in_graph = set()
    if INDEX.exists():
        idx = json.loads(INDEX.read_text(encoding='utf-8'))
        for p in (idx.get('papers') or []):
            if p.get('in_graph'):
                in_graph.add(p['id'])

    # ── ③ 文件系统：PDF 与图 ──────────────────────────────────────
    pdfs = {f.stem for f in CORPUS.glob('*.pdf')}
    graph_ids = {f.stem for f in GRAPHS.glob('*.json') if f.stat().st_size > 0}

    # ── ④ 上传图（**关键的关联**）─────────────────────────────────
    uploads = {}
    for g in sorted(glob.glob(str(ROOT / 'data' / '*' / 'graph.json'))):
        p = Path(g)
        gid = p.parent.name
        try:
            j = json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            continue
        src = j.get('source') or {}
        uploads[gid] = {
            'gid': gid,
            'filename': src.get('name'),
            'arxiv_id': arxiv_from_filename(src.get('name')),
            'created_at': j.get('created_at'),
            'nodes': len(j.get('nodes') or []), 'edges': len(j.get('edges') or []),
            'path': str(p.relative_to(ROOT)).replace('\\', '/'),
        }
    # 用 upload-tasks 补文件名（**有时 graph.json 缺 source**）
    tasks = {}
    for t in sorted(UPLOADS.glob('*.json')) if UPLOADS.exists() else []:
        try:
            j = json.loads(t.read_text(encoding='utf-8'))
        except Exception:
            continue
        tasks[j.get('id') or t.stem] = j
    for gid, u in uploads.items():
        if not u['filename']:
            tk = tasks.get(gid) or {}
            u['filename'] = tk.get('filename')
            u['arxiv_id'] = arxiv_from_filename(u['filename'])
    # 还有"有任务但没有图"的（失败的上传）—— **也要记**，否则"我传过哪些"会漏
    orphans = []
    for gid, tk in tasks.items():
        if gid in uploads:
            continue
        orphans.append({
            'gid': gid, 'filename': tk.get('filename'),
            'state': tk.get('state'), 'stage': tk.get('stage'),
            'arxiv_id': arxiv_from_filename(tk.get('filename')),
            'note': '有上传任务但没有图（失败或被中断）',
        })
    # ⚠️ **反过来的那一类**：**有图但没有上传任务**。
    #
    # 实测发现（而这是守门逼出来的）：任务 8 个、图 3 个、失败 6 个 ⇒ **8 ≠ 3 + 6**。
    # 差的那 1 个是 `ad690dfb` —— **它有图，却没有任何 upload-task**。
    # ⇒ 所以"任务 ↔ 图"**不是一一对应**，有三个类别：
    #    ① 有任务 + 有图（成功）② 有任务 + 无图（失败/中断）③ **无任务 + 有图**（？）
    # **第③类必须单独列出来** —— 它意味着"这个图不是通过上传任务产生的"
    # （可能是脚本直接生成、或任务记录被清过），**不能混进失败表**。
    no_task = [{'gid': u['gid'], 'filename': u['filename'],
                'arxiv_id': u['arxiv_id'], 'nodes': u['nodes'],
                'edges': u['edges'], 'created_at': u['created_at'],
                'note': '有图但没有对应的上传任务（不是通过 /upload 产生的？）'}
               for u in uploads.values() if u['gid'] not in tasks]

    # ── ⑤ 合成 ────────────────────────────────────────────────────
    papers = []
    for pid, m in by_id.items():
        papers.append({
            'arxiv_id': pid,
            'title': m.get('title'), 'year': m.get('year'),
            'query': m.get('query'),
            'downloaded': pid in pdfs,
            'in_graph': pid in in_graph or pid in graph_ids,
            'graph_file': f'data/corpus/graphs/{pid}.json' if pid in graph_ids else None,
            'author': m.get('author'),          # ⚠️ **清单里没有作者**（如实留 None）
            'uploads': [u['gid'] for u in uploads.values() if u['arxiv_id'] == pid],
        })
    # 上传过但**不在清单里**的（外部 PDF）—— 也要有一行，否则"我传过哪些"漏
    extra = {}
    for u in uploads.values():
        aid = u['arxiv_id']
        if aid and aid not in by_id:
            extra.setdefault(aid, {'arxiv_id': aid, 'title': None, 'year': None,
                                   'query': None, 'downloaded': aid in pdfs,
                                   'in_graph': aid in graph_ids,
                                   'graph_file': (f'data/corpus/graphs/{aid}.json'
                                                  if aid in graph_ids else None),
                                   'author': None, 'uploads': []})
        if aid and aid in extra:
            extra[aid]['uploads'].append(u['gid'])
    papers.extend(extra.values())

    payload = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'purpose': 'N7：定义"什么是一篇论文"，把散在三处的真相合成一份注册表',
        'the_key': {
            'chain': 'gid → graph.json 的 source.name → 去掉 .pdf → arXiv id → _manifest.json',
            'where': {
                'gid': 'data/upload-tasks/<gid>.json 的 id；也是 data/<gid>/ 的目录名',
                'filename': 'data/<gid>/graph.json 的 source.name（**与 upload-tasks 的 filename 一致**）',
                'arxiv_id': '_manifest.json 的 id；也就是 data/corpus/<id>.pdf 的文件名',
            },
            'why_todo_said_no_key':
                'TODO 说"没有关联键"是对的 —— 没有**显式的外键字段**。'
                '但**文件名就是那把钥匙**，它在两处都存着（graph.json 的 source.name '
                '与 upload-tasks 的 filename）。',
        },
        'definition': {
            'four_identities': [
                '① 清单条目：_manifest.json 的一条（28）',
                '② 已下载：data/corpus/<id>.pdf 存在（28）',
                '③ 已入库：data/corpus/graphs/<id>.json 存在（17）',
                '④ 上传图：data/<gid>/graph.json 存在（3）—— **gid ≠ arXiv id**',
            ],
            'warning':
                '"一篇论文"在日常对话里指哪一层必须说清：'
                '"我传过哪些"问的是 ④，"语料有几篇"问的是 ③。',
            'two_id_systems':
                'arXiv id（语义 id，人可读、可引用）vs gid（随机 hex，机器生成）。'
                '**本注册表把它们连起来，但不合并** —— 合并会丢掉"这是上传的"这个事实。',
        },
        'counts': {
            'manifest_total': len(manifest),
            'downloaded_pdf': len(pdfs),
            'in_graph': len(graph_ids),
            'uploaded_graphs': len(uploads),
            'upload_tasks': len(tasks),
            'upload_failed_no_graph': len(orphans),
            'upload_graph_no_task': len(no_task),
            'registry_rows': len(papers),
        },
        # ⚠️ **任务与图不是一一对应**（实测 8 个任务 / 3 个图 / 6 个失败 / 1 个无任务图）。
        # ⇒ 三个类别必须都列出来，否则"我传过哪些"要么漏、要么算错。
        'upload_identity': {
            'rule': '① 有任务+有图（成功）· ② 有任务+无图（失败/中断）· ③ 无任务+有图（？）',
            'counts': {'with_task_and_graph': len(tasks) - len(orphans),
                       'task_no_graph': len(orphans),
                       'graph_no_task': len(no_task)},
            'check': '① + ② = 任务数；① + ③ = 图数',
        },
        'papers': sorted(papers, key=lambda x: str(x['arxiv_id'])),
        'uploads': sorted(uploads.values(), key=lambda x: str(x['gid'])),
        'upload_failures': orphans,
        'upload_graphs_without_task': no_task,
        'known_gaps': [
            '**作者缺失**：`_manifest.json` 里没有 `author` 字段 ⇒ 注册表里如实留 `None`，'
            '**不去猜**（猜出来的作者比没有更糟）。要补得从 PDF 首页解析。',
            '**上传图的 id 是随机 hex**：`data/<gid>/graph.json` 的 gid 与 arXiv id 是两套，'
            '靠 `source.name` 连。**上传了但文件名不是 arXiv id 的，连不上**（本库暂无此例）。',
        ],
        'elapsed_s': round(time.time() - t0, 1),
    }

    if '--json' in sys.argv:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0

    print('=' * 92)
    print('N7 · "什么是一篇论文" · 注册表')
    print('=' * 92)
    c = payload['counts']
    print(f"  清单 {c['manifest_total']} · 已下载 {c['downloaded_pdf']} · "
          f"已入库 {c['in_graph']} · 上传图 {c['uploaded_graphs']} · "
          f"上传任务 {c['upload_tasks']}")
    print(f"  注册表 {c['registry_rows']} 行（含上传过但不在清单里的）")
    print()
    print('  ★ 关联链（TODO 说"没有关联键" —— 实测**文件名就是钥匙**）')
    print('      gid → graph.json 的 source.name → 去掉 .pdf → arXiv id → _manifest')
    print()
    print('  ★ 上传的图（**gid 与 arXiv id 是两套体系**）')
    for u in payload['uploads']:
        print(f"      {u['gid']}  {str(u['filename'])[:34]:<36} "
              f"→ {str(u['arxiv_id']):<16} {u['nodes']} 节点 / {u['edges']} 边")
    if orphans:
        print()
        print(f"  ★ 有上传任务但没有图的（{len(orphans)} 个，"
              f'**"我传过哪些"不能漏这些**）')
        for o in orphans:
            print(f"      {o['gid']}  {str(o['filename'])[:30]:<32} "
                  f"{o['state']} / {o['stage']}")
    print()
    for g in payload['known_gaps']:
        print(f'  ⚠️ {g[:110]}')

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                   encoding='utf-8')
    print(f'\n  已写出 {OUT.relative_to(ROOT)}  '
          f'({OUT.stat().st_size / 1024:.1f} KB · {time.time() - t0:.1f}s)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
