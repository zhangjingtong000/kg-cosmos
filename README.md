# 文献知识图谱探索系统（KG Cosmos）

基于 **Python FastAPI + LLM 抽取 + Neo4j + Three.js 3D 可视化** 的文献知识图谱自动构建与多尺度探索系统：上传 PDF 文献，自动抽取实体与关系（每个三元组携带原文页码级证据），存入 Neo4j，并在自研 3D「知识宇宙」中探索——星云总览、社区下钻、证据直达原文页。

本项目为本科项目（含论文）配套交付物。设计原则：**把"可信"做成一等公民**——图谱中的每一条陈述都可回溯到 PDF 原文的具体页码与文本块；把"规模"交给架构——单视图实体数有界（星云聚合 + 详情限流），而非堆渲染性能。

> 状态：核心管线与可视化已完成并实测，高级功能按 [EXECUTION_READINESS.md](docs/EXECUTION_READINESS.md) 里程碑推进中。

---

## 1. 核心特性

- **文档 → 图谱全自动**：PyMuPDF 表格感知分块 → LLM 抽取（15 实体类型 / 15 关系类型，JSON Schema 约束解码）→ 去重合并 → Neo4j 入库，全过程无人工干预
- **证据回链**：每个实体/关系携带 `{来源文献, 页码, 文本块}` 证据链，点节点可跳 PDF 原文页并高亮对应文本块（规划中）
- **双通道抽取**：本地 Ollama（qwen3.5:9b，离线可用）/ 云端 DeepSeek + SiliconFlow 视觉模型，同一管线可切换
- **蒸馏就绪**：所有抽取调用可录制为 JSONL（`KG_RECORD_DIR`），云端通道输出即 QLoRA 蒸馏训练集，零额外标注
- **Neo4j 持久化**：图谱写入 Neo4j（证据合并为原子 Cypher），服务重启后仍在；NetworkX 后端作为离线兜底
- **3D 知识宇宙**：自研 liquid glass 玻璃节点 shader + 双层发光管状连线 + BFS 径向聚焦重排 + 拖拽回弹，CSS2D 标签
- **多尺度探索**：星云总览（Louvain 社区聚合，宇宙网丝状体表达跨主题关联，强/中/弱分级显示）→ 点击下钻实体局图
- **性能有实测**：真实页面同会话成对矩阵（23 组合 × 环境门控），高保真安全量级有数据结论
- **可回归的测试基线**：32 条 pytest（含连真实 Neo4j 的集成测试）+ 真实页面 Playwright 性能驱动器

## 2. 技术栈

| 层 | 选型 | 状态 |
|---|---|---|
| PDF 解析 | PyMuPDF（表格感知分块，页级图片提取） | ✅ |
| LLM 抽取 | 本地 Ollama qwen3.5:9b + qwen3-vl:4b（视觉）；云端 DeepSeek-V3 + SiliconFlow Qwen3-VL-32B | ✅ |
| 约束解码 | JSON Schema 结构化输出 + 截断修复（预测预算耗尽时按最长可解析前缀抢救） | ✅ |
| 图存储 | Neo4j 5 Community（Docker/WSL）+ NetworkX 兜底双后端 | ✅ |
| 后端 | FastAPI + Uvicorn（/upload、/graph、/graph/{id}/explorer、/chat、/stats） | ✅ |
| 前端 | 原生 HTML/JS + Three.js 0.160（自定义 shader、CSS2DRenderer、OrbitControls） | ✅ |
| 向量语义 | Ollama bge-m3 嵌入（同词异义检测、语义召回，规划中） | 🔲 |
| 蒸馏微调 | QLoRA（unsloth）4B 学生模型，云端输出蒸馏 | 🔲 管道就绪 |
| 性能工程 | Playwright 真实页面驱动器 + PowerShell 环境门控 + 帧时间采样 | ✅ |
| 测试 | pytest 32 条（含 Neo4j 集成）+ web/benchmark 单测 | ✅ |

## 3. 系统架构

```
PDF 上传
   │
   ▼
┌────────────────────────────────────────────────────┐
│ 抽取管线（src/pipeline.py，API 与批量入库共用）          │
│  parse_pdf → chunk_document（表格感知）               │
│  → extract_chunk ×N（LLM 抽取，双通道可切换）          │
│  → deduplicate_entities（大小写归一 + 模糊合并）        │
│  → 边去重 + 证据合并 merge_evidence                   │
└──────────────┬─────────────────────────────────────┘
               ▼
┌──────────────────────────┐   ┌─────────────────────┐
│ Neo4j（持久化，证据原子合并）│   │ JSON 快照（单图视图）  │
└──────────────┬───────────┘   └──────────┬──────────┘
               ▼                          ▼
┌────────────────────────────────────────────────────┐
│ FastAPI：/upload · /graph/{id} · /explorer · /chat   │
└──────────────┬─────────────────────────────────────┘
               ▼
┌────────────────────────────────────────────────────┐
│ 3D 前端（Three.js）                                  │
│  · index3d-bounce.html —— 实体局图（玻璃节点/发光边）   │
│  · prototype-semantic-filaments.html —— 星云总览      │
└────────────────────────────────────────────────────┘
```

## 4. 核心设计

- **证据是一等公民**：抽取的每个实体/关系绑定 `{source_id, source_name, page, chunk_idx, text}`；Neo4j 中证据以 JSON 列表属性存储，重复合并在单条 Cypher 内原子完成（列表去重，无读改写竞态）
- **双后端同接口**：`GraphStore` 统一封装 NetworkX / Neo4j，两端点大小写归一行为一致（有对照测试保证）；Neo4j 不可达时自动回退，保住离线演示
- **约束解码 + 截断修复**：本地抽取走 JSON Schema 结构化输出；预测预算耗尽的截断输出按最长可解析前缀抢救（已完成记录保留、残缺尾部丢弃），单元测试固化
- **性能边界有数据**：真实页面矩阵实测——≤80 节点高保真交互全程 60fps 预算内，120 节点静态可用，200 节点必须降保真；成本集中在拖拽全量边刷新（优化靶点已锁定）。多尺度架构（聚合总览 + 限流下钻）正是绕开该边界的答案
- **环境门控纪律**：所有正式性能数据必须来自同会话成对矩阵，CPU/GPU/内存漂移超 10% 样本作废（历史上作废过一轮，纪律有先例）

## 5. 数据模型

- **实体（15 类）**：Concept / Method / System / Component / Process / Artifact / Dataset / Metric / Tool / Material / Person / Organization / Location / Event / Document
- **关系（15 类）**：PART_OF / USES / PRODUCES / MEASURES / EVALUATES / COMPARES_WITH / SUPPORTS / CONTRADICTS / CAUSES / ENABLES / APPLIES_TO / LOCATED_AT / CREATED_BY / CITES / RELATED_TO
- **证据**：节点与关系均携带 evidence 列表；Neo4j 中同源证据自动合并去重
- **语料元数据**：arXiv id 即年份键（YYMM 编码），支撑时间切片与首创性对质

## 6. 功能清单（与代码同源核实）

| 功能 | 状态 |
|---|---|
| PDF 解析 + 表格感知分块 + 页级图片提取 | ✅ |
| LLM 抽取（15+15 本体，双通道，约束解码，截断修复） | ✅ |
| Neo4j 持久化（证据原子合并）+ NetworkX 兜底 | ✅ |
| FastAPI（/upload、/graph、/explorer、/chat、/stats） | ✅ |
| 3D 实体局图（玻璃节点/发光边/聚焦重排/拖拽回弹） | ✅ |
| 星云总览原型（丝状体，强/中/弱分级） | ✅ 已合并 |
| 28 篇 arXiv 语料批量入库 | 🚧 运行中 |
| 性能矩阵复测（23 组合，环境门控） | ✅ |
| 抽取质量四通道对比实验（本地零样本/蒸馏/云端 × Schema 消融） | 🔲 |
| 数据卫生包（同词异义审计 + 别名归一） | 🔲 |
| 社区星云接真实 Louvain + 语义摘要 | 🔲 |
| 争议视图（CONTRADICTS 跨文献冲突并排） | 🔲 |
| 证据直达 PDF 原文页高亮 | 🔲 |
| 问题子图问答（确定性路径 + 证据卡 + 语义召回） | 🔲 |
| 分析面板（中心性 Top / 割点承重墙 / 反事实删文献） | 🔲 |
| 人工修正（可重放日志）/ 导出 / 可用性测试 / 综述生成 | 🔲 |

## 7. 快速开始

前置：Python 3.13+、Ollama、Docker（或 WSL 内 Docker）。

```bash
# 1. 模型与数据库
ollama serve &  ollama pull qwen3.5:9b  ollama pull qwen3-vl:4b
docker run -d --name neo4j-kg --restart unless-stopped \
  -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/kgdemo2026 neo4j:5-community

# 2. 后端
pip install fastapi uvicorn neo4j networkx pymupdf requests
cd src && uvicorn app:app --port 8000

# 3. 批量入库（可选，断点可续）
python tools/ingest_corpus.py

# 4. 打开 3D 探索页（浏览器直接打开 web/index3d-bounce.html）
```

## 8. 测试与性能

```bash
cd src && python -m pytest ../tests -q          # 32 条，含 Neo4j 集成测试
node tools/run_perf_matrix.mjs                   # 真实页面性能矩阵（需 Chrome + 独显）
node tools/summarize_perf.mjs                    # 汇总为协议格式表格
```

性能结论（RTX 4060 Laptop / Chrome 152 / 环境门控 23/23 通过）：≤80 节点高保真交互
安全，120 节点为 60fps 拐点，200 节点需降保真。详见
[docs/PERFORMANCE_RESULTS.md](docs/PERFORMANCE_RESULTS.md)。

## 9. 项目结构

```
kg-demo/
├── src/                  # Python 后端
│   ├── app.py            # FastAPI（上传/检索/问答）
│   ├── pipeline.py       # 抽取管线核心（API 与批量共用）
│   ├── pdf_parser.py     # PyMuPDF 表格感知解析
│   ├── kg_extractor.py   # LLM 抽取（双通道/约束解码/截断修复/SFT 录制）
│   ├── graph_store.py    # Neo4j / NetworkX 双后端
│   └── explorer_models.py# 探索器视图模型
├── web/
│   ├── index3d-bounce.html            # 实体局图（主视觉）
│   ├── prototype-semantic-filaments.html  # 星云丝状体原型
│   ├── kg-data/          # 档位数据 + 基准矩阵 + 环境门控
│   └── benchmark/        # 基准场景与单测
├── tools/
│   ├── fetch_corpus.py   # arXiv 语料抓取
│   ├── ingest_corpus.py  # 批量入库（断点可续）
│   ├── run_perf_matrix.mjs # 真实页面性能驱动器
│   └── capture-benchmark-environment.ps1  # 环境快照
├── tests/                # pytest（含 Neo4j 集成）
├── data/corpus/          # 语料与入库快照（gitignored）
└── docs/                 # 设计文档/性能结果/里程碑/决策记录
```

## 10. 已知边界与演进方向

当前为单机单用户架构：Neo4j 起点为 672 节点级语料规模，图分析采用确定性算法（Louvain/中心性/割点）而非 GNN——规模与可解释性是该选择的依据（论证见论文选型节）；本地 9B 模型抽取速度约 1 分钟/篇，离线演示依赖预构建缓存图谱；数据集中 2024-2025 年文献占 20/28，时间向分析早期档位样本稀疏。演进方向：GDS 图数据科学库、更大规模语料的分层索引、抽取蒸馏模型持续迭代。
