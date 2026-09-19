# -*- coding: utf-8 -*-
r"""M1 的守门：OpenAlex 缓存 / 注册表 / 前端产物三者必须**自洽**。

## 为什么需要它

M1 的实现里踩了**三次坑**，每一次都**不报错**，只是"结果看起来像 OpenAlex 没收录"：

1. 单条路径 `/works/doi:...` + `quote(safe='')` 把 `:` 编成 `%3A` ⇒ **28 篇全 404**；
2. 换成 `?filter=doi:` 后 ⇒ **仍全空**；
3. **真因**：本地 id 是 `2402.12001**v1**`，而 **arXiv DOI 不带版本号** ⇒
   `filter=doi:10.48550/arxiv.2402.12001v1` **`meta.count = 0`**，**HTTP 200**。

**⇒ 三次都是"查不到"，而没有一次是"报错"。**
⇒ 所以必须有一条守门**盯住"查不到"这件事本身**：
**对齐率不能悄悄掉到 0，也不能悄悄涨到不真实的数。**

## 断言五组

1. **缓存与前端产物的对齐数一致**（两个文件不许各说各话）；
2. **对齐率在合理区间**（**>0 且 < 100%**）——
   · `= 0` ⇒ 就是上面那三个坑之一（**URL 拼错**，不是"OpenAlex 没收录"）；
   · `= 100%` ⇒ 那 4 篇老论文（arXiv DOI 后补）本来查不到，**100% 反而可疑**；
3. **每个对齐上的 work 必须带 `openalex_id`**（形状对）；
4. **注册表里的作者只来自可核对的来源** ——
   `author_source` 只能是 `openalex` 或 `None`，
   **不许出现 `probe`/`pdf`**（那是 `probe_authors.py` 的候选，**只有 43% 可信，不写进注册表**）；
   ⚠️ 2026-09-19 扩写：语料里多了 4 篇**中文期刊 OA 论文**（《计算机科学》），
   它们的 id **不是 arXiv id** ⇒ OpenAlex 那条链（按 arXiv DOI 查）**根本查不到它们**。
   原来的判据会把**出版信息**（PDF 首页「引用本文」+ 期刊页）误判成"猜的"。
   ⇒ 判据按两类语料分开写（**arXiv 那一类一个字都没放宽**）：
   · arXiv 条目：仍然只能是 `openalex` 或 `None`；
   · 非 arXiv 条目：允许 `manifest…`，但**必须带 DOI**（作者可被独立核对）。
5. **注册表的作者与 OpenAlex 缓存逐条对得上**（不是"差不多"）
   —— 只对 arXiv 条目成立（非 arXiv 条目不在 OpenAlex 里，见上）。

用法：
    python tools/check_openalex.py
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / 'data' / 'quality' / 'openalex-cache.json'
FE = ROOT / 'web' / 'kg-data' / 'openalex.json'
REG = ROOT / 'web' / 'kg-data' / 'paper-registry.json'
# arXiv id 形态；**不是**这个形态的（中文期刊语料）不走 OpenAlex
RE_ARXIV = re.compile(r'^\d{4}\.\d{4,5}v\d+$')


def main():
    print('=' * 92)
    print('M1 · OpenAlex 元数据双轨（缓存 / 前端 / 注册表 三者自洽）')
    print('=' * 92)
    for p in (CACHE, FE, REG):
        if not p.exists():
            print(f'  ❌ 缺 {p.relative_to(ROOT)}')
            print('     （先跑 tools/fetch_openalex.py 与 tools/build_paper_registry.py）')
            return 1
    cache = json.loads(CACHE.read_text(encoding='utf-8'))
    fe = json.loads(FE.read_text(encoding='utf-8'))
    reg = json.loads(REG.read_text(encoding='utf-8'))
    fails = []

    cc, cf = cache.get('counts') or {}, fe.get('counts') or {}
    cw = (cache.get('works') or {})
    okw = {k: v for k, v in cw.items() if not v.get('_error')}

    # ── ① 缓存与前端一致 ─────────────────────────────────────────
    print(f'  ① 缓存 counts={cc}')
    print(f'     前端 counts={cf}')
    same = all(cc.get(k) == cf.get(k) for k in
               ('manifest', 'aligned', 'failed', 'published', 'with_orcid'))
    print(f'     两边一致：{"✅" if same else "❌"}')
    if not same:
        fails.append(f'缓存与前端 counts 不一致：{cc} vs {cf}')
    print()

    # ── ② 对齐率在合理区间 ───────────────────────────────────────
    n = cc.get('manifest') or 0
    a = cc.get('aligned') or 0
    rate = a / max(1, n)
    print(f'  ② 对齐 {a}/{n} = {rate:.1%}')
    # 分层判断：**0 与 100% 都要报警**（理由见 docstring）
    if a == 0:
        print('     ❌ **对齐 0 篇** —— 那几乎一定是 URL 拼错了。')
        print('        三个已知坑：① 单条路径 + quote 把 `:` 编成 %3A；')
        print('        ② 用了 `/works/{id}` 而不是 `?filter=doi:`；')
        print('        ③ **arXiv DOI 不带版本号**（`2402.12001v1` → `2402.12001`）。')
        print('        ⚠️ 这三种**都不报错** —— HTTP 200、`meta.count=0`。')
        fails.append('对齐 0 篇 —— 先查 URL 拼法（见上面的三个坑）')
    elif rate >= 1.0:
        print('     ⚠️ **对齐 100%** —— 本库有 4 篇老论文的 arXiv DOI 后补，')
        print('        OpenAlex 本应收不到 ⇒ **100% 反而可疑**（是不是把失败的也算了？）。')
        fails.append('对齐率 100%，但已知应有 4 篇查不到')
    else:
        print(f'     ✅ 在合理区间（已知有 {cc.get("failed")} 篇查不到）')
    print()

    # ── ③ 每个对齐上的 work 形状对 ───────────────────────────────
    bad3 = [k for k, v in okw.items() if not v.get('openalex_id')]
    print(f'  ③ 对齐上的 work {len(okw)} 个 ⇒ 缺 openalex_id 的：{len(bad3)}')
    if bad3:
        print(f'     ⚠️ {bad3[:5]}')
    ok3 = not bad3
    print(f'     {"✅" if ok3 else "❌"}')
    if not ok3:
        fails.append(f'{len(bad3)} 个 work 缺 openalex_id：{bad3[:3]}')
    print()

    # ── ④★ 注册表的作者只能来自可核对的来源 ────────────────────────
    # ⚠️ 2026-09-19：语料里多了 4 篇**中文期刊 OA 论文**（id 不是 arXiv id）。
    #   它们**不在 OpenAlex 的覆盖里**（那条链按 arXiv DOI 查）⇒ 元数据只能来自
    #   PDF 首页「引用本文」+ 期刊页（`author_source` 以 `manifest` 开头）。
    #   **这不是"猜"**（不是 `probe_authors.py` 的候选），但也不是机构权威源
    #   ⇒ 给它一条**更严的形状要求**：必须同时带 DOI（否则作者无从核对）。
    #   **arXiv 那一类照旧，一个字都没放宽。**
    papers = reg.get('papers') or []
    arxiv_rows = [p for p in papers if RE_ARXIV.match(str(p.get('arxiv_id')))]
    local_rows = [p for p in papers if not RE_ARXIV.match(str(p.get('arxiv_id')))]
    srcs = {}
    for p in papers:
        s = p.get('author_source')
        srcs[s] = srcs.get(s, 0) + 1
    print(f'  ④ 注册表里 author_source 的分布：{srcs}')
    bad4 = []
    for p in arxiv_rows:
        s = p.get('author_source')
        if s not in (None, 'openalex'):
            bad4.append((p.get('arxiv_id'), s))
    for p in local_rows:
        s = p.get('author_source')
        if s is None:
            continue
        if 'manifest' not in str(s):
            bad4.append((p.get('arxiv_id'), s))
        elif not p.get('doi'):
            bad4.append((p.get('arxiv_id'), f'{s} 但缺 DOI（作者无从核对）'))
    print(f'     arXiv {len(arxiv_rows)} 篇 ⇒ 来源只许 openalex/None；'
          f'非 arXiv {len(local_rows)} 篇 ⇒ 来源须含 manifest 且带 DOI')
    print(f'     **不许出现 probe/pdf**（那是"猜的"，只有 43% 可信）：'
          f'{"✅ 没有" if not bad4 else "❌ " + str(bad4[:3])}')
    if bad4:
        fails.append(f'注册表里混进了不可核对来源的作者：{bad4[:3]}')
    print()

    # ── ⑤ 注册表的作者与 OpenAlex 缓存逐条对得上（**只对 arXiv 条目**）──
    bad5 = []
    for p in arxiv_rows:
        aid = p.get('arxiv_id')
        o = okw.get(aid)
        if not o:
            # 对不上的论文 ⇒ **注册表里不该有作者**（不许从别处补）
            if p.get('author'):
                bad5.append((aid, 'OpenAlex 没对齐，但注册表里有作者'))
            continue
        want = '; '.join(x['name'] for x in (o.get('authors') or []) if x.get('name'))
        if (p.get('author') or '') != want:
            bad5.append((aid, f'作者不符：注册表 {str(p.get("author"))[:40]!r} '
                              f'vs OpenAlex {want[:40]!r}'))
        if p.get('openalex_id') != o.get('openalex_id'):
            bad5.append((aid, 'openalex_id 不符'))
    print(f'  ⑤ 逐条核对 {len(reg.get("papers") or [])} 篇 ⇒ 不符 {len(bad5)}')
    for x in bad5[:4]:
        print(f'     ⚠️ {x}')
    ok5 = not bad5
    print(f'     **作者与 OpenAlex 逐条一致**：{"✅" if ok5 else "❌"}')
    if not ok5:
        fails.append(f'{len(bad5)} 篇的作者/ID 与 OpenAlex 不符：{bad5[:3]}')
    print()

    # 顺带报一个有用的数（不进失败判定）。
    # ⚠️ 分母用**已入库的 arXiv 条目** —— 中文图那一篇不在 OpenAlex 里，
    #    混进分母只会把对齐率往下拽，读起来像"变差了"。
    ing = [p for p in arxiv_rows if p.get('in_graph')]
    ing_al = [p for p in ing if p.get('openalex_id')]
    print(f'  · 参考：已入库的 arXiv {len(ing)} 篇里对齐 {len(ing_al)} 篇 '
          f'= {len(ing_al) / max(1, len(ing)):.1%}')
    print(f'  · 参考：全部 {len(reg.get("papers") or [])} 篇里有作者 '
          f'{sum(1 for p in (reg.get("papers") or []) if p.get("author"))} 篇')
    print()

    print('=' * 92)
    if fails:
        print(f'❌ {len(fails)} 项未通过：')
        for f in fails:
            print(f'   · {f}')
        return 1
    print('✅ 五组断言全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
