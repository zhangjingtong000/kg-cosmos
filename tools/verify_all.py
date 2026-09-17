# -*- coding: utf-8 -*-
"""串行跑全部验证脚本并汇总（回归入口）。

为什么串行：每个前端验证都要起一个带 CDP 的 Chrome 实例，串行最稳，
也避免多个实例同时读同一份 2.6MB 的 nebula.json 造成误判。

前置：本地静态服务已在 8098 跑起来
      `cd D:\\kg-demo && python -m http.server 8098 --bind 127.0.0.1`

用法：
  python tools/verify_all.py            # 全部
  python tools/verify_all.py --fast     # 跳过耗时的 PDF 截图对比
"""
import json
import subprocess
import sys
import time
from pathlib import Path

CHECKS = [
    ("导航页 · 链接可达 · 示意图未溢出", ["tools/cdp_verify_nav.py"]),
    ("全站可访问性（键盘可达 · 回导航页 · 动效可关）", ["tools/cdp_verify_a11y.py"]),
    ("星云交互 · 标签 · 证据面板", ["tools/cdp_verify_ui.py", "0"]),
    ("标签分配与深度遮挡（节点级门槛）", ["tools/cdp_verify_label_detail.py"]),
    # K3/G5（2026-09-15）：实体详情面板去掉两处截断 + 说清"来源论文"。
    # **为什么值得一个回归位**：截断（`slice(0,5)` / `list.slice(0,6)`）的症状是
    # "界面看起来正常" —— 一个关联 99 条、13 种类型的枢纽，只显示 5 类 × 6 个，
    # 用户会以为它就那么点关系。**只有数区块数才看得出来。**
    ("实体详情无截断 · 来源论文（K3/G5）", ["tools/cdp_verify_node_detail.py"]),
    # K1（2026-09-16）：实体详情里的「改名并提交改图计划」入口。
    # **为什么值得断言**：它替换的是原先"只记录、不自动改图"的终态 ——
    # 三种失效都是静默的：入口没渲染（用户以为没这功能）、
    # `data-edit-node` 绑错实体（**提交的计划会改错东西，最危险**）、
    # 空输入没提示（点了没反应，不知道该填什么）。
    ("改图计划入口（K1）", ["tools/cdp_verify_edit_entry.py"]),
    # K8/K9（2026-09-16）：问答历史持久化 + 答案导出（含引文与页码）。
    # **为什么值得断言**：
    #   · K8 —— "存进去了"与"**刷新后还在**"是两件事，只测前者会漏掉键名写错；
    #   · K9 —— TODO 对它的定位是"写论文最直接的用途"，**必须带引文与页码**，
    #           否则它相对截图的唯一优势就没了。`toMarkdown` 是纯函数，好断言。
    ("问答历史与导出（K8/K9）", ["tools/cdp_verify_qa_history.py"]),
    # H4（2026-09-16）：语义兜底（端点 + 前端接入）。
    # **为什么值得断言**：
    #   · 端点必须**区分"未就绪"与"没找到"** —— 否则前端会把"功能没开"
    #     显示成"图里没有这个概念"，那是本项目最想避免的"沉默的错误结论"；
    #   · `min_score` 必须真的过滤 —— 返回一个 0.3 分的"最像的"比返回空更危险；
    #   · 前端**只在字面失败时**才兜底，且**必须把"这是语义兜底"写在界面上**。
    # ⚠️ 它用 8000（要真 API），不是 8098。
    ("语义兜底（H4）", ["tools/cdp_verify_semantic_fallback.py"]),
    ("统一着色引擎（含默认外观等价性回归）", ["tools/cdp_verify_color.py", "0"]),
    # P7「只看分歧」（2026-09-15 加）。**为什么它值得一个回归位**：
    # 这个功能的数据依赖曾经是断的（build_community_details.py 把边类型硬编码成
    # "RELATED"，18 个社区 16569 条边全是同一个类型），所以"看起来做了但没数据"
    # 是很容易发生的事 —— 必须有断言盯着"真的有条数、真的会变色、无数据时不显示"。
    ("争议视图「只看分歧」（有数据 · 能变色 · 无数据不显示）",
     ["tools/cdp_verify_contradicts.py"]),
    ("方法能力与局限视图", ["tools/cdp_verify_method_limits.py"]),
    ("图谱分析面板（数字可追溯）", ["tools/cdp_verify_analytics.py"]),
    # 数据接进去了不等于**显示得出来**：L5 效度与 L7 归因这两个区块是 hidden 的，
    # 只有 boot() 拿到对应字段才会取消隐藏。字段名对不上时，
    # 上面的"数字可追溯"仍会通过（它读的是 analytics.json，不是 DOM）。
    ("分析面板新区块（L5 效度 · L7 归因）", ["tools/cdp_verify_analytics_sections.py"]),
    ("问题子图 PathQA", ["tools/cdp_verify_pathqa.py"]),
    # 盲判界面是 L5 严格效度的**唯一**入口；它坏了会让 3–6 小时的人工投入白费。
    ("L5 盲判界面（盲态不泄漏 · 判定可落盘）", ["tools/cdp_verify_l5_review.py"]),
    # C4：分享链接。**必须用浏览器测** —— 编码与恢复全在前端，
    # 在 Python 里"读源码看有没有那个函数"证明不了它能跑。
    ("分享链接（编码语义状态 · 打开能恢复 · 找不到要明说）",
     ["tools/cdp_verify_share_link.py"]),
    # B7：类型聚焦。断言到**颜色缓冲的和变小**为止 ——
    # "点了没报错"不算通过（那条教训来自 qa_history 那次自欺的测试）。
    ("按类型聚焦（图例可点 · 颜色真变 · 是开关 · 可分享）",
     ["tools/cdp_verify_type_filter.py"]),
    # K5：按论文筛选。同样断言到**边颜色缓冲的和** ——
    # 而且再加一条"恢复后要回到原值"，因为 `BufferAttribute(ec).array is ec`
    # 这个别名关系**会让恢复静默失效**（本轮实测踩到）。
    ("按论文筛选（控件可见 · 边颜色真变 · 恢复回原值 · 可分享）",
     ["tools/cdp_verify_paper_filter.py"]),
    # K6：批量上传。用**空 PDF** 让每篇快速失败，
    # 于是能一次测到"批量 + **一篇失败不停整批** + 完成时不自动跳转"三件事。
    ("批量上传（多选 · 串行排队 · 失败不停批 · 不自动跳转）",
     ["tools/cdp_verify_batch_upload.py"]),
    # F4：使用关系二部图。断言到**SVG 里真的画出了节点与连线**为止 ——
    # "数据取到了"不等于"画出来了"，后者才是这一页真正的失败模式。
    ("使用关系二部图（节点/连线真画出来 · 点节点聚焦 · 是开关）",
     ["tools/cdp_verify_bipartite.py"]),
    # F1：时间演化。断言到**KPI 上的数字与数据帧逐字相等** ——
    # 同样不能只断言"页面没报错"。
    ("时间演化（帧数对 · 末帧 KPI 逐字对 · 折线画全 · 播放是开关）",
     ["tools/cdp_verify_temporal.py"]),
    # F2：多图对比。**最强的那条是"用 Python 独立算一遍交集，与浏览器算出的
    # KPI 逐字相等"** —— 渲染一个错的数也算渲染，所以必须能独立复算。
    ("多图对比（默认对 · **交集与独立复算逐字相等** · 三栏 · 点切换到那对）",
     ["tools/cdp_verify_diff.py"]),
    # A7：历史原型页的横幅。`index-classic.html` 有 20 个出链、
    # **看起来比导航页还"全"** ⇒ 演示时容易误入。
    # 加横幅是一次性的，**但"新增原型页也要带横幅"这条纪律要长期成立** ⇒ 需要守门。
    ('历史原型页都有「这是历史版本」横幅（A7）',
     ['tools/check_legacy_banners.py']),
    # J7：单文件 HTML 导出。**用 `file://` 打开**（与用户双击同一条路径），
    # 第②组断言**零外部请求** —— 那是"单文件"这个承诺的唯一可验证形式。
    # ⚠️ 它需要先跑 `tools/export_single_html.py` 生成产物（产物在 output/，不入库）。
    ('单文件 HTML 导出（J7：**file:// 打开** · 零外部请求 · 可点证据）',
     ['tools/cdp_verify_export_html.py']),
    # L16：立场聚合。**第③④组是"立场是数出来的"那句主张的可核对形式** ——
    # 页面上的 S/O/T 必须与数据逐字相等，进度条宽度必须与 S/T 一致。
    ('立场聚合（L16：**S/O/T 与数据逐字相等** · 条形一致 · 可回溯）',
     ['tools/cdp_verify_stance.py']),
    # L17：结构化字段表。**第④组最关键** ——
    # 导出内容必须与"屏幕上打开的那几列"一致（那是 Elicit `Extract Data` 的同款动作）。
    ('论文对比表（L17：字段摊平 · 列开关 · 导出与屏上一致）',
     ['tools/cdp_verify_paper_table.py']),
    # K12：界面上的「当场跑一遍」。**第③组是端到端**（点按钮 → 真跑 → 出结果），
    # 第④组更关键：**后端不可用时必须说"没跑"，而不是"全过了"**。
    ('当场跑一遍评测（K12：**端到端真跑出结果** · 没跑≠全过）',
     ['tools/cdp_verify_run_verify.py'], 'slow'),
    # H8：E2E 技术栈守门。§3.2 纪律 2 要求 Playwright，而存量有 20 个自建 CDP。
    # **老的不重写**（功能等价、全绿），但**不许新增** —— 这条断言把它钉住。
    ('E2E 技术栈（不许新增「自建 CDP」，新脚本一律 Playwright）',
     ['tools/check_e2e_stack.py']),
    # H9：问题→子图→3D 高亮。**第⑥组是端到端那根线** ——
    # 从 ask.html 点按钮跳到主视图、并真的高亮起来。
    # 分两半测（"参数解析对" + "页面能高亮"）都证明不了它接上了。
    ("问答→3D 高亮联动（**端到端点按钮跳过去真高亮** · 压暗落到渲染 · 缺失如实报）",
     ["tools/cdp_verify_qa_to_3d.py"]),
    ("证据深链 PDF 高亮（pdf.js 自绘，可断言）", ["tools/cdp_verify_pdflink.py"], "slow"),
]

# 纯数据侧的检查：不依赖浏览器，快
DATA_CHECKS = [
    ("本体口径一致性（唯一来源自检）", ["tools/check_ontology_consistency.py"]),
    ("文档数字与产物一致性", ["tools/check_doc_numbers.py"]),
    # J6：命令行工具。**它是 CLI，不是页面** ⇒ 不进上面那组 CDP 检查，
    # 而是"把每个子命令真跑一遍"（六组断言，含 `--no-llm` 与 `--remote` 的失败路径）。
    # ⚠️ 这一条**必须放在数据侧**：它只需要图快照，不需要浏览器，也不调模型。
    ('命令行工具 kg（J6：六个子命令真跑 · **--no-llm 不调模型** · 连不上要说清）',
     ['tools/check_kg_cli.py']),
    # N6：前端不许**运行时**读仓库根的 `data/`（静态站只服务 `web/`，必然 404）。
    # 判据**只查真会发请求的地方**（`fetch`/`getDocument`/`new URL`/`src=`），
    # 不查说明文字与数据字段 —— 否则会扫出一堆假阳性。
    ('前端数据自足（N6：**不许运行时读仓库 data/**，除非有回退链）',
     ['tools/check_frontend_data.py']),
    # 内联 JS 的语法：一旦有语法错，整页白屏（boot() 根本不会执行），
    # 后果比一个数据错严重得多，而检测只要 node --check。
    # 实测我在 analytics.html 里连改四版括号才发现——当时 CDP 检查只报
    # "某元素不可见"，看不出是语法错。
    ("前端内联 JS 语法（防白屏）", ["tools/check_js_syntax.py"]),
    ("指标与数据快照的版本绑定（stale 检测）", ["tools/metric_provenance.py"]),
    # 产物之间的先后依赖：某个产物是不是在它依赖的上游之后生成的。
    # 与上面两项互补：那个答"数据变了没重算"，这个答"上下游顺序对不对"。
    ("数据新鲜度（产物晚于其依赖的上游）", ["tools/freshness_report.py"]),
    ("社区布局数据自检", ["tools/diag_comm.py"]),
    ("claims 诊断（矛盾视图是否成立）", ["tools/diag_claims.py"]),
    ("结构指标与随机图基线", ["tools/l8_structure_audit.py"]),
    # L5 域内效度：可自动化部分（表述敏感性 / 方向盲区 / 独立假阳性下界）
    # + 人工盲判进度追踪。它**不会**因为盲判没做完而失败——
    # "未完成"是一个需要如实报告的状态，不是错误。
    ("L5 域内效度（含盲判进度）", ["tools/check_l5_validity.py"]),
    # 标注者间一致性：这是"上面那些数字可不可信"的元证据。
    # 若 κ < 0.4，判定任务不可稳定复现，则 40.0% 只能报为"一次读数"。
    ("L5 标注者间一致性（结论可信度）", ["tools/check_l5_agreement.py"]),
    # L7 的 role_violation 到底是"约束太严"还是"谓词选错"——58 条独立判读的统计。
    # 这条决定了论文里能不能把它写成"端点类型违规率"（实测 58.6% 是谓词问题）。
    ("L7 角色违规归因（约束精度）", ["tools/check_l7_role_violation.py"]),
    # L9 关系召回：端点召回高（86.2%）**不代表**关系抽全了（实测约四成）。
    # 这是本项目最根本的一个读数。
    ("L9 关系召回（抽全了没有）", ["tools/l9_relation_recall.py"]),
    # 别名修正：严格匹配会把 `LLMs` vs `Large Language Model` 这类写法差异
    # 判成漏，从而低估召回。必须报区间而不是单点。
    ("L9 关系召回区间（别名修正）", ["tools/l9_relation_recall_corrected.py"]),
    # 产出构成：每段抽多少条、pass 率、"兜底关系 RELATED_TO" 占多少。
    # 它把"关系召回约一半"的解读限定住——金标本身列举不全，
    # 不能读成"图只覆盖了原文关系的一半"。
    ("L9 产出构成（多抽/兜底占比）", ["tools/l9_overextraction.py"]),
    # 漏抽归因：漏抽不均匀（集中在特定谓词与 2024+ 新论文），
    # 这决定了补救成本——只重抽新论文 6.2 小时 vs 全量 18.7 小时。
    ("L9 漏抽归因（补救路径）", ["tools/l9_miss_analysis.py"]),
    # 针对性重抽工具：默认 dry-run，只列出目标与耗时，不改任何数据。
    ("选择性重抽（dry-run，不改数据）", ["tools/re_extract_recent.py"]),
    # 本地重抽可行性：实测本地产出只有云端的 1/5–1/10 ⇒ 否决该方案，
    # 避免下一轮又去花几小时跑一个无效的重抽。
    ("本地重抽可行性（否决依据）",
     ["tools/local_reextract_feasibility.py", "--n", "1"]),
    # 离线可用性是「离线演示铁律」（内部记录 §1 第 6 条）的守门检查：
    # 主视图曾因 three.js 走 unpkg CDN 而断网白屏，现已本地化。
    ("主视图离线可用（屏蔽外部域名）", ["tools/cdp_verify_offline.py", "web/kg-nebula.html"]),
    # 依赖 CDN 的 5 个历史原型：断网时必须给出明确提示而不是白屏。
    ("历史原型断网提示（离线守卫）", ["tools/cdp_verify_net_guard.py"]),
    # 前后端连通：需要后端在 8000 跑着（tools/serve.py）。
    # 若后端没起，本项会明确报"后端不可用"而不是静默跳过。
    ("前后端连通 · API 优先 + 静态回退", ["tools/cdp_verify_e2e.py"]),
]


def run(label, args, timeout=300, retry_on=None):
    """跑一项检查。retry_on 给出"可重试"的错误特征——**只对偶发故障重试**。

    为什么需要：CDP 脚本要起一个无头 Chrome，实测偶尔会启动失败
    （`RuntimeError: CDP 未就绪`）。这种偶发失败如果直接算红，
    会让人对回归失去信任、进而忽略真正的红灯。所以只对**明确的偶发特征**重试一次，
    其他失败一律不重试（真问题必须仍然报红）。

    **K12（2026-09-16）**：每项结果都记进 `RESULTS`，供 `--json` 输出。
    为什么需要：`--json` 是给**界面上那个「当场跑一遍」按钮**用的
    （`/api/verify` → `analytics.html`）——
    **"老师问你怎么验证的，当场点一下跑出来"**，比"我跑过"强得多。
    """
    attempts = 2 if retry_on else 1
    for attempt in range(attempts):
        t0 = time.time()
        try:
            p = subprocess.run([sys.executable] + args, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, cwd=str(Path(__file__).parent.parent))
            out = (p.stdout or "") + (p.stderr or "")
            ok = p.returncode == 0
        except subprocess.TimeoutExpired:
            out, ok = "超时", False
        dt = time.time() - t0
        transient = (not ok) and any(k in out for k in (retry_on or ()))
        if ok or not transient or attempt == attempts - 1:
            break
        print(f"⏳ {label:38s} {dt:6.1f}s  偶发失败（CDP 未就绪），重试一次…")
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-1:] or [""]
    print(f"{'✅' if ok else '❌'} {label:38s} {dt:6.1f}s  {tail[0][:88]}")
    if not ok:
        print("   ── 失败输出末尾 ──")
        for ln in [x for x in out.strip().splitlines() if x.strip()][-8:]:
            print("   " + ln[:150])
    RESULTS.append({
        "label": label,
        "ok": bool(ok),
        "seconds": round(dt, 1),
        "detail": (tail[0] if tail else "")[:200],
        # 失败时留最后几行 —— 界面上要能直接看见**为什么红**
        "tail": ([x[:200] for x in out.strip().splitlines() if x.strip()][-6:]
                 if not ok else []),
    })
    return ok


RESULTS = []


def main():
    fast = "--fast" in sys.argv
    # K12（2026-09-16）：两个新开关，给**界面上的「当场跑一遍」**用。
    #
    # `--data-only`：**只跑数据侧**（`DATA_CHECKS`）。
    #   为什么界面只跑这一半：前端检查每一项都要**起一个无头 Chrome**，
    #   从后端进程里拉起来既慢又依赖桌面环境 ⇒
    #   **界面上要的是"快、稳、当场看得见"**，而那正好是数据侧这 19 项。
    #   （完整回归仍然在命令行跑，`--data-only` 不替代它。）
    #
    # `--json`：把每项结果打成 JSON（由界面解析渲染）。
    data_only = "--data-only" in sys.argv
    as_json = "--json" in sys.argv

    if not as_json:
        print("=" * 100)
        print("数据侧检查")
        print("-" * 100)
    for label, args in DATA_CHECKS:
        run(label, args)

    if not data_only:
        if not as_json:
            print()
            print("=" * 100)
            print("前端检查（每个都会起一个带 CDP 的 Chrome）")
            print("-" * 100)
        # 前端检查都要起无头 Chrome；"CDP 未就绪"是实测过的偶发启动失败，
        # 只对这一类重试（真失败仍然报红）。
        CDP_TRANSIENT = ("CDP 未就绪",)
        for item in CHECKS:
            label, args = item[0], item[1]
            slow = len(item) > 2 and item[2] == "slow"
            if slow and fast:
                if not as_json:
                    print(f"⏭  {label:38s}   (--fast 跳过)")
                continue
            run(label, args, retry_on=CDP_TRANSIENT)

    ok = sum(1 for r in RESULTS if r["ok"])
    if as_json:
        print(json.dumps({
            "passed": ok, "total": len(RESULTS),
            "all_ok": ok == len(RESULTS),
            "data_only": data_only,
            "results": RESULTS,
        }, ensure_ascii=False))
    else:
        print()
        print("=" * 100)
        print(f"通过 {ok} / {len(RESULTS)}")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
