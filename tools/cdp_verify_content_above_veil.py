# -*- coding: utf-8 -*-
r"""守门：**内容必须浮在 `#veil` 遮罩之上**（否则整页发暗、看不清）。

## 它守的是一个"改一处、坏另一处"的真实风险

`web/` 下 5 个页面里有这句设计系统兜底：

```css
body > *:not(#atmo):not(#veil){position:relative;z-index:1}
```

⚠️ 它**同时承担两件互不相干的职责**：

1. **别压倒浮层** —— 但它的特异性 (0,2,0,1) **反而压倒**了页面里所有单 ID 的
   `position: fixed`，把星云页 17 个浮层打回文档流（2026-09-18 的事故，
   见 `docs/UI_FIXED_POSITION_ROOT_CAUSE.md`）；
2. **把内容抬到遮罩之上** —— `#veil` 是一层 `z-index:0` 的半透明暗色遮罩，
   内容如果不定位、`z-index` 是 `auto`，**就会被它压住**，整页发暗。

2026-09-18 为修职责 1，把它改成了 `:where()` 版（特异性恒为 0）。
**降特异性有可能把职责 2 一起降没** ⇒ **所以必须有一条断言把职责 2 钉住**。

## 判据（客观、可判定）

对每个**真的有 `#veil` 元素**的页面，取它的 `computed position` 与 `z-index`：

· **`#veil` 不是定位元素**（`position: static`）**或** `z-index` 是 `auto`
  ⇒ **它不构成层叠次序上的遮盖**（靠 DOM 顺序）⇒ **本页不适用**，跳过；
· 否则 ⇒ 要求每个可见的 `body > *`（除 `#atmo` / `#veil`）
  **`position` ≠ `static` 且 `z-index` ≥ 1**。

⚠️ **"不适用"必须显式打印出来，不能静默跳过** ——
这个项目的教训是「**没查 ≠ 通过**」（见 `tools/check_dom_text.py` 的第一版：
37 个页面全部打不开时它报的是"✅ 通过"）。

⚠️ **为什么当时没有"看截图"来验**：写这个脚本时的那条 model route
**没有声明 image 输入**（harness 的 `read-image.ts` 有执行前硬门禁），
所以我看不了图；而"内容有没有被遮罩压住"恰好是**能用数值判定**的。
⇒ 眼睛用不了的时候，判据必须还在。

⚠️ **但这不等于「断言比看图强」—— 这个说法当天就被打脸了一次**：
供应商换回来、我重新能读图之后，**第一次看图就把 `pathqa` 自己的
右侧信息栏误读成"盖住星云的浮层"**，差点去改一个没坏的东西
（用 `grep pdfPane` 一查就否掉了）。

⇒ 两者是**分工**，不是替代：

| | 强在 | 弱在 |
|---|---|---|
| **看图** | 发现"**不对劲**"（设计/布局上的意外） | **会误报**（把正常的东西看成异常） |
| **客观断言** | 核实"**为什么**"（computed 值可回归） | 只能查**你已经想到要查的**东西 |

**⇒ 正确流程：看图起疑 → 断言核实 → 再下结论。**

用法：
    python tools\cdp_verify_content_above_veil.py
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'
BASE = os.environ.get('KG_STATIC', 'http://127.0.0.1:8000')

JS = r"""
() => {
  const veil = document.getElementById('veil');
  if (!veil) return {hasVeil: false};
  const vcs = getComputedStyle(veil);
  const vz = vcs.zIndex;
  const applicable = (vcs.position !== 'static' && vz !== 'auto');
  const items = [];
  let skippedCanvas = 0;
  for (const el of document.body.children) {
    if (el.id === 'atmo' || el.id === 'veil') continue;
    const cs = getComputedStyle(el);
    if (cs.display === 'none') continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    // ⚠️ **跳过 canvas**：`ask` / `new-graph` / `upload` 的 `canvas#gl` 是
    // **WebGL 背景光场**，而 `#veil` 存在的目的**正是压暗它**（design.css:
    // "文字遮罩：让正文在光场上仍然可读"）。
    // ⇒ 把背景画布算成"被遮罩压住的内容"是**判据搞错了对象**（误报）。
    // ⚠️ 但**不静默跳过** —— 下面把跳过的个数报出来，
    //    以免将来某个 canvas 其实是**内容**（图表）却被漏检。
    if (el.tagName === 'CANVAS') { skippedCanvas++; continue; }
    items.push({
      tag: el.tagName.toLowerCase()
           + (el.id ? '#' + el.id : '')
           + (el.className && typeof el.className === 'string' && el.className.trim()
              ? '.' + el.className.trim().split(/\s+/)[0] : ''),
      position: cs.position, zIndex: cs.zIndex,
    });
  }
  return {hasVeil: true, veilPos: vcs.position, veilZ: vz,
          applicable, items, skippedCanvas};
}
"""


def pages_with_veil():
    """只把**源码里有 `<div id="veil">`** 的页面拿去跑浏览器。

    从源码筛而不是"全站 37 页都跑"：没有这个元素的页面天然不适用，
    跑它们只是浪费 A/B 抢不到的时间。
    **⚠️ 但"筛掉"与"不适用"都要打印出来**（见 docstring）。
    """
    out = []
    for p in sorted(WEB.glob('*.html')):
        t = p.read_text(encoding='utf-8', errors='replace')
        if re.search(r'id\s*=\s*["\']veil["\']', t):
            out.append(p.name)
    return out


def main():
    pages = pages_with_veil()
    print('=' * 88)
    print('遮罩守门：**内容必须浮在 #veil 之上**')
    print('=' * 88)
    print(f'  源码里含 `id="veil"` 的页面 {len(pages)} 个：{", ".join(pages)}')
    print()

    from playwright.sync_api import sync_playwright

    bad, skipped, checked = [], [], 0
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        ctx = b.new_context(viewport={'width': 1440, 'height': 1080})
        pg = ctx.new_page()
        for name in pages:
            try:
                pg.goto(f'{BASE}/{name}', wait_until='load', timeout=30000)
            except Exception as e:
                print(f'  ❌ {name:<22} 打不开：{type(e).__name__}')
                bad.append(f'{name} 打不开')
                continue
            try:
                pg.wait_for_function('window.__ready === true', timeout=6000)
            except Exception:
                pass
            pg.wait_for_timeout(350)
            d = pg.evaluate(JS)
            if not d.get('hasVeil'):
                print(f'  ⚠️ {name:<22} 运行期没有 #veil 元素 —— **不适用**')
                skipped.append(name)
                continue
            if not d['applicable']:
                print(f'  ⚠️ {name:<22} #veil 不是遮盖层'
                      f'（position={d["veilPos"]} z-index={d["veilZ"]}）—— **不适用**')
                skipped.append(name)
                continue
            checked += 1
            fails = []
            for it in d['items']:
                zi = it['zIndex']
                val = None if zi == 'auto' else int(zi)
                if it['position'] == 'static' or val is None or val < 1:
                    fails.append(it)
            if fails:
                print(f'  ❌ {name:<22} veil.z={d["veilZ"]} · '
                      f'{len(fails)}/{len(d["items"])} 个内容可能被压住')
                for it in fails:
                    print(f'        ❌ {it["tag"]}  position={it["position"]} '
                          f'z-index={it["zIndex"]}')
                bad.extend(f'{name}:{it["tag"]}' for it in fails)
            else:
                # ⚠️ **跳过的 canvas 必须报出来** —— "跳过了什么"不说，
                #    就是静默跳过；将来某个 canvas 其实是**内容**（图表）时，
                #    这条守门会漏检而没人知道。
                skipnote = (f'（另跳过 {d.get("skippedCanvas", 0)} 个背景 canvas）'
                            if d.get('skippedCanvas') else '')
                print(f'  ✅ {name:<22} veil.z={d["veilZ"]} · '
                      f'{len(d["items"])} 个直接子内容都在其上{skipnote}')
        ctx.close()
        b.close()

    print()
    print('=' * 88)
    print(f'  实际断言 {checked} 个页面 · 不适用 {len(skipped)} 个'
          + (f'（{", ".join(skipped)}）' if skipped else ''))
    if checked == 0:
        print('❌ **一个页面都没断言到** ⇒ 判定失败（服务没起？BASE 写错了？）')
        return 1
    if bad:
        print(f'❌ {len(bad)} 处内容可能落在 #veil 之下（页面会发暗/看不清）')
        print('   ⇒ 那句兜底规则有两件职责：①别压倒浮层的 fixed ②把内容抬到遮罩之上。')
        print('      **改它的特异性时必须两条一起验。**')
        return 1
    print('✅ 所有适用页面：内容都在 #veil 之上')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
