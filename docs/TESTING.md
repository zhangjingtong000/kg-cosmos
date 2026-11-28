# 测试与数据：怎么跑、结果、每条 skip 的原因

这份文档收纳 README 里放不下的验收细节 —— 主要是「为什么有 7 条 skip」和
「上一轮补了什么数据」。README 只留怎么跑、结果和已知边界。

## 怎么跑

```bash
# Windows 控制台先设 PYTHONUTF8=1（见下）
python -m pytest -q
```

2026-11-27 在**干净 clone** 上实测（`git clone` 到临时目录，`python -m pytest -q`，
Python 3.13.0 / pytest 9.0.2，Windows，设 `PYTHONUTF8=1`）：

```text
416 passed, 7 skipped, 27 subtests passed in 12.45s
```

`pytest --collect-only` 收集到 423 条（= 416 passed + 7 skipped）。

## 7 条 skip 的原因（全部是"仓库确实不带的大数据/服务"，没有一条是测试坏了）

| 条数 | 用例 | 缺什么 | 怎么恢复 |
|---|---|---|---|
| 4 | `tests/test_graph_store.py` | 可达的 Neo4j 实例：`bolt://localhost:7687`，账号默认 `neo4j` / `kgdemo2026`（见 `src/graph_store.py`） | 起 Neo4j 后重跑，4 条由 skip 转 pass；只看界面演示不需要。**写入路径已在本机 Neo4j 5.26.12 Community 上实测通过**（4 条全绿），不是未验证代码 |
| 2 | `tests/test_front_matter_strip.py`（plm-survey / completion-llm 两条） | 两份语料 PDF：`jsjkx2025-kg-completion-llm.pdf` 2.4 MB、`jsjkx2025-kg-plm-survey.pdf` 4.3 MB（均超「单个数据文件 < 2 MB」的收录线） | 按 RUNBOOK §3 取回 `data/corpus/*.pdf` |
| 1 | `tests/test_entity_absorb.py::test_full_corpus_absorptions_are_now_tiny` | 全量图快照（源项目 21 篇 / 88.8 MB；本仓库只带 1 份 `jsjkx2025-kg-completion-llm.json`） | 取回 `data/corpus/graphs/*.json` 后 `KG_SLOW_TESTS=1 python -m pytest tests/test_entity_absorb.py`（全量下约 125 秒） |

**反向对照**：本机起一个 Neo4j 5.26.12 Community（`neo4j-admin dbms set-initial-password`
后 `neo4j console`）再跑全量，结果是 **420 passed, 3 skipped** —— 4 条联库用例全绿。
所以表里的 skip 确系缺环境，不是坏测试。

## `PYTHONUTF8=1`

不设时**唯一的失败**是 `test_metric_archive.py::test_write_archives_exactly_one_new_copy`：
`tools/metric_provenance.py` 打印 `📦`(U+1F4E6)，cp936 控制台下 `UnicodeEncodeError`
让子进程退出码变 1，而断言的是 `returncode == 0`。源项目完整树里是 4 条同类失败
（另有 `tools/apply_edit_plan.py` 打印 `⚠️`/`✅`/`❌` 的 3 条），精简树把这几条用例
变成 skip 后只剩 1 条。

## 已补进的小数据（解除 skip 的经过）

早先有 8 条用例因缺小样本数据而 skip，后补进 3 份各自 < 2 MB 的数据：

- `data/corpus/graphs/jsjkx2025-kg-completion-llm.json`（1.1 MB）→ `tests/test_edit_plan.py` 5 条
- `data/ad690dfb/2506.09566v1.pdf`（434 KB）→ `tests/test_upload_dedup.py` 2 条
- `data/quality/archive/metric-provenance/20260919-194048.json`（8 KB）→ `tests/test_metric_archive.py` 1 条

随后又把两份小数据**真正入库**（不再依赖"本地先跑一遍脚本"），9 条用例在干净 clone 上也是 pass：

- `data/corpus/jsjkx2025-kg-kbqa.pdf`（1.93 MB）→ `test_front_matter_strip` 3 条真实首页裁剪用例
- `web/kg-data/nebula.json`（40 KB）→ `test_nebula_papers` 6 条 K5 结构断言。它是
  `tools/build_nebula_data.py` 用仓库内唯一图快照重建的**最小可跑样本**
  （179 节点 / 163 边 / 1 篇论文），**不是** 3.4 MB 全量产物；全量要按 RUNBOOK §6 重建。

再往上就只剩「塞大数据」这一条路，所以另两份 PDF（2.4 / 4.3 MB）与全量图快照保持 skip。

## 曾经的第 17 条 skip（防漂移比对的空转）

原先还有第 17 条 skip（`test_l4_templates.py` 的方向模板字面量比对），它自称
"用例失效、无需恢复"，实际是 skip 条件写成了「文件里出现 `REL_HYPOTHESIS_TEMPLATE` 这个词」
—— `tools/verify_direction.py` 的注释提到该名字，触发 skip，而字面量副本仍在文件里，
于是这份防漂移比对一直空转。现条件改为「真的从 `src/ontology.py` 导入该常量才跳过」，
两份副本（`relation_verifier.py`、`verify_direction.py`）重新做实比对，当前 16 键 0 差异。

## 全量回归（`tools/verify_all.py`）

`verify_all.py` 是前端 + 数据侧的串行回归入口，**依赖两样干净 clone 里没有的东西**：
本地静态服务（8098）与无头 Chrome（CDP 截图）。缺任一项时它会**跳过对应检查并打印原因**，
不把"没环境"报成"功能坏了"。跑全量后端与前端：

```bash
python tools/serve.py --static &          # 8098 静态站（Linux/macOS）
python tools/serve.py --static            # Windows 另开一个窗口
python tools/verify_all.py --fast         # --fast 跳过耗时的 PDF 截图对比
```

各 `tools/cdp_verify_*.py` 的具体断言写在 `tools/verify_all.py` 的 `CHECKS` 注释里。
