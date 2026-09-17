# -*- coding: utf-8 -*-
r"""A7 · 给**历史原型页**加顶部横幅（"这是历史版本，正式入口是 index.html"）。

## TODO 的原话

> | **A7** | ⬜ **历史原型页加提示条** |
> 29 个 HTML 里 17 个是历史迭代。`index-classic.html` 有 20 个出链、
> **看起来比导航页还"全"**，**准备演示时容易误入** |
> 给那 17 页加一条顶部横幅说明"这是历史版本，正式入口是 index.html" | 1 小时

**实测（2026-09-16）：`web/` 下已经有 33 个 HTML**（比写这条时多了），
所以"17 个"这个数字也过期了 —— **更要紧的是别按数字办事，按名单办事。**

## 为什么用脚本而不是手改

1. **手改会漏**：加完就完，**下次新增一个原型页没人记得**；
2. **手改会漂**：17 个文件的横幅文案会各不相同；
3. **脚本 + 检查**才是这个项目一贯的做法：**能自动核对的东西不靠人记**
   （见 `check_e2e_stack.py` / `check_artifacts_tracked.py`）。

## 怎么区分"正式页"与"历史页"

**白名单**（正式入口，**不加横幅**）—— 这份名单是**显式**的，
因为它就是"这个项目到底给人看哪几屏"的定义：

    index.html            导航页（**唯一正式入口**）
    kg-nebula.html        主视图
    ask.html              多 Agent 问答（主看点）
    upload.html           上传
    analytics.html        评测结论
    corpus.html           语料清单
    method-limits.html    方法边界
    refs.html             参考与出处
    l5-review.html        L5 人工盲判（评审用）
    pathqa.html           路径问答
    temporal.html         时间演化
    diff.html             多图对比
    bipartite.html        使用关系二部图
    community-atlas.html  社区图鉴
    exploration.html      实验索引

**其余一律算历史页** —— 用"白名单"而不是"黑名单"：
**新增一个页面时，默认它是历史页**（会得到横幅），
**要成为正式页必须显式加进上面那份名单**。
⚠️ 这个方向是**故意的**：漏加横幅的代价是"用户误入一个原型"，
漏进白名单的代价是"正式页上多一条横幅" —— **后者更容易被发现**。

用法：
    python tools/add_legacy_banners.py            # dry-run：列出会改哪些
    python tools/add_legacy_banners.py --apply     # 真加
    python tools/check_legacy_banners.py           # 守门：历史页必须都有横幅
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'

# ── 正式页（**不加横幅**）──────────────────────────────────────────────
OFFICIAL = {
    'index.html',            # 导航页 —— 唯一正式入口
    'kg-nebula.html',        # 主视图
    'ask.html',              # 多 Agent 问答（主看点）
    'upload.html',
    'analytics.html',
    'corpus.html',
    'method-limits.html',
    'refs.html',
    'l5-review.html',
    'pathqa.html',
    'temporal.html',
    'diff.html',
    'bipartite.html',
    'community-atlas.html',
    'exploration.html',
    # L17（2026-09-16）：论文对比表 —— 它是**功能页**（可导出），不是迭代残留。
    # ⚠️ **这一行是守门逼出来的**：`check_legacy_banners.py` 报
    #   "1 个历史页没有横幅" ⇒ 提醒我"新页面默认是历史页，
    #   要成为正式页必须**显式**加进这份名单"。**这正是那个设计想达到的效果。**
    'paper-table.html',
    # L16（2026-09-17）：立场聚合 —— 同样是**功能页**（可展开看谁支持谁反对）。
    'stance.html',
    # L18（2026-09-17）：PRISMA 流程 —— 功能页（可打印）。
    # ⚠️ **这条守门已经拦下四次新增页面了**（`paper-table` / `stance` / `prisma` / `papers`）。
    #    四次都是"新页面默认算历史页，要成为正式页必须显式加进来"——
    #    **正是那个设计想达到的效果**：漏掉的代价是"多一条横幅"，一眼能看见；
    #    反过来（默认正式页）漏掉的代价是"用户误入原型页"，**看不出来**。
    'prisma.html',
    # K2（2026-09-17）：论文管理（已入库列表 / 删除 / 元数据）—— 功能页。
    'papers.html',
}

MARK = 'data-legacy-banner'      # 幂等标记：有它就不重复加
BANNER = '''<div data-legacy-banner
     style="position:sticky;top:0;z-index:9999;background:#2a1d12;
            border-bottom:1px solid rgba(224,164,105,.55);color:#e8c9a4;
            padding:9px 16px;font:13px/1.6 -apple-system,'Segoe UI','Microsoft YaHei',sans-serif">
  <b>⚠️ 这是历史版本，不是正式入口。</b>
  正式入口是 <a href="./index.html" style="color:#7cc0b6">index.html（导航页）</a> ——
  这一页保留下来是为了<b>备查迭代过程</b>，其中的数据与结论可能已经过期。
</div>
'''


def parse_args():
    ap = argparse.ArgumentParser(description='A7 给历史原型页加横幅')
    ap.add_argument('--apply', action='store_true', help='真改（默认只报告）')
    return ap.parse_args()


def is_legacy(p):
    return p.name not in OFFICIAL and not p.name.startswith('_lab_')


def has_banner(txt):
    return MARK in txt


def inject(txt):
    """把横幅插在 `<body ...>` 之后（**紧跟 body 开始标签**）。

    找不到 `<body` 就插在文件最开头 —— **不假装成功**。
    """
    m = re.search(r'<body[^>]*>', txt, re.I)
    if m:
        return txt[:m.end()] + '\n' + BANNER + txt[m.end():]
    return BANNER + txt


def main():
    a = parse_args()
    print('=' * 92)
    print('A7 · 给历史原型页加顶部横幅')
    print('=' * 92)
    pages = sorted(WEB.glob('*.html'))
    legacy = [p for p in pages if is_legacy(p)]
    official = [p for p in pages if not is_legacy(p)]
    print(f'  web/ 下共 {len(pages)} 个页面')
    print(f'    正式页（白名单）: {len(official)} 个')
    print(f'    历史页（要加横幅）: {len(legacy)} 个')
    print()

    todo, done = [], []
    for p in legacy:
        txt = p.read_text(encoding='utf-8', errors='replace')
        (done if has_banner(txt) else todo).append(p)

    if done:
        print(f'  已有横幅 {len(done)} 个：{", ".join(p.name for p in done[:6])}'
              + ('…' if len(done) > 6 else ''))
    if not todo:
        print('  ✅ 所有历史页都已有横幅')
        return 0

    print(f'  待加横幅 {len(todo)} 个：')
    for p in todo:
        print(f'      · {p.name}')

    if not a.apply:
        print()
        print('  （这是 dry-run。真加请加 --apply）')
        return 0

    n = 0
    for p in todo:
        txt = p.read_text(encoding='utf-8', errors='replace')
        p.write_text(inject(txt), encoding='utf-8')
        n += 1
    print()
    print(f'  ✅ 已给 {n} 个历史页加上横幅')
    print(f'     （正式页 {len(official)} 个未动；标记 `{MARK}` 保证幂等）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
