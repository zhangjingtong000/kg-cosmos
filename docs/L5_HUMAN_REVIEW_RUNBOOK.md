# L5 人工盲判运行手册（单篇 + 全库）

> 本次审计实测环境：8000（后端，挂 `web/`）、8098（静态站）、11434（Ollama）在听；8097 未监听。
> 关键实测：`GET 127.0.0.1:8000/api/quality/l5-validity-key.json` = **200 / 18498B**，
> 明文含 `entail_prob/pass/hit/stratum`；`8098/l5-review.html` 与 `8098/api/items` 均 **404**。

## 0 结论：旧界面不能用于单篇抽样

| 问题 | 实测证据 |
|---|---|
| **前提锚定** | 旧 `web/l5-review.html:154-157` 把判别器读到的 premise 单独列一块并原文标黄，标注者判的是「这句支不支持」 |
| **锚定顺带破盲** | premise = 「同时含两个端点的句子」，标黄位置直接泄漏机器端点命中层（both/one/none），而它正是分层变量 |
| **同源破盲** | 8000 与 `web/` 同源，`/api/quality/l5-validity-key.json` 一条 URL 拿到全部逐条分数 |
| **链路断** | `tools/l5_single_sample.py`、`tools/l5_single_review.py` 不存在（`audit_single_l5.py` 文案引用它们）；报告器吃不了单篇 |
| **κ 同源** | `l5-annotator-agreement.json` 里两个标注者都是 LLM（κ=0.722），不能当人类金标 |

## 1 判读：点哪个 URL

**路径 A（推荐，零服务、现在就能点，8000 已在跑）**

- 单篇 ad690dfb（150 条）：`http://127.0.0.1:8000/l5-review.html?sheet=./l5-data/ad690dfb-sheet.json`
- 全库 200 条：`http://127.0.0.1:8000/l5-review.html`（自动回退 `./l5-data/validity-sheet.json`）
- 原文抽屉走 `./l5-data/2506.09566v1.pdf`（已复制，445KB）；标注存 localStorage，点右上「导出标注 JSON」

**路径 B（要 append-only 落盘，需起服务）**

```
E:\Python313\python.exe tools\l5_single_review.py --hash ad690dfb      # → http://127.0.0.1:8097/l5-review.html
E:\Python313\python.exe tools\l5_single_review.py --validity
```

## 2 两问怎么判

- **Q1（边级真值，键 1/2/3）**：只看证据段落原文，这条三元组成立吗？成立 / 不成立 / 说不清。
- **Q2（证据级，键 4–8，0=跳过）**：证据段落里两个端点各出现了吗？
  `4` 都在且有断言 `5` 都在但仅罗列 `6` 只有一个 `7` 都没出现 `8` 分不清。
- 判「支持」自动进下一条（Q2 记为都在有断言），约 20 秒/条；判「不支持/说不清」停下答 Q2，约 25 秒/条。

## 3 门槛（判多少条 / 多久 / 能分辨多大差）

口径：`deff=0.768`（由全库 `n=200, π̂=0.4003, CI[0.3407,0.4597]` 反推）、`π=0.40`、与全库 40.0% 比较的 80% power。

| n | 95%CI 半宽 | 可分辨最小差 | 分钟 |
|---:|---:|---:|---:|
| 100 | ±8.4pp | 17.0pp | 33 |
| **150（推荐）** | **±6.9pp** | **13.9pp** | **50** |
| 200 | ±5.9pp | 12.0pp | 67 |
| 300 | ±4.9pp | 9.8pp | 100 |

清单固定 150 条（`both 43 / one 99 / none 8`，11 层）。**中途停会引入缺失偏差**，报告会警告；判不完就按已判条数报并写明。

## 4 判完怎么算回指标

```
E:\Python313\python.exe tools\l5_single_report.py --hash ad690dfb --labels "<导出的 json 路径>"
E:\Python313\python.exe tools\l5_single_report.py --validity --labels "<导出的 json 路径>"
```

输出：加权真实支持率 + 层内 bootstrap 95%CI（「说不清」双口径）、分层明细、
隐藏探针（hit_premise=none）支持率、**错误归因**（端点缺失=引错段 / 端点齐但关系不成立=抽取错）、
Q2 与机器 `hit_evidence` 一致率（本轮标注质量）。`--validity` 另有 PPV/NPV/召回/F1/域内加权 AUC/κ/阈值灵敏度。

单篇 ad690dfb **没有** `entail_prob`（该论文不在全库 17 篇内，三元组交集仅 28/951=2.9%）⇒ 单篇 PPV 不适用。
要 PPV 须跑一次 CPU 判别器（需放开「禁止加载模型」）：

```
E:\Python313\python.exe tools\audit_single_l5.py data\ad690dfb\graph.json --dump tmp\l5-single\ad690dfb-dump.jsonl
E:\Python313\python.exe tools\l5_single_report.py --hash ad690dfb --labels "..." --key tmp\l5-single\ad690dfb-dump.jsonl
```

## 5 盲态怎么保证

已堵：清单不含 `premise`/分数（实测 keys 只有 9 个展示字段）；8097 静态 `/data/` 只放行 `data/corpus/`；
8000 `/api/quality/{name}` 加盲判保护名单（`src/app.py`，实测 13 例单测通过）。

⚠ **未堵住**：`src/app.py` 的改动需要**重启 8000** 才生效，本次按纪律没有重启，
所以 `http://127.0.0.1:8000/api/quality/l5-validity-key.json` 现在**仍然是 200**。
判读期间不要开这条 URL；或先重启 8000。

⚠ 标注者绝不能看：`tmp/l5-single/*-design.json`（含分层权重与探针 id）。

## 6 质量闸门

1. 探针 `hit_premise=none` 支持率 > 20% ⇒ 本轮标注不可信，标签作废重判。
2. Q2 vs 机器端点一致率 < 70% ⇒ 标注者没在核对端点。
3. 两个 LLM 的 κ=0.722 只是「任务可复现」；要报人类 κ，需第二位标注者独立判同一 30 条。

## 7 本次改动与限制

新增 `tools/l5_single_sample.py`、`tools/l5_single_review.py`、`tools/l5_single_report.py`；
重写 `web/l5-review.html`（两问式、去 premise、双数据源）；收紧 `tools/l5_validity_review.py` 静态白名单；
给 `src/app.py` 与 `tools/cdp_verify_l5_review.py` 各加盲态拦截/断言。

限制（据实记录）：未做浏览器端到端实测（纪律禁止起服务/GPU 任务），页面只做了 `node --check` 语法检查、
盲态字符串检查（`entail_prob`/`pass`/`"hit"` 各 0 次）与静态资源 200 实测；8000 未重启。
