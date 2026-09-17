# -*- coding: utf-8 -*-
r"""M3 · **DOI / BibTeX 导入**（输入侧）。

## TODO 的原话

> | **M3** | ⬜ **DOI / BibTeX 导入** | A（遗漏） | 补"集成"维度的输入侧（商业产品全都有） |

**⇒ "输入侧"是这个项目的短板**：现在的入口只有"拖一个 PDF 上去"。
而研究者手上通常已经有一个 `.bib`（从 Zotero / Overleaf / 期刊页面来的）。

## ★ 但"导入"这件事，**这个项目只能做一半**

商业产品（Elicit / Consensus）导入 BibTeX 后**能直接给结论** ——
因为它们**没有本地图谱**。而本项目的结论**依赖本地抽取的图**。
⇒ **导入了条目但没下载 PDF，图谱里什么都不会变。**

**⇒ 所以本项做的是**：把 `.bib` 里的条目**与本地语料对齐**，
如实报告**"哪些已经在库里 / 哪些只是条目 / 哪些连元数据都没有"**。
**它不假装"导入就完事了"。**

## 三种对齐结果（**每一种都有用**）

| 结果 | 含义 | 下一步 |
|---|---|---|
| **`in_corpus`** | 库里有（PDF 或图） | 不用管 |
| **`candidate`** | 有 **arXiv id** 但库里没有 | **可以下载并抽取**（加进待办） |
| **`metadata_only`** | 只有 DOI/标题，**没有 arXiv id** | **只能知道有这篇**；本地抽取要先找到全文 |

⇒ 而 `metadata_only` 这一类的价值是：**它告诉你"你的综述漏了哪些"** ——
那正是 `L18`（PRISMA）与 `M1`（广度轨）想回答的问题。

## ★ 解析：自己写还是装库？

`bibtexparser` 是对的库，但它**对"半个合法的 .bib"很脆**（真实的 .bib 常带注释、
自定义字段、`@string`、多行字符串）。**而这里要的信息只有三个**：
`doi` / `eprint`（arXiv）/ `title`。

⇒ **用 60 行正则解析**，比引一个库更可控，而且**解析不出来的条目如实报出来**
（**"我没解析出来"比"我猜它是这句"更诚实**）。

用法：
    python tools/import_bibtex.py --file refs.bib
    python tools/import_bibtex.py --file refs.bib --out data/quality/import-report.json
    python tools/import_bibtex.py --doi 10.1145/1234567.1234568   # 单条 DOI 查 OpenAlex
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
REG = ROOT / 'web' / 'kg-data' / 'paper-registry.json'
CORPUS = ROOT / 'data' / 'corpus'
GRAPHS = CORPUS / 'graphs'
OUT = ROOT / 'data' / 'quality' / 'import-report.json'

# ── 60 行正则解析 ────────────────────────────────────────────────────
RE_ENTRY = re.compile(r'@(\w+)\s*\{\s*([^,]+),', re.I)
# ⚠️ **`|\s*$` 那一支是必需的** —— 实测踩过：
#   一条 `@inproceedings{..., eprint = {2401.04507}}` 的 `eprint` 是**最后一个字段**，
#   它后面就是 body 的结尾（`}` 已被数括号那一步切掉了）。
#   而第一版的前瞻只写了 `(?=\n\s*\w+\s*=|\n\s*\})` ⇒ **末尾字段匹配不上**
#   ⇒ `eprint` 丢了 ⇒ 那一条被误判成"只有 DOI/标题"。
#   **凡是"字段解析漏掉最后一个"的 bug，都是这一支缺失。**
RE_FIELD = re.compile(r'(\w+)\s*=\s*[{"](.*?)[}"]\s*,?\s*'
                      r'(?=\n\s*\w+\s*=|\n\s*\}|\s*$)', re.S | re.I)
RE_DOI = re.compile(r'10\.\d{4,9}/[^\s"{}]+')
RE_ARXIV = re.compile(r'(\d{4}\.\d{4,5})(v\d+)?')
RE_OLD_ARXIV = re.compile(r'([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?')


def unwrap_title(t):
    """去掉 BibTeX 的保护括号。

    **为什么必须做**：`tools/export_citations.py` 为了**保护大小写**会把标题写成
    `{{A Survey on ...}}`（BibTeX 的标准做法）。往返测试时读回来会带上两层花括号，
    于是标题变成 `{{A Survey on ...}}` —— **看起来像原文就这么写的**。
    ⇒ 去掉**最外层**的成对 `{}`（可能有两层），并压掉多余空白。
    """
    s = ' '.join(str(t or '').split())
    for _ in range(3):
        if len(s) >= 2 and s.startswith('{') and s.endswith('}'):
            s = s[1:-1].strip()
        else:
            break
    return s or None


def parse_bib(text):
    """**保守**解析：只取我们需要的三个字段，`@string`/`@comment` 跳过。"""
    items, n_bad = [], 0
    # 按 `@type{` 切开（`@` 前不能是转义；简单处理够用）
    for m in re.finditer(r'@(\w+)\s*\{', text):
        typ = m.group(1).lower()
        if typ in ('string', 'comment', 'preamble'):
            continue
        i = m.end() - 1
        # 找到配对的 `}`（数括号）
        depth, j = 0, i
        while j < len(text):
            if text[j] == '{':
                depth += 1
            elif text[j] == '}':
                depth -= 1
                if depth == 0:
                    break
            j += 1
        body = text[m.end():j]
        if not body.strip():
            n_bad += 1
            continue
        fields = {}
        for fm in RE_FIELD.finditer(body):
            fields[fm.group(1).lower()] = ' '.join(fm.group(2).split())
        if not fields:
            n_bad += 1
            continue
        # arXiv id：`eprint` 优先，其次 `journal`/`note`/`url` 里找
        aid = None
        for k in ('eprint', 'arxiv', 'arxivid'):
            if fields.get(k):
                mm = RE_ARXIV.search(fields[k]) or RE_OLD_ARXIV.search(fields[k])
                if mm:
                    aid = mm.group(0)
                    break
        if not aid:
            for k in ('journal', 'note', 'url', 'howpublished'):
                if fields.get(k):
                    mm = RE_ARXIV.search(fields[k])
                    if mm:
                        aid = mm.group(0)
                        break
        doi = fields.get('doi')
        if not doi:
            for k in ('url', 'note'):
                if fields.get(k):
                    mm = RE_DOI.search(fields[k])
                    if mm:
                        doi = mm.group(0)
                        break
        else:
            mm = RE_DOI.search(doi)
            doi = mm.group(0) if mm else doi
        items.append({
            'key': (re.match(r'\s*([^,]+),', body) or [None, None])[1],
            'type': typ,
            'title': unwrap_title(fields.get('title')),
            'author': fields.get('author'),
            'year': fields.get('year'),
            'doi': doi,
            'arxiv_id': aid,
            'n_fields': len(fields),
        })
    return items, n_bad


def openalex_by_doi(doi):
    """单条 DOI 查 OpenAlex（复用 M1 那条已验证的路径）。"""
    url = (f'https://api.openalex.org/works?filter=doi:{urllib.parse.quote(doi, safe=":/.")}'
           f'&per-page=1&mailto=kg-cosmos@example.org')
    req = urllib.request.Request(url, headers={
        'User-Agent': 'kg-cosmos/1.0', 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.load(r)
        res = d.get('results') or []
        if not res:
            return None, 'OpenAlex 查不到这个 DOI'
        w = res[0]
        return {
            'openalex_id': (w.get('id') or '').rsplit('/', 1)[-1],
            'title': w.get('title'), 'year': w.get('publication_year'),
            'type': w.get('type'), 'cited_by_count': w.get('cited_by_count'),
            'n_authors': len(w.get('authorships') or []),
        }, None
    except urllib.error.HTTPError as e:
        return None, f'HTTP {e.code}'
    except Exception as e:                              # noqa: BLE001
        return None, f'{type(e).__name__}: {e}'


def main():
    ap = argparse.ArgumentParser(description='M3 BibTeX / DOI 导入（**只对齐，不假装导入**）')
    ap.add_argument('--file', default='', help='.bib 文件')
    ap.add_argument('--doi', default='', help='单条 DOI（查 OpenAlex）')
    ap.add_argument('--out', default=str(OUT))
    a = ap.parse_args()
    t0 = time.time()
    print('=' * 92)
    print('M3 · BibTeX / DOI 导入（**对齐，不假装导入**）')
    print('=' * 92)

    if not REG.exists():
        print(f'  ❌ 缺 {REG}')
        return 1
    reg = json.loads(REG.read_text(encoding='utf-8'))
    local_ids = {p['arxiv_id'] for p in (reg.get('papers') or [])}
    local_doi = {str(p.get('doi') or '').replace('https://doi.org/', '').lower()
                 for p in (reg.get('papers') or []) if p.get('doi')}

    # ⚠️ **版本号必须忽略** —— 实测：`@inproceedings{..., eprint = {2401.04507}}`
    #    而库里是 `2401.04507v1` ⇒ 第一版判成了 `metadata_only`（**错的**）。
    #    BibTeX 里写 `eprint` 时**通常不带版本号**，而本地 id 带 ⇒ 必须归一化。
    def nover(x):
        return re.sub(r'v\d+$', '', str(x or ''))

    local_nover = {nover(x): x for x in local_ids}

    def classify(it):
        aid = it.get('arxiv_id')
        doi = (it.get('doi') or '').lower()
        if aid and (aid in local_ids or nover(aid) in local_nover):
            it['matched_local'] = local_nover.get(nover(aid), aid)
            return 'in_corpus'
        # arXiv DOI 形态：`10.48550/arxiv.2402.12001` ⇒ 等价于本地 id
        if doi.startswith('10.48550/arxiv.'):
            guess = nover(doi.split('arxiv.', 1)[1])
            if guess in local_nover:
                it['matched_local'] = local_nover[guess]
                return 'in_corpus'
        if doi and doi in local_doi:
            return 'in_corpus'
        if aid:
            return 'candidate'
        return 'metadata_only'

    if a.doi:
        print(f'  查 DOI：{a.doi}')
        d, err = openalex_by_doi(a.doi)
        if err:
            print(f'  ❌ {err}')
            return 1
        print(f'  ✅ {json.dumps(d, ensure_ascii=False)}')
        # 与本地比
        hit = any(str(x).lower() == a.doi.lower() for x in local_doi)
        print(f'  本地已有这个 DOI：{hit}')
        return 0

    if not a.file:
        ap.print_help()
        return 0
    p = Path(a.file)
    if not p.exists():
        print(f'  ❌ 找不到 {p}')
        return 1
    text = p.read_text(encoding='utf-8', errors='replace')
    items, n_bad = parse_bib(text)
    print(f'  解析 {p.name}：**{len(items)} 条**（跳过/解析失败 {n_bad} 条）')
    print()

    for it in items:
        it['status'] = classify(it)
    dist = {}
    for it in items:
        dist[it['status']] = dist.get(it['status'], 0) + 1
    print(f'  ★ 对齐结果：{dist}')
    print()
    for st, note in (('in_corpus', '库里已有 ⇒ 不用管'),
                     ('candidate', '**有 arXiv id 但库里没有** ⇒ 可以下载并抽取'),
                     ('metadata_only', '**只有 DOI/标题** ⇒ 只能知道有这篇')):
        rows = [x for x in items if x['status'] == st]
        if not rows:
            continue
        print(f'  ── {st}（{len(rows)} 条）—— {note}')
        for x in rows[:8]:
            t = (x.get('title') or '(无标题)')[:56]
            print(f'      {(x.get("arxiv_id") or x.get("doi") or "—")[:26]:<28}{t}')
        if len(rows) > 8:
            print(f'      …另有 {len(rows) - 8} 条')
        print()

    print('  ★★ **本项目只能做一半"导入"**（这一点必须说清）：')
    print('      商业产品导入 .bib 后能给结论，因为它们**没有本地图谱**；')
    print('      而本项目的结论**依赖本地抽取的图** ⇒')
    print('      **导入了条目但没下载 PDF，图谱里什么都不会变。**')
    print('      ⇒ 所以本项做的是"对齐"，并如实区分上表三种状态。')

    payload = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'purpose': 'M3：BibTeX / DOI 导入 —— **对齐，不假装导入**',
        'source_file': str(p.name), 'n_local': len(local_ids),
        'parsed': len(items), 'unparsed': n_bad, 'distribution': dist,
        'design': {
            'only_half_an_import':
                '本项目**只能做一半"导入"**：结论依赖本地抽取的图 ⇒ '
                '导入了条目但没下载 PDF，图谱不会变。**所以本项做"对齐"而不是"导入完成"。**',
            'three_states':
                '`in_corpus`（库里已有）· `candidate`（有 arXiv id 但没有 ⇒ 可下载）· '
                '`metadata_only`（只有 DOI/标题 ⇒ 只能知道有这篇）。'
                '**第三种的价值是"它告诉你你的综述漏了哪些"。**',
            'why_not_bibtexparser':
                '真实的 .bib 常带注释、`@string`、自定义字段，'
                '而 `bibtexparser` 对"半个合法的 .bib"很脆。'
                '这里只要 `doi`/`eprint`/`title` 三个字段 ⇒ **60 行正则更可控**，'
                '而且**解析不出来的条目如实报出来**（"我没解析出来"比"我猜它是这句"更诚实）。',
            'regex_parser_is_conservative':
                '**保守**：数括号找条目边界，字段用 `key = {value}` 形式匹配；'
                '`@string`/`@comment`/`@preamble` 跳过。',
        },
        'items': items,
        'elapsed_s': round(time.time() - t0, 1),
    }
    outp = Path(a.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n  已写出 {outp.relative_to(ROOT) if str(outp).startswith(str(ROOT)) else outp}'
          f'  {time.time() - t0:.1f}s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
