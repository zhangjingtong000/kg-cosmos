# -*- coding: utf-8 -*-
r"""H8 · E2E 技术栈守门（**不许新增"自建 CDP"脚本**）。

## 背景：§3.2 纪律 2 说要用 Playwright，而实际用了自建 CDP

TODO H8 的原话：

> | **H8** | ⬜ **E2E 三套件的技术栈** |
> §3.2 纪律 2 明确要求 **Playwright** 驱动真实 canvas 事件
> （聚焦态 / 上传闭环 / 帧时间采样）；
> **实际用的是自建 CDP 脚本**（`tools/cdp_verify_*.py` 约 20 个）。
> 功能等价，但**技术栈与规划不同** | 🔶 做了等价的事，栈不同
> （要么写进文档说明，要么补 Playwright）

## 实测（2026-09-16）：**迁移其实已经在发生**

```
Playwright:  8 个   ← C4 / B7 / K5 / K6 / F1 / F2 / F4 / H9（**全是最近做的**）
自建 CDP:   20 个   ← 早期脚本
```

⇒ **技术上是 Playwright 完全够用** —— 那 8 个里有几个恰恰做的是
"读 canvas 颜色缓冲"这种听起来最像需要底层控制的事
（`cdp_verify_type_filter.py` / `cdp_verify_qa_to_3d.py` 都在断言颜色和变了），
**而它们用 `page.evaluate` 就做到了**。

## 所以这一项的决定

| 选项 | 做不做 | 理由 |
|---|---|---|
| **把 20 个老脚本重写成 Playwright** | ❌ | **功能等价、27 项全绿**；重写是**纯风险换零收益**（而且会覆盖掉那些脚本里已经沉淀的坑位注释） |
| **写清"为什么栈不同、边界在哪"** | ✅ | 这是 H8 的另一半，也是评审真正要的 |
| **★ 加守门：不许**新增**自建 CDP** | ✅ | **光写文档挡不住漂回去** —— 加一条基线断言 |

**判据**：`tools/` 下识别为"自建 CDP"的脚本数**不得超过基线**。
**要新增 E2E 脚本，就用 Playwright。**

⚠️ **这条检查不评价老脚本的质量** —— 它们**都还在回归里、都绿**。
它只拦"**明知有更好的栈还继续用手搓的**"这一种情况。

用法：
    python tools/check_e2e_stack.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / 'tools'

# 基线：2026-09-16 实测的"自建 CDP"脚本数。
# **只许减少，不许增加** —— 新增 E2E 请用 Playwright。
BASELINE_RAW_CDP = 20

# 识别规则（宽松到不会误判，严格到能拦住新手搓的）：
PW_HINT = re.compile(r'playwright', re.I)
RAW_HINT = re.compile(r'websocket|DevTools|9222|/json/list|json/version', re.I)


def classify(p):
    try:
        src = p.read_text(encoding='utf-8', errors='replace')
    except Exception:
        return 'unreadable'
    if PW_HINT.search(src):
        return 'playwright'
    if RAW_HINT.search(src):
        return 'raw_cdp'
    return 'other'


def main():
    print('=' * 92)
    print('H8 · E2E 技术栈守门（不许新增"自建 CDP"脚本）')
    print('=' * 92)

    files = sorted(TOOLS.glob('cdp_verify_*.py'))
    if not files:
        print('  ⚠️ 没找到 cdp_verify_*.py')
        return 1

    buckets = {'playwright': [], 'raw_cdp': [], 'other': [], 'unreadable': []}
    for p in files:
        buckets[classify(p)].append(p.name)

    print(f'  共 {len(files)} 个 E2E 脚本')
    print(f'    Playwright : {len(buckets["playwright"]):>2} 个')
    print(f'    自建 CDP   : {len(buckets["raw_cdp"]):>2} 个'
          f'   （基线 {BASELINE_RAW_CDP}）')
    if buckets['other']:
        print(f'    其它       : {len(buckets["other"]):>2} 个  {buckets["other"][:4]}')
    if buckets['unreadable']:
        print(f'    读不了     : {len(buckets["unreadable"]):>2} 个')
    print()

    n_raw = len(buckets['raw_cdp'])
    if n_raw > BASELINE_RAW_CDP:
        print(f'  ❌ **自建 CDP 脚本从 {BASELINE_RAW_CDP} 增到了 {n_raw}**')
        print('     ⇒ 新增的 E2E 请用 Playwright（`from playwright.sync_api import sync_playwright`）。')
        print('     ⇒ 理由见本文件头部：那 8 个 Playwright 脚本里')
        print('       **恰恰包括"读 canvas 颜色缓冲"这种最像需要底层控制的事**，')
        print('       而它们用 `page.evaluate` 就做到了。')
        print(f'     新增的（疑似）：'
              f'{[n for n in buckets["raw_cdp"]]}')
        return 1

    if n_raw < BASELINE_RAW_CDP:
        print(f'  ✅ 自建 CDP 从 {BASELINE_RAW_CDP} 减到 {n_raw}'
              f'（**迁移在推进**）—— 请把本文件的 `BASELINE_RAW_CDP` 更新为 {n_raw}，')
        print('     这样"只许减少"这条约束才会继续生效。')
        return 0

    print(f'  ✅ 自建 CDP 维持在基线 {BASELINE_RAW_CDP} 个（**没有新增**）')
    print(f'     · 老脚本**不重写**：它们功能等价、都在回归里且全绿，')
    print(f'       重写是纯风险换零收益（还会覆盖掉里面沉淀的坑位注释）。')
    print(f'     · 新脚本一律用 Playwright —— 这一条由本检查守住。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
