# -*- coding: utf-8 -*-
r"""M1 · **元数据 API 双轨（广度轨）** —— 接 OpenAlex。

## TODO 的原话

> | **M1** | ⬜ **元数据 API 双轨（广度轨）** | **B 的解法** ★★★ |
> 接 **OpenAlex**（CC0，~5.1 亿 works；**额度实测：无 key 约 1000 次/天**，
> 不是我原先写的"$1 / 10 万次"）。**对齐只能到论文级**（实测 **16/17 = 94%**；
> 实体级不可能 —— 本地 `TransE` 没有全局 ID）。
> **必须含"预印本→正式版"解析** …

## ★ 实测：**直连就能通**（不用代理）

```
https://api.openalex.org/works?filter=doi:10.48550/arxiv.2402.12001  ⇒ HTTP 200 · 11.3 KB
（走本地 7897 代理也是 200，但**不需要**）
```

**这一点很重要**：M8（扩语料）的前置是"`arxiv.org` 在当前网络全部 000"，
**但 OpenAlex 通** ⇒ **"广度轨"不必等代理**。

## ★ 它给的是**权威**数据（正好补上 K2 的短板）

```
id                https://openalex.org/W4391987978      ← **全局 ID**（本地的 TransE 没有）
type              **preprint**                           ← 说明还没正式发表
cited_by_count    2
作者 2 位（**带 ORCID**）: Xiaxia Wang / Gong Cheng
locations         2 个（都是 arXiv；**若正式版存在，这里会出现期刊**）
```

**⇒ K2 从 PDF 解析作者只有 43%**（`docs/K2_AUTHOR_PARSING.md`）；
**而这里给的是机构维护的 ORCID 标识** —— **这才是"作者"该有的来源。**

## ★ "对齐只能到论文级"是什么意思

本地图谱里的实体（`TransE`、`Knowledge Graphs`…）**没有全局 ID**，
所以**不可能把本地实体映射到 OpenAlex 的实体**。
⇒ 能对齐的只有**论文级**：**本地的一篇（arXiv id）↔ OpenAlex 的一个 work**。

**⇒ 本脚本只做论文级，并在报告里如实写出这一点。**

## ★ "预印本→正式版"解析

arXiv 论文常常后来正式发表。OpenAlex 的 `locations` 会**同时列出两个版本**：
- arXiv（`type: preprint` 时的 `primary_location`）；
- 期刊/会议（若已发表，会多一个 `source` 是期刊的 location）。

⇒ 本脚本为每篇给出：
- **`published_venue`**：正式发表的期刊/会议（**没有则如实为 `None`**）；
- **`is_published`**：是否已从预印本变成正式版；
- **`version_count`**：locations 里**不同 source** 的个数。

**⚠️ 不猜**：`locations` 里若只有 arXiv，就是 **`is_published = False`** ——
**不能因为"它 2024 年的论文大概发表了"就填一个。**

用法：
    python tools/fetch_openalex.py --probe            # 只探 1 篇，看字段
    python tools/fetch_openalex.py                    # 全 28 篇（**走缓存**）
    python tools/fetch_openalex.py --refresh          # 忽略缓存重取
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / 'data' / 'corpus' / '_manifest.json'
CACHE = ROOT / 'data' / 'quality' / 'openalex-cache.json'
OUT = ROOT / 'web' / 'kg-data' / 'openalex.json'

API = 'https://api.openalex.org/works'
# ⚠️ **礼貌池**：OpenAlex 建议带 `mailto`（能把请求放进更快的池子，
# 也让对方能在出问题时联系到人）。**这不是可选的礼貌，是他们的使用条款。**
MAILTO = 'kg-cosmos@example.org'
UA = 'kg-cosmos/1.0 (academic demo; +https://openalex.org)'


import re

RE_VERSION = re.compile(r'v\d+$')


def slug(arxiv_id):
    """`2402.12001v1` → `10.48550/arxiv.2402.12001`

    ★ **必须去掉版本号** —— 这是第三次踩坑，也是最隐蔽的一次：

        本地 id  = 2402.12001**v1**
        arXiv DOI = 10.48550/arxiv.2402.12001        ← **不带 v1**

    实测：
        filter=doi:10.48550/arxiv.2402.12001v1  ⇒ **meta.count = 0**（查不到）
        filter=doi:10.48550/arxiv.2402.12001    ⇒ **meta.count = 1** ✓

    ⚠️ 而且它**不报错**：HTTP 200，只是 `results` 为空。
    ⇒ 所以前两版都是"28 篇全部查不到"，**看起来像"OpenAlex 没收录"**，
      而真相是**我把 DOI 写错了**。
    **教训**：查不到时，先把自己拼的串与手工验证过的串**逐字符比一遍**。
    """
    return '10.48550/arxiv.' + RE_VERSION.sub('', str(arxiv_id))


def fetch_one(arxiv_id):
    """取一篇。**返回 (数据, 错误)** —— 错误如实返回，不抛。

    ⚠️ **URL 拼法踩过一次坑**：第一版用单条路径 `/works/doi:10.48550/arxiv.xxx`
    并且用 `urllib.parse.quote(..., safe='')` 把 `:` 编成 `%3A` ⇒ **28 篇全部 404**。
    而手工验证过的是**列表查询**：

        /works?filter=doi:10.48550/arxiv.2402.12001&per-page=1   ⇒ 200

    ⇒ 改成用已验证的那一种（`filter=` + 不编码的 `:`）。
    **教训**：手测通了、代码不通，**先怀疑"我拼的和手测的不是同一个东西"**。
    """
    url = (f'{API}?filter=doi:{slug(arxiv_id)}'
           f'&per-page=1&mailto={MAILTO}')
    req = urllib.request.Request(url, headers={'User-Agent': UA,
                                               'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
        results = (d or {}).get('results') or []
        if not results:
            return None, 'OpenAlex 查不到这个 DOI（results 为空）'
        return results[0], None
    except urllib.error.HTTPError as e:
        return None, f'HTTP {e.code}'
    except Exception as e:                              # noqa: BLE001
        return None, f'{type(e).__name__}: {e}'


def shape(d):
    """把 OpenAlex 的原始返回**压成我们要的字段**（不搬整包）。"""
    auths = []
    for a in (d.get('authorships') or []):
        au = a.get('author') or {}
        auths.append({
            'name': au.get('display_name'),
            'orcid': au.get('orcid'),
            'position': a.get('author_position'),
            # 机构（第一个）
            'institution': ((a.get('institutions') or [{}])[0]).get('display_name'),
            'raw_affiliation': (a.get('raw_affiliation_strings') or [None])[0],
        })
    locs, srcs = [], set()
    for l in (d.get('locations') or []):
        src = l.get('source') or {}
        nm = src.get('display_name')
        if nm:
            srcs.add(nm)
        locs.append({'source': nm, 'version': l.get('version'),
                     'is_oa': l.get('is_oa'),
                     'landing': l.get('landing_page_url'),
                     'pdf': l.get('pdf_url')})
    # ── **预印本 → 正式版**：locations 里除 arXiv 之外的 source ──
    non_arxiv = [x['source'] for x in locs
                 if x['source'] and 'arxiv' not in x['source'].lower()]
    return {
        'openalex_id': (d.get('id') or '').rsplit('/', 1)[-1] or None,
        'doi': d.get('doi'),
        'title': d.get('title') or d.get('display_name'),
        'year': d.get('publication_year'),
        'type': d.get('type'),                       # preprint / article / review …
        'cited_by_count': d.get('cited_by_count'),
        'authors': auths,
        'n_authors': len(auths),
        'locations': locs,
        'n_locations': len(locs),
        'sources': sorted(srcs),
        # **正式发表的场所**（没有 ⇒ None，**不猜**）
        'published_venue': (non_arxiv[0] if non_arxiv else None),
        'is_published': bool(non_arxiv),
        'referenced_works_count': d.get('referenced_works_count'),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--probe', action='store_true', help='只探 1 篇')
    ap.add_argument('--refresh', action='store_true', help='忽略缓存重取')
    ap.add_argument('--sleep', type=float, default=0.35,
                    help='每篇之间停多久（礼貌；1000 次/天的额度很宽裕）')
    a = ap.parse_args()
    t0 = time.time()
    print('=' * 92)
    print('M1 · OpenAlex 元数据双轨（**论文级对齐** —— 实体级不可能，见 docstring）')
    print('=' * 92)

    mf = json.loads(MANIFEST.read_text(encoding='utf-8'))
    ids = [p['id'] for p in mf]
    if a.probe:
        ids = ids[:1]
    cache = {}
    if CACHE.exists() and not a.refresh:
        try:
            cache = json.loads(CACHE.read_text(encoding='utf-8'))
            print(f'  缓存命中 {len(cache.get("works") or {})} 篇')
        except Exception:
            cache = {}
    works = cache.get('works') or {}

    hits = miss = fresh = 0
    for i, pid in enumerate(ids):
        if pid in works and not a.refresh:
            hits += 1
            continue
        d, err = fetch_one(pid)
        if err:
            works[pid] = {'_error': err}
            miss += 1
            print(f'    ✗ {pid:<17}{err}')
        else:
            works[pid] = shape(d)
            fresh += 1
            w = works[pid]
            print(f"    ✓ {pid:<17}W={w['openalex_id']}  "
                  f"{w['n_authors']} 作者 · {w['type']} · 被引 {w['cited_by_count']}"
                  f"{' · **已发表到 ' + str(w['published_venue'])[:26] + '**' if w['is_published'] else ''}")
        if i < len(ids) - 1:
            time.sleep(a.sleep)

    ok = {k: v for k, v in works.items() if not v.get('_error')}
    err = {k: v for k, v in works.items() if v.get('_error')}
    n = len(works)
    print()
    print(f'  ★ 对齐结果：**{len(ok)}/{n} 到论文级**（{len(ok) / max(1, n):.1%}）'
          f' · 失败 {len(err)} · 本次新取 {fresh} · 缓存命中 {hits}')
    if err:
        print(f'     失败的：{list(err)[:8]}')
        print('     ⚠️ **失败就是失败** —— 不填占位符、不猜。'
              '（`arXiv` 的 DOI 有些是后补的，OpenAlex 未必收录）')
    # 已发表 vs 预印本
    pub = [k for k, v in ok.items() if v.get('is_published')]
    print(f'  ★ 预印本→正式版：**{len(pub)}/{len(ok)} 已正式发表**'
          f'{"（" + ", ".join(pub[:5]) + "…）" if pub else ""}')
    types = {}
    for v in ok.values():
        types[v.get('type')] = types.get(v.get('type'), 0) + 1
    print(f'     类型分布：{types}')
    # **作者**：有多少篇拿到了 ≥1 个带 ORCID 的作者
    with_orcid = sum(1 for v in ok.values()
                     if any(x.get('orcid') for x in (v.get('authors') or [])))
    print(f'  ★ 作者（**权威源**）：{len(ok)} 篇里 {with_orcid} 篇至少有一个带 ORCID 的作者'
          f'（对比：从 PDF 解析只有 43% 可信）')

    payload = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'purpose': 'M1：OpenAlex 元数据双轨（广度轨）—— **论文级对齐**',
        'design': {
            'why_only_paper_level':
                '本地图谱里的实体（`TransE`、`Knowledge Graphs`…）**没有全局 ID**，'
                '所以**不可能**把本地实体映射到 OpenAlex 的实体。'
                '能对齐的只有**论文级**：本地一篇（arXiv id）↔ OpenAlex 一个 work。'
                '**本产物只做论文级，并如实写明这一点。**',
            'not_guessing_published':
                '"预印本→正式版"**只看 `locations` 里有没有非 arXiv 的 source**。'
                '若只有 arXiv ⇒ `is_published = False`。'
                '**不能因为"这篇 2024 年的论文大概发表了"就填一个场所。**',
            'mailto_is_required':
                f'请求带 `mailto={MAILTO}` —— 这是 OpenAlex 的**使用条款**'
                '（礼貌池），不是可选项。',
            'cache_because_quota':
                '额度约 1000 次/天（实测，无 key）⇒ **落地缓存**，'
                '重跑不会重复消耗额度。',
            'not_an_api_key':
                '**没有用 API key，也没有注册** —— CC0 数据，匿名额度够用。',
        },
        'quota_note': '无 key 约 1000 次/天（TODO 里的实测值）；本脚本带 mailto 与缓存',
        'counts': {'manifest': len(ids), 'aligned': len(ok), 'failed': len(err),
                   'published': len(pub), 'with_orcid': with_orcid},
        'works': works,
        'elapsed_s': round(time.time() - t0, 1),
    }
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                     encoding='utf-8')
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                   encoding='utf-8')
    print(f'\n  已写出 {CACHE.relative_to(ROOT)}（缓存）与 {OUT.relative_to(ROOT)}（前端）'
          f'  {time.time() - t0:.1f}s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
