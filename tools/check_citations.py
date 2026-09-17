# -*- coding: utf-8 -*-
r"""M3 + M5 的守门：**导出 → 导入 往返必须闭合**，且"不编"三条必须成立。

## 为什么"往返"是这一项最该测的东西

`export_citations.py`（M5）与 `import_bibtex.py`（M3）是**一对**：
一个写 BibTeX，一个读 BibTeX。**它们最容易出的错是"自己写的不认识自己写的"** ——
而这一点只有**真跑一遍往返**才看得出来。

**实测已经抓到两个往返 bug**：
1. **标题带保护括号**：导出写 `{{A Survey on ...}}`（BibTeX 保护大小写的标准做法），
   导入读回来变成 `{{A Survey on ...}}` —— **看起来像原文就这么写的**；
2. **最后一个字段丢**：`eprint` 若是条目最后一个字段，第一版的正则前瞻
   `(?=\n\s*\w+\s*=|\n\s*\})` 匹配不上（body 末尾没有 `}`，它在切片时被切掉了）
   ⇒ 那一条被误判成"只有 DOI/标题"。

**⇒ 所以第①组就是往返闭环。**

## 断言六组

1. **★ 往返闭环**：导出 → 导入 ⇒ **全部条目落回 `in_corpus`**（一篇都不许掉）；
2. **标题不带保护括号**（往返后与注册表里的标题逐字相等）；
3. **`--only-complete` 的语义对**：只导有 作者+年份+DOI 的；
4. **★ "不编"**：注册表里没有作者的条目，导出的 BibTeX **不许出现 `author` 字段**
   （**写 `Unknown` 会让人以为原文就这么署的**）；
5. **★ 三种对齐状态都能出现**（用一个含三类条目的合成 `.bib` 验）——
   `in_corpus` / `candidate` / `metadata_only`；
6. **`.`bib` 的条目类型按真实 `work_type`**：OpenAlex 说 `preprint` ⇒ `@misc`，
   **不许升级成 `@article`**。

用法：
    python tools/check_citations.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REG = ROOT / 'web' / 'kg-data' / 'paper-registry.json'
PY = sys.executable


def run(*args):
    p = subprocess.run([PY] + [str(x) for x in args], capture_output=True,
                       text=True, encoding='utf-8', errors='replace',
                       timeout=300, cwd=str(ROOT))
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def main():
    print('=' * 92)
    print('M3+M5 · 引用导出/导入（**往返必须闭合** · 不编）')
    print('=' * 92)
    if not REG.exists():
        print(f'  ❌ 缺 {REG}')
        return 1
    reg = json.loads(REG.read_text(encoding='utf-8'))
    ps = reg.get('papers') or []
    fails = []
    tmp = Path(tempfile.mkdtemp(prefix='kgcite-'))
    bib = tmp / 'roundtrip.bib'

    # ── ① ★ 往返闭环 ─────────────────────────────────────────────
    rc1, o1 = run(ROOT / 'tools' / 'export_citations.py', '--format', 'bib', '--out', bib)
    print(f'  ① 导出 ⇒ rc={rc1} · {bib.stat().st_size if bib.exists() else 0} B')
    if rc1 != 0 or not bib.exists():
        print(f'     ❌ 导出失败：{o1[-300:]}')
        return 1
    rc2, o2 = run(ROOT / 'tools' / 'import_bibtex.py', '--file', bib,
                  '--out', tmp / 'report.json')
    rep = json.loads((tmp / 'report.json').read_text(encoding='utf-8')) \
        if (tmp / 'report.json').exists() else {}
    dist = rep.get('distribution') or {}
    print(f'  ② 导入 ⇒ rc={rc2} · 对齐 {dist}')
    ok1 = (rc2 == 0 and rep.get('parsed') == len(ps)
           and dist.get('in_corpus') == len(ps))
    print(f'     **{len(ps)} 篇全部落回 in_corpus**：{"✅" if ok1 else "❌"}')
    if not ok1:
        fails.append(f'往返没闭合：parsed={rep.get("parsed")} vs {len(ps)}，dist={dist}')
    print()

    # ── ② 标题不带保护括号 ───────────────────────────────────────
    items = rep.get('items') or []
    by_aid = {x['arxiv_id']: x for x in items if x.get('arxiv_id')}
    bad2 = []
    for p in ps:
        it = by_aid.get(p['arxiv_id'])
        if not it:
            continue
        want = ' '.join(str(p.get('title') or '').split())
        got = ' '.join(str(it.get('title') or '').split())
        if want and got != want:
            bad2.append((p['arxiv_id'], want[:40], got[:40]))
    print(f'  ② 标题与注册表逐字相等 ⇒ 不符 {len(bad2)}')
    for x in bad2[:3]:
        print(f'     ⚠️ {x[0]}：注册表 {x[1]!r} vs 往返后 {x[2]!r}')
    ok2 = not bad2
    print(f'     **没带 `{{{{...}}}}` 保护括号**：{"✅" if ok2 else "❌"}')
    if not ok2:
        fails.append(f'{len(bad2)} 篇标题往返后不一致（大概率是保护括号没去掉）')
    print()

    # ── ③ --only-complete 的语义 ─────────────────────────────────
    rc3, _ = run(ROOT / 'tools' / 'export_citations.py', '--format', 'bib',
                 '--only-complete', '--out', tmp / 'complete.bib')
    text3 = (tmp / 'complete.bib').read_text(encoding='utf-8') if (tmp / 'complete.bib').exists() else ''
    n3 = len(re.findall(r'^@', text3, re.M))
    want3 = sum(1 for p in ps if p.get('author') and p.get('year') and p.get('doi'))
    print(f'  ③ --only-complete ⇒ {n3} 条（应为 {want3}）')
    ok3 = rc3 == 0 and n3 == want3
    print(f'     {"✅" if ok3 else "❌"}')
    if not ok3:
        fails.append(f'--only-complete 导出 {n3} 条，应为 {want3}')
    print()

    # ── ④★ "不编"：无作者 ⇒ 不许有 author 字段 ────────────────────
    #   做法：找注册表里无作者的那几篇，在**完整导出**里看它们的条目
    noau = [p for p in ps if not p.get('author')]
    text_all = bib.read_text(encoding='utf-8')
    # 按 eprint 切开条目
    blocks = re.split(r'(?=^@)', text_all, flags=re.M)
    bad4 = []
    for p in noau:
        blk = [b for b in blocks if f'eprint = {{{p["arxiv_id"]}}}' in b]
        for b in blk:
            if re.search(r'^\s*author\s*=', b, re.M):
                bad4.append(p['arxiv_id'])
    print(f'  ④ 注册表里无作者的 {len(noau)} 篇 ⇒ 导出里出现 `author` 的：{len(bad4)}')
    if noau:
        print(f'     （无作者的：{[p["arxiv_id"] for p in noau][:5]}）')
    print(f'     **没编"Unknown"**：{"✅" if not bad4 else "❌"}')
    if bad4:
        fails.append(f'{len(bad4)} 篇无作者的条目却写了 author：{bad4[:3]}')
    print()

    # ── ⑤★ 三种对齐状态都能出现 ──────────────────────────────────
    synth = tmp / 'three.bib'
    synth.write_text(
        '@article{new2025,\n  title = {A Paper Not In Our Corpus},\n'
        '  author = {Someone New}, year = {2025},\n'
        '  eprint = {2509.99999}, archivePrefix = {arXiv},\n}\n\n'
        '@article{doionly,\n  title = {A Journal Paper With Only A DOI},\n'
        '  author = {Another}, year = {2023},\n'
        '  doi = {10.1145/3571234.3579999}, journal = {Some Journal},\n}\n\n'
        # ⚠️ `eprint` 是**最后一个字段** —— 正是第一版漏掉的那个形态
        '@inproceedings{known,\n  title = {Known Paper},\n  author = {X},\n'
        '  year = {2024},\n  eprint = {2401.04507},\n}\n', encoding='utf-8')
    rc5, o5 = run(ROOT / 'tools' / 'import_bibtex.py', '--file', synth,
                  '--out', tmp / 'r5.json')
    d5 = (json.loads((tmp / 'r5.json').read_text(encoding='utf-8'))
          .get('distribution') or {}) if (tmp / 'r5.json').exists() else {}
    print(f'  ⑤ 三种状态 ⇒ {d5}')
    ok5 = (d5.get('in_corpus') == 1 and d5.get('candidate') == 1
           and d5.get('metadata_only') == 1)
    print(f'     **都出现了，且 `eprint` 在末尾也能认**：{"✅" if ok5 else "❌"}')
    if not ok5:
        fails.append(f'三种状态不对（末尾 eprint 可能又漏了）：{d5}')
    print()

    # ── ⑥ 条目类型按真实 work_type ───────────────────────────────
    types = {}
    for p in ps:
        wt = (p.get('work_type') or '').lower()
        bk = [b for b in blocks if f'eprint = {{{p["arxiv_id"]}}}' in b]
        if not bk:
            continue
        got = re.match(r'@(\w+)', bk[0].strip())
        got = got.group(1).lower() if got else '?'
        want = 'article' if wt in ('article', 'review') else 'misc'
        if got != want:
            types[p['arxiv_id']] = (wt, got, want)
    print(f'  ⑥ 条目类型按 work_type ⇒ 不符 {len(types)}')
    for k, v in list(types.items())[:3]:
        print(f'     ⚠️ {k}: work_type={v[0]!r} 导出成 @{v[1]}，应为 @{v[2]}')
    ok6 = not types
    print(f'     **没有"看起来像期刊就升级成 @article"**：{"✅" if ok6 else "❌"}')
    if not ok6:
        fails.append(f'{len(types)} 篇条目类型与 work_type 不符')
    print()

    print('=' * 92)
    if fails:
        print(f'❌ {len(fails)} 项未通过：')
        for f in fails:
            print(f'   · {f}')
        return 1
    print('✅ 六组断言全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
