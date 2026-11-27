# 示例数据（demo）

这里放的是**为了在没有全量图谱时让页面能渲染**而做的示例数据，不是评测产物。

- 完整数据（`nebula.json` 3.4 MB、22 篇论文的 `evidence-*.json`、`data/corpus/*.pdf`）
  不在本仓库，按 [RUNBOOK.md](../../../RUNBOOK.md) §5–§6 重建。
- 页面不需要改代码：`src/app.py` 的 `/api/kg/<name>` 与 `tools/serve.py --static`
  在 `web/kg-data/<name>.json` 缺失时自动回退到本目录的同名文件。
- 裁剪过的文件带 `"_demo": true` 与 `_demo_note`；小图谱只保留 **20 个节点**，
  `method-limits.json` 只留 5 个能力维度，`paper-table.json` 只留 4 篇论文。
- 其余文件（`atlas.json`、`paper-registry.json`、`prisma.json` 等）是源产物原样拷贝，
  本身就在「小 JSON」范围内。

快速预览：

```bash
python tools/serve.py --static      # http://127.0.0.1:8098/web/
```

`serve.py --check` 会把用了示例数据的项标成 `⚠️ 示例`，不会当成阻塞项。
