# 变更日志（CHANGELOG）

> **怎么读这份文件**：它**不是 `git log` 的复制**（那有 400+ 条）。
> 它按**阶段**整理，每个阶段只写"做了什么、关键产物是什么、
> 以及踩过什么坑"—— 因为**踩过的坑才是这个项目最值钱的部分**。
>
> 完整提交历史用 `git log` 看；`docs/ERROR_CASES.md` 收录了按主题整理的错误案例。

---

## v0.5 · 公开发布整理（2026-11-27 ~ 11-28）

**做了什么**：
- **安装链修好**：`requirements.lock` 里 `torch==2.6.0+cu124` 改成 `torch==2.6.0`
  （`+cu124` 本地版本标签只在 PyTorch 官方索引有，默认 PyPI 没有 ⇒ 干净 clone 上
  `pip install -r requirements.lock` 第一步就失败）。README 补上
  `--extra-index-url https://download.pytorch.org/whl/cu124` 的写法，并在顶部给出
  「只想看界面」的零依赖路径 `python tools/serve.py --static`。
- **守门脚本降级**：`tools/check_doc_numbers.py` 不再硬依赖全量产物
  （`web/kg-data/corpus-index.json` / `paper-registry.json` / `diff.json`、
  `docs/已移除的旧版手册`）；缺了只跳过对应检查项并打印「缺 X 份全量产物，已跳过 Y 项」，
  退出码仍为 0。`tools/verify_all.py` 缺 8098 静态站或无头 Chrome 时跳过前端检查并说明，
  文件头不再留着写死盘符的 `cd` 路径。
- **首图重做**：社区星云的概览标签不再显示"社区内最多的类型"—— 那会把
  会议/期刊社区标成 `Person`（「ACL 1329 实体 · Person」）。类型分布移到右侧详情面板并
  标成"成员类型"，首图按 1600×1000 重新截图。
- **页面收敛**：`web/` 顶层从 38 个 HTML 收到 8 个主页面（六步演示动线 + 导航 + 单图查看器），
  其余 26 个并行版本/旧入口移入 `web/legacy/`（带历史横幅，索引 `web/legacy/index.html`），
  5 个零引用 `_lab_*` 试验页删除。
- **文档换气**：README 里约 40% 的测试 skip 来龙去脉移到 `docs/TESTING.md`，
  README 只留「怎么跑 + 结果 + 已知边界」；`.env.example` 去掉"余额只剩 ¥3""本机 8GB 显存"
  这类内部语境；仓库体量数字改为现场实测。

**坑**：
- 搬迁改相对路径时把 `./temporal.html` 这类**同类页链接**也改成了 `../`，
  一搬就断；靠 `tools/check_local_assets.py` 扫 `web/legacy/` 抓回来（现已纳入扫描范围）。
- 守门脚本"缺东西"与"数字错了"是两件事：前者该降级跳过，后者才该红。
  混在一起的代价是干净 clone 上永远一片红，红久了就没人看。

---

## v0.5.1 · 三个 P0 修复（2026-11-28）

**做了什么**：
- **诚信口径**：README 与 `docs/L5_SIX_LAYER_REPORT.md` 把「200 条分层抽样的
  **两人盲判**」「与**人判** κ」改成「**两次 LLM 盲判（不同会话）**」「与 **LLM 标签** κ」。
  依据是 `data/quality/l5-annotator-agreement.json` 的 `note` 自己写着「两个标注者
  都是 LLM（不同会话）」—— 此前 README 的措辞与自家数据直接矛盾。
  README 顶部补一句「研究原型：评测标签由 LLM 生成，不是人工金标」。
- **死代码**：`src/kg_extractor.py` 的 `_load_registry_env()` 定义完 `locations` 就结束，
  扫注册表的 `for` 循环被缩进到了 `relation_types_block()` 的 `return` 之后 ——
  既永远不执行（兜底静默失效），又引用未定义的 `locations`/`BLOCKED`/`winreg`。
  循环移回本函数，新增 `tests/test_registry_env.py`（3 条：真补变量、不覆盖已有值、
  结构上不许再回到 `return` 之后）。
- **Windows 第一条命令**：`python tools/serve.py --static` 在 cp936 控制台打印 `✅` 时
  `UnicodeEncodeError`（README 第 80 行才提 `PYTHONUTF8=1`，比命令晚 70 行）。
  `serve.py` 现在探测 `sys.stdout.encoding`，非 UTF-8 时把 `✅/⚠️/❌/🔒` 换成
  `[OK]/[!]/[X]/[锁]`——不改检查逻辑，也不要求用户改环境。
- **`verify_all.py --fast`**：Playwright 的依赖探测从「手写 2 个脚本名」改成按源码扫描
  （实测有 3 个脚本 `import playwright`，漏掉的那个必然报 ❌），并识别
  「库装了、chromium 没下载」的报错特征，按**跳过**处理而不是报红。
- **仓库卫生**：20 个「被 `.gitignore` 排除却已被跟踪」的文件 —— 逐个查证后都是
  页面/文档/脚本的运行时依赖（11 个字体、`web/l5-data/` 4 个、3 份治理产物），
  所以**改忽略规则而不是 `git rm --cached`**；`D:\kg-demo` 与 `E:\Python313\python.exe`
  的硬编码清零（改成 `__file__` 锚点 / `python`），数据产物里的历史绝对路径改仓库相对路径。

**坑**：
- `git add -A` 把本地跑出来的 `verify_fast.log` 一起提交了 —— 已经移除并改为不跟踪。
  仓库里"跑一遍就多一个被跟踪文件"的坑，与上面那 20 个忽略冲突是同一类问题。
- 跑 `tools/verify_all.py` 会重写 `data/quality/l8-structure-audit.json` 等**被跟踪的产物**
  （by design 的"报告读数"脚本），干净 clone 上会把全量读数覆盖成小样本读数；
  验证完必须 `git checkout` 回去，否则提交里会混进假数字。

---

## v0.4 · 多 Agent 问答与研究点（2026-09-15）

**做了什么**：
- **多 Agent 问答**真正落地：`src/multi_agent_qa.py` ——
  检索（纯规则 BFS）→ **支持 / 反对独立取证（互不可见）** → 综合，
  **每条证据带回溯到论文原文的 `source_name` + `page` + `quote`**；
  后端新增 `POST /api/qa` 与 `/api/qa/step`（分步，让"多 Agent 在分工"在界面上看得见）；
  `web/ask.html` 从硬编码预设问答改为**真提问**，并修掉"默认问另一篇论文"的闭环断裂。
- **外部对照评测**：用 **SciREX**（AllenAI，人工标注，2148 条槽位式关系）
  做首个**非自指**的对照 —— 实体 **F1 0.4965**、**类型命中 70/70**。
- **商业对标**：读完 **Neo4j Bloom / Microsoft GraphRAG / Scite / Zotero /
  Elicit / Consensus / ResearchRabbit / Litmaps** 等 110+ 个官方页面，
  产出功能矩阵与"它们自认的局限"清单。
- **规划文档体系**：`TODO.md`（151 项）· `IMPLEMENTATION_PLAN.md` ·
  `INNOVATION_AUDIT.md`（研究点核对）· `REVIEW_ROUNDUP.md`（4 个独立评审的收口）。
- **5 个批次**的实施计划 + **4 个并行评审**（线一可见性 / 线三正确性 /
  对抗性遗漏审计 / 广度线可行性）。

**坑**：
- **安全**：`tools/serve.py --static` 的根目录是仓库根 ⇒
  **一条 URL 就能下载 `data/quality/l5-validity-key.json`**（会让 L5 人工盲判作废）。
  第一版修法改成 `ROOT/'web'`，**结果打断了 14 个 CDP 脚本**（回归红 2 项）；
  第二版改成**精确拦截敏感路径**才两头兼顾。
- **文档数字**：`L5_SIX_LAYER_REPORT.md` 的「94.4%（14034/15123）」**率和分数取的是两个口径**
  （实际 92.8%）；「914 倍 vs 597 倍」查明是**新图 18780 与旧图 15116** 的差异。
- **对外说明**写着「JSON Schema 约束解码（结构可靠）」，
  而**该能力在生产路径从未接入**（`call_ollama_json` 有 `schema` 参数但三处调用全不传）。
- **`.env` 机制缺失**：项目从不加载 `.env`，所以 `.env.example` 拷了也不生效。

---

## v0.3 · 知识图谱管线与评测（2026-09-07 ~ 09-14）

**做了什么**：
- **完整抽取管线**：PyMuPDF 解析 → 分块（500 字符）→ 逐段 LLM 抽取 →
  实体消歧 → 端点归一 → 入 Neo4j。**纯文本入口** `extract_graph(text=...)`
  让外部数据集（SciREX）能在同一条管线上跑。
- **九层机器可验证评测（L1–L9）**：零人工、零 API 成本、可重跑。
- **数据治理**：从 44172 条边清到 22892（带审计轨迹）、
  `entity_filter`（纯规则噪声过滤）、`entity_absorb`、`direction_witness`（方向见证通道）。
- **消歧判据 v6**：加上首字母缩写通道 + 长度保护，并与 WRatio 对照
  （Neo4j 官方对 `UI`/`build graph.py` 给 0.900 误合，我们的长度保护给 0.286 正确拒绝）。
- **本体 16 类**（补上 `PUBLISHED_IN`）+ 四份拷贝的一致性自检。

**坑**：
- **跨指标误比**：把 `macro_f1 0.1724` 与「全猜基线 accuracy 0.22」相比 ——
  两个口径不同。同口径是一致率 25.73% vs 22.00%（**+3.73pp**）；
  全猜的 macro-F1 约 **0.024**。
- **小样本过拟合**：`entity_absorb` 放宽单词阈值后，小样本 F1 0.3941→0.3988，
  但**全库涨到 3961 对（21.09%）**，含 `Adversarial → Self-Adversarial` 这类错误。已回退。
- **本体扩展漏了存储层**：`graph_store.ALLOWED_RELATION_TYPES` 停在 15，
  导致新增的 `PUBLISHED_IN` 被 `safe_relation_type` **静默改写成 `RELATED_TO`**，扩展的数据增益为 0。

---

## v0.2 · 图探索与视觉统一（2026-08）

**做了什么**：
- 3D 社区星云（Louvain 60 社区 + PageRank 排序 + 标签预算）、
  PathQA 问题子图、分析面板、`l5-review` 盲判界面。
- **共享设计系统** `web/lib/design.css` + 分级光场 `atmosphere.js`；
  **无障碍地基** `a11y.js`（跳过导航、返回链接、对比度）。
- **证据深链**：pdf.js 面板 + 片段高亮（`openPdf(src, page, snippet)`）。

**坑**：
- **不要按名字杀浏览器**：调试截图脚本时 `taskkill /F /IM chrome.exe`
  把用户自己的浏览器关掉过**三次**。此后改为只终止自己 `Popen` 的 PID
  + 隔离的 `--user-data-dir`。
- **GPU 过载**：Ollama 5.11 GB + CUDA 评测 + 7 个 agent + Chrome ×9 + 模拟器
  同时跑，导致 **`VIDEO_DXGKRNL_FATAL_ERROR(0x113)` 蓝屏**。
  此后所有 workflow 都加了硬性资源纪律。

---

## v0.1 · 3D 可视化原型（2026-07-12 ~ 07-31）

**做了什么**：Three.js 三维图谱渲染原型、力导向布局、玻璃节点材质、
导航页与上传页的初版。

**起点**：`2026-07-12 Initial commit: 3D knowledge graph visualization`。

---

## 关于这份日志的一句说明

**这个项目最值得记录的不是"加了什么功能"，而是"发现了什么行不通"**：

- **关系层人工标注不可行** —— 四个模型标注者对同一批关系的 Krippendorff
  **α = −0.0119**（实体层却有 0.2312）；**200 个关系单元里 135 个只有一票**；
- 但 **SciREX 的 2148 条人工标注的槽位式关系**说明：
  **不是"关系不可标注"，而是可标性取决于协议的结构化程度**；
- **L3 端点完整性 68.4%** 是自己找出来的最大短板，且**有明确的结构表现**
  （3036 个孤立节点、度数中位数只有 1）。

**这三条比任何功能清单都更能说明这个项目做了什么。**
