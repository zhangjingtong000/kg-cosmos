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

用法：
    python tools/check_design_system.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'

# 正式的页面（**必须接设计系统**）。
# ⚠️ 依据：`web/` 下的页面里，**没有**挂"这是历史版本"横幅、
#    且不是 `_lab_*` 实验页的那些。
OFFICIAL = [
    'analytics.html', 'ask.html', 'community-atlas.html', 'corpus.html', 'diff.html',
    'force-explorer.html', 'force-graph.html', 'graph-2d.html', 'index.html',
    'kg-nebula.html', 'l5-review.html', 'method-limits.html', 'papers.html',
    'paper-table.html', 'pathqa.html', 'prisma.html', 'refs.html', 'stance.html',
    'temporal.html', 'upload.html', 'bipartite.html', 'new-graph.html',
]
# 野生底色（那份"从 analytics 复制来的 `:root`"的指纹）
WILD = ['#0b0d12', '#0b0c11', '#0d0e13', '#dfe2e8', '#8b95a8']


def strip_comments(text):
    """去掉 CSS/JS 注释 —— **只检查生效的代码**。"""
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)      # /* ... */
    text = re.sub(r'^\s*//.*$', '', text, flags=re.M)      # // ...
    # HTML 注释
    text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
    return text


def main():
    print('=' * 88)
    print('同一个 CSS 文件不许被同一个页面引两次')
    print('=' * 88)
    print('  （这份守门**只查这一条** —— 它误报过四次，\n'
          '   原因与每一次的实测都写在 docstring 里。\n'
          '   配色一致性**靠看，不靠 grep** —— 用 tools/ui_review_shots.py）')
    print()
    dup = []
    for p in sorted(WEB.glob('*.html')):
        raw = p.read_text(encoding='utf-8', errors='replace')
        hrefs = re.findall(r'href="([^"]*\.css)"', raw)
        for h in sorted(set(hrefs)):
            if hrefs.count(h) > 1:
                dup.append((p.name, h, hrefs.count(h)))
    if not dup:
        print('  \u2705 \u6ca1\u6709\u91cd\u590d\u5f15\u7528')
    for nm, h, n in dup:
        print(f'  \u274c {nm}\uff1a{h} \u5f15\u4e86 {n} \u6b21\uff08**\u91cd\u590d\u52a0\u8f7d**\uff09')
    print()
    print('=' * 88)
    if dup:
        print(f'\u274c {len(dup)} \u9879\u672a\u901a\u8fc7')
        return 1
    print('\u2705 \u901a\u8fc7')
    return 0


if __name__ == '__main__':
    sys.exit(main())
