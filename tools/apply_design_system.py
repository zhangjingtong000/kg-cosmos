# -*- coding: utf-8 -*-
r"""把"复制来的"页面接到设计系统（**页面自己的 CSS 一行都不用改**）。

## 做法（**只加一层别名映射**）

```
<link rel="stylesheet" href="./lib/design.css">
<style>
  :root{
    --fg: var(--ink);        /* 私有名 → 系统 token */
    --dim: var(--ink-3);
    --hot: var(--accent);
    --cool: var(--cyan);
    --warn: var(--warm);
    --bad: var(--clay-2);
  }
  ...原文的 CSS，一行不动...
```

**⚠️ 方向不能反** —— 是"私有名 → 系统值"。
`analytics.html` 就是反面教材：它**引了** `design.css`，却又定义了一套自己的
`--bg:#0b0d12`，**把系统覆盖掉** ⇒ 所以它看起来仍然是野生色。

## 为什么用脚本而不是手改 4 遍

**因为这 4 个页面本来就是"手改出来的"** —— 它们的 `:root` **一模一样**
（`--bg:#0b0d12;--fg:#dfe2e8;--dim:#8b95a8;--line:rgba(193,195,201,.18)` …），
**那正是"复制粘贴"留下的指纹**。
⇒ 同一件事**再做一遍手改，就是再复制一遍** ⇒ **用脚本，且把映射写在一处**。

⚠️ 幂等：已经改过的（含 `design.css` 的 link）直接跳过。

用法：
    python tools/apply_design_system.py --dry-run
    python tools/apply_design_system.py --apply
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'

# **要接的页面**（都是"复制来的"那份 :root）
TARGETS = ['papers.html', 'paper-table.html', 'prisma.html', 'stance.html']

# 那份"复制来的 :root"的特征（用来确认我们改的是对的东西）
OLD_ROOT = re.compile(
    r':root\{--bg:#0b0d12;--fg:#dfe2e8;--dim:#8b95a8;--line:rgba\(193,195,201,\.18\);'
    r'\s*\n?\s*--hot:#e0a469;--cool:#7cc0b6;--warn:#e8c9a4(?:;--bad:#e07575)?\}')
# `stance.html` 的变体（它把 --hot/--cool 换成了 --yes/--no/--mixed/--poss）
OLD_ROOT2 = re.compile(
    r':root\{--bg:#0b0d12;--fg:#dfe2e8;--dim:#8b95a8;--line:rgba\(193,195,201,\.18\);'
    r'\s*\n?\s*--yes:#7cc0b6;--no:#e07575;--mixed:#e0a469;--poss:#7a8498\}')

NEW = """<link rel="stylesheet" href="./lib/design.css">
<style>
  /* ══ 接入设计系统（2026-09-18）═════════════════════════════════════════
   *
   * **改之前**：这一页**没引 `design.css`**，样式写在 inline `<style>` 里 ——
   * 而那份样式是**从 `analytics.html` 复制来的** ⇒ 它自带一套 `:root`，
   * **把系统的 token 全换成了自己的**：
   *
   *     本页原来              设计系统的真值
   *     --bg   #0b0d12   →   --bg  **#02040a**
   *                          （#0b0d12 其实是系统的 `--surface #0b0e16` —— 把 surface 当 bg 用了）
   *     --fg   #dfe2e8   →   --ink **#eef3fd**
   *     --dim  #8b95a8   →   --ink-3 rgba(193,195,201,.46)
   *     --line rgba(193,195,201,.18) → --line rgba(178,180,186,.13)
   *     --warn #e8c9a4   →   --warm **#ffcfa8**
   *     --bad  #e07575   →   系统里没有"红" ⇒ 用 --clay-2
   *
   * ⇒ **结果是它比 index/ask 亮一档** —— 一眼就能看出"不是同一个站点"。
   *
   * **改法**：引 `design.css`，然后**只加一层别名映射** ——
   * 把本页的私有名指向系统的 token，**页面自己的 CSS 一行都不用改**。
   *
   * ⚠️ **方向不能反**：是"私有名 → 系统值"。
   * `analytics.html` 是反面教材 —— 它**引了** `design.css`，却又定义了一套
   * 自己的 `--bg`，**把系统覆盖掉**，所以看起来仍然是野生色。
   */
  :root{
    --fg: var(--ink);
    --dim: var(--ink-3);
    --hot: var(--accent);      /* stance.html 没有这个名，多余一行也无害 */
    --cool: var(--cyan);
    --warn: var(--warm);
    --bad: var(--clay-2);
    --yes: var(--cyan);
    --no: var(--clay-2);
    --mixed: var(--accent);
    --poss: var(--ink-3);
  }"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    print('=' * 88)
    print('把"复制来的"页面接到设计系统（**只加别名映射**）')
    print('=' * 88)
    n_done = n_skip = 0
    for name in TARGETS:
        p = WEB / name
        if not p.exists():
            print(f'  ✗ {name} 不存在')
            continue
        t = p.read_text(encoding='utf-8')
        if './lib/design.css' in t:
            print(f'  ⏭  {name}：已接过（幂等跳过）')
            n_skip += 1
            continue
        m = OLD_ROOT.search(t) or OLD_ROOT2.search(t)
        if not m:
            print(f'  ⚠️ {name}：没找到那份"复制来的 :root" ⇒ **不动它**（宁可漏，不可错改）')
            n_skip += 1
            continue
        # 把 `<style>\n  :root{...}` 整体换成"link + 新 :root"
        start = t.rfind('<style>', 0, m.start() + 1)
        if start < 0 or m.start() - start > 400:
            print(f'  ⚠️ {name}：`<style>` 离 `:root` 太远 ⇒ 不动它')
            n_skip += 1
            continue
        new_t = t[:start] + NEW + t[m.end():]
        if a.apply:
            p.write_text(new_t, encoding='utf-8')
            print(f'  ✅ {name}：已接（link + 别名映射）')
        else:
            print(f'  （dry-run）{name}：会加 link 与别名映射')
        n_done += 1
    print()
    print(f'  改了 {n_done} 个 · 跳过 {n_skip} 个')
    if not a.apply and n_done:
        print('  （**dry-run** —— 没写文件。真改请加 `--apply`）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
