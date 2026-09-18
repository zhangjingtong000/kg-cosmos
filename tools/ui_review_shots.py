# -*- coding: utf-8 -*-
r"""前端视觉审查：**批量截图**（审美一致性 / 重叠移位 / 设计感）。

## 为什么先截图，而不是先读 CSS

CSS 能告诉我"用了什么色值"，**但不能告诉我"看起来怎么样"**：
重叠、错位、文字压住图表、留白失衡 —— **这些只有图能看见。**

⇒ 本脚本把关键页面按**同一视口**截图，输出到 `output/ui-review/`，
**由人（或模型）逐张看**。

## 截什么、为什么

1. **全页截图**（`full_page=True`）—— 看**整体节奏**（留白、段落密度、卡片滥用）；
2. **首屏截图**（1080 高）—— 看**第一眼的设计感**（那决定"好不好看"）；
3. **★ 两档宽度**（1440 / 900）—— **看有没有重叠与移位**；
   很多"重叠"只在某个宽度区间出现，一个宽度看不出来。

## ⚠️⚠️ **必须用有头模式（headless=False）** —— 这是本脚本最重要的一条

**我在这里犯过一次，而且差点据此改坏一个页面**：

```
无头截图：`index.html` 的背景是**浅灰过曝**，白字几乎不可读
          ⇒ 我准备把它当"对比度事故"来修。
有头截图：**深色底 + 流动的青蓝光场，"KG COSMOS" 白字对比度极好** —— 设计得很好。
```

**根因**：`index.html` 的背景是 **WebGL 实时光场**（`voyage.js` + `#voyageCanvas`）。
- **有头**：`renderer = ANGLE (Intel UHD Graphics · D3D11)` ⇒ 真实 GPU ✓
- **无头**：`renderer = n/a` ⇒ **SwiftShader 软渲染** ⇒ **那个 shader 过曝** ✗

**⇒ 而这与 `docs/M7_RENDER_PERF.md` 里那条是同一类**：
**无头测量测出来的不是"页面怎么样"，而是"软渲染怎么样"。**

⇒ 所以本脚本**默认有头**（`--headless` 才能切回去，**且会打印警告**）。
**⚠️ 有头会真的弹出浏览器窗口** —— 那是必要的代价：**审查的是视觉，而视觉只能这样看。**

## ⚠️ 另一条：**不隐藏任何东西**

不注入额外 CSS、不隐藏元素、不裁剪 —— **截的就是用户看到的**。
（若为了"好看"而清理页面，那这个审查就没有意义了。）

用法：
    python tools/ui_review_shots.py                    # 默认页面集（**有头**）
    python tools/ui_review_shots.py --pages index,ask,kg-nebula
    python tools/ui_review_shots.py --widths 1440 900
"""
import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'output' / 'ui-review'
BASE = os.environ.get('KG_STATIC', 'http://127.0.0.1:8000')

# **审查的页面集**（按"用户会先看到哪些"排序，不是按字母）
DEFAULT = [
    'index',            # 导航页 —— 第一印象
    'ask',              # 主展示：多 Agent 问答
    'kg-nebula',        # 主视图：3D 星云
    'analytics',        # 评测面板
    'method-limits',    # 方法边界
    'papers',           # 论文管理（新）
    'paper-table',      # 论文对比表（新）
    'stance',           # 立场聚合（新）
    'prisma',           # PRISMA（新）
    'corpus',           # 语料清单
    'upload',           # 上传
    'pathqa',           # PathQA
    'temporal',         # 时间演化
    'diff',             # 多图对比
    'bipartite',        # 二部图
    'refs',             # 参考文献
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pages', default='', help='逗号分隔（不含 .html）')
    ap.add_argument('--widths', nargs='+', type=int, default=[1440, 900])
    ap.add_argument('--height', type=int, default=1080)
    # ⚠️ **`device_scale_factor` 必须可调** —— 实测第一版用 1.0 截 1440×1080，
    #    单张 PNG **1.7 MB**，**看图的工具直接拒绝**（"omitted to fit image limits"）。
    #    ⇒ 审查是为"看清"，不是为"高清存档" ⇒ 默认降到 **0.6**（约 864 宽）。
    #    **要看清细节时可以传 1.0，但要单张看。**
    ap.add_argument('--scale', type=float, default=0.6)
    ap.add_argument('--full', action='store_true',
                    help='同时截全页（**长表页会到 2 万像素**，默认不截）')
    # ⚠️ **默认有头** —— 见 docstring：无头的软渲染会把 WebGL 背景渲染错，
    #    而我差点据此把 `index.html` 判成"对比度事故"。
    ap.add_argument('--headless', action='store_true',
                    help='用无头（**会把 WebGL 背景渲染错，只在没有显示器时用**）')
    a = ap.parse_args()
    if a.headless:
        print('  ⚠️⚠️ **--headless：WebGL 背景会被软渲染画错**')
        print('      ⇒ 这一轮的图**不能**用来判断对比度/配色（`index.html` 会被画成浅灰过曝）')
    pages = [x.strip() for x in a.pages.split(',')] if a.pages else DEFAULT
    OUT.mkdir(parents=True, exist_ok=True)
    print('=' * 92)
    print('前端视觉审查 · 批量截图（**不隐藏任何东西**）')
    print('=' * 92)
    print(f'  页面 {len(pages)} 个 · 宽度 {a.widths} · 输出 {OUT.relative_to(ROOT)}')
    print()

    from playwright.sync_api import sync_playwright
    made, failed = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=a.headless)  # **默认有头**：无头会把 WebGL 背景画错
        for w in a.widths:
            ctx = browser.new_context(viewport={'width': w, 'height': a.height},
                                      device_scale_factor=a.scale)
            pg = ctx.new_page()
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)[:90]))
            for name in pages:
                url = f'{BASE}/{name}.html'
                try:
                    pg.goto(url, wait_until='load', timeout=30000)
                except Exception as e:
                    failed.append((name, w, f'{type(e).__name__}'))
                    print(f'  ✗ {name:<16}{w}  {type(e).__name__}')
                    continue
                # 等页面自己说就绪（**本项目的约定**），最多 8 秒
                try:
                    pg.wait_for_function('window.__ready === true', timeout=8000)
                except Exception:
                    pass
                pg.wait_for_timeout(1400)          # 让动画/字体稳定
                # ⚠️ **不注入任何 CSS、不隐藏元素**
                # ⚠️ **用 JPEG，不用 PNG** —— 实测教训：
                #   `index.html` 是**深色 + 星点 + 光场**的页面，PNG 压不动它：
                #   1440×1080 的 PNG **1.7 MB**，缩到 0.6 仍有 **584 KB**，
                #   **看图的工具直接拒绝**（"omitted to fit image limits"）。
                #   ⇒ 审查的目的是"看清版式"，不是"无损存档" ⇒ **JPEG q=72**。
                first = OUT / f'{w}-{name}-首屏.jpg'
                pg.screenshot(path=str(first), full_page=False, type='jpeg',
                              quality=72)
                full = None
                if a.full:
                    full = OUT / f'{w}-{name}-全页.jpg'
                    try:
                        pg.screenshot(path=str(full), full_page=True, type='jpeg',
                                      quality=72)
                    except Exception:
                        full = None
                made.append((name, w, first, full, errs[-1:] if errs else []))
                h = pg.evaluate('document.body.scrollHeight')
                print(f'  ✓ {name:<16}{w}  首屏+全页  页高 {h:>5}px'
                      + (f'  ⚠️ JS 错 {errs[-1][:40]}' if errs else ''))
            ctx.close()
        browser.close()

    print()
    print(f'  产出 {len(made)} 组截图 · 失败 {len(failed)}')
    if failed:
        for x in failed[:6]:
            print(f'      ✗ {x}')
    print(f'  ⇒ 看图：{OUT}')
    print('  ⚠️ **每一张都是用户会看到的样子**（没隐藏、没注入、没裁剪）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
