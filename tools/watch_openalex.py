# -*- coding: utf-8 -*-
r"""M6 · **实时更新 / 新论文提醒**（"检查更新"，不是"推送"）。

## TODO 的原话

> | **M6** | ⬜ **实时更新 / 新论文提醒** | **B（半）** |
> **做双轨后顺手可得** —— API 天然实时，差一个定时任务 |

**⇒ TODO 说得对**：M1（`e68c257`）接了 OpenAlex 之后，
"有哪些新论文"就是**一次查询**的事。这一项就是那个查询。

## ★★ 但它**不是"推送"**，这一点必须说清

本项目是**本地单机研究工具**（数据在本机、模型在本机、没有常驻服务）。
⇒ **它不能"提醒"你** —— 除非有人去跑它。

**⇒ 所以本脚本做的是「检查更新」**：
你（或一个计划任务）跑一次，它告诉你**自上次以来新出现了哪些**。
**它不假装自己是个订阅服务。**

（如果将来真要"推送"，那需要：一个常驻进程 + 一个通知通道。
**那与"单机研究工具"的形态冲突** —— 所以这里不做，并在文档里写明。）

## ★ 用法与两个真实场景

**场景 A：手动检查**（每次想看看有没有新综述时）
```powershell
python tools/watch_openalex.py            # 用 _manifest.json 里那 3 个检索式
```

**场景 B：计划任务**（Windows 任务计划 / cron —— **这才是"定时"**）
```powershell
# 每周一早上跑一次，把结果写进文件
python tools/watch_openalex.py --since 2026-09-01 --out report.json
```

## ★ 它怎么判断"新"

三种信号，**分别如实报告**（不混成一个数）：

1. **`since`（时间）**：`from_publication_date` 过滤 —— 出版日期在那之后的；
2. **`new_to_us`（库里没有）**：拿回来的 work 里，**DOI 或 arXiv id 不在本地注册表**的；
3. **`no_fulltext`（拿不到全文）**：`is_oa=False` 或没有 arXiv 版的 ——
   **这一类"知道有，但收不进来"**（本项目要 PDF 才能抽取）。

**⇒ 第 3 条是最容易被漏掉、也最容易被误解的一条**：
"发现了 40 篇新论文"听起来很好，**而其中可能 30 篇拿不到 PDF** ——
那对"扩充语料"这件事**没有用**。所以必须分开报。

用法：
    python tools/watch_openalex.py
    python tools/watch_openalex.py --since 2026-06-01 --per-query 50
    python tools/watch_openalex.py --bib output\new-papers.bib   # 顺手导一份 .bib
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / 'data' / 'corpus' / '_manifest.json'
REG = ROOT / 'web' / 'kg-data' / 'paper-registry.json'
OUT = ROOT / 'data' / 'quality' / 'watch-report.json'
STATE = ROOT / 'data' / 'quality' / 'watch-state.json'

API = 'https://api.openalex.org/works'
MAILTO = 'kg-cosmos@example.org'
UA = 'kg-cosmos/1.0 (academic demo; +https://openalex.org)'

RE_ARXIV_DOI = re.compile(r'10\.48550/arxiv\.(\d{4}\.\d{4,5})', re.I)


def query(search, since, per):
    """`title.search` + 日期过滤。**标题检索比全文检索精准得多**。

    ⚠️ **检索词必须 URL 编码** —— 实测踩过：
    `title.search:knowledge graph survey` 里的**空格直接进了 URL** ⇒
    `InvalidURL: URL can't contain control characters`（错误信息里的
    "control characters" 指的就是那个空格）。
    ⇒ 用 `quote(search, safe='')` 编码。
    （`filter` 的其余部分用 `,` 与 `:` 分隔，**那些不能编码**，
      所以只编码检索词本身。）
    """
    filt = 'title.search:' + urllib.parse.quote(search, safe='')
    if since:
        filt += f',from_publication_date:{since}'
    url = (f'{API}?filter={filt}&per-page={per}'
           f'&sort=publication_date:desc&mailto={MAILTO}')
    req = urllib.request.Request(url, headers={'User-Agent': UA,
                                               'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r), None
    except urllib.error.HTTPError as e:
        return None, f'HTTP {e.code}'
    except Exception as e:                              # noqa: BLE001
        return None, f'{type(e).__name__}: {str(e)[:80]}'


def shape(w):
    doi = (w.get('doi') or '').replace('https://doi.org/', '')
    m = RE_ARXIV_DOI.search(doi)
    arxiv = m.group(1) if m else None
    locs = w.get('locations') or []
    # **能不能拿到全文**：有 arXiv 版，或至少一个 OA 的 pdf
    has_arxiv = bool(arxiv) or any('arxiv' in str((l.get('source') or {}).get('display_name') or '').lower()
                                   for l in locs)
    has_oa_pdf = any(l.get('pdf_url') and l.get('is_oa') for l in locs)
    au = [(a.get('author') or {}).get('display_name')
          for a in (w.get('authorships') or [])]
    return {
        'openalex_id': (w.get('id') or '').rsplit('/', 1)[-1],
        'title': w.get('title'), 'date': w.get('publication_date'),
        'year': w.get('publication_year'), 'type': w.get('type'),
        'doi': doi or None, 'arxiv_id': arxiv,
        'cited_by_count': w.get('cited_by_count'),
        'authors': [x for x in au if x][:6],
        'n_authors': len([x for x in au if x]),
        'is_oa': w.get('open_access', {}).get('is_oa'),
        'has_arxiv': has_arxiv, 'has_oa_pdf': has_oa_pdf,
        # **能不能收进来**（本项目要 PDF 才能抽取）
        'obtainable': bool(has_arxiv or has_oa_pdf),
    }


def main():
    ap = argparse.ArgumentParser(description='M6 检查新论文（**不是推送**）')
    ap.add_argument('--since', default='',
                    help='出版日期起点（YYYY-MM-DD）；省略则读上次的 last_checked')
    ap.add_argument('--per-query', type=int, default=25)
    ap.add_argument('--bib', default='', help='顺手把新增的导成 .bib')
    ap.add_argument('--no-state', action='store_true', help='不更新 last_checked')
    a = ap.parse_args()
    t0 = time.time()
    print('=' * 92)
    print('M6 · 检查新论文（**"检查更新"，不是"推送"** —— 见 docstring）')
    print('=' * 92)
    if not MANIFEST.exists() or not REG.exists():
        print('  ❌ 缺 _manifest.json 或注册表')
        return 1

    mf = json.loads(MANIFEST.read_text(encoding='utf-8'))
    reg = json.loads(REG.read_text(encoding='utf-8'))
    # **本地已有什么**（用两把钥匙：DOI 与 arXiv id，且忽略版本号）
    local_doi = {str(p.get('doi') or '').replace('https://doi.org/', '').lower()
                 for p in (reg.get('papers') or []) if p.get('doi')}
    local_arx = {re.sub(r'v\d+$', '', str(p['arxiv_id']))
                 for p in (reg.get('papers') or []) if p.get('arxiv_id')}

    st = {}
    if STATE.exists():
        try:
            st = json.loads(STATE.read_text(encoding='utf-8'))
        except Exception:
            st = {}
    since = a.since or st.get('last_checked') or ''
    print(f'  本地已收 {len(local_arx)} 篇（arXiv id，忽略版本号）· '
          f'{len(local_doi)} 个 DOI')
    print(f'  起点日期：**{since or "（未设，取全部）"}**'
          f'{"" if a.since else "（来自上次记录）" if since else ""}')

    # **检索式从 _manifest 来**：那是真实的选取依据，不是新编的
    queries = sorted({(p.get('query') or '').strip() for p in mf if p.get('query')})
    # OpenAlex 的 `title.search` 不认 `all:"x" AND all:"y"` 这种 arXiv 语法
    # ⇒ **拆成词**（这是必要的转换，**并在报告里写明"检索式被改写过"**）
    def to_openalex(q):
        words = re.findall(r'[\w\-]+', q)
        words = [w for w in words
                 if w.lower() not in ('all', 'and', 'or', 'not') and len(w) > 2]
        return ' '.join(words)
    print(f'  检索式 {len(queries)} 个（来自 `_manifest.json`）：')
    for q in queries:
        print(f'      {q[:52]:<54} → title.search: {to_openalex(q)[:40]}')

    all_new, per_query = [], []
    for q in queries:
        oa_q = to_openalex(q)
        if not oa_q:
            continue
        d, err = query(oa_q, since, a.per_query)
        if err:
            print(f'    ✗ {oa_q[:40]} ⇒ {err}')
            per_query.append({'query': q, 'openalex_query': oa_q, 'error': err})
            continue
        res = [shape(w) for w in (d.get('results') or [])]
        cnt = (d.get('meta') or {}).get('count')
        new = []
        for w in res:
            hit = False
            if w['doi'] and w['doi'].lower() in local_doi:
                hit = True
            if w['arxiv_id'] and re.sub(r'v\d+$', '', w['arxiv_id']) in local_arx:
                hit = True
            if not hit:
                new.append(w)
        print(f'    ✓ {oa_q[:36]:<38} 命中 {cnt} · 取回 {len(res)} · **新的 {len(new)}**')
        per_query.append({'query': q, 'openalex_query': oa_q,
                          'total_hits': cnt, 'fetched': len(res), 'new': len(new)})
        all_new.extend(new)
        time.sleep(0.35)          # 礼貌

    # 去重（不同检索式会重叠）
    seen, uniq = set(), []
    for w in all_new:
        k = w['openalex_id'] or w['doi'] or w['title']
        if k in seen:
            continue
        seen.add(k)
        uniq.append(w)
    obtainable = [w for w in uniq if w['obtainable']]
    print()
    print(f'  ★ 去重后 **{len(uniq)} 篇新的**')
    print(f'     · **能拿到全文的 {len(obtainable)} 篇**（有 arXiv 版或 OA 的 PDF）'
          f' ⇒ **这些才真能收进语料**')
    print(f'     · **拿不到全文的 {len(uniq) - len(obtainable)} 篇** ⇒ '
          f'"知道有，但收不进来"（本项目要 PDF 才能抽取）')
    print()
    for w in (obtainable or uniq)[:10]:
        mark = '✓' if w['obtainable'] else '·'
        print(f"    {mark} {str(w['date'])[:10]}  {str(w['type'] or '')[:9]:<10}"
              f"{str(w['title'])[:52]:<54}"
              f"{'arXiv:' + w['arxiv_id'] if w['arxiv_id'] else ''}")
    if len(uniq) > 10:
        print(f'    …另有 {len(uniq) - 10} 篇')

    payload = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'purpose': 'M6：检查新论文（**"检查更新"，不是"推送"**）',
        'design': {
            'not_a_push_service':
                '本项目是**本地单机研究工具**（数据/模型都在本机、无常驻服务）'
                '⇒ **它不能"提醒"你，除非有人去跑它**。'
                '所以这里是「检查更新」：跑一次，报告自上次以来新出现的。'
                '**不假装自己是订阅服务。**',
            'three_signals_reported_separately':
                '`since`（时间过滤）· `new_to_us`（DOI/arXiv 不在本地）· '
                '`obtainable`（**能不能拿到 PDF**）—— **三个分别报，不混成一个数**。'
                '因为"发现 40 篇新的"听起来很好，而其中可能 30 篇拿不到 PDF，'
                '那对"扩充语料"没有用。',
            'query_was_rewritten':
                '`_manifest.json` 里的检索式是 **arXiv 语法**'
                '（`all:"knowledge graph" AND all:"survey"`），'
                '`title.search` 不认 ⇒ **拆成词**。'
                '**这是改写，所以原始检索式与改写后的都记在报告里。**',
            'state_file':
                '`watch-state.json` 记 `last_checked` ⇒ 下次不传 `--since` 就只看更新的。',
        },
        'since': since,
        'local': {'arxiv_ids': len(local_arx), 'dois': len(local_doi)},
        'per_query': per_query,
        'counts': {'new_total': len(uniq), 'obtainable': len(obtainable),
                   'not_obtainable': len(uniq) - len(obtainable)},
        'new': uniq,
        'elapsed_s': round(time.time() - t0, 1),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                   encoding='utf-8')
    print(f'\n  已写出 {OUT.relative_to(ROOT)}')

    if a.bib and obtainable:
        lines = []
        for w in obtainable:
            au = ' and '.join(w['authors'])
            lines.append(
                '@misc{' + str(w['openalex_id']) + ',\n'
                + (f'  author = {{{au}}},\n' if au else '')
                + f'  title = {{{{{w["title"]}}}}},\n'
                + (f'  year = {{{w["year"]}}},\n' if w.get('year') else '')
                + (f'  doi = {{{w["doi"]}}},\n' if w.get('doi') else '')
                + (f'  eprint = {{{w["arxiv_id"]}}},\n  archivePrefix = {{arXiv}},\n'
                   if w.get('arxiv_id') else '')
                + f'  note = {{M6 检查到的新论文；元数据来自 OpenAlex}}\n}}\n')
        p = Path(a.bib)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('\n'.join(lines), encoding='utf-8')
        print(f'  ✅ 新增的 {len(obtainable)} 篇已导成 {p}（可直接导进 Zotero / LaTeX）')

    if not a.no_state:
        STATE.write_text(json.dumps(
            {'last_checked': time.strftime('%Y-%m-%d'),
             'note': 'M6 的检查起点；下次跑 watch_openalex.py 会用它',
             'counts': payload['counts']},
            ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'  已更新 {STATE.relative_to(ROOT)}（last_checked = 今天）')

    print()
    print('  ★ 想定时的话（**这才是"定时任务"**，而且由你来定）：')
    print('      Windows 任务计划 / cron 里跑 `python tools/watch_openalex.py`，')
    print('      它每次只看 `last_checked` 之后的新论文。')
    print(f'  {time.time() - t0:.1f}s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
