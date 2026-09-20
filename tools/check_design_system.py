# -*- coding: utf-8 -*-
r"""前端设计系统守门：**新增页面必须接 `design.css`，不许自带野生底色**。

## 为什么这条守门非写不可

`docs/UI_AUDIT.md` 的核心发现是：

```
只有 **6 / 37** 个页面引了 `lib/design.css`；
其余 31 个页面把样式写在 inline `<style>` 里，各自为政。
★ 而我最近新加的 4 个（papers / paper-table / prisma / stance）**全部没接** ——
  因为它们是我**从 `analytics.html` 复制**来的。
⇒ 连 `analytics.html` 自己都**引了 design.css 却又定义了一套 `--bg:#0b0d12` 把它覆盖掉**。
```

**⇒ 根因不是"某次忘了"，而是「新增页面的动作本身就是复制粘贴」。**
**⇒ 所以修完那 4 个页面还不够 —— 必须让"下一次复制"当场失败。**

## ⚠️⚠️ 这份检查**误报过四次**，所以它现在**只查一件事**

**我最初想断言"每个正式页面都必须引 `lib/design.css`"，那个判据是错的。** 实测：

| 我报的"问题" | 实测 | 我错在哪 |
|---|---|---|
| `ask.html` / `index.html` 「没接系统」 | 它们用 **`voyage.css`**（**第二套**完整系统，含 WebGL 光场） | 我只认一个文件名 |
| `analytics`/`method-limits`/`pathqa` 「野生底色」 | 三页共用「顶部光晕→深墨黑」渐变，**且注释里写明了为什么**（"蓝紫色相全部移除"） | 把**设计决策**当成违规 |
| `new-graph.html` 「`var(--bg)` 未定义」 | 它**自己**有 `:root{--bg:#02040a}`，**值与系统一致**，实测 `rgb(2,4,10)` | 我的正则只匹配 `body{}`，漏了 `html,body{}` |
| `kg-nebula`/`paper-table` 「野生色」 | 一处是**注释**、一处是**图例文字色**、一处是**小标签色** | 把"任意位置的色值"当成"底色" |

**⇒ 这一整轮的主题是「文档与现实不符」，而我的检查工具本身也在不断地与现实不符。**
**⇒ 所以这份守门收窄到只查「客观、可判定、不会误报」的那一条**：

> ### 同一个 CSS 文件不许被同一个页面引两次。
> **那不需要判断设计意图，看字符串就知道。**

## ★ 配色一致性怎么保证（**不靠这份脚本**）

**配色是"看"出来的，不是"grep"出来的。**
⇒ 用 `tools/ui_review_shots.py`（**有头模式**）截图，**人工/模型逐张看**。
**那份脚本的 docstring 记了更重要的一条**：**无头截图的 WebGL 背景会画错**。

## ★ 正式页名单在哪（2026-09-19 合并为单一来源）

**不在这份文件里。** 唯一权威是 `tools/add_legacy_banners.py` 的 `OFFICIAL`，
本脚本直接 `import`（`tools/check_legacy_banners.py` 也从那里 import）。

**此前这里抄了一份 22 条的副本**，与那份权威的名单**漂开**：
`force-explorer` / `force-graph` / `graph-2d` / `new-graph` 四个页面
**实际都带 `data-legacy-banner` 横幅**（= 按本脚本自己的判据就是历史页），
却在这份副本里被算作"正式页"——**两边同时成立，等于没有判据**
（审计：`docs/DOC_AUDIT_STRUCTURE.md §五.1`）。

**⇒ 这一处与 docstring 上半部分是同一个教训**：文档/名单是**事实的复述**，
事实变了就得跟着变；**同一个事实抄两遍，一定会漂。**

用法：
    python tools/check_design_system.py
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

ROOT = HERE.parent
WEB = ROOT / 'web'

# ── 正式页名单：**唯一权威在 `tools/add_legacy_banners.py`**（见 docstring）──
#   ⚠️ 不要在这里再写第二份 —— 写了就会漂，这里是全项目唯一一次漂过的地方。
from add_legacy_banners import EXPERIMENTAL, OFFICIAL  # noqa: E402

# 野生底色（那份"从 analytics 复制来的 `:root`"的指纹）
# ⚠️ 与 docstring 说的一样：**配色靠看，不靠 grep** ⇒ 这个指纹表
#    **目前不被任何判据使用**（留着是为了下次想查时不必重新找值，不是判据）。
WILD = ['#0b0d12', '#0b0c11', '#0d0e13', '#dfe2e8', '#8b95a8']


def strip_comments(text):
    """去掉 CSS/JS 注释 —— **只检查生效的代码**。

    ⚠️ 关键细节：**删注释时必须把换行留下**。
    原先直接 `re.sub(r'/\\*.*?\\*/', '', ...)` 会把注释里的换行一起吃掉，
    于是它后面所有行的**行号整体前移** —— 报出来的 `voyage.css:188`
    其实是第 204 行。行号错了比不报还费时间（要拿着错行号去翻文件）。
    现在一律用"注释里几个换行就补几个换行"替换，行号逐字对应原文。
    """
    def keep_lines(m):
        return '\n' * m.group(0).count('\n')

    text = re.sub(r'/\*.*?\*/', keep_lines, text, flags=re.S)      # /* ... */
    text = re.sub(r'^\s*//.*$', '', text, flags=re.M)              # // ...
    text = re.sub(r'<!--.*?-->', keep_lines, text, flags=re.S)     # HTML 注释
    return text


def main():
    print('=' * 88)
    print('设计系统守门：**重复引用** + **左边强调竖线**')
    print('=' * 88)
    print('  （配色一致性**靠看，不靠 grep** —— 用 tools/ui_review_shots.py。\n'
          '   这份守门只查两条**客观、可判定**的；它误报过四次，'
          '原因写在 docstring 里。）')
    print()
    # ── ⓪ 正式页名单（**只复述来源与规模，不计入退出码**）──────────────
    #
    # **为什么这一段不计入退出码**：判据只有"重复引用"与"左边竖线"两条
    #   （见 docstring：这份脚本误报过四次，宁可少查不可乱查）。
    #   名单本身归 `tools/add_legacy_banners.py` 管，那里有另一条守门
    #   （`check_legacy_banners.py`）盯着它。
    # **这一段存在的意义**：把"名单是谁的"印在输出里 ——
    #   **名单抄过两份、漂了半年，就是因为没人看得见它从哪来。**
    missing_official = sorted(n for n in OFFICIAL if not (WEB / n).exists())
    print(f'  ⓪ 正式页名单 {len(OFFICIAL)} 个'
          f'（**唯一权威**：`tools/add_legacy_banners.py` 的 `OFFICIAL`，本脚本 import）'
          f' · 名单里文件不存在的：{len(missing_official)} {missing_official[:5]}')
    print(f'     内部实验页（也不算正式页、不加横幅）：{sorted(EXPERIMENTAL)}')
    print()
    dup = []
    for p in sorted(WEB.glob('*.html')):
        raw = p.read_text(encoding='utf-8', errors='replace')
        hrefs = re.findall(r'href="([^"]*\.css)"', raw)
        for h in sorted(set(hrefs)):
            if hrefs.count(h) > 1:
                dup.append((p.name, h, hrefs.count(h)))
    if not dup:
        print('  ① 重复引用 CSS：✅ 无')
    for nm, h, n in dup:
        print(f'  ① 重复引用 CSS：❌ {nm}：{h} 引了 {n} 次（**重复加载**）')
    print()

    # ── ② 左边强调竖线（`design.css` 注释：「竖线已废弃」）────────────
    #
    # **为什么这条可以进守门**（而"配色一致性"不能）：
    #   `lib/design.css` 自己的注释写着 `.note` 用**顶部短横线**、「**竖线已废弃**」
    #   ⇒ 那是**本项目写下来的规矩**，不是我猜的风格偏好。
    #
    # **判据**：`border-left` 用 **≥2px** 的实线。
    #   ⚠️ **1px 不算** —— 那是**分隔线**（`design.css` 自己也用 `border-bottom:1px`）。
    #   ⚠️ **注释不算** —— 我改这些地方时会在注释里引用原来那行，
    #      把注释算进来会把**好文档判成坏代码**（这个坑踩过）。
    #   ⚠️ **色标不算** —— `pathqa` 那种 `::before` 的 3×15px 小条是**状态标记**，
    #      不是"把内容框成一块"的竖线。
    #
    # ★ 2026-09-20 **扩面**：原先只扫 `web/*.html` 的**内联** `<style>`，
    #   于是**任何外部 CSS 都逃过这条判据** —— 连这条规矩的出处
    #   `lib/design.css` 自己都不在被扫范围内。后果是实测出来的：
    #   `lib/voyage.css:204` 的 `.note{border-left:2px solid …}`
    #   （导航页的玻璃盒结论块）一直违规，而守门一路报「✅ 无」。
    #   现在把 `web/*.css` 与 `web/lib/**/*.css` 一并纳入；
    #   `strip_comments` 本来就同时处理 `/* */` 与 `<!-- -->`，HTML 与 CSS 可共用。
    CSS_FILES = sorted(list(WEB.glob('*.css')) + list((WEB / 'lib').rglob('*.css')))
    stripes = []
    n_html = 0
    for p in sorted(WEB.glob('*.html')) + CSS_FILES:
        live = strip_comments(p.read_text(encoding='utf-8', errors='replace'))
        if p.suffix == '.html':
            n_html += 1
        for m in re.finditer(r'border-left\s*:\s*([2-9]|\d\d+)px', live):
            ln = live[:m.start()].count('\n') + 1
            stripes.append((p.name, ln, m.group(0)))
    if not stripes:
        print(f'  ② 左边强调竖线（≥2px）：✅ 无'
              f'（扫了 {n_html} 个页面 + {len(CSS_FILES)} 个外部 CSS）')
    for nm, ln, frag in stripes:
        print(f'  ② 左边强调竖线：❌ {nm}:{ln}  `{frag}`')
    if stripes:
        print('      修法：照 `lib/design.css` 的 `.note` 用**顶部短横线** ——')
        print('            `::before{position:absolute;left:0;top:0;'
              'width:56px;height:2px;background:var(--accent)}`')
        print('            （**1px 的分隔线不受影响**；**色标用 `::before` 小条**也不受影响）')
    print()

    print('=' * 88)
    bad = len(dup) + len(stripes)
    if bad:
        print(f'❌ {bad} 项未通过')
        return 1
    print('✅ 两条全过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
