# -*- coding: utf-8 -*-
r"""N7 · **定义"什么是一篇论文"**，并把散在三处的真相合成一份注册表。

## TODO 的原话

> | **N7** | ⬜ **K2 的前置：先定义"什么是一篇论文"** |
> **K2 被严重低估** —— "我传过哪些"的真相散在**三处且无关联键**：
> `data/corpus/_manifest.json`（28 篇，字段 `id/title/year/query/status`，**无作者**）、
> `data/<gid>/graph.json`（7 个上传图，**gid 是随机 hex**）、
> `data/upload-tasks/*.json`。**没有关联键** |
>
> ⚠️ **2026-09-19 注**（引用原话不改，只在其下补当前值）：
> 那句里的「**28 篇**」是 TODO 写作时的清单条数 —— 现
> `data/corpus/_manifest.json` 为 **34 条**（28 篇英文 arXiv + 6 篇中文期刊），
> 落盘 PDF **33 个**（`jsjkx2025-kg-rag-qa` 只在清单里、没有 PDF），
> 其中 **21 篇**已出图（`data/corpus/graphs/*.json`）。

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
# M1（2026-09-17）：OpenAlex 的元数据（**权威源**，不是猜的）
OPENALEX = ROOT / 'data' / 'quality' / 'openalex-cache.json'
OUT = ROOT / 'web' / 'kg-data' / 'paper-registry.json'

# arXiv id 长这样：2506.09566v1 / 2202.07412v1 / 1703.08098v9
RE_ARXIV = re.compile(r'^\d{4}\.\d{4,5}v\d+$')

# ★ 2026-09-19：`lang` 的**唯一判据** —— 标题里有没有 CJK 统一表意文字。
#
# 为什么判据是"标题里的 CJK"而不是别的（文件名 / 目录 / 人工登记）：
#   · **可测**：一个正则，任何人跑一遍都得到同一个答案；
#   · **可解释**：中文期刊论文的标题必然是中文，英文 arXiv 的标题必然是英文；
#   · **不用维护名单**：加一篇中文语料时，只要它的标题是中文就自动归对
#     （写死 id 前缀 `jsjkx*` 那种做法，下一批中文语料就得再改一次代码）。
# ⚠️ 它**只**看 `\u4e00-\u9fff`（基本区）—— 日文假名 / 韩文谚文**不算 zh**，
#    因为本库没有那种语料，**不为不存在的类别预留分支**（真出现了再加 `other`）。
RE_CJK = re.compile(r'[\u4e00-\u9fff]')


def lang_of(title):
    """标题 ⇒ `'zh'` / `'en'`。**判据只有一条**：标题里含不含 CJK 字符。

    ## 兜底策略（"某条没有标题字段"时怎么办）

    标题的来源按顺序取，**但判据自始至终是同一个**：

    | 顺序 | 来源 | `lang_source` |
    |---|---|---|
    | ① | `_manifest.json` 的 `title`（**首选** —— 每条清单都该有） | `manifest.title` |
    | ② | OpenAlex 的 `title`（`data/quality/openalex-cache.json`） | `openalex.title` |
    | ③ | 两处都没有 ⇒ 空标题 | `no-title` |

    ③ 的结果是 **`en`**（空串当然不含 CJK）—— **这不是特例分支，而是同一条判据
    的自然结果**：所以"没有标题"时不会凭空冒出第三种取值，
    `check_paper_registry.py` 也就能用同一套规则独立复算、并在有人手改成 `zh` 时报红。
    ⚠️ 实测本库 33 条**全都有标题**（①全覆盖），②③ 目前一次都没触发。
    """
    return 'zh' if RE_CJK.search(str(title or '')) else 'en'


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
    # ★ 2026-09-19：**顺带透传节点/边数** —— 论文页要按语言组报"共 K 节点"，
    #   而"节点数"的真相在 `corpus-index.json`（`papers[].nodes`，由语料索引工具
    #   解析图时统计）⇒ **注册表只搬运、不重算**（与它"透传 + 派生"的性质一致）。
    #   ⚠️ 只在**确实出了图**（`graph_ids`）时才带这两个数，否则一律 `None` ——
    #      "没出图"与"出了图但 0 节点"是两件事，不能都用 0 表示。
    in_graph = set()
    index_scale = {}
    if INDEX.exists():
        idx = json.loads(INDEX.read_text(encoding='utf-8'))
        for p in (idx.get('papers') or []):
            if p.get('in_graph'):
                in_graph.add(p['id'])
            if p.get('nodes') is not None:
                index_scale[p['id']] = (p.get('nodes'), p.get('edges'))

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
    #
    # ★ M1（2026-09-17）：**作者与"预印本→正式版"从 OpenAlex 来**。
    #   为什么可以写进注册表（而 `probe_authors.py` 的候选不行）：
    #   **OpenAlex 是机构维护的权威源（带 ORCID），不是"从 PDF 猜出来的"** ——
    #   N7 那条纪律（"猜出来的作者比没有更糟"）针对的是**猜**，不是**查**。
    #   ⚠️ 而 `probe_authors.py` 的候选**仍然不写进来**（它是猜的，只有 43% 可信）。
    oa = {}
    if OPENALEX.exists():
        try:
            oa = json.loads(OPENALEX.read_text(encoding='utf-8')).get('works') or {}
        except Exception:
            oa = {}
    n_oa = sum(1 for v in oa.values() if not v.get('_error'))

    def _oa_of(pid):
        v = oa.get(pid)
        return None if (not v or v.get('_error')) else v

    papers = []
    for pid, m in by_id.items():
        o = _oa_of(pid) or {}
        # ★ 语言的标题来源（**顺序就是 lang_of 的兜底顺序**，如实记在 lang_source 上）
        _t_manifest = m.get('title')
        _t_openalex = o.get('title')
        _title = _t_manifest or _t_openalex
        row = {
            'arxiv_id': pid,
            'title': _title,
            # ★ 2026-09-19：**语言**（rule 见 `lang_of()`；归档条目同样有 lang）
            'lang': lang_of(_title),
            'lang_source': ('manifest.title' if _t_manifest
                            else ('openalex.title' if _t_openalex else 'no-title')),
            'year': m.get('year') or o.get('year'),
            'query': m.get('query'),
            # ★ 归档：**透传清单的事实**（`archived` 是 N7 之后加上的一列，
            #   表示"被替代但**留档不删**"）。页面上这类条目要**照常显示**，
            #   并单独打「已归档」标签 —— 藏起来就等于把 ARCHIVE_POLICY 抹掉。
            'archived': bool(m.get('archived')),
            'archived_to': m.get('archived_to'),
            'archived_reason': m.get('archived_reason'),
            'downloaded': pid in pdfs,
            'in_graph': pid in in_graph or pid in graph_ids,
            'graph_file': f'data/corpus/graphs/{pid}.json' if pid in graph_ids else None,
            # ★ 规模（**只在真的出了图时**才有值；口径见 ② 的注释）
            'nodes': (index_scale.get(pid) or (None, None))[0] if pid in graph_ids else None,
            'edges': (index_scale.get(pid) or (None, None))[1] if pid in graph_ids else None,
            # **作者**：优先用 OpenAlex（权威 + ORCID）；没有就如实 None
            'author': ('; '.join(x['name'] for x in (o.get('authors') or [])
                                 if x.get('name')) if o.get('authors') else None),
            'author_source': 'openalex' if o.get('authors') else None,
            'authors': o.get('authors') or [],
            # M1 带来的新字段
            'openalex_id': o.get('openalex_id'),
            'doi': o.get('doi'),
            'cited_by_count': o.get('cited_by_count'),
            'work_type': o.get('type'),
            'published_venue': o.get('published_venue'),
            'is_published': o.get('is_published'),
            'uploads': [u['gid'] for u in uploads.values() if u['arxiv_id'] == pid],
        }
        # ★ 2026-09-19：**非 arXiv 语料**（《计算机科学》中文 OA 论文 4 篇）。
        #
        # 为什么需要这一段：这些 id 不是 arXiv id ⇒ **OpenAlex 里查不到**
        # （M1 那条链只认 arXiv DOI），于是作者/DOI/期刊全落在清单里。
        # ⇒ 从清单兜底，并**如实把来源写成 `manifest`**（不是 `openalex`）——
        #    与 N7 那条"猜出来的作者比没有更糟"一致：这里不是猜，是期刊页/PDF 自述。
        # ⚠️ 这一段**只给非 arXiv 条目追加键**，arXiv 那 28 条的输出**一个字节不动**。
        if not RE_ARXIV.match(str(pid)):
            authors_cn = [str(x) for x in (m.get('authors') or []) if x]
            row.update({
                'author': row['author'] or ('; '.join(authors_cn) or None),
                'author_source': row['author_source'] or (
                    'manifest（PDF 首页「引用本文」+ 期刊页）' if authors_cn else None),
                'doi': row['doi'] or m.get('doi'),
                'published_venue': row['published_venue'] or m.get('venue'),
                'work_type': row['work_type'] or 'journal-article',
                'corpus_id_kind': 'non-arxiv（中文期刊，id 是本地语料 id，不是 arXiv id）',
                'citation': m.get('citation'),
                'url': m.get('url'),
                'pages': m.get('pages'),
                'license': m.get('license'),
                'note': m.get('note'),
            })
        papers.append(row)
    # 上传过但**不在清单里**的（外部 PDF）—— 也要有一行，否则"我传过哪些"漏
    extra = {}
    for u in uploads.values():
        aid = u['arxiv_id']
        if aid and aid not in by_id:
            o = _oa_of(aid) or {}
            extra.setdefault(aid, {'arxiv_id': aid, 'title': o.get('title'),
                                   'lang': lang_of(o.get('title')),
                                   'lang_source': ('openalex.title' if o.get('title')
                                                   else 'no-title'),
                                   'year': o.get('year'), 'query': None,
                                   'archived': False, 'archived_to': None,
                                   'archived_reason': None,
                                   'downloaded': aid in pdfs,
                                   'in_graph': aid in graph_ids,
                                   'graph_file': (f'data/corpus/graphs/{aid}.json'
                                                  if aid in graph_ids else None),
                                   'nodes': ((index_scale.get(aid) or (None, None))[0]
                                             if aid in graph_ids else None),
                                   'edges': ((index_scale.get(aid) or (None, None))[1]
                                             if aid in graph_ids else None),
                                   'author': ('; '.join(x['name'] for x in (o.get('authors') or [])
                                                        if x.get('name'))
                                              if o.get('authors') else None),
                                   'author_source': 'openalex' if o.get('authors') else None,
                                   'authors': o.get('authors') or [],
                                   'openalex_id': o.get('openalex_id'), 'doi': o.get('doi'),
                                   'cited_by_count': o.get('cited_by_count'),
                                   'work_type': o.get('type'),
                                   'published_venue': o.get('published_venue'),
                                   'is_published': o.get('is_published'),
                                   'uploads': []})
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
                f'① 清单条目：_manifest.json 的一条（{len(manifest)} · '
                f'其中 arXiv {sum(1 for p in papers if RE_ARXIV.match(str(p["arxiv_id"])))} 篇、'
                f'中文期刊 {sum(1 for p in papers if not RE_ARXIV.match(str(p["arxiv_id"])))} 篇）',
                f'② 已下载：data/corpus/<id>.pdf 存在（{len(pdfs)}）',
                f'③ 已入库：data/corpus/graphs/<id>.json 存在（{len(graph_ids)}）',
                f'④ 上传图：data/<gid>/graph.json 存在（{len(uploads)}）—— **gid ≠ arXiv id**',
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
            # M1：OpenAlex 对齐情况
            'openalex_aligned': n_oa,
            'with_author': sum(1 for p in papers if p.get('author')),
            'published': sum(1 for p in papers if p.get('is_published')),
            # ★ 语言分布（由上面的 `lang_of` 派生；页面按它分组）
            'lang_zh': sum(1 for p in papers if p.get('lang') == 'zh'),
            'lang_en': sum(1 for p in papers if p.get('lang') == 'en'),
        },
        # ★ 2026-09-19：语言的**规则本身**也写进产物 ——
        #   这样"33 条里 zh/en 各几条、依据是什么"不必去读代码；
        #   `check_paper_registry.py` 用同一条规则独立复算，手改 lang 会当场报红。
        'lang_rule': {
            'rule': '标题里含 CJK 统一表意文字（\\u4e00-\\u9fff）⇒ zh，否则 en',
            'field': 'papers[].lang（取值只有 zh / en —— 本库没有第三种语料，'
                     '**不为不存在的类别预留 other**）',
            'title_order': ['① _manifest.json 的 title（首选）',
                            '② OpenAlex 的 title（openalex-cache.json）',
                            '③ 两处都无 ⇒ 空标题，按同一条判据自然落 en'],
            'why_title': '标题是"这篇论文是什么语言"最直接的自述；'
                         '**不写死 id 前缀 / 文件名**（下一批中文语料不用改代码）。',
            'archived_also_has_lang': True,
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
            # ⚠️ M1（2026-09-17）**改写了这一条** —— 原来写的是"作者缺失"，
            #    而 OpenAlex 接上之后**24/28 篇有了权威作者（带 ORCID）**。
            #    （「28」是**当时的 arXiv 篇数**，属历史读数；中文语料加入后清单已
            #     34 条，所以紧接的下一段改成**实时算分母**，不再写死 —— 见下两行的
            #     `2026-09-19` 注与 f-string。）
            #    **过时的告警比没有告警更糟**（本项目栽过：H10 的 +8.16pp、
            #    TODO 的 35 项 ⬜ 漂移）⇒ 这里如实改掉。
            # ⚠️ 2026-09-19：分母改成**实时算**（加中文语料后写死的 28 会立刻过时）。
            f'**作者**：arXiv 那 '
            f'{sum(1 for p in papers if RE_ARXIV.match(str(p["arxiv_id"])))} 篇用 OpenAlex'
            f'（**权威源 + ORCID**），'
            f'{sum(1 for p in papers if RE_ARXIV.match(str(p["arxiv_id"])) and p.get("author"))}'
            f' 篇有；剩下的 OpenAlex 查不到（老论文的 arXiv DOI 可能是后补的）。'
            f'⚠️ `tools/probe_authors.py` 从 PDF 解析出的候选**不写进这里**'
            f'（那是猜的，只有 43% 可信）—— **"猜出来的作者比没有更糟"**。',
            f'**OpenAlex 对齐率 '
            f'{sum(1 for p in papers if p.get("in_graph") and p.get("openalex_id"))}/'
            f'{sum(1 for p in papers if p.get("in_graph") and RE_ARXIV.match(str(p["arxiv_id"])))}'
            f'（已入库的 arXiv 篇）** —— '
            f'TODO 里原先写"实测 16/17 = 94%"，**实测偏低**（差 2 篇：'
            f'`2002.00388v4` / `2012.00456v1`，都是 2020 年前后的老论文）。'
            f'**对齐只能到论文级** —— 本地实体（`TransE` 等）没有全局 ID。',
            # ★ 2026-09-19：中文 OA 语料（4 篇）—— **逐篇备注落在 `papers[].note` 上**，
            #    这里只给总述，免得"中文那 4 篇是什么来头"只能从字段里猜。
            f'**中文 OA 语料（'
            f'{sum(1 for p in papers if not RE_ARXIV.match(str(p["arxiv_id"])))} 篇，'
            f'2026-09-19 加）**：《计算机科学》开放获取（**CC BY-NC-ND**，'
            f'https://www.jsjkx.com/CN/column/item20.shtml），'
            f'⚠️ **id 是本地语料 id、不是 arXiv id** ⇒ 用 `corpus_id_kind` 标出来，'
            f'作者/DOI/卷期页的来源写成 `manifest`（PDF 首页「引用本文」+ 期刊页，'
            f'**不是 OpenAlex**）。其中 '
            f'{sum(1 for p in papers if not RE_ARXIV.match(str(p["arxiv_id"])) and p.get("in_graph") and not p.get("archived"))}'
            f' 篇**在库且已出图**；`jsjkx2025-kg-rag-qa`'
            f'「一种基于知识图谱的检索增强生成情报问答技术」因**方向敏感**'
            f'（军事情报问答、公开演示场合不合适）**已归档**，按 `ARCHIVE_POLICY` '
            f'**留档不删**（PDF 与图都在 `data/corpus/_archive-military/`，'
            f'见那里的 README）—— 它曾经的图是**探索性首抽、质量待修**'
            f'（40% chunk 超时导致残图；13% 假边来自「引用本文 + 相似文章推荐」块被当正文）'
            f'—— 详见各行的 `note`。',
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
    print(f"  ★ 语言：中文 {c['lang_zh']} 篇 · 英文 {c['lang_en']} 篇"
          f"（判据：标题含 CJK ⇒ zh；归档条目**同样有 lang**）")
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
