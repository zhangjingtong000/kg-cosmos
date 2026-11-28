# RUNBOOK · 怎么把 KG Cosmos 跑起来

> 本文件是**唯一**的运行说明。此前"怎么跑"散在 README、交接文档和各页面的报错文案里，
> 说法还不一致（有的写 `python -m http.server`，有的写 `python src/app.py`），
> 却没说清什么时候该用哪个。现在收敛到 `tools/serve.py` 一个入口。

---

## 1. 最短路径

```bash
cd <仓库根目录>          # 本文所有命令都从仓库根目录跑
python tools/serve.py
```

然后打开 **http://127.0.0.1:8000/**。

它做三件事：前置检查（数据文件 / 端口 / 依赖 / Neo4j）→ 起 FastAPI →
把 `web/` 挂在同一个 origin 下。**界面与数据面 API 同源**，
前端会走 `/api/kg/*`，这能证明前后端真的连通。

只想先看检查结果、不启动：

```bash
python tools/serve.py --check
```

---

## 2. 两种模式怎么选

| | 后端模式（默认） | 静态模式 `--static` |
|---|---|---|
| 命令 | `python tools/serve.py` | `python tools/serve.py --static` |
| 端口 | 8000 | 8098 |
| 界面 | ✅ 同源挂载 | ✅ 需访问 `/web/` |
| 数据来源 | **API 优先**，失败回退静态 | 只有静态文件 |
| 依赖 | fastapi / uvicorn / networkx | 仅 Python 标准库 |
| 适用 | 主用法；要演示"上传→入图"链路 | 断网演示；换机器只带 `web/` 与 `data/` |

两种模式**前端都能跑**：`web/lib/kg-api.js` 会先试 `/api/kg/<name>`，
失败（静态站下 404）就回退到 `kg-data/<name>.json`。
当前实际走了哪条路径可以从浏览器控制台看 `KGData.source`。

---

## 3. 前置依赖

```bash
pip install -r requirements.lock
```

`requirements.lock` 是**精确版本锁**（2026-09-12 冻结）。
原来的 `requirements.txt` 只有下界，且漏掉了代码实际 import 的
pytest / transformers / torch / websocket-client / rapidfuzz / scikit-learn /
krippendorff / scipy 等——按它装是跑不起测试与 L5 判别器的。

**torch 按机器选**：本机用 `torch==2.6.0+cu124`。CPU-only 环境装 `torch==2.6.0`，
L5 全量会从 ~280 秒变成约 1 小时。

**Neo4j 是可选的**：不起也不影响界面演示（图数据已导出成 JSON）。
只有要重跑管线 / 重导图库时才需要它（`bolt://localhost:7687`，密码默认 `kgdemo2026`）。

**模型权重不随仓库分发**：L5 判别器
（`Babelscape/mdeberta-v3-base-triplet-critic-xnli`，约 1090 MB）
首次使用需联网下一次，之后全程离线。缓存位置
`C:\Users\<user>\.cache\huggingface\hub\models--Babelscape--...`。

---

## 4. 离线演示

现场演示假定无外网。已处理：

- three.js / pdf.js 全部**本地化**到 `web/lib/`，不走 CDN
- 演示主线（导航页 + **六张**主卡片）**断网可用**，有回归守门：
  ```bash
  python tools/cdp_verify_offline.py web/kg-nebula.html
  ```
- 5 个页面仍依赖 esm.sh（**都在 `web/legacy/`**：`community-atlas` / `force-explorer` /
  `force-graph` / `graph-2d` / `official-3-text` —— 按页名单
  `tools/add_legacy_banners.py` 的 `OFFICIAL` / `is_legacy` 分类，不要笼统写成
  "5 个历史原型"），断网时它们会显示「本页需要联网」的提示而不是白屏
  （`web/lib/net-guard.js`）

---

## 5. 验证（改完东西该跑什么）

```bash
python -m pytest tests -q          # 单元测试（420 项）
python tools/verify_all.py --fast  # 全量回归（67 项，跳过慢的 PDF 检查）
python tools/verify_all.py         # 含 PDF 高亮检查
```

`verify_all.py` 会依次跑：

| 检查 | 守什么 |
|---|---|
| 本体口径一致性 | 各脚本的常量是否仍与 `src/ontology.py` 一致 |
| 指标与数据快照的版本绑定 | 数据改了而指标没重跑 → 报 stale |
| 社区布局数据自检 / claims 诊断 / 结构指标 | 数据面 |
| 主视图离线可用 | 「离线演示铁律」 |
| 前后端连通 · API 优先 + 静态回退 | 两端不再平行 |
| 导航页 / 星云交互 / 标签遮挡 / 着色引擎 | 前端功能 |
| 方法能力 / 分析面板 / PathQA | 各视图 |
| 证据深链 PDF 高亮 | pdf.js 自绘与高亮 |

**注意**：前后端连通那一项需要后端在 8000 跑着。
没起的话它会明确报「后端不可用」，不会静默跳过。

---

## 6. 数据重算链路（改了图数据之后）

顺序**不能乱**，否则会出现"同一文件被两个脚本写、后者把前者覆盖"的问题
（踩过：`l5_report.py` 会把 `L2_entity_clustering` 写成 null：

```
① 改图数据（tools/entity_normalize.py / backfill_missing_endpoints.py …）
② python tools/sync_graph.py --apply           # 快照 → Neo4j（约 10~24 分钟）
③ python tools/relation_verifier.py --all      # L5 全量打分（约 280~460 秒，本地 GPU）
④ python tools/build_nebula_data.py && python tools/add_pagerank.py
⑤ python tools/build_filament_data.py          # 以下四步是前端数据链
   python tools/build_community_details.py
   python tools/build_force_data.py --full
   python tools/build_evidence_index.py
⑥ python tools/l5_report.py --json             # 六层（会写 l5-six-layer.json）
   python tools/l5_subgraph.py
   python tools/l2_report.py --write            # L2 —— 必须在 l5_report 之后
   python tools/l7_direction_verify.py
   python tools/l8_structure_audit.py
   python tools/l3_backfill_precision.py
   python tools/l9_recall.py --score
   python tools/build_analytics_data.py
⑦ python tools/metric_provenance.py --write    # 记下新的数据指纹与指标读数
```

第 ⑦ 步是关键：它把「数据指纹 + 指标读数」记进
`data/quality/metric-provenance.json`，之后任何时候跑
`python tools/metric_provenance.py` 都能发现"数据变了但指标没重跑"。

### 判断"有没有东西需要重跑"：三件工具，各答一个问题

```bash
python tools/freshness_report.py     # 产物是否晚于它依赖的上游？（顺序对不对）
python tools/metric_provenance.py    # 数据变了但指标没重算？（指纹对不对）
python tools/check_doc_numbers.py    # 文档与页面里写的数还对吗？
```

三者互补，都已挂进回归：

| 工具 | 回答的问题 | 失败的典型症状 |
|---|---|---|
| `freshness_report.py` | 产物之间的**先后顺序** | 改了 L7 归因却忘了重建分析面板 |
| `metric_provenance.py` | 数据指纹是否变了 | 图谱改了但六层没重算 |
| `check_doc_numbers.py` | 文档/页面里的数字 | 页面还写着上一轮的旧值 |

**改完任何数据后，把这三条都跑一遍**，比记那串重算顺序可靠得多。

---

## 7. 常见问题

**页面空白 / 一直转圈**
用 `--check` 看数据文件是否齐全。也要确认是通过 HTTP 打开的，
`file://` 协议下 ES module 与 fetch 都会被浏览器拦住。

**`KGData.usedApi()` 是 false**
说明回退到了静态文件。若你就是想验证前后端连通，确认后端起在 8000
且页面是从 8000 打开的（不是 8098）。

**证据面板点「在原文中查看」没有内容**
`data/corpus/*.pdf` 大部分不在版本控制里（体积原因）。仓库只带一份 1.93 MB 的
`jsjkx2025-kg-kbqa.pdf` 作为测试锚点；演示用的其余 PDF 克隆后需自行放入。

**⚠️ 克隆下来跑 `verify_all`，有一批会红**
`data/` **只入库了一部分**：`.gitignore` 默认忽略 `/data/*`、**白名单放行
`data/quality/*.json`** ⇒ **89 个质量报告是入库的**（指标类检查照常能跑），
未入库的是大文件：图快照（`data/corpus/graphs/*.json`，**21 → 1**）、
PDF（**33 → 2**：`2506.09566v1.pdf` 与 `jsjkx2025-kg-kbqa.pdf`）、`data/models/`（7.7 GB）、adapter 权重。
**2026-10-04 真的 `git clone` 到临时目录实测**：`--data-only` = **30 / 37**，
红的 7 项全部因为缺这些大文件（如「论文注册表」报幽灵条目、
「蒸馏微调」缺 `adapter_model.safetensors`）。
要跑全绿，先按下面「数据重算链路」把 `data/corpus/graphs/` 生成出来。
这是 `docs/TODO.md` 的 `N2`（该条目原文说"20 项回归全红"，与实测不符，已更正）。

**L5 判别器报显存不足**
`tools/relation_verifier.py` 的 batch 默认 32，可调小；
或设 `CUDA_VISIBLE_DEVICES=""` 走 CPU（慢很多）。

**分析面板显示 `? 组`**
`l5-six-layer.json` 的 `L2_entity_clustering` 被覆盖成 null 了。
按第 6 节的顺序重跑，注意 `l2_report.py` 必须在 `l5_report.py` **之后**。
`metric_provenance.py` 现在会把这种 null 当失败报出来。
