#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""页面相对引用存在性守门：**页面里写了的本地文件，必须真的在**。

## 为什么这条非有不可（不是"顺手加的"）

2026-09-20 引入 GSAP 动效时踩过一次，而且是**最难发现的那一类**：

    给 `web/index.html` 加首屏动效时忘了引 `web/lib/gsap/SplitText.min.js`。
    页面**完全正常**：排版对、链接能点、控制台甚至没有明显的红色报错
    —— 因为编排脚本里有一句 `if (!window.SplitText) { bail(); return; }`，
    它把整段动效**安静地关掉了**。
    结果是"看起来一切正常，但一个动画都没有"。

这类缺陷的共同点：**引用错了路径 ≠ 页面报错**，只是某个能力悄悄消失。
写路径的人（我）不报错，读路径的人（人）也看不出。
所以它必须由机器守：**页面上写了的相对路径，逐个到磁盘上对**。

## 判据（两组）

① **相对引用存在性**
   扫 `web/*.html` 与 `web/benchmark/*.html` 里的 `src=` / `href=`，
   跳过锚点（`#…`）、协议（`http:` `https:` `data:` `mailto:` `javascript:` `//`）
   与查询串（`?…` 参与解析但不参与存在性判断）。
   · 目标落在 `web/` **里面** → 缺了就是 **失败**（这些文件是站点的一部分，必须随站发布）。
   · 目标落在 `web/` **外面**（`../…`）→ 缺了只**报告**，不计入退出码。
     理由：那些引用多数指向 `data/`（约 9.5 GB，`.gitignore` 里只放行
     `data/quality/*.json` 等少数文件）—— 它们在本机存在，
     但在**没有 data 的克隆里必然缺失**，那不是"路径写错了"，
     而是"这份仓库本来就不带数据"。把它算成失败会让守门意义失真。

② **GSAP 只许从本地引**
   任何页面里出现"含 `gsap` 的外部 URL"（`unpkg` / `jsdelivr` / `cdnjs` / `esm.sh` …）
   → 失败。这是「离线演示铁律」与"不跑云端"两条约束在动效上的落点：
   `web/lib/gsap/` 下 12 个插件就是唯一来源。
   ⚠️ 这条**不用"凡外部 URL 就报"**的写法 —— 项目里确有几个页面有意依赖 `esm.sh`
   （`community-atlas` 等，见 `web/lib/net-guard.js` 的说明与
   `tools/cdp_verify_net_guard.py` 的守门），一刀切会把它们误判成违规。

用法：
    python tools/check_local_assets.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'

# 只扫这两处：顶层页面与 benchmark；`web/_archive/` 是历史快照，
# 里面的路径按当时的样子保留，不参与守门（与 check_js_syntax.py 一致）。
PAGE_DIRS = [WEB, WEB / 'benchmark']

ATTR_RE = re.compile(r'(?:\bsrc|\bhref)\s*=\s*["\']([^"\']+)["\']', re.I)
SKIP_PREFIX = ('#', 'http:', 'https:', 'data:', 'mailto:', 'javascript:', 'tel:', '//')
# 含 gsap 的外部 URL：动效一律走本地
GSAP_CDN_RE = re.compile(
    r'https?://[^\s"\'<>]*gsap[^\s"\'<>]*', re.I)
# `<script>` 的**标签本身**要留着（`<script src=…>` 正是要查的东西），
# 但**标签体**必须挖空：体里是 JS，JS 里到处是 `'src="' + x` 这种字符串拼接
# （实测 `ask.html` / `kg-nebula.html` 各有一条，会被当成"缺失的本地文件"报出来）。
SCRIPT_BODY_RE = re.compile(r'(<script\b[^>]*>)(.*?)(</script\s*>)', re.S | re.I)
IMPORTMAP_RE = re.compile(
    r'<script\b[^>]*type\s*=\s*["\']importmap["\'][^>]*>(.*?)</script\s*>', re.S | re.I)


def strip_script_bodies(text: str) -> str:
    return SCRIPT_BODY_RE.sub(lambda m: m.group(1) + m.group(3), text)


def importmap_paths(text: str) -> list[str]:
    """取出 importmap 里所有以 `./` 或 `../` 开头的映射值。

    为什么值得单独查：`web/kg-nebula.html` 的 three.js 就是靠 importmap
    指到 `./lib/three/...` 才做到离线的。importmap 指错文件**不会**让页面报错，
    只会让 `import` 在运行时失败 —— 与"忘引 SplitText"是同一类静默故障。
    """
    out: list[str] = []
    for m in IMPORTMAP_RE.finditer(text):
        try:
            data = json.loads(m.group(1))
        except Exception:
            continue
        imports = data.get('imports') if isinstance(data, dict) else None
        if not isinstance(imports, dict):
            continue
        for v in imports.values():
            if isinstance(v, str) and (v.startswith('./') or v.startswith('../')):
                out.append(v)
    return out


def pages() -> list[Path]:
    out: list[Path] = []
    for d in PAGE_DIRS:
        if d.exists():
            out.extend(sorted(d.glob('*.html')))
    return out


def main() -> int:
    print('=' * 88)
    print('本地引用守门：**页面写了的相对路径必须真的在** + **GSAP 只许本地引**')
    print('=' * 88)

    ps = pages()
    missing_inside: list[tuple[str, str, str]] = []   # (页面, 原样引用, 解析出的相对路径)
    missing_outside: list[tuple[str, str, str]] = []
    n_refs = 0

    for p in ps:
        raw = p.read_text(encoding='utf-8', errors='replace')
        for m in ATTR_RE.finditer(strip_script_bodies(raw)):
            u = m.group(1).strip()
            if not u or u.startswith(SKIP_PREFIX):
                continue
            u_clean = u.split('?')[0].split('#')[0]
            if not u_clean:
                continue
            n_refs += 1
            target = (p.parent / u_clean).resolve()
            if target.exists():
                continue
            shown = os.path.relpath(target, ROOT).replace('\\', '/')
            row = (p.name, u, shown)
            if u.startswith('..'):
                missing_outside.append(row)
            else:
                missing_inside.append(row)

        # importmap 里的本地映射
        for u in importmap_paths(raw):
            n_refs += 1
            target = (p.parent / u).resolve()
            if target.exists():
                continue
            shown = os.path.relpath(target, ROOT).replace('\\', '/')
            row = (p.name, f'importmap: {u}', shown)
            if u.startswith('..'):
                missing_outside.append(row)
            else:
                missing_inside.append(row)

    print(f'  扫了 {len(ps)} 个页面 · {n_refs} 条相对引用')
    print()
    if missing_inside:
        print(f'  ① 相对引用存在性：❌ 缺 {len(missing_inside)} 个**站内**文件')
        for name, u, shown in missing_inside:
            print(f'      {name}: `{u}` → 找不到 `{shown}`')
    else:
        print('  ① 相对引用存在性（站内）：✅ 全部存在')

    if missing_outside:
        print(f'  ① 相对引用存在性（站外 ../）：报告 {len(missing_outside)} 个缺失'
              '（**不计入退出码** —— 多数指向不带进仓库的 data/）')
        for name, u, shown in missing_outside[:10]:
            print(f'      {name}: `{u}` → 找不到 `{shown}`')
        if len(missing_outside) > 10:
            print(f'      …另有 {len(missing_outside) - 10} 个')
    else:
        print('  ① 相对引用存在性（站外 ../）：✅ 无缺失')
    print()

    gsap_cdn: list[tuple[str, str]] = []
    for p in ps:
        raw = p.read_text(encoding='utf-8', errors='replace')
        for m in GSAP_CDN_RE.finditer(raw):
            gsap_cdn.append((p.name, m.group(0)[:110]))
    n_local = len(list((WEB / 'lib' / 'gsap').glob('*.js'))) if (WEB / 'lib' / 'gsap').exists() else 0
    if gsap_cdn:
        print(f'  ② GSAP 只许本地引：❌ {len(gsap_cdn)} 处从外部引 GSAP')
        for name, u in gsap_cdn:
            print(f'      {name}: `{u}`')
        print('      修法：改引 `./lib/gsap/<插件>.min.js`（本地已备 12 个插件）')
    else:
        print(f'  ② GSAP 只许本地引：✅ 无任何页面从外部引 GSAP'
              f'（本地 `web/lib/gsap/` 有 {n_local} 个插件）')
    print()

    print('=' * 88)
    bad = len(missing_inside) + len(gsap_cdn)
    if bad:
        print(f'❌ {bad} 项未通过')
        return 1
    print('✅ 两组全过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
