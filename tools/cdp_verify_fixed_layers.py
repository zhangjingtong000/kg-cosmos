# -*- coding: utf-8 -*-
r"""守门：**声明了 `position: fixed` 的元素，实际算出来必须真的是 `fixed`**。

## 为什么需要这条（血案）

2026-09-18，用户在浏览器里打开社区星云主视图，看到的是"整个页面乱掉了"：
标题三行压在一起、PDF 预览面板盖在星云正中、图例跑到屏幕外、导出条拉成满宽。

根因是 `web/` 下 5 个页面里的一句兜底 CSS：

```css
body > *:not(#atmo):not(#veil){position:relative;z-index:1}
```

`:not()` 的特异性取参数里最高的那个 ⇒ 这条选择器是 **(0,2,0,1)**，
而星云页每个浮层都是**单 ID**（`#pdfPane` = (0,1,0,0)）——
**它压倒了全部 17 个 `position: fixed`**，把它们算成 `relative`。

⇒ **源码里白纸黑字写着 `fixed`，浏览器算出来却是 `relative`。**
⇒ 所以**源码文本检查（grep）永远抓不到这个 bug**，必须读**计算值**。

详见 `docs/UI_FIXED_POSITION_ROOT_CAUSE.md`。

## 判据（两条，都只看客观事实）

**① 计算值一致性**（全站）
   遍历每个页面的 CSSOM，收集**所有声明了 `position: fixed` 的选择器**，
   用它们选中元素，逐个读 `getComputedStyle(el).position`。
   **只要有一个不是 `fixed`，就是有人用更高的特异性把它改掉了 ⇒ 判失败。**

   ⚠️ 这条判据是"我想要的语义"而不是"浏览器语法" —— 理论上存在
   **故意覆盖**（如 `@media` 里把某元素从 fixed 改回 static）。
   真出现时应当**改判据并写清理由**，而不是关掉这条检查。

**② 星云页文档高度**（单页专项）
   `kg-nebula.html` 是"全屏 3D 场景 + 浮层"，**文档高度必须等于视口高度**。
   事故当时它是 **1645px**（比视口多 565px）—— 多出来的正是被塞进文档流的浮层。
   修复后是 **1080px = 视口高**。这条**不会误报**，是最硬的指纹。

用法：
    E:\Python313\python.exe tools\cdp_verify_fixed_layers.py
    E:\Python313\python.exe tools\cdp_verify_fixed_layers.py --json
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'
# 静态服务的根是**项目根**（`/web/...`），与 `tools/cdp_verify_a11y.py` 保持一致
BASE = os.environ.get('KG_STATIC', 'http://127.0.0.1:8098/web')

# 页高专项：文档高度允许比视口高出的余量（滚动条 / 取整）
HEIGHT_TOL = 40

JS = r"""
() => {
  // ① 收集所有声明了 position:fixed 的选择器（含 @media 里的）
  const sels = [];
  const walk = (list) => {
    for (const r of list) {
      // ⚠️ 判"是不是容器规则"**不能**用 `if (r.cssRules)` ——
      //    CSS Nesting 之后 CSSStyleRule 自己也带 cssRules（空列表是真值），
      //    那样会把所有普通规则都当成容器跳过，一条都查不到。
      //    **有 selectorText 的才是普通规则。**
      if (r.selectorText) {
        if (r.style && r.style.position === 'fixed') sels.push(r.selectorText);
        continue;
      }
      if (r.cssRules) walk(r.cssRules);
    }
  };
  for (const sh of document.styleSheets) {
    try { walk(sh.cssRules); } catch (e) {}
  }

  // ② 逐个选择器找元素、读计算值
  const bad = [], checked = [];
  for (const sel of sels) {
    let els = [];
    try { els = Array.from(document.querySelectorAll(sel)); } catch (e) { continue; }
    for (const el of els) {
      const got = getComputedStyle(el).position;
      const rec = {sel: sel,
                   el: el.id ? ('#' + el.id)
                             : (el.className ? (el.tagName.toLowerCase()
                                                + '.' + String(el.className).split(/\s+/)[0])
                                             : el.tagName.toLowerCase()),
                   computed: got};
      checked.push(rec);
      if (got !== 'fixed') bad.push(rec);
    }
  }
  return {
    url: location.pathname,
    n_sels: sels.length,
    checked: checked,
    bad: bad,
    win: {w: innerWidth, h: innerHeight},
    docH: document.documentElement.scrollHeight,
    docW: document.documentElement.scrollWidth,
  };
}
"""


def pages_with_fixed():
    """**先扫源码**：只把"声明过 `position: fixed`"的页面拿去跑浏览器。

    这样将来任何页面新增 fixed 都会被自动纳入，不需要维护一份名单
    （本项目栽过"新页面没加进名单 ⇒ 检查根本不覆盖它"的坑）。
    """
    out = []
    for p in sorted(WEB.glob('*.html')):
        txt = p.read_text(encoding='utf-8', errors='replace')
        # 去注释，避免把"注释里引用的旧写法"当成真声明
        live = re.sub(r'/\*.*?\*/', '', txt, flags=re.S)
        live = re.sub(r'<!--.*?-->', '', live, flags=re.S)
        if re.search(r'position\s*:\s*fixed', live):
            out.append(p.name)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true', help='输出 JSON（给 verify_all 汇总用）')
    a = ap.parse_args()

    pages = pages_with_fixed()
    if not pages:
        print('⚠️ 没有任何页面声明 position:fixed —— 这条检查等于没覆盖任何东西')
        return 0 if a.json else 1

    print('=' * 88)
    print('浮层守门：**声明 fixed 的元素，计算值必须也是 fixed**')
    print('=' * 88)
    print(f'  源码里含 `position:fixed` 的页面：{", ".join(pages)}')
    print(f'  取自：{BASE}　（事故背景见 docs/UI_FIXED_POSITION_ROOT_CAUSE.md）')
    print()

    from playwright.sync_api import sync_playwright

    fails, results = [], []
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)   # 读几何值不依赖 GPU，无头即可
        for name in pages:
            ctx = b.new_context(viewport={'width': 1440, 'height': 1080})
            pg = ctx.new_page()
            try:
                pg.goto(f'{BASE}/{name}', wait_until='load', timeout=30000)
            except Exception as e:
                fails.append(f'{name}: 打不开（{type(e).__name__}）')
                print(f'  ✗ {name:<20} 打不开：{type(e).__name__}')
                ctx.close()
                continue
            try:
                pg.wait_for_function('window.__ready === true', timeout=8000)
            except Exception:
                pass
            pg.wait_for_timeout(1000)
            d = pg.evaluate(JS)
            results.append({'page': name, **d})
            n_bad = len(d['bad'])
            mark = '✅' if n_bad == 0 else f'❌ {n_bad} 个被改掉'
            print(f'  {name:<20} 声明 fixed 的选择器 {d["n_sels"]:>2} 个 · '
                  f'实际命中元素 {len(d["checked"]):>2} 个 · {mark}')
            for r in d['bad']:
                print(f'      ❌ {r["sel"]} → {r["el"]}  computed={r["computed"]}'
                      f'（源码声明 fixed，**被更高特异性的规则改掉了**）')

            # ② 星云页专项：全屏浮层页，文档高度不该溢出视口
            if name == 'kg-nebula.html':
                over = d['docH'] - d['win']['h']
                if over > HEIGHT_TOL:
                    msg = (f'kg-nebula 文档高 {d["docH"]}px > 视口 {d["win"]["h"]}px'
                           f'（多 {over}px）—— 这是"浮层被打回文档流"的指纹')
                    fails.append(msg)
                    print(f'      ❌ {msg}')
                else:
                    print(f'      ✅ 文档高 {d["docH"]}px ≈ 视口 {d["win"]["h"]}px'
                          f'（全屏浮层页应有的样子）')
            ctx.close()
        b.close()

    n_bad = sum(len(r['bad']) for r in results)
    print()
    print('=' * 88)
    if a.json:
        print(json.dumps({'pages': results, 'n_bad': n_bad, 'fails': fails},
                         ensure_ascii=False))
    if n_bad or fails:
        print(f'❌ 未通过：{n_bad} 个 fixed 元素被改掉、{len(fails)} 项其他失败')
        return 1
    print('✅ 全部 fixed 元素的计算值都是 fixed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
