# 主流方案调研：实体消歧 / 关系质量 / 质量评估 / 可视化

> 2026-09-11 重新调研。上一版 `ENTITY_RESOLUTION_SURVEY.md` 只做了自研对比表，
> 深度不足；本文**实际抓取主流项目源码与文档**，记录可借鉴的具体实现。
> 调研方式：subagent 并行深挖四个方向 + 直接抓取 GitHub raw 源码。

## 一、实体消歧（GraphRAG / Neo4j / FalkorDB）

### 1.1 neo4j-graphrag-python（官方实现，Apache 2.0）

源码：`src/neo4j_graphrag/components/resolver.py`
（[GitHub](https://github.com/neo4j/neo4j-graphrag-python/blob/main/src/neo4j_graphrag/components/resolver.py)）

**类结构**：

```
EntityResolver（基类，Component）
├── SinglePropertyExactMatchResolver     精确匹配（同一 label + 同一属性值）
└── BasePropertySimilarityResolver       相似度匹配（抽象基类）
    ├── FuzzyMatchResolver               RapidFuzz fuzz.WRatio / 100
    └── SpaCySemanticMatchResolver       spaCy 静态向量 + 余弦相似度
```

**相似度解算流程**（`BasePropertySimilarityResolver.run`）：
1. `MATCH (entity:__Entity__)` + 可选 `filter_query` 限定范围
2. `UNWIND labels(entity)` 跳过保留标签，**按 label 分组** `collect({id, name, ...})`
3. 拼接 `resolve_properties`（默认 `["name"]`）为单一文本
4. 组内**两两组合**算相似度，`>= similarity_threshold`（默认 **0.8**）的记为待合并对
5. `_consolidate_sets` 把重叠的 pair 集合合并（并查集式传递闭包）
6. `apoc.refactor.mergeNodes(nodes, {properties: 'discard', mergeRels: true})`

**可直接借鉴的三点**：
- 阈值 **0.8** 作为默认值（有官方依据）
- **按标签分组后再匹配**——只在同类型内合并，比跨类型合并保守
- `mergeRels: true` 让 APOC 自动合并同类型同方向的边

**关键局限（我们实测发现）**：`FuzzyMatchResolver` 用 `fuzz.WRatio`，**对短缩写
没有长度保护**——实测 `UI` vs `build graph.py` 得 **0.900**，超过 0.8 阈值会被
错误合并。我们自己实现的 `_names_are_duplicates` 加了长度保护，在同一对上得
0.286（正确不合并）。**照搬官方实现会引入这个错误。**

### 1.2 FalkorDB GraphRAG-SDK（跨标签合并的正确答案）

源码：`graphrag_sdk/src/graphrag_sdk/ingestion/resolution_strategies/base.py`
（[GitHub](https://github.com/FalkorDB/GraphRAG-SDK/blob/main/graphrag_sdk/src/graphrag_sdk/ingestion/resolution_strategies/base.py)）

**两阶段设计**：
- **阶段 1（同标签）**：按 `(normalized_name, label)` 分组，**无条件合并**（安全）
- **阶段 2（跨标签）**：可选，**交给 LLM 判定是否同一实体**

跨标签验证提示词（原文）：
```
Entities named '{entity_name}' appear under different types: {types}.
Descriptions: {descriptions}
Do ALL of these descriptions refer to the SAME real-world entity?
If YES: Line 1 'YES <canonical_type>'  Line 2+ summary
If NO:  Line 1 'NO'  Line 2 reason
Do not attempt partial merges. Answer YES only if all entries describe the same entity.
```

**其他值得借鉴的机制**：
- `force_summary_threshold=3`：描述数不足 3 时**不问 LLM**（失败保护，省调用）
- 同标签合并后用 LLM 生成**单一摘要**（`_SUMMARY_PROMPT`）
- LLM 调用失败 / 返回 NO / 返回畸形 → **一律不合并**（fail-safe）
- `id_remap` + **传递闭包解析**（sl-loser → sl-survivor → cl-survivor）
- `remap_relationships` 用 `id_remap` 重写关系端点并按 `(start, type, end)` 去重

**对本项目的直接启示**：我此前的做法是"同名跨标签**按标签优先级直接合并**"
（`LABEL_PRIORITY`），而 FalkorDB 明确指出这需要 **LLM 验证**。这意味着我可能
错误合并了一些同名但实为不同实体的节点（如 `BERT` 在不同论文里可能是
Method / Tool / System，指向不同侧面）。**这是一个待修正项。**

### 1.3 微软 GraphRAG

- 抽取提示词 `extract_graph.txt` 要求实体名**全大写**（`MARTIN SMITH`）——命名
  规范化前置到抽取阶段，从源头收敛变体
- `create_summarized_entities` 工作流：**名称标准化后精确匹配分组**，再用 LLM
  合并同组描述（`summarize_descriptions.txt`）
- **从不用编辑距离判定实体同一性**

### 1.4 RapidFuzz vs difflib 实测对比（本项目真实数据）

| 实体对 | difflib+长度保护 | RapidFuzz WRatio | 期望 |
|---|---|---|---|
| Knowledge Graph / Knowledge Graphs | 1.000 | 0.968 | 合并 |
| LLM / LLMs | 1.000 | 0.857 | 合并 |
| Tabula / Tabula PDF | 1.000 | 0.900 | 合并 |
| **UI / build graph.py** | **0.286** | **0.900** | **分开** |
| TransE / TransR | 0.833 | 0.833 | 分开 |
| 性能（300 对真实名） | 14.0 ms | **3.2 ms（4.4×）** | — |

**结论**：采用 RapidFuzz 的速度 + 保留自研的长度保护。不能直接照搬官方
`FuzzyMatchResolver`。

## 二、关系抽取质量

（待 subagent 调研结果补充）

## 三、质量评估框架

（待 subagent 调研结果补充）

## 四、可视化与交互

（待 subagent 调研结果补充）

## 五、待修正项（由本次调研暴露）

1. **跨标签合并过于激进**：应改为 FalkorDB 式 LLM 验证，而非标签优先级裁决
2. **`_names_are_duplicates` 应换用 RapidFuzz**（性能 4.4×），但必须保留长度保护
3. 相似度阈值统一为 **0.8**（Neo4j 官方默认）
