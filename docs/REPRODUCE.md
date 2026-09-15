# 大产物的重建方式（REPRODUCE）

> **为什么有这份文件**：按 `docs/ARCHIVE_POLICY.md`，
> **进不了版本控制的大产物，必须保证"能重跑出来"** ——
> 否则 clone 之后既没有文件、又不知道它怎么来的，证据链就断了。
>
> **背景（TODO N2）**：`data/` 全量被 `.gitignore` 排除，而
> `docs/verification.md` 的每行"证据"、`METRIC_CALIBERS.md` 的每张表、
> `tools/verify_all.py` 的 20/31 项数据检查**全都指向 `data/`**。
>
> **处理方式（已执行）**：
> - **被引用过、且 ≤ 2 MB 的 53 个产物** ⇒ 用 `git add -f` **强制纳入版本控制**
>   （合计 **6.13 MB**）。用 `git add -f` 而不是改 `.gitignore` 白名单，
>   是为了保持`.gitignore` 的"整体排除 `data/`"语义不动 ——
>   运行时产物默认不进库，只有**被当作证据引用的**才手工纳入。
> - **超过 2 MB 的 6 个** ⇒ **不进库**，但**在本文写清怎么重跑**。

---

## 一、不进库的 6 个（以及怎么重建）

| 产物 | 大小 | 被引用 | **怎么重跑** | 耗时 / 资源 |
|---|---|---|---|---|
| `data/quality/relation-verify-all.json` | 52.7 MB | **23 处（最多）** | `python tools/relation_verifier.py --all` | **280–460 秒**，**要本地 GPU**（判别器推理） |
| `data/quality/baseline/relation-verify-all.PRE-T3.json` | 53.0 MB | 4 | **它是 T3 治理前的快照** —— 重跑方式同上，但要在 `sync_graph.py --apply` **之前**留档 | 同上 |
| `data/quality/baseline/relation-verify-all.PRE-CB-FIX.json` | 52.9 MB | 1 | 同上（`CREATED_BY` 修正前） | 同上 |
| `data/quality/subgraph-supported.json` | 5.1 MB | 4 | `python tools/l5_subgraph.py` | 秒级，纯规则 |
| `data/3e38f803/graph.json` | 3.2 MB | 2 | **上传那篇 PDF 重抽**（走 `upload.html` 或 `/api/upload/start`） | **约 36 分钟**，要 Ollama |
| `data/corpus/graphs/2012.00456v1.json` | 2.2 MB | 1 | `python tools/sync_graph.py`（从 Neo4j 导出）或重跑该篇的整条管线 | 视来源而定 |

**⚠️ 两个 baseline 的特殊性**：它们是**"治理前"的历史快照**，
**重跑不出来**（因为语料与代码都已经变了）。它们属于「**只能留档、不能重建**」的一类 ——
而这恰恰是 `ARCHIVE_POLICY.md` 要保护的东西。

⇒ **建议**：如果将来要归档它们，走 **方式 B（移入归档目录 + 压缩）**，
把 53 MB 压成 gzip（这类 JSON 通常能压到 3–8 MB）。

---

## 二、被纳入版本控制的 53 个（6.13 MB）

它们**不需要重建** —— 直接 clone 就有。
清单可由脚本随时重算：

```powershell
python tools/_probe/_probe_archive_artifacts.py          # 打印清单
python tools/_probe/_probe_archive_artifacts.py --apply  # 真的 git add -f
```

**判据**：**被 `docs/` / `README.md` / `RUNBOOK.md` / `tools/*.py` / `src/*.py` /
`web/` 实际引用过** ⇒ 它不只是"运行时产物"，而是"证据"。

---

## 三、clone 之后应该是什么状态

| 层 | 状态 |
|---|---|
| **前端** | ✅ 完整（`web/` 全在库，含 `kg-data/*.json` 与 `l5-data/*.pdf`） |
| **被引用的证据** | ✅ 完整（53 个关键产物，6.13 MB） |
| **大产物** | ⚠️ 缺 6 个 —— **`relation-verifier` 类检查会失败**，但**知道怎么重建** |
| **语料 PDF** | ⚠️ 缺（`data/corpus/*.pdf` 不在库）—— 证据深链的「在原文中查看」会失败 |
| **图数据库** | ⚠️ 缺（Neo4j 内容需 `sync_graph.py` 重建） |

**结论**：clone 之后**界面能开、被引用的数字能核对**；
但**`verify_all` 的约 20 项数据检查里会有一部分失败** ——
**这是如实反映"证据有大小之分"，不是仓库坏了**。

---

## 四、还没做的（记在这里，别忘）

1. **两个 baseline 的归档**（53 MB × 2）—— 建议 gzip 后移入 `data/quality/archive/`；
2. **语料 PDF 是否入库** —— 28 篇 PDF 大约几十 MB，且**有版权问题**，
   建议**不入库**，改为在 README 里写清"语料从哪来、怎么下"；
3. **Neo4j 内容的重建脚本** —— `sync_graph.py` 能导出，但**从 JSON 反向导入 Neo4j**
   的路径要确认（这关系到"clone 后能不能把库建起来"）。
