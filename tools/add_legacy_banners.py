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
因为它就是"这个项目到底给人看哪几屏"的定义。

★ **下面这份 `OFFICIAL` 是全项目唯一的正式页名单**：
`tools/check_design_system.py` 从这里 `import OFFICIAL`，
`tools/check_legacy_banners.py` 也从本模块 `import`。
**任何别处都不许再抄一份** —— 2026-09-19 的结构审计
（`docs/DOC_AUDIT_STRUCTURE.md §五.1`）就是因为在
`check_design_system.py` 里发现了**第二份 22 条的副本**：
它与这里的名单**漂开**（当时 19 条 vs 22 条），把 `force-explorer` / `force-graph` /
`graph-2d` / `new-graph` 四个**实际都挂着历史横幅**的页面算作正式页。
**两份名单一定会漂，而漂的那一份就是漏洞。**

    index.html            导航页（**唯一正式入口**）
    kg-nebula.html        主视图
    ask.html              多 Agent 问答（主看点）
    upload.html           上传
    analytics.html        评测结论
    method-limits.html    方法边界
    refs.html             参考与出处
    l5-review.html        L5 人工盲判（评审用）
    pathqa.html           路径问答
    temporal.html         时间演化
    diff.html             多图对比
    bipartite.html        使用关系二部图
    community-atlas.html  社区图鉴

⚠️ **`corpus.html`（语料清单）已于 2026-09-19 移出这份名单** ——
它的能力（搜索过滤 / 「只看已入库」/ 关系数 / 社区数 / 每行「进入主视图」）
**并入了 `papers.html`**，`web/index.html` 的入口 07 也**改指 `papers.html`**。
它现在**是历史页**（按 `ARCHIVE_POLICY` **方式 A 原地留档**，由本脚本加横幅），
**不是删除** —— 原行留档如下，理由写在 `web/corpus.html` 的头部注释里：

    corpus.html           语料清单 —— ⚠️ 已移出（2026-09-19，并入 papers.html）

**判据（为什么它是"待弃"而不是"正式页"）**：结构与措辞过时
（表头写死「arXiv id」对中文期刊论文不适用、无语言列、placeholder 只提 arXiv），
而**不是数字错** —— 它的三个统计块是运行时读 `corpus-index.json` 的 `stats`。
详见 `docs/DOC_AUDIT_FRONTEND_COMMENTS.md §特别说明`。

**内部实验/基准页**（`EXPERIMENTAL`）—— **不是**给人看的展示页，
所以**也不是**正式页、**同样不加横幅**：

    exploration.html      深空背景 + `index3d-bounce.html` 的 iframe 组合，
                          带 `benchmark` / `seed` / `graph` 参数；由
                          `web/kg-data/test/fusion-contract.test.mjs` 当契约对象测
                          （"passive 背景 + 透明 KG 层 + benchmark 透传"）。
                          ⇒ 三条事实：**不引任何 CSS**（`design.css` / `voyage.css`
                          都没有）、**不在 `index.html` 的任何入口里**、
                          **不在 `web/lib/demo-rail.js` 的 `FLOW` 里**
                          ⇒ 只有直接 URL 能到达，**演示时不会误入**，
                          所以不需要那条"别误入"的横幅。
                          （2026-09-19 改判：它此前靠"实验索引"四个字被算作正式页。
                            改判**不是废弃** —— 它仍被上面两个测试与
                            `docs/早先的讨论记录.md` 的分层先例引用。）

**其余一律算历史页** —— 用"白名单"而不是"黑名单"：
**新增一个页面时，默认它是历史页**（会得到横幅），
**要成为正式页必须显式加进上面那份名单**。
⚠️ 这个方向是**故意的**：漏加横幅的代价是"用户误入一个原型"，
漏进白名单的代价是"正式页上多一条横幅" —— **后者更容易被发现**。

⚠️ **口径提醒**：`check_legacy_banners.py` 打印的"正式页 N"是
**不加横幅的页面数**（= `OFFICIAL` + `EXPERIMENTAL` + `_lab_*`），
**不是 `OFFICIAL` 的条数** —— 那份守门关心的是"哪些页面没有横幅"，
不区分"正式页"与"实验页"（两者都不该有横幅）。

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
# ★ **全项目唯一的正式页名单**：`tools/check_design_system.py` 从这里
#   `import OFFICIAL`，`tools/check_legacy_banners.py` import 本模块的 `is_legacy`。
#   **别处不许再抄一份** —— 2026-09-19 审计发现设计系统脚本里抄了一份 22 条的，
#   与这里漂开：`force-explorer` / `force-graph` / `graph-2d` / `new-graph`
#   四个页面**实际都带 `data-legacy-banner` 横幅**，却被那份副本当正式页。
OFFICIAL = {
    'index.html',            # 导航页 —— 唯一正式入口
    'kg-nebula.html',        # 主视图
    'ask.html',              # 多 Agent 问答（主看点）
    'upload.html',
    'analytics.html',
    # ⚠️ 2026-09-19：**`corpus.html` 从这份名单移出**（它现在是历史页、要加横幅）——
    #   "语料清单"的能力并入了 `papers.html`（搜索 / 只看已出图 / 关系·社区列 /
    #   进主视图），`web/index.html` 的入口 07 改指 `papers.html`。
    #   **这不是删除**：文件在原地，由本脚本加"这是历史版本"的横幅（方式 A）。
    #   ⇒ 原行留档（按 `ARCHIVE_POLICY`：改的是归类，不是文件）：
    #       'corpus.html',        # 语料清单 —— 已并入 papers.html
    'method-limits.html',
    'refs.html',
    'l5-review.html',
    'pathqa.html',
    'temporal.html',
    'diff.html',
    'bipartite.html',
    'community-atlas.html',  # 社区图鉴
    # 'exploration.html',    # 实验索引 —— ⚠️ 2026-09-19 **移出正式页**：
    #                        #   三条事实（不引任何 CSS / 不在 `index.html` 的任何入口里 /
    #                        #   不在 `demo-rail.js` 的 `FLOW` 里）⇒ 它是**内部实验/基准页**，
    #                        #   见下面的 `EXPERIMENTAL`。**不是废弃** —— 它仍被
    #                        #   `web/kg-data/test/fusion-contract.test.mjs` 的两个测试与
    #                        #   `docs/早先的讨论记录.md` 的分层先例引用。
    #                        #   按 ARCHIVE_POLICY：**原行留档，不删**（改的是归类，不是文件）。
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

# ── 内部实验/基准页（**不是正式展示页，也不加横幅**）──────────────────────
# **判据（三条全中才放这里）**：
#   ① **不引**任何样式表（`design.css` / `voyage.css` 都没有）；
#   ② **不在** `index.html` 的任何入口里；
#   ③ **不在** `web/lib/demo-rail.js` 的 `FLOW` 演示动线上。
# ⇒ 三条的合意是"**只有直接 URL 能到达**"：演示时不会误入，
#    所以不需要那条"别误入"的横幅；但它也**不是**给人看的正式页。
# ⚠️ 与 `_lab_*` 前缀的区别：那些是**临时试验**（命名即标记）；
#    这里的页面是**长期在用的工具/基准**（有测试盯着），只是不在交付面上。
EXPERIMENTAL = {
    'exploration.html',      # 深空背景 + `index3d-bounce.html` 的 iframe 组合 +
                             # `benchmark`/`seed`/`graph` 参数；见模块 docstring。
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
    """历史原型页 = **既不是正式页、也不是内部实验页、也不是 `_lab_*`**。

    ⚠️ `EXPERIMENTAL` 与 `OFFICIAL` 一起被排除，是**行为不变**的：
    `exploration.html` 在这之前就因为没有横幅需求而留在白名单里 ——
    这次只是把它从"正式页"挪到"实验页"，**它照旧不加横幅**。
    """
    return (p.name not in OFFICIAL and p.name not in EXPERIMENTAL
            and not p.name.startswith('_lab_'))


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
