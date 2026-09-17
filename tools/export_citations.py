# -*- coding: utf-8 -*-
r"""M5 · **Word / LaTeX 引用导出**（.bib / .ris / CSL-JSON / Markdown）。

## TODO 的原话

> | **M5** | ⬜ **Word / LaTeX 引用导出** | A（遗漏） | K9 只写了"答案导出成 Markdown" |

**⇒ 缺口是真的**：K9 导出的是**答案**（带引文与页码），
而这里要导出的是**参考文献条目** —— 让人能把 KG Cosmos 筛出来的那批论文
**直接倒进自己的论文里**。

## ★ 它靠 M1 才成立

`42a3067` 之前，注册表里**没有作者**（`_manifest.json` 没有这个字段）；
`e68c257`（M1）从 OpenAlex 拿到了 **24 篇的权威作者（带 ORCID）+ 年份 + DOI**。
⇒ **M5 是 M1 的直接兑现**：没有作者与年份，BibTeX 条目是残的。

## ★ 四种格式，各为一种真实用途

| 格式 | 给谁用 |
|---|---|
| **`.bib`（BibTeX）** | LaTeX / Overleaf —— **学术写作最直接** |
| **`.ris`** | Zotero / EndNote / Mendeley —— **点两下就进文献库** |
| **`CSL-JSON`** | **Word 的引用插件（Zotero Word plugin）与 Pandoc** —— **"Word"那半就是它** |
| **`.md`** | 直接贴进笔记 / 报告 |

## ★★ 三条"不编"

1. **缺字段就缺**：没有作者 ⇒ 不写 `author` 字段（**不写"Unknown"**——
   那会让人以为原文就这么署的）；没有 DOI ⇒ 不写 `doi`。
2. **`@misc` 与 `@article` 的分界按真实 `work_type`**：
   OpenAlex 说 `preprint` ⇒ **`@misc`**；说 `article` ⇒ **`@article`**。
   **不因为"它看起来像期刊论文"就升级成 `@article`。**
3. **`note` 里如实写出"元数据来自 OpenAlex，未逐条人工核对"** ——
   导出的 BibTeX 会被贴进别人的论文，**它得知道自己有多可信**。

用法：
    python tools/export_citations.py                     # 全部 28 篇 → output/
    python tools/export_citations.py --only-complete     # 只导有作者+年份+DOI 的
    python tools/export_citations.py --format bib --out D:\refs\kg.bib
    python tools/export_citations.py --ids 2402.12001v1,2401.04507v1
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REG = ROOT / 'web' / 'kg-data' / 'paper-registry.json'
OUTDIR = ROOT / 'output'

# BibTeX key 里不能有的字符
RE_BADKEY = re.compile(r'[^a-z0-9]')
RE_BADTEX = re.compile(r'[{}]')


def bibkey(p, used):
    """生成一个**可读且唯一**的 BibTeX key（如 `wang2024survey`）。"""
    au = (p.get('authors') or [{}])[0].get('name') or ''
    fam = RE_BADKEY.sub('', (au.split()[-1] if au else 'anon').lower()) or 'anon'
    yr = str(p.get('year') or '')
    title = (p.get('title') or '')
    word = ''
    for w in RE_BADKEY.sub(' ', title.lower()).split():
        if len(w) > 3 and w not in ('with', 'from', 'using', 'based', 'towards'):
            word = w
            break
    k = f'{fam}{yr}{word}'
    if k in used:
        i = 2
        while f'{k}{chr(96 + i)}' in used:
            i += 1
        k = f'{k}{chr(96 + i)}'
    used.add(k)
    return k


def tex_escape(s):
    """BibTeX 里 `&` `%` `_` `#` 要转义（**`{}` 不转义：它们本来就可能成对出现**）。"""
    s = RE_BADTEX.sub('', str(s or ''))
    for a, b in (('\\', r'\textbackslash{}'), ('&', r'\&'), ('%', r'\%'),
                 ('_', r'\_'), ('#', r'\#'), ('$', r'\$')):
        if a == '\\':
            continue
        s = s.replace(a, b)
    return ' '.join(s.split())


def bib_entry(p, key):
    """按真实的 `work_type` 选条目类型 —— **不升级**。"""
    typ = (p.get('work_type') or '').lower()
    # OpenAlex 的 type：preprint / article / review / book-chapter …
    kind = 'article' if typ in ('article', 'review') else 'misc'
    fields = []
    if p.get('authors'):
        # BibTeX 的 `and` 分隔
        names = ' and '.join(x['name'] for x in p['authors'] if x.get('name'))
        fields.append(('author', tex_escape(names)))
    if p.get('title'):
        fields.append(('title', '{{' + tex_escape(p['title']) + '}}'))
    if p.get('year'):
        fields.append(('year', str(p['year'])))
    if kind == 'article' and p.get('published_venue'):
        fields.append(('journal', tex_escape(p['published_venue'])))
    elif kind == 'misc':
        fields.append(('howpublished', 'arXiv preprint'))
    if p.get('doi'):
        fields.append(('doi', str(p['doi']).replace('https://doi.org/', '')))
    if p.get('arxiv_id'):
        fields.append(('eprint', p['arxiv_id']))
        fields.append(('archivePrefix', 'arXiv'))
    if p.get('cited_by_count') is not None:
        fields.append(('note', f'被引 {p["cited_by_count"]}（OpenAlex，未逐条人工核对）'))
    body = ',\n'.join(f'  {k} = {{{v}}}' for k, v in fields)
    return f'@{kind}{{{key},\n{body}\n}}\n'


def ris_entry(p):
    """RIS（Zotero / EndNote 都吃）。"""
    typ = (p.get('work_type') or '').lower()
    lines = [f'TY  - {"JOUR" if typ in ("article", "review") else "GEN"}']
    for x in (p.get('authors') or []):
        if x.get('name'):
            lines.append(f'AU  - {x["name"]}')
    if p.get('title'):
        lines.append(f'TI  - {p["title"]}')
    if p.get('year'):
        lines.append(f'PY  - {p["year"]}')
    if p.get('published_venue'):
        lines.append(f'JO  - {p["published_venue"]}')
    if p.get('doi'):
        lines.append(f'DO  - {str(p["doi"]).replace("https://doi.org/", "")}')
    lines.append(f'UR  - https://arxiv.org/abs/{p.get("arxiv_id")}')
    lines.append(f'AN  - {p.get("openalex_id") or ""}')
    lines.append('ER  - ')
    return '\n'.join(lines) + '\n'


def csl_entry(p, key):
    """CSL-JSON（**Zotero Word 插件与 Pandoc 用这个**）。"""
    typ = (p.get('work_type') or '').lower()
    e = {
        'id': key,
        'type': 'article-journal' if typ in ('article', 'review') else 'article',
        'title': p.get('title'),
        'issued': ({'date-parts': [[int(p['year'])]]} if p.get('year') else None),
        'author': [{'family': (x['name'].split()[-1] if x.get('name') else ''),
                    'given': ' '.join((x['name'].split()[:-1] if x.get('name') else []))}
                   for x in (p.get('authors') or []) if x.get('name')],
        'DOI': (str(p['doi']).replace('https://doi.org/', '') if p.get('doi') else None),
        'URL': f'https://arxiv.org/abs/{p.get("arxiv_id")}',
        # **如实**：元数据来自 OpenAlex，未逐条人工核对
        'note': f'元数据来自 OpenAlex（未逐条人工核对）；被引 {p.get("cited_by_count")}',
    }
    if p.get('published_venue'):
        e['container-title'] = p['published_venue']
    return {k: v for k, v in e.items() if v not in (None, [], '')}


def md_entry(p, key):
    au = '; '.join(x['name'] for x in (p.get('authors') or []) if x.get('name')) or '（无作者记录）'
    ven = f" *{p['published_venue']}*" if p.get('published_venue') else ''
    doi = f" · doi:{str(p['doi']).replace('https://doi.org/', '')}" if p.get('doi') else ''
    return (f'- **{p.get("title") or "(无标题)"}**\n'
            f'  {au} · {p.get("year") or "—"}{ven}\n'
            f'  arXiv:{p.get("arxiv_id")}{doi} · 被引 {p.get("cited_by_count")}'
            f' · `{key}`\n')


def main():
    ap = argparse.ArgumentParser(description='M5 引用导出（bib / ris / csl / md）')
    ap.add_argument('--format', default='all',
                    choices=['all', 'bib', 'ris', 'csl', 'md'])
    ap.add_argument('--out', default='', help='单文件输出路径（--format 不是 all 时）')
    ap.add_argument('--only-complete', action='store_true',
                    help='只导有 **作者 + 年份 + DOI** 的（可直接进参考文献）')
    ap.add_argument('--ids', default='', help='只要这几篇（逗号分隔）')
    a = ap.parse_args()
    t0 = time.time()
    print('=' * 92)
    print('M5 · 引用导出（BibTeX / RIS / CSL-JSON / Markdown）')
    print('=' * 92)
    if not REG.exists():
        print(f'  ❌ 缺 {REG}（先跑 tools/build_paper_registry.py）')
        return 1
    d = json.loads(REG.read_text(encoding='utf-8'))
    ps = d.get('papers') or []
    if a.ids:
        want = {x.strip() for x in a.ids.split(',') if x.strip()}
        ps = [p for p in ps if p['arxiv_id'] in want]

    def complete(p):
        return bool(p.get('author') and p.get('year') and p.get('doi'))

    n_all = len(ps)
    if a.only_complete:
        ps = [p for p in ps if complete(p)]
    print(f'  注册表 {n_all} 篇 ⇒ 导出 {len(ps)} 篇'
          f'{"（--only-complete）" if a.only_complete else ""}')
    inc = [p['arxiv_id'] for p in ps if not complete(p)]
    if inc:
        print(f'  ⚠️ 其中 **{len(inc)} 篇字段不全**（缺作者/年份/DOI）：{inc[:6]}')
        print('     ⇒ **照样导出，但缺的字段就不写**（不编"Unknown"、不编年份）')

    used = set()
    bib, ris, csl, md = [], [], [], []
    md.append(f'# KG Cosmos 导出的参考文献（{len(ps)} 篇）\n')
    md.append('> ⚠️ 元数据来自 **OpenAlex**（机构维护、带 ORCID），**未经逐条人工核对**。\n'
              '> 缺字段的条目**如实留空**，不填占位符。\n')
    for p in sorted(ps, key=lambda x: str(x['arxiv_id'])):
        k = bibkey(p, used)
        bib.append(bib_entry(p, k))
        ris.append(ris_entry(p))
        csl.append(csl_entry(p, k))
        md.append(md_entry(p, k))
    n_note = sum(1 for p in ps if p.get('is_published'))
    n_noau = sum(1 for p in ps if not p.get('author'))
    print(f'  ★ {len(ps)} 篇里：已正式发表 {n_note} · **无作者 {n_noau}** · '
          f'preprint/其他 '
          f'{sum(1 for p in ps if (p.get("work_type") or "") != "article")}')

    OUTDIR.mkdir(parents=True, exist_ok=True)
    made = []
    if a.format in ('all', 'bib'):
        p = Path(a.out) if (a.out and a.format == 'bib') else OUTDIR / 'kg-cosmos.bib'
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('\n'.join(bib), encoding='utf-8')
        made.append(p)
    if a.format in ('all', 'ris'):
        p = Path(a.out) if (a.out and a.format == 'ris') else OUTDIR / 'kg-cosmos.ris'
        p.write_text('\n'.join(ris), encoding='utf-8')
        made.append(p)
    if a.format in ('all', 'csl'):
        p = Path(a.out) if (a.out and a.format == 'csl') else OUTDIR / 'kg-cosmos.csl.json'
        p.write_text(json.dumps(csl, ensure_ascii=False, indent=1), encoding='utf-8')
        made.append(p)
    if a.format in ('all', 'md'):
        p = Path(a.out) if (a.out and a.format == 'md') else OUTDIR / 'kg-cosmos-refs.md'
        p.write_text(''.join(md), encoding='utf-8')
        made.append(p)
    for p in made:
        print(f'  ✅ {p}  （{p.stat().st_size / 1024:.1f} KB）')
    print()
    print('  ★ 四种格式各自的用途：')
    print('      .bib      → LaTeX / Overleaf（学术写作最直接）')
    print('      .ris      → Zotero / EndNote / Mendeley（点两下进文献库）')
    print('      .csl.json → **Word 引用插件与 Pandoc**（"Word"那半就是它）')
    print('      .md       → 直接贴进笔记')
    print(f'\n  {time.time() - t0:.1f}s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
