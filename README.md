# KG Cosmos

**一句话**：把综述 PDF 里的方法、材料、属性与关系抽成知识图谱，用三维星云浏览，并给每一层抽取算一个可复核的质量分。

![社区星云主视图](assets/screenshot-3d.jpg)

主视图是 `web/kg-nebula.html`：18 个 Louvain 社区、120 条跨社区纤维，共 12090 个节点。

## 它做什么

- 读一篇综述 PDF，把里面的方法、材料、属性与概念抽成图：节点是实体，边是关系，每条边带页码级引文。
- 图按 Louvain 社区聚成星云团：3D 主视图导出 12090 个节点、18 个社区，完整图谱 20785 个节点；Three.js 渲染成可旋转、可下钻的三维空间，跨社区的边画成纤维束。
- 抽取质量不靠「看着像」验收：词形归一、实体聚类、关系端点、谓词、三元组、证据六层各测各的，每层给一个带分母、带基线的数。
- 界面纯静态，前端无构建链，Three.js 与 pdf.js 已本地化，断网可跑。

## 快速开始

**零依赖看界面**（只用 Python 标准库）：

```bash
python tools/serve.py --static      # Windows 先 $env:PYTHONUTF8='1'
# → http://127.0.0.1:8098/web/
```

界面直接读仓库自带的产物与 `web/kg-data/demo/` 示例，开箱即可浏览。仓库自带的产物：

| 文件 | 规模 | 撑起什么 |
|---|---|---|
| `web/kg-data/filaments.json` | 12090 节点 / 120 条纤维 / 18 社区 | 3D 主视图与社区导航 |
| `web/kg-data/community-summaries.json` | 18 个社区 | 社区概览 |
| `web/kg-data/analytics.json` | 六层指标 + 结构统计 | 分析面板 |
| `web/kg-data/nebula.json` | 179 节点 / 163 边 / 1 篇论文 | 单图渲染与测试锚点 |
| `web/kg-data/demo/` | 17 个文件 / 255 KB，裁到 20 节点 | 其余页面的兜底示例（带 `_demo` 标记） |

`python tools/serve.py --check` 只做前置检查（数据、端口、依赖、Neo4j）不起服务；`--port` 可改端口。
Neo4j 是可选的：只有重跑管线、重导图库和 4 条联库用例才需要它，演示与静态站不依赖。

**完整链路**（抽取 + 后端）：

```bash
$env:PYTHONUTF8 = '1'                 # Windows 必设；Linux / macOS 跳过
pip install -r requirements.lock      # 精确版本；requirements.txt 只有下界
python tools/serve.py                 # 前置检查后起 http://127.0.0.1:8000/
```

LLM 抽取需要 `DEEPSEEK_API_KEY`（云端）或本机 Ollama；只看界面、跑测试都不需要它。

## 功能

| 功能 | 入口 | 做什么 |
|---|---|---|
| 导航 | `web/index.html` | 六步动线：上传 → 星云 → 提问 → 分析 → 子图 → 边界 |
| 三维星云 | `web/kg-nebula.html` | 社区聚团 + 纤维束，点开节点看类型、社区与出处 |
| 抽取 | `web/upload.html` · `tools/ingest_corpus.py` | 上传 PDF → 解析 → 抽实体与关系 → 入库 |
| 问答 | `web/ask.html` | 对图谱提问，同时给支持与反对方的证据 |
| 分析面板 | `web/analytics.html` | 六层指标、图规模、社区结构与质量的对照 |
| 问题子图 | `web/pathqa.html` | 从问题定位到实体路径与三元组 |
| 方法边界 | `web/method-limits.html` | 逐层写明哪层能用、哪层不能拿去做推理 |
| 单图查看 | `web/new-graph.html` | 上传或选中一篇论文后的单图浏览 |
| 导出 | 星云页右下角 | 当前视图导出 PNG / JSON / GEXF，或生成分享链接 |
| 论文对比 | `web/legacy/diff.html` | 两篇论文的节点与边交集对比，读 `diff.json` |
| 文档守门 | `tools/check_doc_numbers.py` | 拿 `data/quality/` 的权威值核文档与页面里的数 |

`web/legacy/` 存并行开发过的历史版本，带「这是历史版本」横幅，索引在 `web/legacy/index.html`。

## 评测结果

六层在同一个评测全集（45708 条边）上各测各的：

| 层 | 测什么 | 实测 |
|---|---|---|
| L1 词形归一 | 实体名的归一化形式是否出现在该实体的原文 `evidence[0].text` 里 | **93.6%**（19463/20785）；抽取口径 92.0%（15161/16481） |
| L2 实体聚类 | 规则归一 / 形态模糊 / 缩写三路一共合并了多少组 | **585 组**（只计数，没有逐组对错的判据） |
| L3 关系端点 | 边的两端是否都是独立抽取出来的节点 | 抽取口径 **70.1%**；闭合口径 96.5% |
| L4 谓词 | 谓词是否落在 15 类模板内 / 与抽取器标注的一致率 | **99.5%** / **25.73%**（平凡基线 22.00%） |
| L5 三元组 | 本地 NLI 判别器判「被原文支持」 | 通过率 63.6%，**PPV 49.7%** |
| L6 证据 | 每条边是否带页级引文 | **100%** |

**负结果照实写**，这三条比上面任何一个高分都更该被看到：

- **L5 的 49.7% 是 PPV**：判通过的边里约一半不被原文支持，假阳性 13960 比真阳性 13778 还多。阈值从 0.5 调到 0.8，PPV 只从 47.9% 抬到 56.7%——那是数量旋钮，不是质量旋钮。
- **L4b 的 25.73% 只比平凡基线高 3.73pp**：15 类谓词 argmax 对「全预测 `RELATED_TO`」的 22.00% 几乎没有优势，谓词层不能拿去做推理。
- **L2 只有 585 组**：缺「这一组合得对不对」的判据，所以它是计数，不是质量分。

六层之外还有 L7（`CREATED_BY` 方向合规 92.4%）与 L9（端点召回 86.2%，分母只有 29 个实体），不计入上面的分数。每个数的出处、分母与易混口径见 [docs/METRIC_CALIBERS.md](docs/METRIC_CALIBERS.md)。

**哪层能拿出去用**：L6（页级证据）与 L1（词形归一）可以当工程指标；L3 的抽取口径 70.1% 说明边上七成端点是独立抽出来的，另外 4304 个是从边反建的，不能算进端点完整性。

**哪层不能直接引用**：L4b 的谓词选择、L5 的「被原文支持」判定——引用即传播错误。

复现这几个数：`python tools/metric_provenance.py` 从 `data/quality/*.json` 汇总出 `metric-provenance.json`，每个数都能顺着它回到具体产物与那次运行。

## 设计要点

- **六层各测各的**：实体、端点、谓词、三元组、证据不共用一份总金标，每层有自己的判据与样本；对外报 PPV、κ，不报笼统准确率。
- **指标可溯源**：每个数都落在 `data/quality/`，`python tools/check_doc_numbers.py` 拿权威值核 README、RUNBOOK 与页面，对不上就报红；缺全量产物时它降级跳过并说明。
- **三维布局**：Louvain 社区决定团的位置，跨社区的边聚成纤维；前端直接吃 JSON，没有后端也能转起来。
- **测试**：`python -m pytest -q` 在干净 clone 上 416 passed / 7 skipped（收集 423 条）。7 条 skip 全是「缺 Neo4j 与大文件」，逐条原因与恢复方法见 [docs/TESTING.md](docs/TESTING.md)。

## 已知边界

- **标签由 LLM 生成，不是人工金标**：L5 那 200 条盲判是两个会话各判一次（只重叠 30 条），κ=0.236 读作「与 LLM 判定标签的一致性」，不能读作与人工标注者的一致性。
- **全量数据不随仓库**：全量评测快照 287 MB、33 份语料 PDF、`data/models/` 7.7 GB 都在仓库外；页面先吃 `web/kg-data/demo/` 的 20 节点示例，示例数字不是评测结果。重建顺序见 [RUNBOOK.md](RUNBOOK.md)。
- **L4 第二判定者未做**：`tools/l4_second_judge.py` 不在仓库，「25.73% 是判别器弱，还是本体边界不可复现」这个决定性对照还没跑。
- **Windows 控制台先设 `PYTHONUTF8=1`**：`tools/metric_provenance.py` 会打印 emoji，cp936 下 `UnicodeEncodeError` 会让 1 条测试变红。
- **5 个页面仍依赖 esm.sh**（`web/legacy/` 里的历史原型页）：断网时由 `web/lib/net-guard.js` 拦成明确提示，不会白屏。

<!-- esm-sh-pages: runtime=community-atlas,force-explorer,force-graph,graph-2d,official-3-text mention_only=index,index-classic,official-1-blocks -->

## 目录结构

```text
kg-cosmos/
├── src/            抽取、本体、图库、问答（Python）
├── tools/          serve.py、数据构建、指标守门与 CDP 回归脚本
├── web/            8 个主页面 + kg-data/ 产物 + legacy/ 历史页
├── data/           金标与质量产物（107 个文件 / 10.6 MB）
├── docs/           设计、评测、口径与测试细节（53 份）
├── tests/          pytest 用例
├── assets/         README 截图与视觉素材
└── RUNBOOK.md      全量数据的重算顺序
```

整仓按 `git ls-files` 口径 **594 个文件 / 25.2 MB**，只带能跑起来的最小样本；全量语料与评测快照需按 RUNBOOK 自行生成。

## 许可

MIT，见 [LICENSE](LICENSE)。
