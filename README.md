# KG Cosmos

**一句话**：把一篇综述 PDF 拆成实体与关系，建成可三维探索的知识图谱，并给每层抽取打一个可复核的质量分。

![社区星云主视图](assets/screenshot-3d.jpg)

> 主视图 `web/kg-nebula.html`：18 个 Louvain 社区、120 条跨社区纤维（`web/kg-data/filaments.json`
> 的 `meta` 记录 12090 个节点）。

---

## 为什么不是又一个 RAG demo

RAG demo 的验收标准通常是「问几个问题、回答看着像」。这个项目把验收换成可复核的数：

1. **每层有独立金标准。** 实体、端点、谓词、三元组、证据各自判各层的，不共用一份总金标。
2. **报 PPV / κ，不报笼统准确率。** 「判通过的边里有多少真成立」是 PPV（49.7%）；
   与人判一致的程度是 Cohen's κ（0.236）。这两个数比「accuracy 95%」有用得多，也难看得多。
3. **负结果照实写。** 关系层多人标注的 Krippendorff α 在双口径下都是负的
   （实体层 0.2312 / 0.2880，关系层 −0.0119 / −0.1465），留一法验证后仍为负。
   证据指向「关系抽取在这类综述上本身就难以主观标注」，不是「再调一轮就好了」。
4. **平凡基线必须打平或被打败。** 15 类谓词 argmax 与抽取器标注的一致率 25.73%，
   而「全预测 `RELATED_TO`」的平凡基线是 22.00% —— 只差 3.73pp。所以 L4b 不是准确率。

## 六层分别测什么

| 层 | 测什么 | 判据 | 实测 |
|---|---|---|---|
| L1 | 词形归一 | 实体名的归一化形式是否出现在 `evidence[0].text` | 93.6%（全口径 19463/20785）；抽取口径 92.0% |
| L2 | 实体聚类 | 三层合并（规则归一 / 形态模糊匹配 / 缩写抽取）的合并组数 | 585 组（只计数，未逐组判对错） |
| L3 | 关系端点 | 两端是否都是独立抽取出来的节点 | 70.1%（抽取口径）/ 96.5%（闭合口径） |
| L4a | 关系谓词 | 谓词是否落在本体 15 类内（**只判在不在**） | 99.5% |
| L4b | 谓词一致性 | 15 类模板 argmax vs 抽取器标注 | 25.73%（平凡基线 22.00%） |
| L5 | 三元组验证 | 本地 NLI 判别器判「被原文支持」 | 通过率 63.6%，**PPV 仅 49.7%** |
| L6 | 证据完整性 | 每条边是否带页级引文 | 100% |

> L3 必须报两个口径：闭合口径含 4304 个「从边反建」的节点，等于用边的产物去证明边的端点齐全，
> 拿它当质量分是粉饰。L1 同理 —— 那段 `evidence` 就是该实体被抽出时的 chunk 原文，
> 对独立抽取的节点是定义恒等，它实际度量的是词形归一一致性，**不是「实体存在性」**。
> 面板与 `web/kg-data/analytics.json` 的 L1 / L3 主值已按上面两条改成词形归一与**抽取口径**
> （闭合口径降为 `detail` 里的副注，并带 `caliber` 字段）。

### 当前实现范围（设计目标 ≠ 已有能力）

上表每一层都有产物支撑，但下面这些能力只出现在设计与历史报告里，**当前没有实现或没有验证**，
不要读成已有能力：

| 能力 | 状态 |
|---|---|
| L4 第二判定者（`tools/l4_second_judge.py`） | **未实现**：该脚本不在仓库里。`docs/L4_PREDICATE_REPORT.md` 里「用独立第二判定者判定 25.73% 是判别器弱还是本体边界不可复现」仍是计划，**决定性对照实验没做** |
| L2 独立评测层 | **未建**：只有合并组数，没有逐组「对不对」的判据与金标；`docs/NUMBERS_CARD.md` 记的是同一结论 |
| PR / AP 曲线、金标规模敏感性 | **未做**：六层没有覆盖这两个评测维度（见 `docs/L5_SIX_LAYER_REPORT.md` 的待补清单） |
| H5 LoRA 蒸馏的完整训练 | **未跑**：只做过 5 步冒烟（`train_loss` 1.5165，实测外推 1722 步约 40 小时），**完整训练从未执行**（见 `docs/NUMBERS_CARD.md`） |

这些缺口不改变上表的分数：分数是「已经测过的那几件事」的结果，缺口是「还没测的那几件事」。

## 前置要求

**Python 3.10+**，依赖用精确版本锁安装（`requirements.txt` 只有下界，缺 pytest / transformers 等）：

```bash
pip install -r requirements.lock
```

**Windows 控制台需设 `PYTHONUTF8=1`**（或 `chcp 65001`）。`tools/metric_provenance.py` 会打印 `📦`
等 emoji，cp936 编码下 `UnicodeEncodeError` 让子进程退出码变 1，1 条测试因此变红。Linux / macOS 无需设置。

**LLM 抽取**需要 `DEEPSEEK_API_KEY`（云端）或本机 Ollama。界面演示与测试都不需要它；只有走抽取链路
（页面上传 PDF、`tools/ingest_corpus.py`）才需要，缺 key 时那一步会直接报错，不会假装跑过。

**Neo4j 可选**：`bolt://localhost:7687`，默认账号 `neo4j` / 密码 `kgdemo2026`（见 `src/graph_store.py`）。
不启动不影响界面演示（图数据已导出成 JSON），只有重跑管线、重导图库、或跑 `tests/test_graph_store.py`
那 4 条联库用例时才需要。

### 完整数据要自己补齐

仓库刻意只带「能跑起来的最小样本」。不带全量是**体积 + 许可**的取舍：全量评测快照 287 MB、
22 篇论文的边级证据单份到 4 MB、`data/models/` 7.7 GB、33 份语料 PDF 受期刊再分发限制 ——
塞回来会同时撞破「单文件 < 2 MB」的收录线和仓库体量预算。所以自带的是：

| 自带文件 | 规模 | 撑起什么 |
|---|---|---|
| `web/kg-data/filaments.json` / `community-summaries.json` / `analytics.json` | 12090 节点 | 导航页、3D 主视图、分析面板，开箱即用 |
| `web/kg-data/nebula.json` | 179 节点 / 163 边 / 1 篇论文（40 KB） | `test_nebula_papers` 的测试锚点 |
| `data/corpus/jsjkx2025-kg-kbqa.pdf` | 1.93 MB | `test_front_matter_strip` 的首页裁剪锚点 |
| `web/kg-data/demo/` | 16 文件 / 254 KB，裁到 20 节点 | 其余页面的兜底示例（带 `_demo: true`） |

**跑完整功能需要哪些文件、从哪来、用哪个脚本重建**：

| 缺什么 | 从哪获取 | 用哪个脚本重建 |
|---|---|---|
| `data/corpus/*.pdf`（33 份语料） | `python tools/fetch_corpus.py` 拉开放获取的 arXiv 综述；或自备 PDF 走页面上传流程 | 无需重建，放进目录即可 |
| `data/corpus/graphs/*.json`（21 篇论文的图快照，88.8 MB，仓库只带 1 份） | 由上一步的 PDF 抽取 | `python tools/ingest_corpus.py`（需要 LLM key 或本机 Ollama） |
| 全量 `web/kg-data/nebula.json`（3.4 MB） | 由上式快照重建 | `python tools/build_nebula_data.py && python tools/add_pagerank.py`（RUNBOOK §6 ④） |
| `web/kg-data/evidence-*.json`（边级证据，数百 KB–4 MB 每份） | 由快照 + PDF 生成 | `python tools/build_evidence_index.py`（RUNBOOK §6 ⑤） |
| 社区概览 / PRISMA / 立场 / 时间演化 / 对比表读的产物 | 由快照重建 | `tools/build_community_details.py`、`build_prisma_data.py` 等（RUNBOOK §6 ⑤） |
| L5 判别器权重（约 1090 MB） | 首次使用自动从 HuggingFace 下载并缓存 | 无需手动处理 |

补齐之前，社区概览、PRISMA、立场、时间演化、论文对比表等页面读的是 `web/kg-data/demo/` 的
20 节点示例，**示例里的数字不代表真实规模，也不是评测结果**。完整重算顺序（顺序不能乱）见
[RUNBOOK.md](RUNBOOK.md) §5–§6。

## 快速开始

前端无构建链（Three.js / pdf.js 已本地化，断网可用）。

```bash
$env:PYTHONUTF8 = '1'            # Windows 必设（见上）；Linux/macOS 跳过此行
pip install -r requirements.lock
python tools/serve.py            # 前置检查（数据/端口/依赖/Neo4j）后起服务
# 打开 http://127.0.0.1:8000/
python tools/serve.py --static   # 只起纯静态站，零 Python 依赖
```

## 测试与数据

2026-11-27 在**干净 clone** 上实测（`git clone` 到临时目录，`python -m pytest -q`，Python 3.13.0 / pytest 9.0.2，Windows，设 `PYTHONUTF8=1`）：

```text
413 passed, 7 skipped, 27 subtests passed in 12.59s
```

**7 条 skip 全部是「仓库确实不带的大数据/服务」，没有一条是测试坏了。** 看到 `7 skipped` 时请对照这张表：

| 条数 | 用例 | 缺什么 | 怎么恢复 |
|---|---|---|---|
| 4 | `tests/test_graph_store.py` | 可达的 Neo4j 实例：`bolt://localhost:7687`，账号默认 `neo4j` / `kgdemo2026`（见 `src/graph_store.py`） | 起 Neo4j 后重跑，4 条由 skip 转 pass；只看界面演示不需要。**写入路径已在本机 Neo4j 5.26.12 Community 上实测通过**（4 条全绿），不是未验证代码 |
| 2 | `tests/test_front_matter_strip.py`（plm-survey / completion-llm 两条） | 两份语料 PDF：`jsjkx2025-kg-completion-llm.pdf` 2.4 MB、`jsjkx2025-kg-plm-survey.pdf` 4.3 MB（均超「单个数据文件 < 2 MB」的收录线） | 按 RUNBOOK §3 取回 `data/corpus/*.pdf` |
| 1 | `tests/test_entity_absorb.py::test_full_corpus_absorptions_are_now_tiny` | 全量图快照（源项目 21 篇 / 88.8 MB；本仓库只带 1 份 `jsjkx2025-kg-completion-llm.json`） | 取回 `data/corpus/graphs/*.json` 后 `KG_SLOW_TESTS=1 python -m pytest tests/test_entity_absorb.py`（全量下约 125 秒） |

**反向对照**：本机起一个 Neo4j 5.26.12 Community（`neo4j-admin dbms set-initial-password` 后 `neo4j console`）
再跑全量，结果是 **417 passed, 3 skipped**——4 条联库用例全绿。所以表里的 skip 确系缺环境，不是坏测试。

原先还有第 17 条 skip（`test_l4_templates.py` 的方向模板字面量比对），它自称"用例失效、无需恢复"，
实际是 skip 条件写成了「文件里出现 `REL_HYPOTHESIS_TEMPLATE` 这个词」——`tools/verify_direction.py`
的注释提到该名字，触发 skip，而字面量副本仍在文件里，于是这份防漂移比对一直空转。
现条件改为「真的从 `src/ontology.py` 导入该常量才跳过」，两份副本（`relation_verifier.py`、
`verify_direction.py`）重新做实比对，当前 16 键 0 差异。

上一轮已补进 3 份小数据（各自 < 2 MB），因此有 8 条用例由 skip 转 pass：

- `data/corpus/graphs/jsjkx2025-kg-completion-llm.json`（1.1 MB）→ `tests/test_edit_plan.py` 5 条
- `data/ad690dfb/2506.09566v1.pdf`（434 KB）→ `tests/test_upload_dedup.py` 2 条
- `data/quality/archive/metric-provenance/20260919-194048.json`（8 KB）→ `tests/test_metric_archive.py` 1 条

本轮再把两份小数据**真正入库**（不再依赖「本地先跑一遍脚本」），9 条用例在干净 clone 上也是 pass：

- `data/corpus/jsjkx2025-kg-kbqa.pdf`（1.93 MB）→ `test_front_matter_strip` 3 条真实首页裁剪用例
- `web/kg-data/nebula.json`（40 KB）→ `test_nebula_papers` 6 条 K5 结构断言。它是
  `tools/build_nebula_data.py` 用仓库内唯一图快照重建的**最小可跑样本**
  （179 节点 / 163 边 / 1 篇论文），**不是** 3.4 MB 全量产物；全量要按 RUNBOOK §6 重建。

再往上就只剩「塞大数据」这一条路，所以另两份 PDF（2.4 / 4.3 MB）与全量图快照保持 skip，
**要跑请按 RUNBOOK 取回数据**。

**不设 PYTHONUTF8 时唯一的失败**是 `test_metric_archive.py::test_write_archives_exactly_one_new_copy`：
`tools/metric_provenance.py` 打印 `📦`(U+1F4E6)，cp936 控制台下 `UnicodeEncodeError`
让子进程退出码变 1，而断言的是 `returncode == 0`。设置方式见上面「前置要求」那一行。
源项目完整树里是 4 条同类失败（另有 `tools/apply_edit_plan.py` 打印 `⚠️`/`✅`/`❌` 的 3 条），
精简树把这几条用例变成 skip 后只剩 1 条。

## 示例数据（`web/kg-data/demo/`）

`web/kg-data/` 只带 4 个开箱可用的产物（`filaments.json` / `community-summaries.json` /
`analytics.json`，以及测试锚点 `nebula.json`）。其余十几个页面读的 `atlas.json`、
`prisma.json` 等依赖全量图谱，精简版不带 —— 于是这些页面原本只剩一句「数据加载失败」。

> ⚠️ 入库的 `web/kg-data/nebula.json` 是 **179 节点 / 163 边 / 1 篇论文** 的最小可跑样本
> （由 `tools/build_nebula_data.py` 用仓库内唯一图快照重建，40 KB），**不是** 3.4 MB 全量。
> 因此 `kg-nebula` 页面开箱显示的是小样本；要看全量需按 RUNBOOK §6 重建覆盖。
> 样例数据的数字一律**不是**评测结果。

`web/kg-data/demo/` 补了 **16 个文件 / 254 KB** 的最小示例，让页面先有东西可渲染：

- 小图谱：`nebula.json`、`comm-0.json`、`force-top3000.json`、`official-format-top1500.json`
  裁到 **20 个节点**（节点、边、`edge_papers` 三者保持对齐）；
- 表类：`method-limits.json` 留 5 个能力维度、`paper-table.json` 留 4 篇论文；
- 其余 10 个（`atlas.json` 55 KB、`paper-registry.json` 50 KB、`corpus-index.json` 16 KB 等）
  是源产物的原样拷贝，本身就在「小 JSON」范围内。

**这些数字是示例，不是评测结果。** 裁剪过的文件都带 `"_demo": true` 与 `_demo_note`；
完整数据按 [RUNBOOK.md](RUNBOOK.md) 重建。

页面怎么用到它们：`src/app.py` 的 `/api/kg/<name>` 与 `tools/serve.py --static`
在 `web/kg-data/<name>.json` 缺失时自动回退到 `demo/<name>.json`，**页面代码一行没改**。
`serve.py --check` 也会把这三项标成 `⚠️ 示例` 而不是阻塞启动。

实测（2026-11-27，Chrome 1440×900，`python tools/serve.py --static` → `http://127.0.0.1:8098/web/`）：
18 个页面全部渲染出内容，无一显示「数据加载失败」——
`index` / `kg-nebula` / `community-atlas` / `prisma` / `stance` / `temporal` / `diff` /
`method-limits` / `papers` / `paper-table` / `bipartite` / `analytics` / `force-explorer` /
`graph-2d` / `corpus` / `official-2-kg` / `official-3-text` / `ask`。

仍然缺的部分（示例数据兜不住）：边级证据 `kg-data/evidence-*.json`（每份数百 KB 到 4 MB）、
`diff.html` 点开单篇论文时请求的单图数据、以及 `/api/*` 的写接口。

## 评测结果与局限

| 数字 | 值 | 这意味着什么 |
|---|---|---|
| L5 判别器 PPV | **49.7%** | 判通过的边里约一半不被原文支持，假阳性 13960 比真阳性 13778 还多。**「被原文支持」这一层不能直接当事实用。** |
| L5 与人判 κ | **0.236** | 一致性偏低；而零成本的 `hit=both` 字符串规则 κ=0.407，比这个花代价的判别器更接近人判。阈值 0.5→0.8 只把 PPV 抬到 56.7%：它是数量旋钮，不是质量旋钮。 |
| L4b 谓词一致率 | **25.73%** | 只比平凡基线 22.00% 高 3.73pp。**谓词层基本不可信，别拿它做推理。** |
| L1 词形归一 | 93.6% | 可用，但它测的是「名字写对了没有」，不是「实体找对了没有」。 |
| L6 证据完整性 | 100% | 可用：每条边都能跳回页码。这是全项目最稳的一层。 |

**哪层能用**：L1（词形归一）、L6（页级证据）、L3 抽取口径 70.1%（端点是否独立抽出）可以当工程指标。
**哪层不能用**：L4b（谓词选择）、L5 的「被原文支持」判定 —— 直接引用会传播错误。
**L2 只有合并组数、没有对错判据**，不能称作质量分。

其他限度：

- L5 的支持率 40.0% / PPV 49.7% 来自 200 条分层抽样的两人盲判，样本不大；κ=0.236 本身也说明人判并不稳固。
- 另有 L7（方向校验：15 类里仅 3 类有方向指针，`CREATED_BY` 92.4% 还含「用同一套规则改完再检查」的循环论证）
  与 L9（端点召回 86.2% / 关系召回 42.9%，**分母仅 29 实体 / 7 段 / 42 条三元组**）。
  两层都不在六层内，不要当成六层的补充分数。
- 本文所有数字都能在 `docs/` 或 `data/quality/` 找到出处；找不到出处的数字（例如外部资料里的
  「634 节点」）一律不写。唯一的例外是「仓库说明」里的文件数与体积 —— 那是 `git ls-files`
  的现场统计（口径已写在该节），不是历史运行产物。

## 仓库说明

精简版：移出 `data/quality/baseline/`、`data/quality/graphs-published-in/`、
`data/quality/relation-verify-all.json` 等评测全量快照与语料 PDF。干净 clone 实测
**595 个文件 / 26.3 MB**（`git ls-files` 口径，只数受版本控制的文件；含本轮补的 3 份小数据与
`web/kg-data/demo/` 的 254 KB 示例）。口径说明：`Get-ChildItem -Recurse -File` 会把工作区里
未跟踪的 `HIST-REPORT*.md`、`output/` 日志与 `__pycache__` 一并数进去（那样会到 673 个，
且随本地缓存变化），那不是在数仓库内容。全量数据重建步骤见 [RUNBOOK.md](RUNBOOK.md) 的 §5–§6。

**`data/` 107 个文件 / 11.15 MB**：`data/quality/` 94 个（六层指标、金标、L5 人工复核样本、
`edit-plans/` 5 个编辑计划、`archive/metric-provenance/` 1 份归档）、`data/gold/` 7 个金标准、
`data/external/` 2 个外部对照、`data/ad690dfb/` 2 个（`graph.json` 演示图，`/api/qa` 端点测试与
`web/ask.html` 的默认图；`2506.09566v1.pdf` 434 KB，`test_upload_dedup` 的去重用例）、
`data/corpus/` 2 个（`jsjkx2025-kg-kbqa.pdf` 1.93 MB，`test_front_matter_strip` 的首页裁剪锚点；
`graphs/` 1 份论文图快照 1.1 MB，`test_edit_plan` 沙箱用）。
全量评测数据、语料 PDF 与 LoRA 蒸馏中间产物不在本仓库，**需自行生成，见 [RUNBOOK.md](RUNBOOK.md)**。

`docs/` 是按日期存档的历史文档，里面的数字反映的是当时那次运行，过时属正常；
要引用数字请以本 README 与 `data/quality/metric-provenance.json` 为准。

## 许可

MIT，见 [LICENSE](LICENSE)。
