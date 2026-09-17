# -*- coding: utf-8 -*-
r"""K2 的"半天" · **从 PDF 首页解析作者**（候选 + 置信度，**不直接写入注册表**）。

## 为什么不能"解析出来就写进去"

N7 已经定下这条纪律（`docs/N7_WHAT_IS_A_PAPER.md §三` 洞 1）：

> **作者缺失** —— `_manifest.json` 里没有 `author` 字段 ⇒ 注册表里**如实留 `None`**，
> **不去猜**。要补得从 PDF 首页解析。
> **猜出来的作者比没有更糟。**

⇒ 所以本脚本的产物是 **`data/quality/author-candidates.json`**：
**每篇给出候选、置信度、以及"凭什么"** —— 人和页面都能复核。
**它不碰 `web/kg-data/paper-registry.json`。**

## ★ 实测：arXiv 论文首页的版面信号非常清楚

```
页面 612 x 792（Letter）
14.3  A Survey on Extractive Knowledge Graph Summarization:   ← **标题（最大字号）**
14.3  Applications, Approaches, Evaluation, and Future Directions
12.0  Xiaxia Wang            8.0 2                           ← **作者（次大 + 上标）**
12.0  ,
12.0  Gong Cheng
12.0  State Key Laboratory for Novel Software Technology, Nanjing University
12.0  xiaxia.wang@cs.ox.ac.uk, gcheng@nju.edu.cn              ← **邮箱（极强）**
12.0  Abstract                                                ← **边界**
```

⇒ 于是判据不是"猜一行像人名的文字"，而是**用版面**：
1. **边界**：`Abstract` / `摘要` 所在的行（作者一定在它**之前**）；
2. **字号**：**严格小于标题、且不小于正文**的那些行才是候选区；
3. **人名样式**：按 `,` / `and` / `;` 切开，每个名字 2–4 个词、首字母大写、
   允许尾随上标数字；
4. **邮箱**：同区出现 `@` ⇒ **这是"作者区"的铁证**（置信度直接拉满）。

## 置信度怎么给（**每个分数都对应一个可复核的依据**）

| 分 | 依据 |
|---|---|
| **0.95** | 该区出现邮箱（`@`），且切出的人名都合规 |
| **0.85** | 无邮箱，但字号严格小于标题，且至少 2 个合规人名 |
| **0.60** | 无邮箱，字号层次也判不出（只有 1 种字号），但切出合规人名 |
| **0.00** | 切不出任何合规人名 ⇒ **候选为空**（**如实说"没解析出来"**） |

**⇒ 低于 0.85 的一律只是"线索"**，页面上要标出来。

用法：
    python tools/probe_authors.py                 # 全部 28 篇
    python tools/probe_authors.py --id 2402.12001v1
    python tools/probe_authors.py --min-conf 0.85
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / 'data' / 'corpus'
OUT = ROOT / 'data' / 'quality' / 'author-candidates.json'

# 作者区的结束标志（作者一定在它之前）
STOP_WORDS = ('abstract', '摘要', 'introduction', '1 introduction', 'keywords')
# 明显的非人名（机构/邮箱/编号）
RE_EMAIL = re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+')
# ⚠️ 机构/地理词表 —— **它注定列不完**，所以它还配合下面那条"降级"判据用。
#    实测漏过一个：`Université Paris Cité, New York` 被判成 0.95 的人名（**最坏的一种错**：
#    高置信度的错）。`Cité` 这种非英语词、`New York` 这种地名，靠词表很难穷尽。
RE_ORG = re.compile(r'\b(university|université|universite|institute|department|'
                    r'laboratory|lab|college|school|academy|research|inc\.|ltd|'
                    r'corporation|center|centre|state key|beijing|china|usa|uk|'
                    r'germany|abstract|cited?|york|paris|london|tokyo|berlin)\b', re.I)
# ⚠️ 上标**不只是数字** —— 实测 `Li Caia, Xin Maoa, Yuhao Zhoua`
#    里的尾随 `a` 是 arXiv 常见的上标字母。第一版只认 `[\d*†‡§¶]` ⇒
#    `Li Caia` 被当成一个人名（多了一个字母），**人名就错了**。
RE_SUPER = re.compile(r'(?:[\d\*†‡§¶]+|[a-h](?=\s*$|[,\s]))\s*$')
RE_NAME = re.compile(r'^[A-Z][\w\'\-\.]*(?:\s+[A-Z][\w\'\-\.]*){0,4}\s*[\d\*†‡§¶]*$')
RE_SUPER = re.compile(r'[\d\*†‡§¶]+\s*$')


def is_name(s):
    """一个片段像不像人名。**保守**：宁可漏，不可错。"""
    t = RE_SUPER.sub('', s.strip()).strip(' ,;.')
    if not t or len(t) < 3 or len(t) > 48:
        return False
    if RE_EMAIL.search(t) or RE_ORG.search(t):
        return False
    if not RE_NAME.match(t):
        return False
    words = t.split()
    # **至少 2 个词**（"Wang" 这种单词太容易误判成机构缩写或栏目名）
    if len(words) < 2:
        return False
    # 每个词都得首字母大写（"van der Berg" 这类前缀允许小写）
    lows = {'van', 'von', 'de', 'del', 'della', 'der', 'di', 'da', 'la', 'le', 'bin'}
    for w in words:
        if w.lower() in lows:
            continue
        if not w[:1].isupper():
            return False
    return True


def split_names(line):
    """把一行作者切成若干个名字。"""
    # 先去掉邮箱（邮箱里的 `.` 和 `,` 会干扰切分）
    line = RE_EMAIL.sub(' ', line)
    parts = re.split(r'\s*(?:,|;|\band\b|&)\s*', line)
    return [p for p in (x.strip() for x in parts) if p]


def probe_one(pdf):
    """返回一篇的候选（**含依据**）。"""
    import fitz
    d = fitz.open(str(pdf))
    try:
        if d.page_count < 1:
            return {'candidates': [], 'confidence': 0.0,
                    'why': 'PDF 没有页面'}
        pg = d[0]
        dd = pg.get_text('dict')
        # ★ **必须按坐标排除页眉页脚** —— 这是第一版的 bug 根因：
        #   arXiv 的页眉/水印字号（**20.0**）比标题（**14.3**）还大，
        #   于是 `max_size` 取到了页眉 ⇒ "标题"被判成"次大字号"的正文，
        #   `zone` 里只剩下真正的标题那一行 ⇒ **切不出人名、整篇 0.00**。
        #   实测两篇本该解析出来的（1703.08098v9 / 2502.00681v2）都栽在这上面。
        H = pg.rect.height
        y_top, y_bot = H * 0.06, H * 0.94
        rows = []
        for b in dd.get('blocks', []):
            if b.get('type') != 0:
                continue
            y0, y1 = b.get('bbox', (0, 0, 0, 0))[1], b.get('bbox', (0, 0, 0, 0))[3]
            if y1 < y_top or y0 > y_bot:
                continue                      # **页眉 / 页脚** ⇒ 不参与字号判断
            for l in b.get('lines', []):
                txt, size = '', 0.0
                for s in l.get('spans', []):
                    if s.get('text'):
                        txt += s['text']
                        size = max(size, float(s.get('size') or 0))
                txt = txt.strip()
                if txt:
                    rows.append((round(size, 1), txt))
        if not rows:
            return {'candidates': [], 'confidence': 0.0, 'why': '首页取不到文本'}

        # ── 定"标题字号"：**不能取全页最大** ──
        #   第二版的 bug 就在这里：**arXiv 的侧边水印字号（20.0）比标题（14.3）大**，
        #   而它**不在页眉页脚的位置**（坐标过滤挡不住），于是 `max_size=20.0`
        #   ⇒ 标题被判成"次大字的正文"、`zone` 里只剩标题 ⇒ 整篇 0.00。
        #
        #   ⇒ 判据改成：**在"明显大于正文"的字号里，取出现次数最多的那个。**
        #     · 标题**跨行** ⇒ 同一个字号出现 2+ 次；
        #     · 水印/期刊名通常是**孤零零一行** ⇒ 只出现 1 次。
        #   （实测 1703.08098v9：20.0 × 1 行 · 14.3 × **2 行** ⇒ 正确定到 14.3）
        from collections import Counter
        cnt = Counter(r[0] for r in rows)
        body_med = sorted(r[0] for r in rows)[len(rows) // 2] if rows else 10.0
        big = [s for s in cnt if s >= body_med * 1.15]
        if not big:
            big = [max(cnt)] if cnt else [10.0]
        # 先按出现次数、再按字号大小（次数优先：标题跨行）
        max_size = sorted(big, key=lambda s: (cnt[s], s), reverse=True)[0]
        # ── 边界：第一个 Abstract/Introduction 之前 ──
        stop = len(rows)
        for i, (_s, t) in enumerate(rows):
            low = t.strip().lower()
            if any(low.startswith(w) for w in STOP_WORDS):
                stop = i
                break
        head = rows[:stop]
        # ── 标题的**续行**要排除 ──
        #   第一版的 6 个 0.60 候选全是假阳性，它们都是**标题的续行**：
        #   `Survey Article Tables` / `Future Directions` / `Representation Learning`
        #   / `Knowledge Graphs` / `Scalable E-Commerce` / `LLM-EMPOWERED …`。
        #   续行的字号**常常略小于标题首行**（期刊排版），所以"字号 < 标题"挡不住它。
        #   ⇒ 加一条：**该行的词大多出现在标题里 ⇒ 判为续行**。
        title_txt = ' '.join(t for s, t in head if s >= max_size - 0.05).lower()
        title_words = {w.strip('.,:;()[]') for w in title_txt.split() if len(w) > 2}

        def is_title_continuation(line):
            ws = [w.strip('.,:;()[]') for w in line.lower().split()]
            ws = [w for w in ws if len(w) > 2]
            if not ws:
                return True                     # 没有实词 ⇒ 不是人名
            hit = sum(1 for w in ws if w in title_words)
            return hit / len(ws) >= 0.6         # 六成以上的词在标题里 ⇒ 续行

        # ── 作者区 = 标题之后、边界之前，且字号 < 标题 ──
        body = [r for r in head if r[0] < max_size and not is_title_continuation(r[1])]
        # 候选区再收一层：字号落在 [正文尺寸, 标题尺寸) 的那些
        if body:
            body_sizes = sorted({r[0] for r in body}, reverse=True)
            cand_size = body_sizes[0]
            zone = [r for r in body if r[0] >= cand_size - 0.1]
        else:
            zone = []
        has_email = any(RE_EMAIL.search(t) for _s, t in head)

        names, evidence = [], []
        for _s, t in zone:
            for p in split_names(t):
                if is_name(p):
                    nm = RE_SUPER.sub('', p).strip(' ,;.')
                    if nm and nm not in names:
                        names.append(nm)
        if names:
            evidence.append(f'作者区字号 {cand_size} < 标题 {max_size}')
        if has_email:
            evidence.append('作者区出现邮箱（@）')
        if zone:
            evidence.append(f'候选区 {len(zone)} 行，切出 {len(names)} 个合规人名')

        if not names:
            conf, why = 0.0, '切不出合规人名 ⇒ **如实说没解析出来**'
        elif has_email and len(names) >= 2:
            conf, why = 0.95, '有邮箱 + ≥2 个合规人名'
        elif len(names) >= 2 and zone and cand_size < max_size:
            conf, why = 0.85, '字号严格小于标题 + ≥2 个合规人名'
        else:
            conf, why = 0.60, '只有 1 个字号层次或只切出 1 个名字 ⇒ **只能当线索**'
        return {'candidates': names, 'confidence': conf, 'why': why,
                'evidence': evidence, 'title_size': max_size,
                'author_zone_size': (cand_size if zone else None),
                'has_email': has_email, 'zone_lines': len(zone)}
    finally:
        d.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--id', default='')
    ap.add_argument('--min-conf', type=float, default=0.0)
    a = ap.parse_args()
    t0 = time.time()
    print('=' * 92)
    print('K2 · 从 PDF 首页解析作者（**候选 + 置信度，不直接写入注册表**）')
    print('=' * 92)

    pdfs = sorted(CORPUS.glob('*.pdf'))
    if a.id:
        pdfs = [p for p in pdfs if p.stem == a.id]
        if not pdfs:
            print(f'  ❌ 没有 {a.id}.pdf')
            return 1
    print(f'  解析 {len(pdfs)} 篇（PyMuPDF 版面：标题=最大字号，作者=次大）')
    print()

    out, dist = {}, {0.95: 0, 0.85: 0, 0.6: 0, 0.0: 0}
    for p in pdfs:
        try:
            r = probe_one(p)
        except Exception as e:
            r = {'candidates': [], 'confidence': 0.0,
                 'why': f'解析异常 {type(e).__name__}: {e}'}
        out[p.stem] = r
        dist[r['confidence']] = dist.get(r['confidence'], 0) + 1
        mark = {0.95: '★', 0.85: '✓', 0.6: '·', 0.0: '✗'}.get(r['confidence'], '?')
        if r['confidence'] >= a.min_conf:
            print(f"    {mark} {p.stem:<17}{r['confidence']:.2f}  "
                  f"{', '.join(r['candidates'][:3])[:58]:<60}{r['why'][:34]}")

    n = len(out)
    ok95 = sum(1 for r in out.values() if r['confidence'] >= 0.95)
    ok85 = sum(1 for r in out.values() if r['confidence'] >= 0.85)
    none_ = sum(1 for r in out.values() if not r['candidates'])
    print()
    print(f'  ★ 置信度分布：0.95 × {dist.get(0.95, 0)} · 0.85 × {dist.get(0.85, 0)}'
          f' · 0.60 × {dist.get(0.6, 0)} · **解析不出 × {dist.get(0.0, 0)}**')
    print(f'     ≥0.95（有邮箱）{ok95}/{n} · ≥0.85 {ok85}/{n} · 完全没有 {none_}/{n}')

    payload = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'purpose': 'K2 的"半天"：从 PDF 首页解析作者 —— **候选 + 置信度**',
        'design': {
            'not_written_into_registry':
                '**本产物不写进 `paper-registry.json`。** N7 定的纪律是'
                '「**猜出来的作者比没有更糟**」⇒ 所以这里只给'
                '**候选 + 置信度 + 依据**，让人和页面复核后再决定。',
            'not_guessing_from_text':
                '判据不是"猜一行像人名的文字"，而是**用版面**：'
                '标题 = 最大字号 · 作者 = 次大 · 边界 = `Abstract` · '
                '**同区出现 `@` 是"作者区"的铁证**。',
            'conservative_is_name':
                '`is_name` **保守**：至少 2 个词、首字母大写、排除机构词与邮箱。'
                '「宁可漏，不可错」—— 因为一个错的作者会污染引用。',
        },
        'scoring': {
            '0.95': '该区出现邮箱（@），且切出 ≥2 个合规人名',
            '0.85': '无邮箱，但字号严格小于标题，且 ≥2 个合规人名',
            '0.60': '无邮箱且字号层次判不出，但切出合规人名 ⇒ **只能当线索**',
            '0.00': '切不出任何合规人名 ⇒ **如实说"没解析出来"**',
        },
        'summary': {'papers': n, 'conf_95': ok95, 'conf_85_plus': ok85,
                    'none': none_},
        'candidates': out,
        'elapsed_s': round(time.time() - t0, 1),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                   encoding='utf-8')
    print(f'\n  已写出 {OUT.relative_to(ROOT)}  '
          f'({OUT.stat().st_size / 1024:.1f} KB · {time.time() - t0:.1f}s)')
    print('  ⚠️ **没有写入注册表** —— 那是刻意的（N7：猜出来的作者比没有更糟）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
