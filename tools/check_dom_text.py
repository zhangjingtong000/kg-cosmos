# -*- coding: utf-8 -*-
r"""守门：**浏览器不认识 markdown** —— 会进 DOM 的文案里不许出现 `**粗体**`。

## 为什么值得单独守一条

这个项目已经栽过两次同一个坑：

1. 第一次：18 处 `**粗体**` 写进了 HTML 可见文本 / `innerHTML`，
   用户看到的是**字面星号**（`**这张图不存在。**`）。
2. 第二次（2026-09-18）：修完第一轮之后**还剩 9 处** —— 而且我当时的检查脚本
   **报"无泄漏"**，因为它只在页面**初始加载**时扫 DOM，而泄漏大多在
   `#result` 这类**提交后才显示**的区块里。

⇒ 所以这条守门的判据有三条纪律：

| 纪律 | 原因 |
|---|---|
| **跳过 `<script>` / `<style>` / `<template>`** | 它们的代码本身就是 DOM 文本节点，扫进去会把注释和字符串报成泄漏（第一版就这么误报过） |
| **不看可见性（不跳过 `display:none`）** | 隐藏区块**迟早会显示**，漏检等于没检（这正是第二版漏掉 9 处的原因） |
| **注释不算** | `<!-- -->` 是 Comment 节点（nodeType 8），本来就不会被当文本节点扫到；JS 注释在 `<script>` 里也已跳过 |

**唯一保留 `**` 的地方是"导出 Markdown"的拼装** —— 那是在**生成 .md 文件**，
`**` 本来就是语法。它在 `<script>` 里，所以天然被跳过 ✓。

用法：
    E:\Python313\python.exe tools\check_dom_text.py
    E:\Python313\python.exe tools\check_dom_text.py --pages ask,index
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'
BASE = os.environ.get('KG_STATIC', 'http://127.0.0.1:8000')

JS = r"""
() => {
  const out = [];
  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE']);
  const walk = (n) => {
    if (n.nodeType === 1) {
      if (SKIP.has(n.tagName)) return;
      // ⚠️ **这里刻意不判可见性** —— 见 docstring：
      //    隐藏区块迟早要显示，跳过它等于漏检。
    }
    if (n.nodeType === 3 && n.nodeValue && n.nodeValue.includes('**')) {
      const t = n.nodeValue.replace(/\s+/g, ' ').trim();
      const p = n.parentElement;
      out.push({
        text: t.slice(0, 110),
        where: p ? (p.tagName.toLowerCase()
                    + (p.id ? '#' + p.id : '')
                    + (p.className ? '.' + String(p.className).split(/\s+/)[0] : '')) : '?',
      });
    }
    for (const c of n.childNodes) walk(c);
  };
  walk(document.body);
  return out;
}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pages', default='', help='逗号分隔（不含 .html），默认全站')
    a = ap.parse_args()

    if a.pages:
        pages = [x.strip() + '.html' for x in a.pages.split(',')]
    else:
        pages = sorted(p.name for p in WEB.glob('*.html'))

    print('=' * 88)
    print('DOM 文案守门：**会进浏览器的文字里不许有 markdown 粗体**')
    print('=' * 88)
    print(f'  页面 {len(pages)} 个 · 取自 {BASE}')
    print()

    from playwright.sync_api import sync_playwright

    bad_pages, total, skipped, checked = [], 0, [], 0
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        ctx = b.new_context(viewport={'width': 1440, 'height': 1080})
        pg = ctx.new_page()
        for name in pages:
            try:
                pg.goto(f'{BASE}/{name}', wait_until='load', timeout=30000)
            except Exception as e:
                # ⚠️⚠️ **打不开的页面必须计入失败，不能"跳过"**。
                #    第一版这里只打印一行"⚠️ 跳过——不算通过"，**然后就 continue 了**，
                #    最后的判定只看 `total`（泄漏数）⇒ 当服务没起、**37 个页面
                #    全部打不开**时，脚本打印的是「✅ 全站 DOM 文本里没有 markdown
                #    粗体」并 exit 0。**这是最坏的失败模式：什么都没查，却报通过。**
                skipped.append(f'{name}（{type(e).__name__}）')
                print(f'  ❌ {name:<26} 打不开：{type(e).__name__}')
                continue
            try:
                pg.wait_for_function('window.__ready === true', timeout=6000)
            except Exception:
                pass
            pg.wait_for_timeout(500)
            leaks = pg.evaluate(JS)
            checked += 1
            if leaks:
                total += len(leaks)
                bad_pages.append(name)
                print(f'  ❌ {name:<26} {len(leaks)} 处')
                for x in leaks[:6]:
                    print(f'        <{x["where"]}> {x["text"]!r}')
                if len(leaks) > 6:
                    print(f'        …还有 {len(leaks) - 6} 处')
            else:
                print(f'  ✅ {name}')
        ctx.close()
        b.close()

    print()
    print('=' * 88)
    print(f'  实际检查了 {checked} / {len(pages)} 个页面'
          + (f'，打不开 {len(skipped)} 个' if skipped else ''))
    # **零覆盖也是失败** —— 没查到不等于没问题（见上面那段注释）
    if checked == 0:
        print('❌ **一个页面都没检查到** ⇒ 判定失败（服务没起？BASE 写错了？）')
        print(f'   BASE = {BASE}')
        return 1
    if total or skipped:
        if total:
            print(f'❌ {len(bad_pages)} 个页面共 {total} 处 markdown 泄漏：'
                  f'{", ".join(bad_pages)}')
            print('   修法：把 `**xxx**` 改成 `xxx`（或用 `<b>xxx</b>`，'
                  '但 `textContent` 那条路只能用纯文本）')
        if skipped:
            print(f'❌ {len(skipped)} 个页面打不开（**未覆盖**）：'
                  f'{", ".join(skipped[:6])}' + (' …' if len(skipped) > 6 else ''))
        return 1
    print('✅ 全站 DOM 文本里没有 markdown 粗体')
    return 0


if __name__ == '__main__':
    sys.exit(main())
