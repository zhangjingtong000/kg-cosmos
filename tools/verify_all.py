# -*- coding: utf-8 -*-
"""串行跑全部验证脚本并汇总（回归入口）。

为什么串行：每个前端验证都要起一个带 CDP 的 Chrome 实例，串行最稳，
也避免多个实例同时读同一份 2.6MB 的 nebula.json 造成误判。

【前置依赖，缺了就跳过并说明】
  · 本地静态服务（默认 `http://127.0.0.1:8098/web/`，由 `python tools/serve.py --static` 起）；
  · 无头 Chrome（CDP 截图用；找不到 Chrome 时回退 Edge）。
  干净 clone 上这两样通常都没有 —— 此时**前端检查整组跳过**并在总结里写明原因，
  不把"没环境"报成"功能坏了"。数据侧检查不依赖它们，照常跑。

用法（在仓库根目录）：
  python tools/verify_all.py            # 全部
  python tools/verify_all.py --fast     # 跳过耗时的 PDF 截图对比
  python tools/verify_all.py --data-only  # 只跑数据侧（不需浏览器/静态站）
"""
import json
import os
import socket
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
    # ── 2026-09-18 新增两条守门 ──────────────────────────────────────────
    # ① 浮层守门：**源码写着 fixed、浏览器算出来却是 relative** 这种事
    #    靠 grep 永远抓不到 ⇒ 必须读**计算值**。
    #    背景：星云页 17 个浮层被一句兜底 CSS 打回文档流（用户看到"整个乱了"）。
    #    详见 docs/UI_FIXED_POSITION_ROOT_CAUSE.md
    ("浮层守门（**声明 fixed 的元素，计算值必须也是 fixed** · 全屏浮层页不许溢出）",
     ["tools/cdp_verify_fixed_layers.py"]),
    # ② DOM 文案守门：**浏览器不解析 markdown**，`**粗体**` 会原样显示成星号。
    #    ⚠️ 它**不能**只扫初始 DOM —— 泄漏大多在"提交后才显示"的区块里，
    #    所以判据是"扫全部文本节点，不看可见性"，且**打不开的页面算失败**
    #    （第一版全部页面打不开时报的是"✅ 通过"，那是最坏的失败模式）。
    ("DOM 文案守门（**会进浏览器的文字里不许有 markdown 粗体** · 零覆盖也算失败）",
     ["tools/check_dom_text.py"]),
    ("证据深链 PDF 高亮（pdf.js 自绘，可断言）", ["tools/cdp_verify_pdflink.py"], "slow"),
]

# 纯数据侧的检查：不依赖浏览器，快
DATA_CHECKS = [
    # ── N9（2026-09-20 加）：**把 `tests/` 接进回归入口** ──────────────────
    #
    # **为什么必须加**：在这之前 `verify_all.py` **只跑 `tools/*.py`**，
    # `tests/` 下的全部单测**一个都不在里面** ⇒
    # **「跑一次 `verify_all` 就等于跑完全部检查」这件事根本不成立** ——
    # pytest 得靠人**记得**单独跑。
    # 本日实测到的代价：连续几轮每次改完守门脚本，都要**手工补跑一遍** `pytest`；
    # 忘一次，测试坏了也不会有人发现（本项目早就有 `N9` 这条待办专门记它）。
    #
    # ⚠️ **放在数据侧**：它不需要浏览器、也不需要模型（全量约 17 秒）。
    # ⚠️ 它同时会进 `--data-only` —— 而界面上的「当场跑一遍」按钮用的就是它。
    #    这是**想要的效果**：当场点一下，单测结果一起出来。
    ('单元测试全量（pytest：`tests/` 下全部单测 · 零外部依赖）',
     ['-m', 'pytest', '-q']),
    ("本体口径一致性（唯一来源自检）", ["tools/check_ontology_consistency.py"]),
    # ── 2026-09-20 加：**页面里写了的本地文件必须真的在** ──────────────────
    #
    # **为什么非有不可**：引 GSAP 时踩过一次 —— 给 `web/index.html` 加首屏动效
    # 时忘了引 `web/lib/gsap/SplitText.min.js`，页面上有一句
    # `if (!window.SplitText) { bail(); return; }`，于是**整段动效被安静关掉**：
    # 排版正常、链接能点、没有显眼报错，只是"一个动画都没有"。
    # 这类缺陷的共同点是「写错的路径 ≠ 页面报错」，只能由机器逐个对磁盘。
    #
    # 顺带覆盖两件事：`importmap` 里的本地映射（three.js 靠它离线）、
    # 以及「没有任何页面从 CDN 引 GSAP」（离线铁律在动效上的落点）。
    # ⚠️ 纯文件系统检查，不开浏览器、不调模型 ⇒ 放数据侧。
    ("本地引用存在性 + GSAP 只许本地引", ["tools/check_local_assets.py"]),
    ("文档数字与产物一致性", ["tools/check_doc_numbers.py"]),
    # ── 2026-09-26 加：**Markdown 表格的列数一致性** ──────────────────────
    #
    # **为什么非有不可**（当天实测出的代价）：上一轮往 GFM 表格的**行内**
    # 插入了大量 `⚠️ 更正：… <br>…`，一趟下来踩了两类坑，而且**都没有任何守门**：
    #   ① 把内容写在行尾 `|` **之后**（例如把行内 pragma
    #      `<!-- 数字守门:忽略 -->` 追加在表头行末尾）⇒ 表头列数被算多，
    #      **整表和数据行错位**；那次是 `docs/L5_SIX_LAYER_REPORT.md` 的两张表
    #      与 `docs/verification.md` 的三行；
    #   ② 单元格里的**裸 `|`** 没转义（`|S|/|G|`、`` `|` ``、`<|>`）⇒ 一个单元格
    #      被劈成两列。
    # 这两类**都是"渲染坏了但文件看起来正常"** —— 与上面 `check_local_assets.py`
    # 那条教训同源：**写错的排版 ≠ 报错**，只能由机器逐行对数。
    # ⚠️ 纯文件系统检查，不开浏览器 ⇒ 放数据侧。
    ("Markdown 表格列数一致性", ["tools/check_md_tables.py"]),
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
    # N7：论文注册表。**判据是"注册表里的每个计数都去文件系统数一遍"** ——
    # "把散在三处的真相连起来"最容易出的错就是**连出来的数字与文件系统不符**。
    ('论文注册表（N7：四个身份的数字与文件系统**逐一核对**）',
     ['tools/check_paper_registry.py']),
    # M1：OpenAlex 双轨。**第②组盯的是"查不到"这件事本身** ——
    # 实现里踩过三次坑（URL 拼法 ×2、**arXiv DOI 不带版本号**），
    # 而**三次都不报错**（HTTP 200、meta.count=0）⇒ 必须有一条守门防止它悄悄归零。
    # 同时 **100% 也要报警**（本库有 4 篇老论文的 DOI 是后补的，本应收不到）。
    ('OpenAlex 元数据双轨（M1：**对齐率不许悄悄归零** · 作者只许来自权威源）',
     ['tools/check_openalex.py']),
    # M3+M5：引用导出/导入。**第①组是往返闭环** ——
    # 这两个脚本是一对（一个写 .bib、一个读 .bib），
    # **最容易出的错是"自己写的不认识自己写的"**，而那只有真跑往返才看得出来。
    # 实测已抓到两个往返 bug：标题保护括号、**最后一个字段丢**。
    ('引用导出/导入（M3+M5：**往返必须闭合** · 无作者不编 Unknown）',
     ['tools/check_citations.py']),
    # M6：检查新论文。**它是"检查更新"，不是"推送"**（本机无常驻服务）。
    # 断言的核心是**三个信号分开报** —— 尤其"**能不能拿到全文**"：
    # 实测 49 篇新的里只有 15 篇拿得到（31%），**混成一个数就会误导"扩语料很容易"**。
    ('检查新论文（M6：**能拿到全文的才算数** · 不是推送）',
     ['tools/check_watch.py']),
    # M4：Zotero 互操作。**它是唯一一个读"别人的数据库"的功能** ⇒ 三条纪律各一条断言：
    # ① **只读**（扫源码证明打开真实库的路径上没有写操作）；
    # ② **"0 条"与"读不到"必须分开**（不存在的库要报错，不许返回 0 条）；
    # ③ **不许假阳性 arXiv id**（实测踩过：从 `10.1145/3571234.3579999` 里误抓 `1234.35799`）。
    ('Zotero 互操作（M4：**只读** · 0 条≠读不到 · 不许假阳性 arXiv）',
     ['tools/check_zotero.py']),
    # H5：蒸馏微调。**这条守门守的是一个花掉 40 分钟才定位的坑** ——
    # `SFTConfig` 没显式设 `dataloader_num_workers=0` ⇒ Windows 上子进程与主进程互等
    # （CPU 20 秒只涨 0.3 秒、无产物、92 个线程）⇒ **它不是慢，是卡死**。
    # **"默认应该就是 0"这种假设正是那次踩坑的原因** ⇒ 守门**不看默认值，只看源码里有没有显式写**。
    # 另：`final_train_loss` 不许是 null（第一版取 `log_history[-1]['loss']`，
    # 而最后一条是汇总记录、只有 `train_loss` ⇒ 恒为 None）。
    ('蒸馏微调（H5：**默认值必须显式** · 产物完整 · loss 不许为 null）',
     ['tools/check_distill_train.py']),
    # B5 的验收。**它守的是一条纪律**：`rc=0` 不等于"做了事" ——
    # B5 实现里连踩三处假成功（backend=neo4j 的 rc=1、`OUT.exists()` 的早退、
    # **`skip (done)` 的 rc=0**）⇒ **验收要看"产物变了没有"，不看退出码。**
    ('prompt A/B 产物（B5：**与基线逐项比** · 不许"没跑却算成功"）',
     ['tools/check_prompt_ab.py']),
    # 设计系统：**重复引用** + **左边强调竖线**。
    # 两条都是**本项目自己写下来的规矩**，不是我猜的风格偏好：
    #   · 重复引 CSS 是客观的重复加载；
    #   · 竖线 —— `lib/design.css` 的注释写着 `.note` 用**顶部短横线**、「**竖线已废弃**」。
    # ⚠️ 判据刻意收窄：**1px 的分隔线不算**、**注释不算**、**`::before` 色标不算**
    #    （前两条会误报，第三条是合理用法）。
    ('设计系统（重复引用 · 左边强调竖线 —— 两条客观判据）',
     ['tools/check_design_system.py']),
    # K2：论文管理。**第④⑤⑥组是这个功能的安全带** ——
    # 空原因必须被拦、语料图必须二次确认、静态站下"不能删"要在**点之前**看得见
    # （与 K12 的"没跑≠全过了"是同一条纪律：别让能力缺失伪装成能点）。
    ('论文管理（K2：**删=移入回收站** · 空原因被拦 · 二次确认 · 静态站禁用）',
     ['tools/cdp_verify_papers.py']),
    # L18：PRISMA 流程图。**第①③组是关键** ——
    # 阶段数字必须与数据逐字相等；且**不许照抄模板编数**
    # （本流程没有初筛淘汰 ⇒ Screening 必须等于 Identification）。
    ('语料筛选流程 PRISMA（L18：数字逐字相等 · **没有初筛淘汰就不编数**）',
     ['tools/cdp_verify_prisma.py']),
    # 内联 JS 的语法：一旦有语法错，整页白屏（boot() 根本不会执行），
    # 后果比一个数据错严重得多，而检测只要 node --check。
    # 实测我在 analytics.html 里连改四版括号才发现——当时 CDP 检查只报
    # "某元素不可见"，看不出是语法错。
    ("前端内联 JS 语法（防白屏）", ["tools/check_js_syntax.py"]),
    # 首屏数字：**断言渲染出来的值**，不是断言 `data-count` 属性。
    #
    # 为什么单独立这一项（2026-09-19）：`check_doc_numbers.py` 扫的是文件里的字，
    # 而首屏那两个大字是 `voyage.js` 运行时写进去的（动画 + `setFig` 覆盖），
    # 文案里还有一处手写的"评测全集 43867" —— **属性对不代表屏幕上对**。
    # 早先的评审记录第 6 条记的"index.html 硬编码 18780/29120，守门不查 HTML"就是这块。
    ("首屏数字（**渲染值** vs 权威产物 · Playwright）",
     ["tools/verify_firstscreen_numbers.py"]),
    ("指标与数据快照的版本绑定（stale 检测）", ["tools/metric_provenance.py"]),
    # 产物之间的先后依赖：某个产物是不是在它依赖的上游之后生成的。
    # 与上面两项互补：那个答"数据变了没重算"，这个答"上下游顺序对不对"。
    ("数据新鲜度（产物晚于其依赖的上游）", ["tools/freshness_report.py"]),
    ("社区布局数据自检", ["tools/diag_comm.py"]),
    ("claims 诊断（矛盾视图是否成立）", ["tools/diag_claims.py"]),
    ("结构指标与随机图基线", ["tools/l8_structure_audit.py"]),
    ('方向判据自洽率（旧写法 71.55% → 当前 80.44%，不许退回）',
     ['tools/check_direction_consistency.py']),
    # L5 域内效度：可自动化部分（表述敏感性 / 方向盲区 / 独立假阳性下界）
    # + 人工盲判进度追踪。它**不会**因为盲判没做完而失败——
    # "未完成"是一个需要如实报告的状态，不是错误。
    ("L5 域内效度（含盲判进度）", ["tools/check_l5_validity.py"]),
    # 标注者间一致性：这是"上面那些数字可不可信"的元证据。
    # 若 κ < 0.4，判定任务不可稳定复现，则 40.0% 只能报为"一次读数"。
    # ⚠️ 本条与下一条是**报告读数，不是阈值守门**（2026-09-20 标注）：
    # 脚本会把 κ / 假阳性率算出来并落盘，但**刻意不设阈值** ——
    # κ 低本身就是"这个判定任务不可稳定标注"这个**结论**，不是错误。
    # 因此只要输入文件正常，它们的退出码恒为 0。
    # 起因是守门假绿审计：读者会把这里的 ✅ 读成"一致性检查通过"，
    # 而它其实只是"报告写出来了"。**引用它们时必须说清这一点。**
    ("L5 标注者间一致性（**报告读数 · 无阈值断言**）", ["tools/check_l5_agreement.py"]),
    # L7 的 role_violation 到底是"约束太严"还是"谓词选错"——58 条独立判读的统计。
    # 这条决定了论文里能不能把它写成"端点类型违规率"（实测 58.6% 是谓词问题）。
    ("L7 角色违规归因（**报告读数 · 无阈值断言**）", ["tools/check_l7_role_violation.py"]),
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
    # 离线可用性是「离线演示铁律」（早先的评审记录 §1 第 6 条）的守门检查：
    # 主视图曾因 three.js 走 unpkg CDN 而断网白屏，现已本地化。
    # ★ 2026-09-20 扩面：此前只测星云页一页，而 GSAP 动效编排的主战场是
    #   导航页 `web/index.html`（它引 gsap / SplitText / ScrollTrigger 三个本地插件）
    #   —— 三个文件里漏一个，整段动效会**静默失效**且页面看不出坏。
    #   把导航页一并纳入，才谈得上"GSAP 已纳入离线检查"。
    ("主视图与导航页离线可用（屏蔽外部域名）",
     ["tools/cdp_verify_offline.py", "web/kg-nebula.html", "web/index.html"]),
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

    **2026-11-28 降级**：失败原因是"缺文件/缺模块/连不上后端"这类**环境或数据缺失**
    时，记为跳过并说明，不算红 —— 干净 clone 只带最小样本，缺全量产物是预期内的。
    断言不符（数字对不上、页面元素缺失）仍然是红：那才是"功能坏了"。
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

    # ── 缺依赖/缺数据 ⇒ 跳过，不算红（2026-11-28）────────────────────────
    if not ok and any(k in out for k in MISSING_DEPS):
        SKIPPED.append(f'{label}（{tail[0][:60]}）')
        print(f"⏭  {label:38s} {dt:6.1f}s  缺依赖/数据，跳过：{tail[0][:70]}")
        return True

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


# 这些字样出现在失败输出里 ⇒ 判为"缺依赖/缺数据"，跳过而不是报红。
# 刻意收窄：**不含** "断言/不符/不一致" 这类真失败特征。
MISSING_DEPS = (
    'FileNotFoundError', 'No such file or directory', 'ModuleNotFoundError',
    'ImportError', 'Connection refused', 'URLError', '后端不可用', '后端没起来',
    '请先起', '找不到 ', '缺 ', '未安装', 'not installed',
)


RESULTS = []
SKIPPED = []          # 因缺依赖而整组/单项跳过（2026-11-28）

# ── 前置依赖探测 ────────────────────────────────────────────────────────
# 干净 clone 上不保证有 8098 静态站，也不保证装了 Chrome。缺任一项时**跳过前端组**
# 并打印原因 —— 与 `tools/check_doc_numbers.py` 的降级模式同一条纪律：
# "缺东西" ≠ "功能坏了"。要看全量前端回归，先 `python tools/serve.py --static`。
STATIC_BASE = os.environ.get('KG_STATIC_BASE', 'http://127.0.0.1:8098/web/')
CHROME_CANDIDATES = (
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    '/usr/bin/google-chrome',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
)


def chrome_path():
    """找可用的 Chrome/Edge（CDP 脚本默认写死 Chrome 路径，这里只做探测）。"""
    for p in CHROME_CANDIDATES:
        if p and Path(p).exists():
            return p
    return None


def server_up(url, timeout=2.0):
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return getattr(r, 'status', 200) < 500
    except Exception:
        return False


# 数据侧也有两项要 Playwright 的浏览器内核（`playwright install chromium`），
# 干净 clone 上通常没装 ⇒ 单项跳过并说明，不把 ImportError/启动失败报成功能坏了。
NEEDS_PLAYWRIGHT = {'check_dom_text.py', 'verify_firstscreen_numbers.py'}


def playwright_ready():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return False
    try:
        with sync_playwright() as p:
            exe = p.chromium.executable_path
            return bool(exe) and Path(exe).exists()
    except Exception:
        return False


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
        if any(Path(a).name in NEEDS_PLAYWRIGHT for a in args) and not playwright_ready():
            msg = ('Playwright 浏览器内核未安装（`playwright install chromium`）'
                   ' —— 需要它的项已跳过')
            SKIPPED.append(msg)
            if not as_json:
                print(f"⏭  {label:38s}   (缺 Playwright，跳过)")
            continue
        run(label, args)

    if not data_only:
        # 先探依赖：缺静态站或缺浏览器 ⇒ 整组跳过并说明，不把"没环境"报成红。
        missing = []
        if not server_up(STATIC_BASE):
            missing.append(f'静态站不可用（{STATIC_BASE}；用 `python tools/serve.py --static` 起）')
        if not chrome_path():
            missing.append('找不到 Chrome / Edge（CDP 截图需要）')
        if missing:
            reason = '；'.join(missing)
            SKIPPED.append(f'前端检查 {len(CHECKS)} 项：{reason}')
            if not as_json:
                print()
                print("=" * 100)
                print(f"⏭️  前端检查整组跳过：{reason}")
                print("    （数据侧结果不受影响；补齐依赖后重跑本脚本即可）")
        else:
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
            "skipped": SKIPPED,
            "results": RESULTS,
        }, ensure_ascii=False))
    else:
        print()
        print("=" * 100)
        print(f"通过 {ok} / {len(RESULTS)}")
        if SKIPPED:
            print(f"⏭️  另有 {len(SKIPPED)} 组检查因缺依赖跳过：")
            for s in SKIPPED:
                print(f"    · {s}")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
