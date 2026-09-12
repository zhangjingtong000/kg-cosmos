# 主流方案调研：实体消歧 / 关系质量 / 质量评估 / 可视化

> 2026-09-11 重新调研。上一版 `ENTITY_RESOLUTION_SURVEY.md` 只做了自研对比表，
> 深度不足；本文**实际抓取主流项目源码与文档**，记录可借鉴的具体实现。
> 调研方式：subagent 并行深挖四个方向 + 直接抓取 GitHub raw 源码。
>
> 2026-09-12 合并：三份提炼稿（实体归一化 / 质量评测 / 可视化标签与聚合）
> 并入本文，此前第二至四节一直是"待补充"占位。原始 5 份调研报告为
> `neo4j-merge-research.md`、`research_evidence_kg_normalization.md`、
> `kg-quality-eval-survey.md`、`3d-kg-visual-research.md`、
> `InfraNodus-事实核查报告.md`。
>
> **约定**：标注「已抓取」= 本轮实测 HTTP 200 且读到内容；「已索引」=
> 搜索结果或 OpenAlex 确认存在但未读正文；「未验证」= 未确认 URL。
> 未验证项一律原样保留，不得当作业已确认。

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

> **1.5–1.8 = 本项目可直接借鉴的具体实现**（源码级、参数级证据，能直接改代码）；
> **1.9–1.13 = 仅用于论文论证的文献证据**（不进入代码，用于论证「为什么必须做归一化、为什么规则法不够」）；
> **1.14 = 未能验证的项**；**1.15 = 对本项目的直接结论**。
> 全部内容取自 `neo4j-merge-research.md` 与 `research_evidence_kg_normalization.md` 两份原始调研文件；原文件标「未验证」者，本节同样标注，不做补白。凡引用 `main` 分支源码处均未固定 commit，复现时内容可能随后续提交变化。

---

### 1.5 `apoc.refactor.mergeNodes` 的确切语义：结构性不可逆

**签名与语义**（[APOC overview 页](https://neo4j.com/docs/apoc/current/overview/apoc.refactor/apoc.refactor.mergeNodes/) 原文）：

```
apoc.refactor.mergeNodes(nodes [, config ]) :: (node)
Description: Merges the given LIST<NODE> onto the first NODE in the LIST<NODE> .
All RELATIONSHIP values are merged onto that NODE as well.
```

- 合并落点是**列表第一个节点**，其余节点被删除；
- 返回值只有 `node` 一个字段。对照 `apoc.refactor.cloneNodes` 返回 `(input, output, error)`，`mergeNodes` **不返回任何被销毁节点的快照或引用**——这是「不可逆」的结构性证据，而不是性能或实现细节问题：过程返回后原节点已不在图中，也没有可复原它们的句柄。

**config 键清单（官方 overview 页逐字，共 11 个键）**：

```
config MAP { mergeRels :: BOOLEAN, selfRef :: BOOLEAN, produceSelfRef = true :: BOOLEAN,
preserveExistingSelfRels = true :: BOOLEAN, countMerge = true :: BOOLEAN, collapsedLabel :: BOOLEAN,
singleElementAsArray = false :: BOOLEAN, avoidDuplicates = false :: BOOLEAN,
relationshipSelectionStrategy = "incoming" :: ["incoming", "outgoing", "merge"],
properties :: ["overwrite", ""discard", "combine"] }
```

默认值为 `{}`。原文中 `""discard"` 的多余引号是官方页面自身的排版错误，可原样引用。

**这里有一个可直接引用的文档缺口**：guide 页（[merge-nodes](https://neo4j.com/docs/apoc/current/graph-refactoring/merge-nodes/)）**只解释了 4 个键**——`mergeRels`、`produceSelfRel`、`preserveExistingSelfRels`、`singleElementAsArray`；其余 `selfRef`、`countMerge`、`collapsedLabel`、`avoidDuplicates`、`relationshipSelectionStrategy` 在官方文档中**没有任何文字说明**。这 5 个键的实际语义属未验证项，不能按名字反推。已确认原文的四条：

- `mergeRels`：`true/false: give the possibility to merge relationships with same type and direction.`
- `produceSelfRel`：`true/false: if true (default), any eventual new self-relationship that would be created from the nodes merged into target node are inserted, otherwise not.`（且明确说明该参数独立于 `mergeRels`，不影响既有自环）
- `preserveExistingSelfRels`：`true/false: is valid only with mergeRels:true . If true (default), the pre-existing self-relationships in the final node will remain, otherwise will be deleted.`
- `singleElementAsArray`：`false/true: if is false (default) and type is combine in case the merge of two arrays is a new array with size 1 it extracts the single value.`

**属性合并策略（丢数据的精确条件）**：

| 取值 | 官方原文 |
|---|---|
| `discard` | `the property from the first node will remain if already set, otherwise the first property in list will be written` |
| `overwrite` / `override` | `last property in list wins` |
| `combine` | `if there is only one property in list, it will be set / kept as single property otherwise create an array, tries to coerce values` |

最关键的一句原文：`Properties with a name not included in the config map are overridden.` —— **没有在 config 里逐项点名的属性一律被覆盖**，这是最容易被忽略的静默丢数据路径。关系属性用同一套办法处理，唯一例外是 `config map` 为 null 时实体属性改为 combine。

`properties` 支持逐属性与通配符（官方示例原文）：

```
CALL apoc.refactor.mergeNodes(nodes, {properties: { name:'discard', age:'overwrite', kids:'combine', `addr.*`: 'overwrite', `.*`: 'discard' }})
```

**对「name 即主键」场景的硬约束**：官方示例中 `properties:"combine"` 的执行结果原文是 `node (:Person {name: ["John", "Tom"]})`。也就是说，对以 name 作身份键的节点用 `combine` 会把主键变成**数组**，破坏 `MERGE (x {name: $name})` 的身份语义；改用 `discard` 则被合并方的名字**永久丢失**。两种选择都伤主键——这解释了为什么「先软合并、后人工决断」比「一次 mergeNodes 定终身」更适合本项目。

**官方库对破坏性的两处自我确认**。其一是 `neo4j-graphrag-python` 的 resolver 源码内 Cypher 注释（[resolver.py](https://raw.githubusercontent.com/neo4j/neo4j-graphrag-python/main/src/neo4j_graphrag/components/resolver.py)）直接承认冲突属性只留一个：

```
# * properties: if the two entities have the same property key with
# different values, only one of them is kept in the created node
"CALL apoc.refactor.mergeNodes(entities,{ properties:'discard', mergeRels:true })"
```

其二是官方文档的 warning 块（[user_guide_kg_builder.rst](https://raw.githubusercontent.com/neo4j/neo4j-graphrag-python/main/docs/source/user_guide_kg_builder.rst)，渲染版见[官方 HTML](https://neo4j.com/docs/neo4j-graphrag-python/current/user_guide_kg_builder.html)）：

```
The SinglePropertyExactMatchResolver, SpaCySemanticMatchResolver, and FuzzyMatchResolver replace the nodes created by the KG writer.
```

官方示例脚本的 docstring 也自带警告（[fuzzy_match_entity_resolver_pre_filter.py](https://raw.githubusercontent.com/neo4j/neo4j-graphrag-python/main/examples/customize/build_graph/components/resolvers/fuzzy_match_entity_resolver_pre_filter.py)）：

```
WARNING: this process is destructive, initial nodes are deleted and replaced
by the resolved ones, but all relationships are kept.
```

**关系侧存在对应过程**：`apoc.refactor.mergeRelationships(rels [, config ]) :: (rel)`，同样是「保留列表第一条、删除其余」，且 `All relationships in the list must share the same start and end nodes, or the procedure will throw an error.`；guide 页明确 `These config option also works for apoc.refactor.mergeRelationships([rels],{config})`。

**本节小结**：APOC 提供的是原地销毁式合并，**没有 undo 通道**，也没有任何「先备份再合并」的选项。可逆性必须在数据模型层自建。

---

### 1.6 官方生态里的软合并（可逆）实现：tombstone + `SAME_AS`

`neo4j-graphrag-python` 的 resolver 是唯一被广泛引用的官方 ER 实现，但它不可逆（1.5）。真正可逆的一手实现出自 **Neo4j 官方 Labs 项目 `neo4j-labs/agent-memory`**，证据固定在 commit `0dbfaf8a`，可直接复现。

**合并时删不删节点：不删。** `MERGE_ENTITIES`（[queries.py](https://raw.githubusercontent.com/neo4j-labs/agent-memory/0dbfaf8a/src/neo4j_agent_memory/graph/queries.py)）的做法是转移关系、打 tombstone、追加别名，三条动作原文如下：

```
// Mark source as merged
SET source.merged_into = target.id,
    source.merged_at = datetime()
// Add source name as alias on target
SET target.aliases = CASE
    WHEN target.aliases IS NULL THEN [source.name]
    WHEN NOT source.name IN target.aliases THEN target.aliases + source.name
    ELSE target.aliases
END
RETURN source, target
```

转移关系用 `CALL` 子查询：`MENTIONS` 边按 `WHERE NOT (m)-[:MENTIONS]->(target)` 去重后并入 target；`SAME_AS` 边带上 `confidence: r.confidence, match_type: 'merged', created_at: datetime()` 搬到 target。`merge_duplicate_entities` 的 docstring 原文：`The source entity's name is added as an alias to the target.`（[long_term.py](https://raw.githubusercontent.com/neo4j-labs/agent-memory/0dbfaf8a/src/neo4j_agent_memory/memory/long_term.py)）

**已合并节点在后续处理里被显式排除**，等价于逻辑删除：`GET_ENTITIES_WITH_EMBEDDINGS` 的过滤条件原文是 `WHERE e.embedding IS NOT NULL AND e.merged_into IS NULL`。

**三档阈值策略（可直接对标本项目的激进/保守取舍）**。`DeduplicationConfig`（[long_term.py 第 22–48 行](https://raw.githubusercontent.com/neo4j-labs/agent-memory/0dbfaf8a/src/neo4j_agent_memory/memory/long_term.py)）逐字字段与默认值：

```python
enabled: bool = True
auto_merge_threshold: float = 0.95
flag_threshold: float = 0.85
use_fuzzy_matching: bool = True
fuzzy_threshold: float = 0.9
max_candidates: int = 10
match_same_type_only: bool = True
```

docstring 原文把三档语义写死：`- Auto-merged (score >= auto_merge_threshold)`、`- Flagged for review (score >= flag_threshold but < auto_merge_threshold)`、`- Treated as distinct (score < flag_threshold)`。判定分支原文：`if best_score >= config.auto_merge_threshold: ... action="merged"` / `elif best_score >= config.flag_threshold: ... action="flagged"`。

**「不合并」是一个可显式记录、可撤销的决策**，而不是静默忽略。`SAME_AS` 边的 `status` 取值为 `pending` / `confirmed` / `rejected`，三类查询原文如下：

```
CREATE_SAME_AS_RELATIONSHIP:
CREATE (e1)-[r:SAME_AS { confidence: $confidence, match_type: $match_type,
                          created_at: datetime(), status: $status }]->(e2)

UPDATE_SAME_AS_STATUS:
SET r.status = $status, r.updated_at = datetime()

GET_POTENTIAL_DUPLICATES:
MATCH (e1:Entity)-[r:SAME_AS]-(e2:Entity)
WHERE r.status = 'pending'
RETURN e1, e2, r ORDER BY r.confidence DESC LIMIT $limit
```

写入侧只在 `action == "flagged"` 时建边，`"status": "pending"`；人工入口 `review_duplicate(source_id, target_id, *, confirm: bool)` 以 `confirm=False` 表示「判定不是重复」并留痕，确认时置 `confirmed`、拒绝时置 `rejected`。

**簇可还原**：`GET_SAME_AS_CLUSTER` 用 `MATCH path = (e)-[:SAME_AS*1..3]-(other:Entity) RETURN DISTINCT other AS entity, length(path) AS distance ORDER BY distance`，即别名簇最多展开 3 跳。

**Neo4j 官方论坛的别名建模共识**（[Modeling "also known as" (AKA) relationships](https://community.neo4j.com/t/modeling-also-known-as-aka-relationships/27615)，Andrew Bowman 2020-10-21 原文）：`I'd go with something like :ProductAlias nodes, each node representing an alias, with relationships pointing to the product they refer to.` 查询模式为 `MATCH (:ProductAlias {name:$nameInput})-[:ALIAS_FOR*0..1]->(p:Product)`；Tony Chiboucas 补充 `Then, Andrew's pattern is the only way to go: (:Alias)-[:FOR]->(p:Product:Alias)`，并提醒脏数据里「关系挂在别名节点上」时必须用 `apoc.refactor` 迁移。置信度：中（官方社区帖，属社区共识而非官方规范）。

**另一条可逆性路线：不改图，改建模。** Neo4j 官方 [Versioning 数据建模页](https://neo4j.com/docs/getting-started/data-modeling/versioning/) 给出的是**建模模式**而非内核特性，原文要点：`- The entity Product is linked to its different versions by an explicit relationship.` `- The entity Product is immutable.` `- Only the properties that are stored in the different versions ( State nodes) change.` `- The LATEST relationship links the entity Product to its most recent version ( State )`；查询示例 `MATCH (:Product {id:1})-[:LATEST]->(s:State) RETURN s.name`。同页时间维度版本化原文：`- Each element has dedicated validFrom / validTo time properties.` `- Nodes can only share a relationship if their validity timespan overlap.` `- Complete history of the graph is usable.`。同页 linked list 一节明确写到 undo 场景：`You need the previous and next elements in a list ... (e.g. what song is the next on a playlist, or undo an action on a text document)`。

**所谓「时间旅行」是应用层参数，不是内核语法。** agent-memory 的 `get_preferences_for` docstring 原文：`Implements the v0.5 bi-temporal time-travel API on top of valid_from / valid_until`，查询条件为 `(p.valid_from IS NULL OR p.valid_from <= datetime($as_of)) AND (p.valid_until IS NULL OR p.valid_until > datetime($as_of))`；废止用 `MERGE (old)-[:SUPERSEDED_BY]->(new)` 加 `SET old.valid_until = coalesce(old.valid_until, datetime())`，并注明幂等。**内核没有 `AS OF` 查询语法**（否定性证据，置信度中）。

**自建审计钩子存在**：APOC 提供 `apoc.trigger.add` / `install` / `pause` / `resume` / `show` / `dropAll` 等 12 个 trigger 过程（[overview/apoc.trigger](https://neo4j.com/docs/apoc/current/overview/apoc.trigger/)），可用来自建变更日志，从而自建撤销能力；内核本身不提供 undo。

**两条否定性结论必须一起记录**：

1. **n10s（neosemantics）没有任何合并过程**。`n10s.onto.mergeNodesWithSameUri` **不存在**——n10s 4.0 参考文档穷举出的 44 个过程为：`n10s.graphconfig.drop/init/set/show`、`n10s.inference.getRels/hasLabel/inCategory/nodesInCategory/nodesLabelled`、`n10s.mapping.add/drop/dropAll/list`、`n10s.nsprefixes.add/addFromText/list/remove/removeAll`、`n10s.onto.import.fetch/import.inline`、`n10s.rdf.*`（delete/getDataType/getIRILocalName/getIRINamespace/getLangTag/getLangValue/getValue/hasLangTag/import/import.fetch/import.inline/preview.fetch/preview.inline/shortFormFromFullUri/stream.fetch/stream.inline/fullUriFromShortForm）、`n10s.validation.shacl.*`，其中无一是 merge / sameAs / dedup；源码佐证 `n10s/onto/OntoProcedures.java` 仅含 `doOntoImport` / `doPreviewOnto` 两个 protected 方法与 `@Context` 注入（[OntoProcedures.java](https://raw.githubusercontent.com/neo4j-labs/neosemantics/2025.06/src/main/java/n10s/onto/OntoProcedures.java)）。来源：[n10s 4.0 reference](https://neo4j.com/labs/neosemantics/4.0/reference/)。
2. **没有任何 Neo4j 官方博客 / 文档页把 tombstone 或 `SAME_AS` 列为「官方推荐模式」**。这些做法只以「实现」形式存在于官方 Labs 项目源码（本节上述）与社区讨论中。置信度：中（否定性结论，检索范围见 1.14）。

**另需避坑**：`neo4j-graph-examples/entity-resolution` 仓库名具有误导性——其内容实为电影/用户相似度推荐示例（`gds.nodeSimilarity`、`gds.alpha.similarity.pearson`，阈值 `WHERE similarity > 0.9`），**与实体解析无关**，不能作为 ER 策略证据。

---

### 1.7 `FuzzyMatchResolver` 默认配置、resolver 全清单与照搬的坑

1.1 已给出类结构与流程，此处补充**默认值的逐字出处**与**照搬的具体风险点**。

**默认参数（源码逐字）**：`FuzzyMatchResolver.__init__` 的 `similarity_threshold: float = 0.8`，`BasePropertySimilarityResolver` 中 `self.resolve_properties = resolve_properties or ["name"]`；docstring 原文 `similarity_threshold (float): The similarity threshold above which nodes are merged. Defaults to 0.8.`、`resolve_properties (Optional[List[str]]): The list of properties to consider for similarity. Defaults to ["name"].` 相似度计算原文：

```python
def compute_similarity(self, text_a: str, text_b: str) -> float:
    # RapidFuzz's fuzz.WRatio returns a score from 0 to 100
    # normalize the input strings before the comparison is done (processor=utils.default_process)
    # e.g., lowercase the text, strip whitespace, and remove punctuation
    # normalize the score to the 0..1 range
    return fuzz.WRatio(text_a, text_b, processor=utils.default_process) / 100.0
```

**判定方向已定死**：`if sim >= self.similarity_threshold: pairs_to_merge.append({id1, id2})`——用 `>=`，且相似对先被 `_consolidate_sets` 归并成簇、再对整簇一次性调用 `mergeNodes`。

**resolver 全清单：只有 3 个具体类 + 2 个抽象基类**（逐一核对 [resolver.py](https://raw.githubusercontent.com/neo4j/neo4j-graphrag-python/main/src/neo4j_graphrag/components/resolver.py)）：

| 类名 | 类型 | 关键默认值 |
|---|---|---|
| `EntityResolver` | 抽象基类 | `run()` 抛 `NotImplementedError` |
| `SinglePropertyExactMatchResolver` | 具体 | `resolve_property: str = "name"` |
| `BasePropertySimilarityResolver` | 抽象基类（`CombinedMeta`） | `similarity_threshold: float = 0.8` |
| `SpaCySemanticMatchResolver` | 具体 | `spacy_model: str = "en_core_web_lg"`、`auto_download_spacy_model: bool = True`、`similarity_threshold: float = 0.8` |
| `FuzzyMatchResolver` | 具体 | RapidFuzz `fuzz.WRatio`、`similarity_threshold: float = 0.8` |

**两个不存在的东西，引用时必须避免写错**：`ExactMatchResolver` **不存在**（正确名字带前缀 `SingleProperty`）；`LLMEntityResolver` **不存在**——`src/neo4j_graphrag/llm/` 下只有供应商实现（`anthropic_llm.py`、`bedrock_llm.py`、`cohere_llm.py`、`google_genai_llm.py`、`mistralai_llm.py`、`ollama_llm.py`、`openai_llm.py`、`vertexai_llm.py`），容易混淆的是抽取器 `LLMEntityRelationExtractor`。官方文档原文也只承认三个：`In practice, this package implements three resolvers`。CHANGELOG 只记录过这三次引入。

**过滤范围与默认行为（照搬风险集中处）**：

- 解析范围默认是全图文本节点：`match_query = "MATCH (entity:__Entity__) "`，且 `WHERE NOT lab IN ['__Entity__', '__KGBuilder__']`——只跳过这两个保留标签；官方 warning 原文 `By default, all nodes with the __Entity__ label will be resolved.`
- KG Builder **默认自动执行 ER**：`By default, after each run, an Entity Resolution step is performed to merge nodes that share the same label and name property.`，关闭方式是 `SimpleKGPipeline(..., perform_entity_resolution = False)`。

**依赖与版本**（[PyPI neo4j-graphrag](https://pypi.org/pypi/neo4j-graphrag/json)）：包名 `neo4j-graphrag`，最新版本 `1.19.0`，`requires_python: <3.15,>=3.10.0`；`rapidfuzz` 只在 `fuzzy-matching` extra 中（`rapidfuzz<4.0.0,>=3.12.2; extra == "fuzzy-matching"`），未装则构造时直接 `ImportError`（提示 `pip install "neo4j-graphrag[fuzzy-matching]"`）；`spacy==3.8.7` 只在 `nlp` extra 中且限定 `python_version < "3.14"`。

**照搬会踩的坑（逐条对应上面的原文证据）**：

1. **单档阈值 + 硬合并**：0.8 之上直接删节点合并，没有 agent-memory 那样的 0.85 人工复核档（1.6）。误判即不可逆。
2. **静默丢属性**：内部固定 `properties:'discard'`，源码注释自己承认「同键不同值时只保留一个」。项目的实体描述字段（来自 LLM 抽取）会在合并中丢掉一半。
3. **主键形态被破坏**：若把它改成 `combine`，name 会变成数组；保持 `discard` 则丢名。二者都不适合以 name 作身份键的图。
4. **默认全图解析**：不写 `filter_query` 就会对全部 `__Entity__` 节点做两两比较，且只跳过 `__Entity__` / `__KGBuilder__` 两个标签，跨类型对也会进入比较。
5. **簇级传递合并（机制性推论，非实测）**：相似对先经 `_consolidate_sets` 归并成簇再整体合并，因此 A~B、B~C 各自过线就会把 A/B/C 并成一个节点，单对阈值无法阻断链式扩散。
6. **默认自动改图**：KG Builder 默认跑 ER，若未显式关掉 `perform_entity_resolution`，一次流水线运行就会改变已有图。
7. **官方文案自身有串台**：`FuzzyMatchResolver.__init__` 未安装 rapidfuzz 时抛出的错误信息写的是 `python module needs to be installed to use the SpaCySemanticMatchResolver`（原文如此），排障时不要被误导。
8. **源码引用未固定 commit**：上述 URL 均为 `main` 分支，未固定 SHA，后续提交可能改变行为与行号。

---

### 1.8 record linkage / ER 工具的工程质量证据

这一节是「工具层」证据，用途有二：为本项目自建 resolver 提供工程范式参照；为论文「同类工作」章节提供带许可证与维护状态的引用。所有 `stars / pushed_at / license` 取自 GitHub REST API，PyPI 字段取自 `pypi.org/pypi/<pkg>/json`。

| 工具 | 许可证 | 最后维护 | 可借鉴 / 可引用点 |
|---|---|---|---|
| **Splink** | **MIT** | GitHub push **2026-09-10**；PyPI **4.0.17**（2026-09-03）；2398 stars，208 open issues | 官方无标准 benchmark 分数表，但示例直接用 FEBRL（`Deduplicating the febrl3 dataset`、`Linking the febrl4 datasets`，并注明数据集来自 febrl / recordlinkage 文档），另有 `Accuracy analysis and ROC charts using a ground truth (cluster) column` 示例——**用带 ground truth 簇列的 ROC 分析替代单一 F1 汇报**，正是本项目评测可以照抄的做法 |
| **Ditto** | **Apache-2.0** | GitHub push **2024-04-17**；317 stars，26 open issues；**PyPI 上无官方包** | 论文 Table 5（ER-Magellan，F1）：`Amazon-Google DM+ 70.7 / Ditto 75.58 (+4.88)`、`DBLP-ACM 98.45 / 98.99 (+0.54)`、`Abt-Buy 92.7 / 93.85`、`Fodors-Zagats 100 / 100`；Table 6（WDC）：`Ditto achieves the highest F1 score of 94.08`；摘要称提升 `up to 29% of F1 score`、`by up to 31% in F1 scores`；企业匹配案例 `96.5% F1 on a holdout dataset` |
| **pyJedAI** | **Apache-2.0** | GitHub push **2026-03-22**；101 stars；PyPI `pyjedai` **0.3.6**（2025-11-03） | 有正式论文（CEUR Vol-3254 paper 366，摘要仅定位为系统演示，**未给 benchmark 数字**）；其价值在于 **COMEM 论文的实验数据来源**：`we used eight clean-clean ER datasets collected by pyJedAI (Nikoletos et al., 2022)`——本项目做对比实验时可用这 8 个 clean-clean 数据集 |
| **Zingg** | **AGPL-3.0** | GitHub push **2026-09-04**；1246 stars；PyPI `zingg` **0.7.0**（2026-08-10） | README 原文 `Zingg is licensed under AGPL v3.0 … Need a different license? Write to us.`——**许可证有传染性，本项目不应拼进代码**；官方以 FEBRL / NC Voters 做性能回归（徽章 `performance-test-febrl120K`、`performance-test-ncVoters5M`）并附 FEBRL 预训练模型；**无第三方精度 benchmark** |
| **dedupe** | **MIT** | GitHub push **2025-07-29**；4512 stars；PyPI **3.0.3**（2024-08-15） | 无标准数据集分数表；README 提供 canonical benchmark 入口 `python benchmarks/benchmarks/canonical.py`（仓库含 `benchmarks` 子包），理论来源是 Bilenko 博士论文——**主动式标注（active learning）路线**，与本项目「人工审核队列」思路同源 |
| **recordlinkage**（Python Record Linkage Toolkit） | **BSD-3-Clause** | GitHub push **2024-02-21**；1062 stars；PyPI **0.16**（**2023-07-20**） | 无官方精度 benchmark；它的价值是 FEBRL 数据集的分发源：`recordlinkage.readthedocs.io/en/latest/ref-datasets.html`（Splink 官方示例即指向该页）。维护已停滞，**不建议引入生产链路，但可作为合成评测数据来源** |
| **LinkTransformer** | **GPL-3.0** | GitHub push **2026-09-06**；145 stars；PyPI `linktransformer` **0.1.18**（2026-02-15） | 论文 ACL 2024 demos，DOI `10.18653/v1/2024.acl-demos.21`（arXiv 2309.00789）；对比表原文：`Structured_Amazon-Google` 上 `Ours (ZS) 45`、`Ours (FT) 73.8`、`Magellan 71.9`、`Deep matcher 67.6`、`Ditto 85.81`、`REMS 71.34`；`Dirty_DBLP-ACM` 上 `Ours (FT) 92`、`Ditto 95.6`。**zero-shot 45 vs 微调 73.8 的落差，是「无标注数据时靠现成模型做实体匹配不现实」的直接证据** |
| **Senzing** | 引擎本体许可**未验证**（`senzing.com/licensing/`、`senzing.com/docs/license/` 均返回 HTTP 403） | PyPI `senzing` **4.1.2**（2026-05-07，license 字段 Apache-2.0）；GitHub 组织仓库 push 活跃（2026-09） | 无公开 FEBRL / Amazon-Google / DBLP-ACM / WDC 分数；其价值是**官方 ER 教程的实际技术栈**——Neo4j 的实体解析教程页结构原文是 `Installing the Senzing Library` / `Building an Entity Resolved Knowledge Graph`，即官方示范也用第三方引擎而非内核功能 |

**第三方评测是这一节里质量证据最强的一条**：FCDS 2024 Record Linkage Monograph（[PDF](https://fcds.med.miami.edu/downloads/RecordLinkageMonograph/2024/linkage_monograph_2024.pdf)）摘要原文：`The software are tested on 670,214 simulated records from the pseudopeople package. A requirement is no expected false positives (FP) after a clerical review. Compared with fastLink, Splink predicts about 9% more true positives (TP) and Match*Pro predicts about 20% fewer TP. Therefore, the FCDS recommends to use Splink for PRL. The main limitation is that Splink requires beginner-level skills in Python and initial Python setup which is labor intensive.` 注意：这是 **TP 相对量**，不是 F1 或精确率绝对值，引用时不要改写成准确率。

**Ditto 的两处口径需按原文写**：① 正式发表于 **PVLDB**，DOI `10.14778/3421424.3421431`，题名 `Deep entity matching with pre-trained language models`；**未找到它发表在 EMNLP 2020 的证据**（检索未命中，非绝对排除）。② `pip install ditto` **不会**得到这篇论文的实现——PyPI 上的 `ditto`（ver 1.2.4，home_page `https://github.com/ironport/ditto`，2014-10-24 最后上传）是同名的 IronPort 工具，与实体匹配无关。

---

### 1.9 缩写消解：Ab3P 与 Schwartz-Hearst（文献证据）

本项目从学术 PDF 抽取实体，缩写/全称异形（`HCQ` vs `Hydroxychloroquine`）是字面匹配失效的主要来源之一。这一节给出规则法缩写抽取的**精度天花板**。

**文献信息先纠正一处**：Ab3P 原始论文是 **BMC Bioinformatics 2008;9:402**，DOI `10.1186/1471-2105-9-402`，PMID 18817555，PMCID PMC2576267——**不是** `BMC Bioinformatics 9:82`（按 9:82 抓取会落到别的文章）。来源：[Europe PMC fullTextXML](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC2576267/fullTextXML)。

| 结论 | 数字（原文引用） |
|---|---|
| Ab3P 在 Medstract 上的表现 | `On the Medstract corpus our algorithm produced 97% precision and 85% recall which is higher than previously reported results.` |
| Ab3P 在自标注 1250 条 MEDLINE 记录上的表现 | `We also annotated 1250 randomly selected MEDLINE records as a gold standard. On this set we achieved 96.5% precision and 83.2% recall.` |
| 上述结果的绝对计数 | `The gold standard of 1250 MEDLINE records includes 1221 true SF-LF pairs. Our algorithm identified 1053 pairs with 1016 correct pairs – 96.5% precision and 83.2% recall.` |
| 去掉最弱策略 AnyLet 后 | `The resulting algorithm achieves 96.9% precision and 83.1% recall.` |
| Medstract gold 规模 | `The gold standard of Medstract has 168 SF-LF pairs.` |
| 与同期方法对比（原文） | `For comparison Schwartz and Hearst achieved 96% precision and 82% recall ..., Chang et al. achieved 80% precision and 83% recall, and Pustejovsky et al. achieved 98% precision and 72% recall.` |
| 高 P-precision 也会错的已知反例 | `The SF "IVA-SIV" was matched to "Ivanovas-Sieve colony" with 0.99 P-precision.` |

**可运行性与许可（"现在还能装吗"）**：Ab3P 官方仓库 `ncbi-nlp/Ab3P`，51 stars，**最后 push 2020-07-06**，`archived=false`，**0 个 release**，5 个未关闭 issue——**仓库仍在线未下线**。它是纯 C++ 库（`identify_abbr.C`、`lib/Ab3P.C`、`lib/AbbrStra.C`、`lib/MPtok.C`、`Makefile`，带 `MED1250_labeled`、`MED1250_unlabeled`、`WordData/Ab3P_prec.dat`），README 第 1 步要求先安装 `ncbi-nlp/NCBITextLib`。许可证为公有领域，README 原文：`This work is a 'United States Government Work' … thus cannot be copyrighted within the United States. The data is freely available to the public for use.`（GitHub API 的 `license` 字段为空，仓库内无 LICENSE 文件）。**PyPI 上不存在名为 `ab3p` 的包**（`https://pypi.org/pypi/ab3p/json` → HTTP 404）；存在的替代是 `pyab3p`（summary 原文 `Python bindings for Ab3p`，ver 0.1.1，2024-08-29 上传）。

**Schwartz-Hearst（PSB 2003）**（[论文 PDF](http://psb.stanford.edu/psb-online/proceedings/psb03/schwartz.pdf)）：

| 结论 | 数字（原文引用） |
|---|---|
| 标准测试集 | `The algorithm achieves 96% precision and 82% recall on a standard test collection, which is at least as good as existing approaches.` |
| 更大测试集 | `It also achieves 95% precision and 82% recall on another, larger test set.` |
| Medstract 评测细节 | `the Medstract Gold Standard Evaluation Corpus, which includes 168 <short form, long form> pairs. On a corrected version of the gold standard, the algorithm identified 143 pairs. Out of these, 137 pairs were correct, resulting in a recall of 82% at precision of 96%.` |
| yeast 语料细节 | `1000 MEDLINE abstracts were randomly selected from the results of a query on the term "yeast". These were then hand tagged, producing a list of 954 correct <short form, long form> pairs.` |
| 无需训练数据 | `A notable advantage of the algorithm is that, unlike other approaches, it does not require any training data.` |
| 速度（用于论证纯规则法成本极低） | `using an IBM T21 laptop with a single CPU (800 MHz, 256 Mb RAM) running MS-Windows 2000, it takes our algorithm about 1 second to process 1000 abstracts` |

论文页码/会议为 PSB 2003；scispacy 论文引用时写成 "Pacific Symposium on Biocomputing, pages 451–62"，并把年份写成 2002（**属 scispacy 的笔误**，可原样引用作为勘误说明）。可用开源实现是 scispacy 的 `abbreviation.py`（Apache-2.0）与 PyPI `abbreviations`（ver 0.2.5，2019-12-30 上传，summary 原文 `Python3 implementation of the Schwartz-Hearst algorithm for extracting abbreviation-definition pairs`；其许可**未验证**）。

**论文可直接用的总结句**：规则法缩写抽取的精度天花板约 96–97%、召回约 82–85%；Ab3P 与 Schwartz-Hearst 都只做「配对字符串对齐」，不解决大小写/单复数之外的同义异形。

---

### 1.10 scispacy：采用 Schwartz-Hearst，但论文没报数字

- **文献信息纠正**：scispacy 论文实际发表于 **BioNLP 2019 Workshop**（anthology `W19-5034`，DOI `10.18653/v1/w19-5034`），**不是 2020**；OpenAlex 记录的 `publication_year` 为 2019。来源：[aclanthology.org/W19-5034.pdf](https://aclanthology.org/W19-5034.pdf)。
- **论文没有给出 AbbreviationDetector 的任何 precision/recall 数值**。原文只有定性表述 `Figure 3 demonstrates the improved recall of gold concepts`；Figure 3 标题为 `Gold Candidate Generation Recall for different values of K`，即只有 recall–K 曲线（图形，非数字表）。**引用时不能声称 scispacy 报告了缩写检测准确率。**
- 论文明确采用 Schwartz-Hearst：`To partially remedy this, we implement the unsupervised abbreviation detection algorithm of Schwartz and Hearst (2002), substituting mention candidates marked as abbreviations for their long form definitions before searching for their nearest neighbours.`
- **为什么缩略语处理值得做（对本项目动机直接有用）**：`During development of the candidate generator, we noticed that abbreviated mentions account for a substantial proportion of the failure cases where none of the generated candidates match the correct entity.`
- 代码确认：`scispacy/abbreviation.py` docstring 原文 `Implements the abbreviation detection algorithm in 'A simple algorithm for identifying abbreviation definitions in biomedical text.', (Schwartz & Hearst, 2003).`（[raw 源码](https://raw.githubusercontent.com/allenai/scispacy/main/scispacy/abbreviation.py)）
- 许可证 **Apache-2.0**（仓库 `LICENSE` 为 Apache License 2.0 全文；PyPI classifier `License :: OSI Approved :: Apache Software License`）。
- 版本与依赖：最新 **0.6.2**，最后一次上传 **2025-10-01**，共 17 个 release；`requires_dist` 原文约束 **`spacy<3.9.0,>=3.7.0`**，另有 `numpy<2.0`、`nmslib>=1.7.3.6; python_version < "3.10"`、`nmslib-metabrainz==2.1.3; python_version >= "3.10"`、`scikit-learn>=0.20.3`、`scipy`、`pysbd`、`joblib`、`conllu`。
- 规模参考：`all data associated with the candidate generator including cached vectors for 2.78M concepts occupies 1.1GB of space on disk`。

---

### 1.11 SapBERT：别名归一的收益与「硬负例」

- **文献信息纠正**：`aclanthology.org/2021.naacl-main.272.pdf` 是 **ZS-BERT，不是 SapBERT**；SapBERT 的正确位置是 [2021.naacl-main.334](https://aclanthology.org/2021.naacl-main.334/)，DOI `10.18653/v1/2021.naacl-main.334`。
- **论文通篇不含 "abbreviation" 一词**（对提取文本全文检索命中 0 次）。它只按数据集报 recall@1/@5，**没有基因名、蛋白名、短缩写字面形式的切片评测**——凡引用「SapBERT 在缩写实体上的表现」，只能引 1.12 的 EMNLP 2023 评测。
- PubMedBERT + SapBERT 的 recall@1/@5（Table 1）：`NCBI 92.0 / 95.6`、`MedMentions 70.5 / 88.9`、`AskAPatient 65.9 / 77.9`；对照 PUBMEDBERT 为 `77.8 / 86.9`、`42.5 / 49.6`、`46.8 / 53.2`。注意：该表由 `pdftotext -layout` 解析，双栏交错，这几组数字与正文论述一致，但更细的 BC5CDR 列存在错行风险，**未逐格复核**。
- 论文对规模的描述：`We design a scalable metric learning framework that can leverage UMLS, a massive collection of biomedical ontologies with 4M+ concepts.`；`UMLS 2020AA has 4M+ concepts and 10M+ synonyms which stem from over 150 controlled vocabularies including MeSH, SNOMED CT, RxNorm, Gene Ontology and OMIM`（[arXiv 2010.11784](https://arxiv.org/abs/2010.11784)）。
- **硬负例的存在（对本项目直接相关）**：`Most of Hydroxychloroquine's variants are easy: Hydroxychloroquin, Hydroxychloroquine (substance), Hidroxicloroquina, but a few can be very hard: Plaquenil and HCQ.` ——这是「纯字面相似度不可能解决同义异形」的最短论证。
- 官方 model id（[SapBERT README](https://raw.githubusercontent.com/cambridgeltl/SapBERT/master/README.md) 原文）：`cambridgeltl/SapBERT-from-PubMedBERT-fulltext`（用 `[CLS]` before pooler）与 `cambridgeltl/SapBERT-from-PubMedBERT-fulltext-mean-token`（mean-pooling）；base model 为 `microsoft/BiomedNLP-PubMedBERT-base-uncased-abstract-fulltext`，训练数据 UMLS 2020AA English only。跨语言版本 model id：`cambridgeltl/SapBERT-UMLS-2020AB-all-lang-from-XLMR`、`cambridgeltl/SapBERT-UMLS-2020AB-all-lang-from-XLMR-large`。
- 运行环境要求（官方原文）：`The code is tested with python 3.8, torch 1.7.0 and huggingface transformers 4.4.2.`——对本机 Python 3.13 是风险点。
- 许可证**未验证**（GitHub API 对该仓库返回 403 限流，未取到 `license.spdx_id`；huggingface.co 在本机被墙，未读到 model card 的 license 字段）。

---

### 1.12 EMNLP 2023 生医实体链接评测：0.075 / 0.753

论文身份已确认：**A Comprehensive Evaluation of Biomedical Entity Linking Models**，作者 Kartchner D, Deng J, Lohiya S, Kopparthi T, Bathala P, Domingo-Fernández D, Mitchell CS；**Proceedings of EMNLP 2023**, pages 14462–14478，DOI `10.18653/v1/2023.emnlp-main.893`，PMCID PMC11097978；代码 <https://github.com/davidkartchner/biomedical-entity-linking>。来源：[PDF](https://aclanthology.org/2023.emnlp-main.893.pdf)。

| 结论 | 数字（原文） |
|---|---|
| **0.075 / 0.753 属实** | Figure 2 原表：`SapBERT 0.883 0.934 0.611 0.786 0.637 0.788 0.234 0.614 0.812 0.889 0.075 0.348 0.753 0.896`，列顺序 `BC5CDR MM-Full MM-ST21PV GNormPlus NLM-Chem NLM-Gene NCBI-Disease`，每列含 @1 与 @5 |
| 精确解读 | SapBERT 在 **NLM-Gene（基因）recall@1 = 0.075、recall@5 = 0.348**；在 **NCBI-Disease（疾病）recall@1 = 0.753、recall@5 = 0.896**。注意 0.753 是疾病数据集整体值，**不是「长名疾病实体」的专门切片** |
| 论文核心结论 | `Systematic evaluation reveals several notable gaps in current methods. In particular, current methods struggle to correctly link genes and proteins and often have difficulty effectively incorporating context into linking decisions.` |
| 缩写消解（AR）的整体增益与反例 | `AR improved performance by up to 69.5% on abbreviated entities in some datasets. However, this was not the case for gene normalization where AR showed a negative or insignificant effect. We hypothesize this is because genes are more commonly referred to by their abbreviations than by their longer full names, which limits the usefulness of AR.` |
| AR 逐数据集绝对增益（Table 8） | 列 `SapBERT / MetaMap / KrissBERT / SciSpacy / ClusterEL / ArboEL`；BC5CDR `0.598 / 0.588 / 0.136 / 0.695 / 0.329 / 0.263`；MM-Full `0.426 / 0.472 / 0.142 / 0.408 / 0.181 / N\A`；MM-ST21PV `0.398 / 0.454 / 0.131 / 0.403 / 0.187 / 0.198`；GNormPlus `0.039 / 0.004 / 0.019 / -0.169 / -0.039 / 0.004`；NLM-Chem `0.644 / 0.602 / 0.058 / 0.548 / 0.33 / 0.375`；**NLM-Gene `0.058 / 0.018 / -0.003 / 0.003 / -0.063 / -0.087`**；NCBI-Dis `0.139 / 0.468 / 0.035 / 0.381 / 0.221 / 0.091`；**Overall `0.447 / 0.464 / 0.095 / 0.426 / 0.22 / 0.227`** |
| 缩写消解用的工具 | `We used Ab3P (Sohn et al., 2008) to identify and (optionally) resolve abbreviations at train/inference time.` |
| 字面差异导致失败的实例 | `the entity ncbigene:37970 can be referred to by the aliases 'ORC', 'ORC4', 'origin recognition complex subunit 4', 'CG2917', 'rDmORC', 'dmOrc4', etc.`；`within the Unified Medical Language System (UMLS), 'AD' could refer to Alzheimer's Disease, Atopic Dermatitis, Actinomycin D, or Admitting Diagnosis.` |
| 别名匹配切片效应（Table 6，micro 平均绝对差） | `has_alias_match 0.280`；`no_alias_match -0.052`；`wrong_alias_match -0.259`；`single_alias -0.075`；`five_alias_or_less -0.074`；`no_definition -0.101`；`zero_shot -0.051` |
| 参与评测的模型 | 摘要原文 `comprehensively evaluate nine recent state-of-the-art biomedical entity linking models`：SapBERT、MetaMap、KRISSBERT、SciSpacy、MedLinker、ClusterEL、ArboEL、BioBART、BioGenEL |

**三条可写进论文的硬结论**：① 同一模型在基因上 recall@1 只有 0.075，说明「实体名越短、越依赖字面」的领域，字面归一化本身的收益上限很低；② 缩写消解不是万能药，在基因上甚至负收益（NLM-Gene：ClusterEL `-0.063`、ArboEL `-0.087`）；③ `wrong_alias_match -0.259` 与 `has_alias_match 0.280` 的对照说明**别名匹配错误会主动伤害结果**，因此「宁可不合并」比「尽量合并」更安全。

---

### 1.13 LLM 时代的 ER 方法（2024–2026，仅论文论证）

这些是「同类工作」章节的素材，其中只有 COMEM 的结论对本项目选型有直接影响。本节所有条目除注明外，**只取到 arXiv 摘要，摘要未含数值，故具体指标未验证**。

| 方法 | 论文 / 年份 | 原文报告指标 |
|---|---|---|
| **COMEM**（Match / Compare / Select 复合框架） | `Match, Compare, or Select? An Investigation of Large Language Models for Entity Matching`，[arXiv 2405.16884](https://arxiv.org/abs/2405.16884)，2024 | Table 4：`Ditto (Li et al., 2020)` 八数据集 **Mean F1 80.69**；`HierGAT (Yao et al., 2022)` 83.34；`ZeroER` 49.67；`Sudowoodo` 70.32；GPT-4o Mini 下 `COMEM` **Mean 86.42**（含成本列，约 $0.09）。正文：`the comparing strategy outperforms the independent matching strategy by an average of 10.7% F1 score, and the selecting strategy further improves the F1 score by an average of 5.32%`；`COMEM achieves 2~18% F1 improvements while spending less`。**可本地跑（部分）**：论文明确测了 8 个开源 instruct 模型 `Llama-3.1-8B, Qwen2-7B, Mistral-7B, Mixtral-8x7B, Command-R-35B, Flan-T5-XXL, Flan-UL2 and Solar-10.7B`，并称 `open-source medium-sized (3B~11B) LLMs` 表现良好 |
| **BATCHER** | `Cost-Effective In-Context Learning for Entity Resolution: A Design Space Exploration`，[arXiv 2312.03987](https://arxiv.org/abs/2312.03987)（ICDE 2024，DOI `10.1109/icde60146.2024.00284`） | 摘要原文：`we introduce a framework BATCHER consisting of demonstration selection and question batching … we devise a covering-based demonstration selection strategy that achieves an effective balance between matching accuracy and monetary cost`（摘要无具体数值） |
| **BoostER** | `BoostER: Leveraging Large Language Models for Enhancing Entity Resolution`，[arXiv 2403.06434](https://arxiv.org/abs/2403.06434)，2024 | 摘要原文：`Our approach optimally selects a set of matching questions and poses them to LLMs for verification, then refines the distribution of entity resolution results with the response of LLMs.` |
| **LLM-CER** | `In-context Clustering-based Entity Resolution with Large Language Models: A Design Space Exploration`，[arXiv 2506.02509](https://arxiv.org/abs/2506.02509)，2025 | 摘要原文：`we propose a novel in-context clustering approach for ER, where LLMs are used to cluster records directly, reducing both time complexity and monetary costs … we develop LLM-CER (LLM-powered Clustering-based ER), which achieves high-quality ER results while minimizing LLM API calls` |
| **DistillER** | `DistillER: Knowledge Distillation in Entity Resolution with Large Language Models`，[arXiv 2602.05452](https://arxiv.org/abs/2602.05452)，2026 | 摘要原文：`DistillER, the first framework that systematically bridges this gap across three dimensions: (i) Data Selection … (ii) Knowledge Elicitation, where we compare single- and multi-teacher settings across LLMs and smaller language models (SLMs); and (iii) Distillation Algorithms, where we evaluate supervised fine-tuning and reinforcement learning approaches.` **方向与本机 8GB 显存约束一致**（把大模型能力蒸馏到 SLM） |
| **SGER** | `Structure-Guided Entity Resolution: Fine-Tuning LLMs for Robust Name Matching in Complex Linguistic Contexts`，[arXiv 2605.23597](https://arxiv.org/abs/2605.23597)，2026 | 摘要原文：`SGER achieves 99.02% accuracy and an F1 of 0.994 on a held-out set of 50,000 real-world pairs, outperforming GPT-4o few-shot prompting and single-stage fine-tuning baselines. The system is fully deployed in production at Dream11 … serving 250M+ users.` **代码/权重是否开源未验证** |
| 权威基准 | `WDC Products: A Multi-Dimensional Entity Matching Benchmark`，[arXiv 2301.09521](https://doi.org/10.48550/arXiv.2301.09521)，2023 | 二手（OpenAlex 元数据核实 title/year/DOI，**未读全文**） |

---

### 1.14 未能验证的项（本节相关）

以下条目**本次未取得可引用原文或未实测**，不得当作业已确认：

1. `apoc.refactor.mergeNodes` 的 `selfRef`、`countMerge`、`collapsedLabel`、`avoidDuplicates`、`relationshipSelectionStrategy` 五个 config 键的语义——官方文档无文字说明。
2. **APOC 合并可否撤销**：未找到任何官方 undo / rollback 过程；「不可逆」是由「返回值只含合并后节点」结构推出的，未做实测回滚实验。
3. **Neo4j 是否曾以博客/文档形式「推荐」tombstone 或 `SAME_AS` 模式**：只找到 Labs 项目实现与社区帖，**未找到**规范性推荐文本（否定性结论，置信度中）。
4. **Neo4j 5.x / 2025.x 内核无 ER 功能**：属否定性检索，仅覆盖官方 ER 博客、2025.06 发布说明、GraphRAG 文档三处，未做全站/全版本穷举。本机 Neo4j 内核实际版本**未探测**。
5. **`n10s.onto.mergeNodesWithSameUri` 的绝对不存在性**：已穷举 n10s 4.0 参考文档 44 个过程与 `2025.06` 分支源码树，但未逐文件 grep 全部 Java 源码、未检索历史版本。
6. **企业版 point-in-time recovery / 事务日志恢复的官方原文**：`neo4j.com/docs/operations-manual/current/backup-restore/recovery/` 与 `/backup-restore/restore-backup/` 两次抓取均失败；Jina Reader 取的 `restore-backup` 正文中 `point-in-time` 命中 0。Cypher 事务文档 `https://neo4j.com/docs/cypher-manual/current/transactions/` 多次抓取失败，**故「已提交事务不可回滚」缺乏本次抓到的官方原文**。
7. **`neo4j-graphrag-python` 历史版本**（如 0.x）中是否曾存在 `LLMEntityResolver` 或 `ExactMatchResolver`：仅核对了 `main` 分支源码、文件树与 `CHANGELOG.md`，未逐版本回溯。且 `main` 分支 commit SHA 未固定（调研后段 `api.github.com` 返回 403）。
8. **Cypher 事务回滚**与 `Zingg` 的精度 benchmark、`Splink` 官方精度分数表：均无绝对值证据（Splink 只有 FCDS 的 TP 相对量）。
9. **Ab3P 的 conda 渠道情况**：`api.anaconda.org/search?q=ab3p` 返回 HTTP 400，未取到任何信息。
10. **Ab3P 在 Python 3.13 / Windows / RTX 4060 上的实际可编译可安装性**：只确认 PyPI 无 `ab3p` 包、存在 `pyab3p`（需 Ab3P + NCBITextLib C++ 本体），**未实测 `pip install pyab3p`**。
11. **SapBERT 的许可证**（GitHub API 403、huggingface.co 被墙）；**SapBERT 在 ModelScope 是否有镜像**（尝试的 API 形态返回 404）。
12. **scispacy 0.6.2 在 Python 3.13 上的可安装性**：`nmslib` / `nmslib-metabrainz` 是 C++ 扩展，PyPI classifier 只声明到 Python 3.6，未实测。
13. **scispacy AbbreviationDetector 的 precision/recall**：论文确实没报数字（非抓取失败）。
14. **SapBERT 在「短缩写字面形式」上的专项 recall@1**：SapBERT 论文全文无 `abbreviation`，原文无此项。
15. **BATCHER / BoostER / LLM-CER / DistillER 的具体实验数字**：只取到 arXiv 摘要，未下载全文逐表核对。
16. **SGER 的代码/权重是否开源**；**Senzing 引擎本体的许可证与定价**（官网 403，只能确认其 GitHub 生态仓库与 PyPI 客户端包为 Apache-2.0）。
17. **`recordlinkage` 的 README 原文**（`master/README.rst` 返回 404）；**Ditto 是否曾以 EMNLP 2020 论文形式发表**（检索未命中，非已排除）。
18. **OpenAlex 对 2024–2026 实体解析的系统性检索**：该 API 本轮持续返回 HTTP 429，1.13 的清单来自 arXiv 检索，**覆盖面可能不全**。

---

### 1.15 对本项目的直接结论

1. **合并动作必须换成「先建边、后合并」，并把阈值拆成两档。** 官方 `FuzzyMatchResolver` 是 `>= 0.8` 一次性 `mergeNodes` 硬删（1.7），而官方 Labs 的 agent-memory 用 `auto_merge_threshold = 0.95` / `flag_threshold = 0.85` 分档，0.85–0.95 只建 `status: 'pending'` 的 `SAME_AS` 边（1.6）。本项目应把自动合并线抬到 **0.95**、复核线放在 **0.85**，`0.8` 只能作为「进入候选池」的门槛，不能作为删除节点的门槛。理由不只是保守：EMNLP 2023 的 `wrong_alias_match -0.259` 说明错误别名会主动恶化结果（1.12）。
2. **不要调用 `apoc.refactor.mergeNodes` 做常规消歧。** 它合并到列表首节点、返回里没有任何原节点引用（`cloneNodes` 才有 `(input, output, error)`），且未点名的属性一律被覆盖、`discard` 丢名 / `combine` 把主键变数组（1.5）。若确需硬合并，必须先在应用层把被合并节点的完整属性快照落盘（`apoc.trigger` 可作变更钩子），并把 `properties` 逐属性写全，不能依赖默认值。
3. **采用 agent-memory 的 tombstone + 别名模型，而不是删节点。** 具体三件套：`SET source.merged_into = target.id, source.merged_at = datetime()`；`target.aliases` 追加 source 名；所有读取路径加 `WHERE merged_into IS NULL`（1.6 原文）。别名簇查询可直接用 `MATCH path = (e)-[:SAME_AS*1..3]-(other) RETURN DISTINCT other, length(path)`。这样 3D 可视化里的历史视图与「撤销合并」都有数据可依。
4. **实体名规范化分两段做，且不要指望规则法解决同义异形。** 缩写/全称配对可用 Schwartz-Hearst 系算法（scispacy `abbreviation.py`，Apache-2.0；或 PyPI `abbreviations`），但要按它的天花板定位期望：**精度 95–97%、召回 82–85%**（Ab3P 96.5 P / 83.2 R，Schwartz-Hearst 96 P / 82 R），且它对 `Plaquenil` vs `HCQ` 这类硬负例完全无效（1.9–1.11）。不要引入 Ab3P C++ 本体（PyPI 无 `ab3p` 包、需先编译 NCBITextLib、仓库 2020 年后停更）。
5. **保留「长度保护」并显式记录不合并的决策。** 官方 `FuzzyMatchResolver` 在比较前会 lowercase、strip、去标点（`processor=utils.default_process`），短串（缩写、`UI` 之类）天然容易过 0.8 线；1.4 已用真实数据验证了误合并，结论不变。同时把「判定不是重复」写成 `status: 'rejected'` 的边而不是丢弃——agent-memory 的 `review_duplicate(confirm=False)` 就是这个语义（1.6），本项目的人工审核队列应照此留痕。
6. **评测口径用 Splink 的 ROC + ground truth 簇列，而不是单个 F1；合成数据用 FEBRL。** Splink（MIT）官方示例给出了 `Accuracy analysis and ROC charts using a ground truth (cluster) column`，并有 FCDS 2024 在 670,214 条模拟记录上的第三方背书（比 fastLink 约多 9% TP，且明确推荐 Splink，但**只有 TP 相对量、无 F1 绝对值**）（1.8）。FEBRL 数据集经由 `recordlinkage.readthedocs.io/en/latest/ref-datasets.html` 分发；跨方法对比可复用 COMEM 所用的 pyJedAI 八个 clean-clean 数据集（1.8、1.13）。不要引入 Zingg（AGPL-3.0）、LinkTransformer（GPL-3.0）或 Senzing（引擎许可未验证）进生产链路。

---

## 二、关系抽取质量

> **本节的一句话结论**：调研所见，**没有任何通用评测框架提供三元组集合级的
> P/R/F1**（§3.4 的横向对比逐项核实过），所以这一层必须自建。
> 因此本节不重复框架罗列，只记录本项目自建 L5 时的实测结论与外部依据。

### 2.1 从"关系存在吗"改问"关系能被原文支持吗"

主观关系标注这条路已被证伪：4 个独立 LLM 标注者在关系层的
Krippendorff α = **-0.0119 / -0.1465**（负值），留一法下剔除任一标注者仍全为负，
且 67.5% / 74.9% 的候选只被 1 人提出（`docs/GOLD_METRIC_DIAGNOSIS.md` §7）。
换成一个有唯一答案的问题——「这条关系能不能被它自带的原文证据支持」——
判定可复现、零成本、不需要金标。实现见 `tools/relation_verifier.py`。

### 2.2 判别器与阈值都不照抄文献

| 模型 | 官方校准集 ROC-AUC | 宏平均 F1 | 中文实测 AUC | 结论 |
| --- | --- | --- | --- | --- |
| **Babelscape/mdeberta-v3-base-triplet-critic-xnli** | **0.9851** | **0.943** | **0.9966** | ✅ 采用 |
| cross-encoder/nli-deberta-v3-base | 0.6370 | 0.427 | 0.5445（≈随机） | ❌ 弃用 |

阈值同理：模型卡写 0.75，而 RED^FM 论文附录 A 原文是 *"we set a lower threshold,
**0.1**"*（0.75 属 cRocoDiLe 的 NLI 过滤阶段）。本机用官方 11998 行校准集自搜，
取宏平均 F1 最优点 **0.67**。详见 `docs/GRAPH_QUALITY_ASSESSMENT.md` §1.5。

### 2.3 实测结果与最大短板

全量 44032 条边有分数（280 秒，¥0），L5 通过率 **63.7%**。
但 **L3 端点完备性只有 68.5%**——31.5% 的边至少有一端不是已抽取的节点，
这是当前最大短板，且 **L5 过滤对它毫无改善**（68.5% → 68.6%），
两者是正交维度。想提升 L3 只能靠补抽取或端点归一化。

### 2.4 一个已确证的能力盲区：方向

六层里**没有任何一层能可靠发现关系方向错误**——L3/L4 根本不看方向，
L5 实测只能拦住 **23.9%** 的方向反转（本体 12 类非对称关系 720 条样本）。
而 T3.1 已用规则反转了 2348 条边。详见 `docs/L5_SIX_LAYER_REPORT.md` §4.5。

补救方向是**独立于语义模型的规则层**（端点类型约束），而不是复用 L5；
但那要为本体 15 类逐条定义约束，属本体设计决策，尚未实施。

---

## 三、质量评测框架

素材取自原报告《LLM 构建的知识图谱质量评测：方法、框架与可落地指标调研》；数字、指标名、框架名与链接原样保留，原报告标「未验证」的同样标注。证据强度三档：**已抓取**（HTTP 200 且读到内容）、**已索引**（搜索结果或 OpenAlex 确认存在，未读正文）、**未验证**（未确认 URL）。抓取失败域名（网络受限，非链接失效）：`arxiv.org`、`ar5iv.labs.arxiv.org`、`r.jina.ai`、`huggingface.co/papers`、`docs.smith.langchain.com`；`web_search` 中途返回 `DeepSeek API error (HTTP 402): Insufficient Balance` 后停用。调研对象：LLM 从 18 篇 arXiv 综述抽取的文献 KG，16301 实体 / 41745 关系，15 类实体本体 + 15 类关系本体。

### 3.1 GraphRAG / GraphRAG-Bench 系列

**GraphRAG-Bench（ICLR'26）**：论文《When to use Graphs in RAG: A Comprehensive Analysis for Graph Retrieval-Augmented Generation》，arXiv 2506.05690；[论文页](https://arxiv.org/abs/2506.05690)（已索引）、[代码库](https://github.com/GraphRAG-Bench/GraphRAG-Benchmark)（已抓取 README 与 `Evaluation/README.md`）、[排行榜与数据集站](https://graphrag-bench.github.io/)（已索引）、[HuggingFace 数据集](https://huggingface.co/datasets/GraphRAG-Bench/GraphRAG-Bench)（已索引）。作者 Xiang Zhishang、Wu Chuanjie、Zhang Qinggang 等（厦门大学、吉林大学）；README 的 News 段确认 ICLR'26 接收。

评测设计把流水线拆三段，**图谱构建质量与下游问答质量分开评**：**Generation** 按题型给指标（Fact Retrieval / Complex Reasoning → ROUGE-L、Answer Correctness；Contextual Summarization → Answer Correctness、Coverage Score；Creative Generation → Answer Correctness、Coverage Score、Faithfulness）；**Retrieval** → Context Relevancy、Evidence Recall；**Indexing（图谱构建质量）** → density、connectivity、clustering coefficient、实体/关系分布。脚本入口 `Evaluation.generation_eval` / `retrieval_eval` / `indexing_eval`，其中 `indexing_eval` 支持 Microsoft GraphRAG（`entities.parquet` + `relationships.parquet`）、LightRAG（`graph_chunk_entity_relation.graphml`）、Fast-GraphRAG、HippoRAG2 及**通用 GraphML**。局限：可补「结构指标规范化」空白（项目原报 Louvain 模块度 0.675、度集中度、孤立节点率、证据完整率缺参照系；报告建议用 `networkx` 复现同组定义），但只看结构不看语义，须与语义指标联合报告并绑定「同规模随机图基线」。

**同名基准 GraphRAG-Bench（领域特定推理）**：arXiv 2506.02404（已索引）、[Semantic Scholar 阅读页](https://www.semanticscholar.org/reader/9cf732974468fc887a84c53568fa109cb19939e4)（已索引）；聚焦文学/医学等领域推理评测，与前者是两条独立路线；未抓全文，**作者与指标细节未验证**。工程侧另有 [FalkorDB GraphRAG-SDK benchmark 文档](https://github.com/FalkorDB/GraphRAG-SDK/blob/main/docs/benchmark.md)（已索引，未读）与 [futureagi 博客综述](https://futureagi.com/blog/how-to-evaluate-graphrag-pipelines/)（已索引，非学术来源，不建议引用）。

### 3.2 RAGAS：能评什么，图谱部分能不能迁移

[代码库 vibrantlabsai/ragas](https://github.com/vibrantlabsai/ragas)（已抓取 README；原 `explodinggradients/ragas` 已迁移至新组织）、[文档](https://docs.ragas.io/)、pip 包名 `ragas`；[指标总索引](https://raw.githubusercontent.com/explodinggradients/ragas/main/docs/concepts/metrics/available_metrics/index.md)（已抓取）。

官方指标分组（已抓取）：**RAG** —— Context Precision、Context Recall、**Context Entities Recall**、Noise Sensitivity、Response Relevancy、Faithfulness、Multimodal Faithfulness、Multimodal Relevance；**Nvidia** —— Answer Accuracy、Context Relevance、Response Groundedness；**Agent/工具调用** —— Topic Adherence、Tool Call Accuracy、**Tool Call F1**、Agent Goal Accuracy；**自然语言比较** —— Factual Correctness、Semantic Similarity、Non LLM String Similarity、BLEU、CHRF、ROUGE、String Presence、Exact Match；**SQL** —— Execution based Datacompy Score、SQL query Equivalence；**通用** —— **Aspect Critic**、**Simple Criteria Scoring**、**Rubrics based scoring**、**Instance specific rubrics scoring**；其他 —— Summarization。

**结论：RAGAS 没有知识图谱专用指标。** 最接近图谱的 `ContextEntitiesRecall` 评的是「检索上下文覆盖了参考答案中多少实体」，不是「图谱本身对不对」。可迁移三条路径：① **Context Entities Recall → 实体召回**，公式 $\text{Context Entity Recall} = \frac{|\text{RCE} \cap \text{RE}|}{|\text{RE}|}$（`RE` = 参考答案实体集合，`RCE` = 检索上下文实体集合），把金标实体集当 `RE`、系统输出实体集当 `RCE` 即得实体召回（本项目 R = 0.95 已很高，用不用无所谓）。② **Faithfulness → 三元组幻觉率**：官方做法把回答拆成原子陈述（statements）逐条判断是否被上下文支持，得分 = 被支持陈述数 / 总陈述数，**可原样套在三元组上**——每条三元组转成自然语言陈述、判断是否被原文支持，得「陈述级 groundedness」，比「证据完整率」更严格（判「原文是否支持」而非「是否附了引文」）。③ **Rubrics based scoring / Aspect Critic / DiscreteMetric → 自定义判定器**：README 给了 `DiscreteMetric` 完整示例（自定义 `allowed_values` 与 prompt），适合「实体类型是否属于 15 类本体」「关系方向是否正确」这类二值判定。

**局限**：① 所有 LLM 指标是 0–1 连续打分或离散标签，**不产出集合级 P/R/F1**；② LLM-as-judge 的方差与偏差无内建缓解，需自行多模型投票与校准；③ 输入契约 `user_input / response / retrieved_contexts / reference` 与 `(head, relation, tail, evidence)` 图谱结构不对齐，需写适配层。

### 3.3 知识图谱质量评估的经典指标体系

**KGTorrent 澄清**：[KGTorrent](https://2021.msrconf.org/details/msr-2021-data-showcase/4/)（MSR 2021 Data Showcase，已索引；[Semantic Scholar 页](https://www.semanticscholar.org/paper/24dbf2b111ad3e5f9984f127c694e9298a92a7f8) 已索引）是 Kaggle Jupyter Notebook 语料数据集，**与 KG 质量评估无关**；论文写「参考 KGTorrent」会被评审直接指出概念错误，应换成 KGHeartBeat / Luzzu / KGrEaT。

**Zaveri 体系与 KGHeartBeat**：理论来源 Zaveri et al., *Quality assessment for Linked Data: A Survey*, Semantic Web Journal, 2016（已索引；[semantic-web-journal.net](https://www.semantic-web-journal.net/) 的 PDF 抓取失败，因 web_fetch 不支持 `application/pdf`）。实现 KGHeartBeat：[代码库](https://github.com/isislab-unisa/KGHeartBeat)（已索引）、[指标文档](https://isislab-unisa.github.io/KGHeartBeat/)（**已完整抓取**）、[PyPI 包 `kgheartbeat`](https://pypi.org/project/kgheartbeat/)（已索引）；6 大类约 40 个指标——Accessibility、Intrinsic、Trust、Dataset dynamicity、Contextual、Representational。核心条目与口径（原文已抓取）：

- **Intrinsic — Semantic Accuracy**：Functional property violation `FP = 1 - (函数属性取值不一致的三元组数 / 三元组总数)`；Inverse functional violation `IFP = 1 - (逆函数属性取值不一致的三元组数 / 三元组总数)`；Empty annotation labels（标签/注释为空字符串的三元组比例）；Datatype consistency（值域数据类型不兼容的比例）；White space in annotation（标签含多余空白字符的比例）。
- **Intrinsic — Consistency**：Entities as members of disjoint classes（`互斥类成员实体数 / 实体总数`）；Misplaced classes or properties（定义为 class 的 URI 被当 property 用，或反之）；Invalid usage of undefined classes and properties（使用无形式化定义的类/属性的比例）；Use of deprecated classes or properties（`owl:DeprecatedClass` / `owl:DeprecatedProperty`）；Ontology hijacking（第三方重定义外部类/属性）。
- **Intrinsic — Conciseness**：Intensional conciseness（`数据集去重后属性数 / 目标 schema 属性数`）；Extensional conciseness（`去重后对象数 / 对象表示总数`）。
- **Contextual — Completeness / Amount of data**：Interlinking completeness（缺失的互链比例）；Number of triples / Level of detail / Scope（三元组数 / 属性数 / 实体数）。
- **Representational**：URI 长度合理性、是否复用既有词表、`rdfs:label` 覆盖率、是否使用空白节点、多语言标签、多序列化格式。

对本项目：15 + 15 类本体即小型 schema，以下指标**可零成本算出且有文献依据**——**本体依从率**（对应 Misplaced / Undefined，实体类型与关系谓词落在 15 + 15 约定内的比例，纯规则计算）、**类型冲突率**（对应 disjoint class 违反）、**函数关系违反率**（如「发表于（Venue）」这类应唯一谓词，同一论文出现多个不同尾实体的比例）、**简洁度**（`去重后关系谓词数 / 15`；产出 400 个自由谓词而本体仅 15 类时立刻暴露本体未被遵守）、**标签完整率**。**局限**：许多指标依赖 SPARQL endpoint、VoID 元数据、URI 可解引用性与 HTTP 延迟吞吐——**只取 Intrinsic + Contextual + Representational 中有意义的子集，不要照搬整体框架**。

**Luzzu**（[代码库](https://github.com/eis-bonn/Luzzu) 已索引，`master`/`main` 的 raw README 均 404；[CEUR PDF](https://ceur-ws.org/Vol-1486/paper_74.pdf) 已索引，未读）：*Luzzu — A Framework for Linked Data Quality Assessment*，CEUR Vol-1486, paper 74；Java/Maven，指标为可插拔的「度量（metric）+ 维度（dimension）」，**适用性低**（成本高于收益），价值在其**架构思想**（度量做成独立、可组合、可版本化的对象并输出机器可读报告）。

**KGrEaT**（[代码库](https://github.com/dwslab/kgreat)，**已抓取 README**；论文 arXiv 2308.10537，CIKM 2023，[ACM DOI](https://dl.acm.org/doi/10.1145/3583780.3615241)，均已索引）：*KGrEaT: A Framework to Evaluate Knowledge Graphs via Downstream Tasks*——不做内部指标，把 KG 当背景知识喂给分类、回归、推荐三类下游任务，**固定实验设置、只把 KG 当唯一变量**，Docker 化三阶段 `Mapping → Preprocessing → Task`。**思路可借，框架不建议直接用**（需带实体链接的标注数据集 `entities.tsv` + 任务数据；README 明示生成 DBpedia/YAGO 规模 embedding 建议至少 100 GB 内存）。可借用的最小版本：**自定义下游任务作为图谱可用性的外部效度证据**（如从 18 篇论文留出若干篇，用图谱回答「X 方法与 Y 方法的关系是什么」并人工判定答对率）。

**相关综述**：*A survey on augmenting knowledge graphs (KGs) with large language models (LLMs): models, evaluation metrics, benchmarks, and challenges*，DOI `10.1007/s44163-024-00175-8`（已索引，报告评为「最贴近本课题的综述之一，建议必读」）；*Constructing knowledge graphs by Large Language Models: Methodology, practice, and challenges*，ScienceDirect `S0950705126016898`（已索引）。

### 3.4 LLM 抽取 / 结构化输出的开源评测框架

**DeepEval**（[代码库](https://github.com/confident-ai/deepeval)，**已抓取 README**；pip 包 `deepeval`）：**G-Eval** 用自然语言 `criteria` 做 LLM-as-judge（0–1 分加理由）；**DAG**（图结构确定性 LLM-as-judge 构建器）在 roadmap 中为未完成项 `[ ] DAG custom metrics`，**是否可用未验证**；**JSON Correctness** 校验输出是否符合预期 JSON schema，可统计结构合规率，另有 `Hallucination`、`Faithfulness`、`Contextual Recall / Precision` 可套在「三元组是否有原文证据」上。局限：`DAG` 未完成；不提供图结构分析。

**promptfoo**（[代码库](https://github.com/promptfoo/promptfoo) 已索引；[JSON 评测文档](https://www.promptfoo.dev/docs/guides/evaluate-json/) **已抓取**；npm 包 `promptfoo`）：`is-json`（可带 JSON Schema）、`javascript`、`transform`（先 `JSON.parse(output)` 或取字段）、`llm-rubric` / model-graded assertions。适合**抽取 prompt 的消融与回归测试**（换 prompt 后看本体依从率与结构合规率是否退化），但**不做集合级 P/R/F1**；改造点是给每条三元组写 test case 批量跑通过率（评「抽取稳定性」而非「正确性」）。

**instructor 与 outlines**（[instructor 文档](https://python.useinstructor.com/) / [GitHub](https://github.com/567-labs/instructor)，pip 包 `instructor`；[outlines](https://github.com/dottxt-ai/outlines)，pip 包 `outlines`；均已索引）：**结构化输出保障工具**而非评测框架——`instructor` 把 Pydantic 模型接到 LLM 调用上、不符 schema 时自动重试并保留 `validation` 结果（该计数即「结构合规率」）；`outlines` 用受限解码（constrained decoding）保证输出符合正则/语法/schema。价值在**降低结构错误噪声**，**这是让错误分布可解释的前置条件**。

**LangSmith**（[SDK 仓库](https://github.com/langchain-ai/langsmith-sdk) **已抓取 README**；[文档](https://docs.smith.langchain.com/) 已索引，抓取被跨域重定向拦截）：README 确认 tracing（`@traceable`、`wrap_openai`）、数据集管理与离线评测、生产监控；评测 API（`evaluate`、自定义 evaluator、LLM-as-judge）**未抓到文档正文，细节未验证**。适用性低到中。

**横向对比**：结构合规——`deepeval`、`promptfoo`、`instructor`/`outlines`（保障）、`langsmith` 具备，`ragas` 不具备；语义正确性——`ragas`、`deepeval`、`promptfoo`、`langsmith` 具备（LLM judge）；**集合级 P/R/F1——六者全部不具备**；图结构指标——仅 GraphRAG-Bench `indexing_eval` 具备。**没有任何通用框架提供三元组集合级 P/R/F1，这部分必须自建；报告建议只需批量评测时用本地脚本 + JSON 结果文件。**

### 3.5 三元组抽取的评测指标：除 P/R/F1 之外还有什么

**5.1 分层评测（最重要的方法论改进）**。三元组级严格匹配把三个独立误差源合成一个数字：头实体错、尾实体错、谓词错；F1 极低时无法定位问题。业内通用做法是**分层报告**（原报告口径）：

| 层 | 定义 | 说明 |
| --- | --- | --- |
| L1 实体层 | 系统输出的实体 span / 归一化实体 与金标实体的 P/R/F1 | 分 strict（字面相同）与 relaxed（相似度或同义判定） |
| L2 实体聚类层 | 同一真实实体的多个别名是否被正确合并 | 用 B-cubed 或 CEAF 类指标；**本项目当前完全缺失这一层** |
| L3 关系存在层 | 给定金标实体对，系统是否识别出两者存在关系（忽略谓词标签） | 剥离实体误差，单独看关系召回 |
| L4 谓词层 | 在实体对已对齐的前提下，谓词类型是否匹配 | 分离「找没找到关系」与「关系说没说对」 |
| L5 三元组层 | 端到端严格匹配 | 现有指标 |
| L6 证据层 | 每条三元组是否有可核验的原文证据 | 现有「证据完整率」，建议升级为 groundedness 判定 |

L3 与 L4 是定位「关系 F1 = 0.038」成因的关键：若 L3 尚可而 L4 接近零，问题在本体对齐或谓词命名空间；若 L3 本身接近零，问题在实体对齐或关系根本未抽出。

**5.2 严格 / 宽松 / 类型感知匹配**。**Strict match**：头、尾、谓词三者字面（或归一化后）相同。**Relaxed / partial match**：允许相似度阈值、同义词、上下位替换（本项目已实现「相似度宽松」一级）。**Type-aware match**：不要求谓词字面相同，只要求映射到同一本体关系类——**这是与 15 类关系本体最匹配的口径**，也是最可能缺失的一环；若系统输出自由谓词而金标用本体标签（或相反），严格匹配会系统性归零。标准做法是**同时报告三档**并用「档间差值」解释误差来源；只报一档会被质疑口径操纵。

**5.3 OpenIE 传统**。**OIE2016** 是第一个大规模 OpenIE 自动评测基准。**CaRB: A Crowdsourced Benchmark for Open IE**（Bhardwaj, Aggarwal, Mausam，EMNLP-IJCNLP 2019，ACL Anthology ID `D19-1651`；[落地页（已抓取）](https://aclanthology.org/D19-1651/)、[PDF](https://aclanthology.org/D19-1651.pdf)、[数据集附件](https://aclanthology.org/attachments/D19-1651.Attachment.zip)）：① 指出 OIE2016 数据有噪声、tuple matching 有缺陷使自动评测不可信，并给出修正后的匹配代码与指标（对应本项目需做的「金标质量自检」）；② 首个众包 OpenIE 数据集，有 NLP 专家标注验证其更准确；③ 发现 CaRB 与 OIE2016 对同一对系统给出**矛盾排名**，人工评估确认 CaRB 才对——**换一套匹配口径就可能反转两个系统排名**，报告据此怀疑本项目「云端实体 F1 = 0.40 而本地 9B = 0.624」是口径产物。局限：开放谓词场景，tuple-level 匹配加多策略（词法、依存、蕴含）与 AUC；文献 KG 是本体约束场景不能照搬，但「多策略匹配 + 匹配策略敏感性分析」必须学。

**5.4 MINE（KGGen）**：论文《KGGen: Extracting Knowledge Graphs from Plain Text with Language Models》，NeurIPS 2025——[落地页（已抓取）](https://proceedings.nips.cc/paper_files/paper/2025/hash/2b368455e832d2b1a60bcad8c4c6481f-Abstract-Conference.html)，DOI `10.52202/085713-1010`，arXiv 2502.09956（已索引）；代码 [stair-lab/kg-gen](https://github.com/stair-lab/kg-gen)（**已抓取 README 与 `experiments/MINE/README.md`、`_2_compare_results.py`**），pip 包 `kg-gen`。摘要（已抓取原文）：**MINE = Measure of Information in Nodes and Edges，是第一个测试「抽取器从纯文本产生有用 KG」能力的基准**；以 MINE 对照 Microsoft GraphRAG 的结论是「检索准确率相当，但信息保留更好，实体与关系更简洁、更可泛化」。流程（代码确认）：① 用待评工具生成 KG；② 用 KG 回答一组 QA（`_1_evaluation.py`，数据从 HuggingFace `kyssen/kg-gen-evaluation-answers` 自动下载，源文章 `kyssen/kg-gen-evaluation-essays`）；③ 统计 **accuracy**，并做跨配置对比（均值、中位数、标准差、箱线图、配对胜负统计）；④ `_2_compare_results.py` 含启发式提示（`minimal` 配置最好时输出「更多推理 token 可能引入噪声或幻觉」）。**适用性高**：「图谱 → QA 准确率」是外部效度指标，`entity_clusters` / `edge_clusters` 结构对应缺失的实体聚类层评测。局限：信息密度、实体一致性等图级指标**确切公式未验证**；QA 数据集是英文长文（essay），需自建中文/学术域 QA；`kg-gen` 依赖 LiteLLM 与 DSPy，接入本地 `qwen3.5:9b`（Ollama）可行但需验证。

**5.5 实体归一化 / 聚类质量指标**。16301 个实体里必然有大量别名与重复，这一层当时无指标覆盖。可用：**B-cubed**（Bagga & Baldwin 1998，对每个 mention 算精确率与召回率再全局平均）；**CEAF**（Luo 2005，基于实体对齐的一对一匹配，输出 φ3 / φ4 变体）；**配对法（pairwise）**（聚类转成 mention 对的二分类算 P/R/F1，**报告推荐作为最低成本方案**）。三者经典论文链接本次**未验证**，引用时需核对 ACL Anthology ID。

**5.6 图谱补全类指标（参考，不适用）**：Hits@k、MRR、MRR-filtered 需「留出边」设置；本项目是抽取而非链接预测任务，**不建议使用**，仅在分析「图谱可推理能力」时可沿用指标名。

### 3.6 人工标注质量的方法论

**6.1 三个一致性统计量的适用条件**

| 统计量 | 标注者数量 | 缺失值 | 测量尺度 | 备注 |
| --- | --- | --- | --- | --- |
| Cohen's κ | **恰好 2 人** | 不允许 | 名义 | 对类别流行度（prevalence）与标注者偏差（bias）敏感 |
| Fleiss' κ | 任意 k 人 | **不允许**（每单元必须被同样多的人标） | 名义 | 要求固定的标注者数 per item |
| Krippendorff's α | 任意 k 人 | **允许**（每单元有效评分数可不同，最少 2） | 名义 / 有序 / 区间 / 比率 | 最通用，唯一能直接处理 4 方不等量标注的统计量 |

**Fleiss' κ 无法处理「每人标注数不同」**——其 P_i 公式假设每个单元恰好被 n 个标注者评过；对「4 方、每人标注数不同」，唯一规范选择是 **Krippendorff's α**。参考 *Measuring inter-rater reliability for nominal data — which coefficients and confidence intervals are appropriate?*，BMC Medical Research Methodology 2016，[Springer 链接](https://link.springer.com/article/10.1186/s12874-016-0200-9)（已索引）。

**6.2 α 的计算公式（支持不等标注者数与缺失）**。设：单元集合 $U$；单元 $u$ 有 $m_u$ 个有效评分，$2 \le m_u \le M$，被所有标注者跳过的单元不计入；总有效评分数 $n = \sum_{u \in U} m_u$；评分值域 $\{1, \dots, C\}$ 共 $C$ 类；$n_{uc}$ = 单元 $u$ 中被判为类别 $c$ 的评分数，$n_c = \sum_u n_{uc}$。观测不一致度 $D_o = \frac{1}{n} \sum_{u \in U} \frac{1}{m_u - 1} \sum_{c=1}^{C} n_{uc}\,(m_u - n_{uc})$；期望不一致度（由边际分布推出的偶然一致水平）$D_e = \frac{1}{n(n-1)} \sum_{c=1}^{C} n_c\,(n - n_c)$；**α** $= 1 - \frac{D_o}{D_e}$。所有单元完全一致时每个 $n_{uc}(m_u - n_{uc}) = 0$ 于是 $D_o = 0$、$\alpha = 1$；评分与真值独立时为 0；**α 可以取负值（系统性分歧）**。出处 Krippendorff, K. (2011), *Computing Krippendorff's Alpha-Reliability*, University of Pennsylvania——**该文档 URL 本次未验证**。$m_u$ 出现在分母 $m_u - 1$ 上，这正是 α 能处理「不同单元标注者数不同」的原因，也是 Fleiss' κ 做不到的地方。

**6.3 Python 实现**。**`krippendorff` 包（推荐）**：[PyPI](https://pypi.org/project/krippendorff/)（**已抓取包元数据**：当前版本 0.8.2，2025-11-03 发布，`requires_python >= 3.10`，依赖 `numpy<3,>=1.21`，License GPL-3.0）；[GitHub fast-krippendorff](https://github.com/pln-fing-udelar/fast-krippendorff)（已索引，作者 Santiago Castro，基于 Thomas Grill 的实现）；安装 `pip install krippendorff`。官方 README 调用形式（已抓取原文）：

```python
import krippendorff

krippendorff.alpha(reliability_data=...)
```

`reliability_data` 是「标注者 × 单元」的二维数组，缺失值用 `numpy.nan` 表示。README 的实现注意事项：内部会构造 $V \times V$ 矩阵（$V$ 为评分值域大小），**值域必须小**；对二值或十几类标签够用。**`fast-krippendorff` 不是 PyPI 包名**（`pypi.org/pypi/fast-krippendorff/json` 返回 404，已验证），包名就是 `krippendorff`。其他可选实现：`nltk.metrics.agreement.AnnotationTask`（`alpha()`、`kappa()`、`pi()`、`multi_kappa()`，未验证）；`statsmodels.stats.inter_rater`（`fleiss_kappa`、`cohens_kappa`，未验证）；`sklearn.metrics.cohen_kappa_score`（仅两标注者）。

**6.4 集合值标注：更贴切的替代方案**。抽取标注的本质是**集合值（set-valued）或 unitizing 问题**——标注者给的是一组实体/三元组，而非给已定义单元贴标签；用 α 也要先人为定义「单元」，单元定义方式会显著影响 α 数值，必须在论文交代。**MASI**（Passonneau，LREC 2006，[链接（已索引）](http://www1.cs.columbia.edu/~becky/DUC2006/pyramidpubs/lrec06-pyramid-abstract.pdf)）：对集合 $A$、$B$ 定义相似度再跨单元平均——$|A| = |B|$ 时 $M(A,B) = \frac{|A \cap B|}{|A \cup B|}$，$|A| \neq |B|$ 时再乘 $\frac{|A \cap B|}{\max(|A|,|B|)}$，即惩罚集合大小不一致（**公式细节本次未逐字验证，引用前请核对原文**）；它是「成对 Jaccard」的规范化版本。**γ（Gamma）**（Mathet、Widlöcher、Métivier，*Computational Linguistics*，[期刊页（已索引）](https://submissions.cljournal.org/index.php/cljournal/article/view/555)）：同时处理「边界对齐」与「类别标注」并给出对齐算法，对「LLM 抽取的实体边界与人工标注不同」最贴切（**是否有成熟 Python 实现未验证**；落地建议先用 α + 成对 F1）。

**实务建议**：论文里最稳妥的是三条并列报告——① **Krippendorff's α**（名义，单元 = (文档, 候选实体/三元组)，缺失用 nan），chance-corrected 的规范统计量；② **平均成对 F1 或 Jaccard，附 bootstrap 95% 置信区间**——现有的 69.9% / 65.0% 保留，但**改名为「成对集合相似度」而不是「一致性」**（Jaccard 没有做机遇校正）；③ **分歧结构分析**（分歧集中在哪些文档、实体类型、关系类型）。

**6.5 LLM 作为标注者：支持与质疑证据**。支持：① **Gilardi et al. 2023, PNAS**《ChatGPT outperforms crowd workers for text-annotation tasks》（[链接](https://www.pnas.org/doi/abs/10.1073/pnas.2305016120)，已索引，DOI `10.1073/pnas.2305016120`）——ChatGPT 标注准确率高于众包工人，成本低两个数量级。② **PoLL**（arXiv 2404.18796，已索引）：单一大型 judge（如 GPT-4）成本高且有 **intra-model bias**；**不同模型家族**的较小模型组成评审团更好、成本低 7 倍以上——这既是「3 个 DeepSeek agent + 1 个 GLM」的最佳依据，也点出其缺陷：3 个 DeepSeek agent 仍属**同模型家族**，无法消除 intra-model bias，应改为跨家族配置并把「同家族 3 票」改成「每家族 1 票」。③ **KGLens**（arXiv 2312.11539，已索引）：自动评估与人类标注接近等价、**一致率 95.7%**，前提是报告与人类的一致性数字（本项目缺失）。质疑：① **自偏好偏差**（Panickssery et al.，arXiv 2404.13076，已索引，摘要未抓取）——评审者偏好自己或同家族输出，抽取与标注同源时必须显式讨论；② **位置偏差等 judge 偏差**（arXiv 2406.07791、arXiv 2509.26072、Zenodo record 21158214，均已索引）；③ **一致性不等于效度**（CLPsych 2025 [ACL](https://aclanthology.org/2025.clpsych-1.1/)、*SEM 2025 [ACL](https://aclanthology.org/2025.starsem-1.19/)）——高一致率可能只反映**共享训练偏差**，**69.9% 的同模型成对一致率不能作为「金标可信」的证据**；④ **分歧的价值**：*CrowdTruth*（Dumitrache et al.）与 Sabou et al. 2014 主张**分歧是任务模糊性的信号**而非噪声（URL 未验证）；⑤ **等价性研究**（[Mendeley 条目](https://www.mendeley.com/catalogue/677a1724-d43d-354f-9a55-5262f4e78791/)、[PhilArchive](https://philarchive.org/rec/BULIRO-2)，均已索引）。

**6.6 一致性计算的推荐方案与代码骨架（原报告 7.3 节，完整保留）**。**问题陈述**：4 方标注（3 个 DeepSeek agent 不同策略 + 1 份 GLM），每人标注数不同，任务是集合值的实体/关系判定。**推荐方案**：主统计量 **Krippendorff's α（名义，含缺失）**；辅助用**平均成对 F1 / Jaccard + bootstrap 95% CI**（改名「成对集合相似度」）与**按文档 / 实体类型 / 关系类型分组的 α 或分歧率**；并**分别报告 DeepSeek 内部 α 与 DeepSeek-GLM 跨家族 α**。**单元定义**：**定义 A（推荐，封闭集判定）**——候选集合 $C$ = 所有标注者提出的候选项的并集，每个 (文档, 候选实体) 为一个单元，标注值 ∈ {接受, 拒绝}，未标注者记为缺失（nan），α 即「对候选是否成立的判定一致性」；**定义 B（开放集）**——把每个标注者输出视为完整集合，用 MASI 或成对 F1，无法区分「漏标」与「判定为否」；**定义 C（仅交集）**——只在所有标注者都标注过的单元上算，样本量大幅缩小且引入选择偏差。**推荐 A**，并写清「未标注 = 缺失，不视为否」，因为这正是 α 的缺失值机制所假设的语义。

```python
import numpy as np
import krippendorff

# annotations: dict[annotator_id -> set of accepted candidate_ids]
# candidates: 所有候选项的并集（文档 × 候选实体/三元组）
candidates = sorted(set().union(*annotations.values()))
annotators = sorted(annotations.keys())

# reliability_data 形状为 (标注者数, 单元数)，缺失用 np.nan
data = np.full((len(annotators), len(candidates)), np.nan)
for i, ann in enumerate(annotators):
    for j, cand in enumerate(candidates):
        if cand in annotations[ann]:          # 已标注且判定为「接受」
            data[i, j] = 1.0
        elif cand in annotations[ann + "_all"]:  # 已标注但判定为「拒绝」
            data[i, j] = 0.0
        # 否则保持 np.nan —— 该标注者未标注此单元

alpha = krippendorff.alpha(
    reliability_data=data,
    level_of_measurement="nominal",
)
print(f"Krippendorff's alpha = {alpha:.4f}")
```

成对集合相似度与置信区间：

```python
from itertools import combinations
import numpy as np

def pairwise_f1(sets):
    scores = []
    for a, b in combinations(sets, 2):
        if not a and not b:
            continue
        tp = len(a & b)
        p = tp / len(a) if a else 0.0
        r = tp / len(b) if b else 0.0
        scores.append(0.0 if p + r == 0 else 2 * p * r / (p + r))
    return np.array(scores)

def bootstrap_ci(values, n_boot=2000, alpha=0.05, seed=0):
    rng = np.random.default_rng(seed)
    boots = [rng.choice(values, size=len(values), replace=True).mean()
             for _ in range(n_boot)]
    lo, hi = np.percentile(boots, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return values.mean(), lo, hi

scores = pairwise_f1([entity_sets[a] for a in annotators])
mean, lo, hi = bootstrap_ci(scores)
print(f"pairwise F1 = {mean:.3f} [{lo:.3f}, {hi:.3f}]")
```

**报告阈值说明**：Krippendorff 本人建议 α ≥ 0.800 作为可接受下限，0.667–0.800 仅可用于探索性结论；该阈值必须在论文里引用并说明取舍。**未验证该阈值具体出处页码**，引用前请核对 Krippendorff (2004) *Content Analysis*。

### 3.7 原报告「本项目评测方案该怎么升级」的全部建议

**7.1 现有四项指标评估与错误分类重构**。报告判定**不够**：实体/关系 P/R/F1（严格 + 宽松两级）**保留但需重构**（缺三元组层分解评测、类型感知匹配、置信区间；F1 在该数据形态下不可比）；四类错误分布（幻觉/类型/边界/遗漏）**保留但需补全**；成对 Jaccard 一致性**保留但需改名与补充**（不是 chance-corrected 统计量，需补 Krippendorff's α）；结构指标（模块度/度集中度/孤立率/证据率）**保留但需加基线**（缺本体依从率、实体聚类质量、跨论文连通性）。四类错误分类**不互斥**（一个实体既可能边界错误也是类型错误）且**不完备**（缺「实体归一化错误」「关系方向反转」「谓词错误」「证据不足/无原文支持」「重复输出」）；建议按评测层切分：**实体层**——幻觉（逐字回查原文）、边界错误（字符偏移对比）、类型错误（与 15 类本体核验）、归一化错误（聚类质量抽样）；**关系层**——关系遗漏（与金标实体对集合比对）、谓词错误（与 15 类关系本体核验）、方向反转（金标方向比对）、实体对错误（分解自 L5）；**证据层**——无原文支持（LLM judge + 人工抽检）；**结构层**——结构合规失败（JSON/schema，用 `instructor` 的 validation 计数）。

**7.2 必须先做的诊断：现在的数字很可能是口径问题，不是质量问题**（报告称这是「本次调研后最需要优先处理的事项」）。**异常模式**：① 实体 P = 0.26、R = 0.95。设金标实体数 $G$、系统输出实体数 $S$、真正例 $TP$，则 $R = TP/G = 0.95$ 得 $TP \approx 0.95G$，$P = TP/S = 0.26$ 得 $S \approx TP/0.26 \approx 3.65G$——**系统输出实体数约为金标的 3.65 倍**，是「金标显著小于系统输出」的典型指纹；成因可能包括金标只覆盖部分章节/论文、金标只认可「重要」实体、实体匹配口径（大小写、单复数、缩写、中英混排）导致本应命中的被算作假正例。② 云端实体 F1 = 0.40、本地 `qwen3.5:9b` 实体 F1 = 0.624——**9B 本地模型优于云端模型**属强反直觉结果；CaRB 的教训直接适用：**换一套匹配口径就可能反转系统排名**；更可能是两系统 precision-recall 工作点不同（云端抽得多、本地抽得少），而 $S \approx 3.65G$ 会**系统性惩罚输出更多的系统**——不代表本地模型更好，只代表它更保守。③ 关系 F1 = 0.038 / 0.052——端到端严格匹配把三个误差源相乘，即使每层正确率都有 0.3–0.5，端到端也会落到 0.03–0.1；**这个数字本身不说明系统差，只说明口径极严。** **四项低成本诊断实验**：① **输出规模对比**（报告 $|S|$ 与 $|G|$ 的绝对值与比值）；② **PR 曲线 / 等规模比较**（取不同输出规模 top-K 画 precision-recall 曲线、算平均精度 AP）；③ **匹配口径消融**（同一输出与同一金标，分别用严格、归一化（大小写/复数/缩写）、相似度宽松、类型感知四档算 P/R/F1 并**报告档间差值**）；④ **金标规模敏感性**（按金标覆盖的文档比例分层计算）。

**7.3–7.4 一致性方案与「LLM 作为标注者」的论文写法**。7.3 方案与代码见 6.6 节。7.4 **必须如实说明的缺陷**：① **同家族偏差**——3 个 DeepSeek agent 共享同一基座模型与训练数据，一致率包含共享偏差成分，**不能作为独立标注者间一致性的估计**；GLM 是唯一的家族外参照却权重严重低估。② **自偏好偏差**——抽取与标注模型同源时标注结果对系统输出存在结构性偏好，属混杂变量。③ **一致性 ≠ 效度**——69.9% 的同模型一致率只证明「稳定」不证明「正确」。④ **多数投票不产生真值**——金标是**操作性定义（operational gold）**，不是 ground truth；这个措辞必须出现在论文里。⑤ **不可复现风险**——模型版本、温度、prompt 文本、投票与仲裁规则任一未固定就无法复现；云端模型静默更新是真实威胁。**缓解措施（建议全部落实）**：① 增加真正的跨家族标注（「每个模型家族 1 票，家族内多数」；资源允许时加入第 2 个非 DeepSeek 家族）；② **报告人类抽检的交叉验证**——从金标与系统输出的分歧区域分层抽 100–200 个单元人工盲判（不看哪个是系统、哪个是金标），报告金标错误率（估计值 + 置信区间）、系统输出正确率、人工与 LLM 团队一致性（Krippendorff's α 或成对 F1），把「LLM 标注是否可信」从断言变成测量；③ **金标噪声传播分析**——把金标错误率 $e$ 代入，给出指标区间估计而非点估计（如金标 5% 错误率时 P/R/F1 的置信区间大致是多少）；④ **报告金标稳定性**——对标注者做 bootstrap 重采样（随机丢弃一个标注者，或对 3 个 DeepSeek agent 随机取 2 个）重算全部指标并报告均值与标准差；留一法下波动超过 0.05 就说明金标不稳定，该数字要写进限制节；⑤ **公开全部材料**（prompt 全文、模型版本号与调用日期、温度、原始标注输出、投票与仲裁规则、评测脚本）；⑥ **明确写出引用**——用 PoLL（arXiv 2404.18796）为多模型评审团背书，同时用 Panickssery et al.（arXiv 2404.13076）与 CLPsych 2025 的 reliability/validity 讨论说明已知局限；**主动写出局限比被评审指出要好。**

**7.5 工具清单与不建议项**。pip 包：`krippendorff`（**已抓取 PyPI 元数据 v0.8.2**）、`statsmodels`（`fleiss_kappa`、`cohens_kappa`，未验证）、`scikit-learn`（`cohen_kappa_score`、`f1_score`、`jaccard_score`，未验证）、`nltk`（`nltk.metrics.agreement.AnnotationTask`，未验证）、`seqeval`（序列标注 P/R/F1，支持 strict 与 relaxed 模式，未验证）、`networkx`（图结构指标、Louvain `networkx.algorithms.community.louvain_communities`、模块度 `community.modularity`，未验证）、`deepeval`（**已抓取 README**）、`ragas`（**已抓取 README 与指标文档**）、`instructor`（已索引）、`outlines`（已索引）、`kg-gen`（**已抓取 README 与 MINE 代码**）、`kgheartbeat`（已索引）、`langsmith`（**已抓取 README**）、`sentence-transformers`（相似度宽松匹配、实体聚类，未验证）；npm / npx：`promptfoo`（**已抓取 JSON 评测文档**）。GitHub：[GraphRAG-Bench/GraphRAG-Benchmark](https://github.com/GraphRAG-Bench/GraphRAG-Benchmark)（`indexing_eval` 提供图结构指标，支持通用 GraphML，**已抓取 README 与 Evaluation README**）、[stair-lab/kg-gen](https://github.com/stair-lab/kg-gen)（**已抓取 README 与 MINE 代码**）、[dwslab/kgreat](https://github.com/dwslab/kgreat)（**已抓取 README**）、[isislab-unisa/KGHeartBeat](https://github.com/isislab-unisa/KGHeartBeat)（已索引 + **文档已抓取**）、[eis-bonn/Luzzu](https://github.com/eis-bonn/Luzzu)（已索引）、[pln-fing-udelar/fast-krippendorff](https://github.com/pln-fing-udelar/fast-krippendorff)（**PyPI 元数据已抓取**）。**不建议使用**：`langsmith`（云端托管不利复现）、`kgheartbeat` 全量（Linked Data 指标无关）、`kgreat` 全框架（硬件要求高，缺下游任务标签）。

**7.6 建议的最终评测矩阵**（前四项「必须做」，后三项「做了会加分」）

| 序号 | 指标 | 层 | 实现方式 | 成本 |
| --- | --- | --- | --- | --- |
| 1 | 输出规模与金标规模（含比值） | 元 | 计数 | 极低 |
| 2 | 匹配口径消融（严格/归一化/相似度/类型感知） | 元 | 复用现有代码改 4 次 | 低 |
| 3 | Krippendorff's α（名义，含缺失）+ 成对 F1 及 CI | 一致性 | `krippendorff` + bootstrap | 低 |
| 4 | 分层 P/R/F1（L1 实体 / L3 关系存在 / L4 谓词 / L5 三元组 / L6 证据） | 抽取 | 自建，用 `networkx` + `pandas` | 中 |
| 5 | 本体依从率、类型冲突率、函数关系违反率、简洁度 | 结构/语义 | 规则计算（KGHeartBeat 口径） | 低 |
| 6 | 实体聚类质量（配对法 P/R/F1 或 B-cubed） | 归一化 | 自建 + 抽样人工核验 | 中 |
| 7 | 结构指标 + 随机图基线（GraphRAG-Bench 口径） | 结构 | 导 GraphML 跑 `indexing_eval` 或用 `networkx` | 低 |
| 8 | 下游 QA 准确率（MINE 式外部效度） | 应用 | 借 `kg-gen` 的 MINE 脚本，自建 30–50 题 | 高 |
| 9 | 人工盲检金标错误率 + 人机一致性 | 金标质量 | 100–200 单元盲判 | 中 |
| 10 | 标注者留一法下的指标稳定性 | 稳健性 | bootstrap 重采样 | 低 |

### 3.8 不确定项与未验证声明汇总（原样列出）

以下内容本次**未能逐字验证**，使用前请核对：

1. **arXiv 2506.02404**（第二个 GraphRAG-Bench）的作者、指标细节。
2. **KGGen 论文中 MINE 的图级指标公式**（信息密度、实体一致性等）。只从摘要、NeurIPS 落地页与代码确认了「图谱 → QA accuracy」这一主流程。
3. **MASI 的确切公式**。集合大小不一致时的惩罚项写法需核对 Passonneau (2006) 原文。
4. **γ（Gamma）方法是否有成熟 Python 实现**。
5. **Krippendorff's α 可接受阈值 0.800 / 0.667 的具体出处页码**。
6. **Krippendorff (2011) *Computing Krippendorff's Alpha-Reliability* 的 URL**。
7. **B-cubed (Bagga & Baldwin 1998)、CEAF (Luo 2005) 的 ACL Anthology ID 与链接**。
8. **Panickssery et al. 自偏好偏差论文（arXiv 2404.13076）的摘要内容**，仅凭既有知识与搜索结果确认标题与主题。
9. **LangSmith 评测 API 的具体模块名与调用方式**，其文档域在本环境被跨域重定向拦截。
10. **`nltk`、`statsmodels`、`seqeval`、`networkx` 相关 API 的确切签名**，仅确认包名与能力方向。
11. **CrowdTruth（Dumitrache et al.）与 Sabou et al. 2014 的具体 URL**。
12. **Luzzu 仓库的默认分支名**（`master` 与 `main` 的 raw README 均 404）。
13. **`kgheartbeat` PyPI 包的当前可用性与 Python 版本兼容性**。
14. **DeepEval 的 `DAG` 指标是否已发布可用**，README 的 roadmap 显示为未完成项。

**链接真实性分级**：所有标记「已抓取」的链接本次均实际返回 HTTP 200 并读到内容；所有标记「已索引」的链接均在搜索结果或 OpenAlex 元数据中出现但未抓到正文；标记「未验证」的内容不含 URL 断言。

### 3.9 对本项目六层评测的对照结论

**项目版六层**（当前方案）：L1 实体存在层（实体名是否字面出现在证据引文里）、L2 实体聚类层、L3 关系端点层（端点是否属于已知实体集）、L4 关系谓词层（谓词是否在本体 15 类内）、L5 三元组验证层（本地 NLI 模型判三元组能否被原文支持）、L6 证据层（每条边是否有页面级引文）。**报告版六层**（报告 5.1 节）：L1 实体层（与金标实体算 P/R/F1）、L2 实体聚类层、L3 关系存在层（给定金标实体对是否识别出关系）、L4 谓词层（实体对已对齐前提下谓词是否匹配）、L5 三元组层（端到端严格匹配）、L6 证据层。**两套序号重名而定义不同，只有 L2 语义一致。**

**9.1 已被六层方案覆盖**

- **分层评测（报告 5.1）**：「三元组级严格匹配把头实体错、尾实体错、谓词错合成一个数字，F1 极低时无法定位问题」被完全采纳，六层即分层报告。
- **L2 实体聚类层**（报告明写「本项目当前完全缺失这一层」，7.6 第 6 项）：项目已补上，但**六层未指定度量**（报告给了配对法 P/R/F1、B-cubed、CEAF 三个选项）。
- **L6 证据层升级为 groundedness**（报告 5.1 明确要求）：项目 L5（本地 NLI 判能否被原文支持）实现了，与 RAGAS Faithfulness 的「陈述级 groundedness」吻合。
- **本体依从率**（报告 3.1 的 Misplaced / Undefined，7.6 第 5 项）：项目 L4「谓词是否在本体 15 类内」即其谓词侧实现，属报告说的「纯规则、不涉及 LLM、零成本、有文献依据」。
- **实体幻觉判定**（报告 7.1.1）与 **RAGAS 迁移第 2/3 条**（报告 2 节）分别对应项目 L1 与 L5/L4；RAGAS 第 1 条（Context Entities Recall）与六层无关，因为六层不做召回。

**9.2 报告建议中六层尚未覆盖的部分**

- **元层面诊断与一致性统计（报告 6.1–6.4、7.2、7.3、7.6 第 1–3 项）**：输出规模与金标规模比值、PR 曲线/AP、匹配口径四档消融、金标规模敏感性；Krippendorff's α、成对集合相似度 + bootstrap CI、分歧结构分析、家族内 vs 家族间 α。六层不产生 P/R、不用金标，也不含标注者一致性维度，故无对应。
- **结构与合规指标（报告 1.1、3.1、4.1–4.3、7.6 第 5/7 项）**：density、connectivity、clustering coefficient、实体/关系分布及随机图基线；类型冲突率、函数关系违反率、**简洁度**（`去重后关系谓词数 / 15`）、标签完整率；「结构合规率」与 `instructor`/`outlines`/`promptfoo is-json`/DeepEval JSON Correctness 的结构噪声治理。六层只覆盖本体依从率，没有结构层，也未把 JSON/schema 失败计入任何一层；尤其简洁度——L4 只判「谓词是否落在这 15 类内」，无法反映谓词空间膨胀。
- **下游任务外部效度（报告 3.3、5.4 MINE、7.6 第 8 项）**：自建 30–50 题下游 QA 准确率；六层全是内部指标，缺「这个图谱有用吗」的外部证据。
- **金标质量与稳健性（报告 7.4 第 2–4 条、7.6 第 9–10 项）**：人工盲检金标错误率 + 人机一致性、金标稳定性留一法、金标噪声传播区间估计——六层均无对应。
- **错误分类的互斥完备重构（报告 7.1.1）**：**关系方向反转、重复输出、归一化误合并**在六层无显式对应层——方向反转不会被 L3（只判端点是否在已知实体集）捕捉、不会被 L4（只看谓词是否在本体内）捕捉，也常不会被 L5（NLI 判支持）捕捉。**这是六层的一个真实漏洞。**

**9.3 与六层方案存在实质分歧的部分（如实指出）**

1. **最大分歧：参照系与召回维度。** 报告 5.1 的分层评测是**以人工金标为参照**的 P/R/F1 体系（7.6 第 4 项亦然）；项目六层的 L1、L3、L4、L6 **完全不用金标**（改用证据引文、已知实体集、本体、引文存在性），只有 L5 用本地 NLI。结果是**六层是精度型/自洽型校验，不产出召回**——报告 L3「剥离实体误差，单独看关系召回」在六层没有对应物，项目 L3 判的是「端点是否属于已知实体集」，是内部一致性检查。报告警告过「只报一档会被质疑口径操纵」，而六层连「漏抽了多少」都没有；若只报六层，需说明为什么不报召回。
2. **报告并未建议放弃金标 P/R/F1，项目做法超出了报告的结论。** 报告 7.1 判定是「不够，需重构」，7.2 认为 P = 0.26 / R = 0.95 / 关系 F1 = 0.038 **很可能是口径问题而非质量问题**，路径是「先诊断、再重构口径、继续用金标」；项目因 α 为负直接停用。报告内部其实有支持项目的证据——6.5 明写「69.9% 的同模型成对一致率不能作为『金标可信』的证据」「一致性 ≠ 效度」，7.4 要求把金标称为**操作性定义（operational gold）而非 ground truth**；但报告的处理是「把金标不确定性与 α 一起报告、给出区间估计」而非绕过。因此**方法论不冲突，结论强度不同**：六层能证明「输出自洽、有证据、合本体」，不能证明「抽取完整、正确」，而报告要的正是后一半。
3. **报告版 L4 与项目版 L4 不等价，项目覆盖面更窄。** 报告版 L4 是「谓词**类型是否匹配**」（隐含与正确谓词比较）；项目版只判「谓词是否在 15 类本体**内**」，选错但在本体内会被判通过。项目 L4 实际等价于报告 3.1 的「本体依从率」，而非报告 5.1 的 L4。
4. **报告版 L6「升级为 groundedness」被拆到了 L5，项目 L6 回到存在性判定。** 报告认为「证据完整率」（只是附了引文）不够严格，应升级为「原文是否支持」；项目 L5 做到了，但 L6 仍是页面级引文存在性——按报告标准属于降级保留的旧指标，命名上会让人以为 L6 已完成升级。
5. **口径敏感性并未因改用六层而消除。** 报告引用 CaRB：**换一套匹配口径就可能反转两个系统排名**，并据此怀疑「云端 F1 = 0.40 vs 本地 9B F1 = 0.624」是口径产物。六层虽不再用金标匹配，但 L1 的「字面出现」判定、L5 的 NLI 模型与阈值同样是口径；比较两个抽取器时仍需报告「六层分数对 L1 判定方式与 L5 阈值的敏感度」，否则会重演同一个错误。
6. **两套 L1–L6 编号并存会造成文档级混淆。** 报告版 L3 是「关系存在层」而项目版是「关系端点层」；报告版 L5 是「端到端严格匹配」而项目版是「NLI 验证」。建议显式区分（如把报告版称为「报告分层评测 A1–A6」），并声明「本项目六层与报告 5.1 的六层同名不同义」。
7. **不构成冲突、仅为不采用**：报告不建议直接使用 Luzzu（适用性低）、KGrEaT 全框架（硬件要求高、缺下游任务标签）、LangSmith（云端托管不利复现）、`kgheartbeat` 全量（Linked Data 指标无关）、KGTorrent（概念错误，不可引用）。

---

## 四、可视化与交互

### 4.1 12 个核心案例：逐个说明可借鉴的机制

> 来源：`3d-kg-visual-research.md`。**✅ 已验证** = 本轮实测 HTTP 200；**⚠️ 反爬** = 返回 403 但链接真实存在；**❓ 未验证** = 本机网络受限无法确认。英文均为原报告照录。

#### A 类｜开源 demo / 引擎（可直接读源码）

#### 1. Obsidian Galaxy View —— 与本项目需求重合度最高 ⭐ 首推

[插件主页](https://github.com/Longwind1984/galaxy-view) ✅｜[社区插件页](https://community.obsidian.md/plugins/galaxy-view) ✅｜MIT 协议，可直接读源码

作者自己在 README 写下定位：**"fly through your vault like NASA's Eyes on Asteroids"**、**"Your notes become a galaxy"**。

- **节点形态**：发光点（luminous nodes），按 graph group 着色；不是球体 mesh，是带光晕的亮点。
- **边**：**"thin desaturated link filaments"** —— 细、去饱和的丝状线，默认视觉权重极低，绝不与节点抢注意力；curvature 滑杆可让边弧离星系核心（0 = 直线）。这一条正对应本项目的"社区间纤维连线"。
- **深度感**：背景拆成**四层可叠加**——star shell（星壳）、nebula veil（星云纱，烘焙一次后跟随主题着色）、drifting field stars（带视差的漂移星场）、**cluster mist（簇雾：贴着密集聚类的彩色云）**。第四层就是"星云球"要的形态。
- **材质**：bloom 后处理 + 12 套预设（Galaxy / Spiral / Orbits / Deep Field / Nebula / Minimal / Fireworks / Supernova）。
- **镜头**：**"相机滑行而非跳跃"**——点击节点后相机从斜侧方位抵达并立刻环绕，扫过邻居聚集的那一侧；开屏时星系从中心"绽放"。
- **标签**：默认不给所有节点贴标签；Focus mode 选中后其余全部变暗，只高亮邻域，并弹出可拖拽卡片（backlinks / tags / 摘要）。
- **物理**：`flatten` 力能把点云压成螺旋星系盘——"星云"成型的关键旋钮。
- **性能工程**：3,000+ 笔记 / 20,000+ 边跑 60fps，**少于 10 次 draw call**；力模拟跑在 Web Worker，布局期间主线程零卡顿；移动端自动降级（关后处理、只留 top-1500 节点）。

#### 2. 100,000 Stars（Chrome Experiment）—— 星云 / 飞入 / 标签时机的教科书

[stars.chromeexperiments.com](https://stars.chromeexperiments.com/) ✅

- **节点形态**：十几万颗恒星用 sprite / point 渲染，大小随距离与亮度变化，近处是大光斑，远处退化为细密噪点。
- **聚合机制（关键）**：**它不画"一个球代表一群星"，而是靠密度自然聚簇**——原报告称为"最省成本也最好看的聚合表达"。
- **标签**：**只在 hover / 靠近时出现**。原文："这是 3D 里标签不翻车的核心策略。"
- **镜头**：平滑 fly-to，从"整个银河"连续拉近到单颗恒星，全程无跳切；悬停显示名称卡片，点击飞入。

#### 3. 3d-force-graph 官方示例集 —— 与原生 three.js 最接近的技术栈

[仓库](https://github.com/vasturiano/3d-force-graph) ✅｜[示例总览](https://vasturiano.github.io/3d-force-graph/) ✅

| 能力 | 示例（均已验证） | 视觉上做对了什么 |
|---|---|---|
| bloom 发光 | [bloom-effect](https://vasturiano.github.io/3d-force-graph/example/bloom-effect/) | UnrealBloomPass + EffectComposer，**阈值调高只让节点溢光，背景保持纯黑** |
| **层级展开** ⭐ | [expandable-nodes](https://vasturiano.github.io/3d-force-graph/example/expandable-nodes/) | 点击节点 → 子节点注入 graphData → 力模拟升温 → 节点自然"炸开"，**"点球展开"的交互基线** |
| 文字标签（Sprite） | [text-nodes](https://vasturiano.github.io/3d-force-graph/example/text-nodes/) | three-spritetext 生成 Sprite，**永久面向相机（billboard）**，带描边保证任意背景可读 |
| 文字标签（DOM） | [html-nodes](https://vasturiano.github.io/3d-force-graph/example/html-nodes/) | CSS2DRenderer 把 DOM 挂在 3D 坐标上，字号恒定、可用 CSS 排版中文 |
| 节点几何体 | [custom-node-geometry](https://vasturiano.github.io/3d-force-graph/example/custom-node-geometry/) | 自定义球体/多面体，可为不同社区换几何形状 |
| 飞入聚焦 | [click-to-focus](https://vasturiano.github.io/3d-force-graph/example/click-to-focus/) | 点击后相机 lerp 到节点，"飞进去"的最小实现 |
| 流动边光效 | [directional-links-particles](https://vasturiano.github.io/3d-force-graph/example/directional-links-particles/) | 沿边运动的粒子表达方向与"活跃度" |
| 曲线边 | [curved-links](https://vasturiano.github.io/3d-force-graph/example/curved-links/) | 弧形边避免直线互相压叠，立体感明显提升 |
| 大图性能 | [large-graph](https://vasturiano.github.io/3d-force-graph/example/large-graph/) | ~4k 元素的性能参考线 |
| 混入外部 3D 对象 ⭐ | [scene](https://vasturiano.github.io/3d-force-graph/example/scene/) | **往场景里加自己的星云雾/球体**——做"星云球"必须用这个口子 |

其他可用示例（均 ✅）：[highlight](https://vasturiano.github.io/3d-force-graph/example/highlight/)、[directional-links-arrows](https://vasturiano.github.io/3d-force-graph/example/directional-links-arrows/)、[auto-colored](https://vasturiano.github.io/3d-force-graph/example/auto-colored/)、[fit-to-canvas](https://vasturiano.github.io/3d-force-graph/example/fit-to-canvas/)、[multi-selection](https://vasturiano.github.io/3d-force-graph/example/multi-selection/)、[emit-particles](https://vasturiano.github.io/3d-force-graph/example/emit-particles/)；[VR 版](https://vasturiano.github.io/3d-force-graph-vr/) ✅。

#### 4. Reagraph —— react-three-fiber 生态里最好看的 3D 图库

[官网 / Storybook](https://reagraph.dev/) ✅｜[自定义节点文档](https://reagraph.dev/docs/advanced/CustomNodes) ✅｜[仓库](https://github.com/reaviz/reagraph) ✅

默认节点是带光影的圆润 3D 球体，材质比 3d-force-graph 更"产品化"；原生支持弧形边与带标签的边，边随视角变化粗细；内置轨道 + 聚焦动画与主题系统。本项目是纯 three.js，可取的是它的**材质参数**。

#### 5. Apple Embedding Atlas —— 大规模点云的"标签自动挑选"范式

[在线 Demo](https://apple.github.io/embedding-atlas/) ✅｜[仓库](https://github.com/apple/embedding-atlas) ✅

数十万点的密度着色点云，**用密度而不是边表达结构**；自动聚类着色，同簇同色、簇间色相拉开。**标签**：不给所有点贴标签，而是**算法自动挑"不重叠的代表点"**。原报告评价："这是大规模图标签的正确解法，20–50 节点这一层可以简化为'只在视野中心半径内显示'。"

#### 6. Cosmograph / cosmos.gl —— GPU 点云密度美学（注意它是 2D 布局）

[Cosmograph](https://cosmograph.app/) ✅｜[在线跑](https://cosmograph.app/run/) ✅｜[Storybook](https://cosmosgl.github.io/graph/) ✅｜[仓库](https://github.com/cosmosgl/graph) ✅

半透明圆片密集叠加成"星云"亮度堆积，原报告称这是"'星云感'最廉价的来源"；边默认几乎不可见，只用透明度区分。**重要事实修正（原报告明确写出）**：cosmos.gl 是 **GPU 加速的力导向引擎，主要在 2D 平面**（`spaceSize` 是平面尺寸，计算在 fragment/vertex shader 里做），**不能**直接给 3D 星云。可借用的只有配色策略、点密度渲染、cluster/timeline 这类图层化表达。

#### 7. ECharts GL graphGL 与 deck.gl —— 中文技术栈 / 大规模点云两条支线

[ECharts GL 3D 关系图](https://echarts.apache.org/examples/en/editor.html?c=graphgl-graph) ✅（中文文档生态好、配置式，适合快速出原型）；[deck.gl 示例集](https://deck.gl/examples) ✅、[标签层（SDF 文字）](https://deck.gl/docs/api-reference/layers/text-layer) ✅、[点云层](https://deck.gl/docs/api-reference/layers/point-cloud-layer) ✅。

**诚实说明（原报告原文）**：deck.gl **没有官方的 3D 图布局 layer**（社区 `deck.gl-community/graph-layers` 仓库未能确认存在）。可用部分是 `PointCloudLayer` / `ScatterplotLayer`（3D 点）+ `TextLayer`（SDF 标签）。做知识图谱不如直接用 three.js。

#### 8. anvaka/pm —— 星系式大图的"高亮路径"手法

[anvaka.github.io/pm](https://anvaka.github.io/pm/) ✅｜[仓库](https://github.com/anvaka/pm) ✅

几十万 npm 包做成 3D 星系点云；**选中一个包时只高亮它的一条依赖链，其余整体淡出**。原报告评价："这比'选中后隐藏其他节点'更高级：保留了空间上下文。" 本项目的"点击实体看它的关系"（证据面板）应当用这个手法。

#### 9. graph.gl（Uber，已归档）

[graph.gl](https://graph.gl/) ✅｜[仓库](https://github.com/uber/graph.gl) ✅（已归档）

边用 GPU 曲线渲染 + 流动光效的早期标杆，弧边与粒子沿边流动的实现值得读源码，是"边怎么画才好看"的经典参考。

#### B 类｜商业 / 产品（看交互与工业级取舍）

#### 10. Neo4j Bloom —— 产品级标签与"场景"概念

[产品页](https://neo4j.com/product/bloom/) ✅｜[用户手册](https://neo4j.com/docs/bloom-user-guide/current/) ✅｜新一代可视化库 NVL：[neo4j.com/docs/nvl/current](https://neo4j.com/docs/nvl/current/) ✅

- **节点形态**：柔和圆形卡片（不是球），配色低饱和、低对比。
- **标签（反直觉的一条结论）**：**始终可见但字号极小、颜色低对比，整体形成"纹理"而不是噪音**——原报告称这是"标签全显示"唯一不翻车的做法：让它退到背景层。**反面案例是标签又大又亮。**
- 有 "Bloom scene"（可保存视图）+ 自然语言检索短语，把"上一级概括下一级"做成了保存视图。注意 Bloom 主体是 2D/2.5D。

#### 11. GraphXR（Kineviz）—— 真 3D 知识图谱的商业标杆

[graphxr.dev](https://graphxr.dev/) ✅｜[在线版](https://graphxr.kineviz.com/) ✅｜[文档](https://graphxr.dev/docs) ✅

球 / 立方体可自定义形状编码实体类型；支持把节点按属性（时间、地理、聚类）做 3D 空间变换——"布局即查询"；有 clustering / grouping，把一组节点聚成 super node 并可展开。原报告定性："这是你能找到的、最接近'3D 知识图谱产品'的商用参考。"

#### 12. 其他值得一看的产品与两个 GitHub 3D 贡献图

[Kumu](https://kumu.io/) ✅（"地图即系统"叙事，主体 2D）、[示例库](https://kumu.io/explore) ✅、[模板](https://kumu.io/templates) ✅；[Graphistry](https://www.graphistry.com/) **⚠️ 反爬**（curl 403，站点真实存在）；[Linkurious Ogma](https://linkurious.com/ogma/) ✅；[InfraNodus](https://infranodus.com/) ✅（见第四节）；[github-profile-3d-contrib](https://github.com/yoshi389111/github-profile-3d-contrib) ✅（等距 3D 柱状贡献图）；[Obsidian Cosmograph 插件](https://community.obsidian.md/plugins/cosmograph) ✅。

---

### 4.2 3D 里显示文字标签的成熟做法（本项目重点）

原报告核心论断：**"20–50 个节点这个量级，渲染方式随便选都不会有问题；决定成败的是'什么时候显示'。"**

#### 4.2.1 六种渲染方式（按实用性排序）

| # | 做法 | 原理 | 优点 | 代价 | 代表案例 |
|---|---|---|---|---|---|
| 1 | **Sprite + Canvas 纹理**（three-spritetext） | 文字画进 canvas → `THREE.Sprite`，天然朝向相机 | 实现最快、性能好、天然 billboard、支持描边 | 纹理分辨率固定，**放大后会糊**；每个标签一张纹理 | [3d-force-graph text-nodes](https://vasturiano.github.io/3d-force-graph/example/text-nodes/)；100,000 Stars 星名 |
| 2 | **CSS2DRenderer（DOM 覆盖层）** | DOM 按 3D 坐标投影做 transform | **中文排版最好**、字号恒定、像素级清晰、可加 CSS 阴影/圆角底板 | **不参与深度遮挡**（球后面的标签会透出来）；DOM 上千就卡 | [three.js css2d_label](https://threejs.org/examples/#css2d_label)；[3d-force-graph html-nodes](https://vasturiano.github.io/3d-force-graph/example/html-nodes/) |
| 3 | **SDF / MSDF 字体**（troika-three-text） | 距离场字形，shader 里按屏幕空间重建 | **任意缩放都清晰**；一把 draw call；支持换行/对齐 | 中文字体文件巨大，**必须做子集化** | [troika-three-text](https://github.com/protectwise/troika) ✅ |
| 4 | **实例化 bitmap 字体图集**（GPU instancing） | 字形预烘到图集，`InstancedMesh` 批量画四边形 | **上千标签不掉帧** | 分辨率固定、无动态排版、中文图集体积大 | [deck.gl TextLayer](https://deck.gl/docs/api-reference/layers/text-layer) |

另两种不推荐：**CSS3DRenderer** 有真实透视与遮挡关系，但仍有 DOM 上限、斜看字体发虚（[css3d_periodictable](https://threejs.org/examples/#css3d_periodictable)）；**3D 挤出几何**可做体积/倒角/PBR，但中文需 typeface.json、顶点数爆炸、侧面很难看（[webgl_geometry_text](https://threejs.org/examples/#webgl_geometry_text)）。

必读官方入口：[CSS2DRenderer 文档](https://threejs.org/docs/#examples/en/renderers/CSS2DRenderer) ✅｜[UnrealBloomPass 文档](https://threejs.org/docs/#examples/en/postprocessing/UnrealBloomPass) ✅。

#### 4.2.2 可见性策略（原报告标注"**这才是关键**"，按推荐度排序）

1. **hover / 邻近才显示**——100,000 Stars、Neo4j Bloom、Galaxy View 都这么做。原文："3D 里最难看的画面就是'每个球头上顶一行字'。"
2. **LOD：只给 top-N 或当前焦点邻域加标签**——Apple Embedding Atlas 的"自动挑不重叠代表点"是这个思路的算法化版本。
3. **给标签加描边或半透明底板**——three-spritetext 的 `strokeWidth` 就是干这个的。"没有描边，浅色节点前的白字会直接消失。"
4. **透明度随距离衰减**，远处标签淡出，避免糊成一片噪点。
5. **遮挡剔除**——CSS2D 的硬伤。简易做法：对每个标签做一次 raycast 或用深度纹理判断它前面是否有节点，被挡住就降透明度。
6. **标签避让布局**（力导向推开重叠标签）——"节点数少时可省略；一旦超过 30 个标签重叠就会出现。"

---

### 4.3 聚合节点 / "星云球"的层级化表现

#### 4.3.1 出现过聚合形态的案例

| 案例 | 聚合形态 | URL |
|---|---|---|
| **Obsidian Galaxy View** | **cluster mist：贴着密集聚类的彩色云雾** + `flatten` 力压成星系盘 | [galaxy-view](https://github.com/Longwind1984/galaxy-view) ✅ |
| **100,000 Stars** | 不画球，**靠恒星密度自然形成星团/悬臂** | [stars.chromeexperiments.com](https://stars.chromeexperiments.com/) ✅ |
| **WilmaScope** | **球体包围聚类**（最早的"球代表一群"视觉样本） | [wilmascope.org](https://www.wilmascope.org/) ✅ |
| **H3 (Munzner)** | 双曲空间中焦点子树放大、其余指数收缩成壳层 = "层级球" | [graphics.stanford.edu/papers/h3](http://graphics.stanford.edu/papers/h3/) ✅ |
| **TU Wien Immersive Analytics** | overview 是一团点云球，detail 才展开 | [sorger-2019-odn](https://www.cg.tuwien.ac.at/research/publications/2019/sorger-2019-odn/) ✅ |
| **GraphXR** | clustering / grouping → super node，可展开 | [graphxr.dev](https://graphxr.dev/) ✅ |
| **Cosmograph** | cluster 用凸包/色块（**2D**，非球） | [cosmograph.app](https://cosmograph.app/) ✅ |
| **Nomic / Apple Embedding Atlas** | topic 层级用**密度区域**表达，不是球 | [embedding-atlas](https://apple.github.io/embedding-atlas/) ✅ |
| **3d-force-graph expandable-nodes** | 交互范式：点击 → 子节点注入 → 力模拟炸开 | [expandable-nodes](https://vasturiano.github.io/3d-force-graph/example/expandable-nodes/) ✅ |
| **NASA Eyes on Asteroids / Solar System** | 天体 LOD 与"飞入"体验的工业标杆 | [asteroids](https://eyes.nasa.gov/apps/asteroids/) ✅、[solar-system](https://eyes.nasa.gov/apps/solar-system/) ✅ |
| **Gaia Sky / ESASky** | 星表渲染 + 星团，天文级聚合与 LOD | [Gaia Sky](https://zah.uni-heidelberg.de/gaia/outreach/gaiasky) ✅、[ESASky](https://sky.esa.int/) ✅ |

学术首推 **Munzner, H3: Laying Out Large Directed Graphs in 3D Hyperbolic Space (IEEE InfoVis 1997)**（[论文主页，含彩色 figure + QuickTime/MPEG 视频 + 高清 TIFF](http://graphics.stanford.edu/papers/h3/) ✅；[后续论文](http://graphics.stanford.edu/papers/h3cga/) ✅）：在 **3D 双曲空间**布局层级图，成功处理 **20,000+ 节点**。核心是**"焦点 + 上下文"（Focus+Context）**——焦点子树在中心放大，其余沿双曲空间**指数收缩**成环绕的"星云壳层"，用户可显式修剪/展开子树。关键启示：**"层级化 3D 图不应该靠'缩放'，而应该靠空间本身提供指数级容量。"**

#### 4.3.2 一个球怎么"代表"里面一群节点（可叠加的编码通道，按优先级）

1. **内部粒子密度**（原报告标注"最优雅"）——把该社区 N 个实体按 **Fibonacci 球面分布 / 高斯球**采样放进球内。宏观看是"一团发光的东西"，拉近才发现每个粒子是一个实体。**这天然实现"上一级概括下一级"的连续性**：同一粒子既是"球的一部分"也是"展开后的节点"，展开时不用换对象，只需把粒子从球内位置 tween 到力导向位置。
2. **半径**编码社区规模或度数和。**必须用 `sqrt` 或 `log`，不能线性**，否则大社区球穿模、小社区看不见；并给半径设硬上下限。
3. **自发光 / bloom 强度**编码社区内部边密度：内部连接越密，球越亮。
4. **光晕（additive sprite 外圈）**表达"这是一团弥散物"而非"一个实心球"。Refik Anadol（[refikanadol.com](https://refikanadol.com/) ✅）是这种观感的顶点。
5. **颜色** = 社区色相，且**必须与内部实体颜色完全一致**——飞入过程中颜色连续，观众才知道"我进的就是刚才那个球"。
6. **表面纹理 / 环绕卫星点**——球面撒少量绕行小点，暗示"里面有东西、在动"；球体材质用 `MeshBasicMaterial` + 低透明度，或用 `ShaderMaterial` 做菲涅尔边缘发光（fresnel rim），比 PBR 实心球更省性能。

#### 4.3.3 点击展开的过渡动画（原报告称这套组合是从 Galaxy View / 100,000 Stars 反推出来的）

1. **相机先行，球体后动**：先 500–700ms 把相机 lerp/dolly 到目标球面，**相机到位后再引爆**；反过来会让人失去空间定位感。
2. **力模拟热启动**：把球内粒子初始坐标直接设成"刚才在球内的位置"，再 `alpha(1)` 重新升温，力导向自然把它们推开——**不需要手写任何关键帧**（3d-force-graph `expandable-nodes` 的做法）。
3. **球的 impostor 淡出 + 边渐入**：球先 `scale` 到 1.10–1.15，同时 `material.opacity` 1 → 0（300–500ms），**放大再消失**比直接消失自然；边用 `drawRange` 或透明度从 0 → 目标值（300–600ms），避免瞬间冒出蜘蛛网（曲线边比直线好看，但计算量大一些）。
4. **返回反向 + 节奏**：粒子收拢回球心 → 球重新实体化 → 相机拉远，收拢约用 0.6 倍时长；缓动统一 `easeInOutCubic` / `easeInOutQuad`，总时长 500–900ms（"3D 场景里动画超过 1 秒就会觉得卡"），末尾加一点过冲（球 scale 到 1.15 再回 1.0）——"这是'活'与'死'的分界线"。

---

### 4.4 InfraNodus：标签避让机制与「一次显示多少词」

> 素材来源：`InfraNodus-事实核查报告.md`。调研方式为 Windows + PowerShell + `curl.exe` 逐条实测 HTTP 状态码，所有 URL 均为实际请求所得。报告第 0 节表格逐条记录状态码，全部为 200（含首页、`/docs` 及子页、`/tutorial/*`、`/use-case/*`、`/graphs`、`/knowledge-graphs`、`/showcase`、`/api`、`/mcp`，以及自托管的 `/javascripts/sigma/sigma.min.js` → 200，115,431 B）。

#### 4.4.1 「一次显示多少词节点」：官方口径与实测

**硬上限 500 个节点。** 两处官方原文：

- [use-case/network-analysis-visualization](https://infranodus.com/use-case/network-analysis-visualization)（**200**）定价对比表，表头 `Feature | Basic | Advanced | Premium`，其中一行原文：

  > **Max Nodes in the Graph**　500　500　500

- [docs/network-visualization-software](https://infranodus.com/docs/network-visualization-software)（**200**）"InfraNodus" 一节 Cons 列表原文：

  > Cons:
  > • Requires subscription
  > • Not as customizeable as other tools
  > • **Not suitable for graphs with more than 500 nodes**

**URL 参数 `maxnodes` 直接控制显示数量。** 从 `/graphs` 提取的所有 demo 图链接中，参数一律为 `&maxnodes=150`，仅 `ml/1fnoise` 为 `&maxnodes=500`。公开 JSON 端点实测（**未使用任何 API key**）：

| 请求 | 状态码 | 实测结果 |
|---|---|---|
| `https://infranodus.com/graph/public/nodes/expert/?graphname=ray_dalio_principles&demo=1&maxnodes=150&` | **200** | **nodes = 150，edges = 1455** |
| `https://infranodus.com/graph/public/nodes/ml/?graphname=1fnoise&demo=1&maxnodes=500&` | **200** | **nodes = 426，edges = 1542**（该图实际只有 426 个节点，未达 500 上限） |

前端计数显示逻辑（`graphdemo_1.html` 第 22648 行）：

```js
$('#totalnodesinfo').text(totalnodescount + ' of ' + userFactory.getMaxNodes() + ' max');
```

分享链接生成时带上该参数（`graphdemo_1.html` 第 26241–26243 行）：

```js
if (userFactory.getMaxNodes()) {
    graph_share_url += '&maxnodes=' + userFactory.getMaxNodes();
}
```

**应用内还有按指标过滤节点的 UI**（[docs/how-it-works](https://infranodus.com/docs/how-it-works)，**200**，1,191,918 B，第 149–157 行面板文字原文照录）：

> Node Filters
> Graph Display
> Language Processing
> Show Nodes with Degree >
> 0
> :
> 0
> 0
> **Total Nodes Shown:**
> extend

**「只显示最重要的那几个词」的等价机制：Jenks elbow cutoff。** 公开 demo 图 Analytics 面板 "Most Influential Concepts" 一节（抓自 `https://infranodus.com/expert/ray_dalio_principles?...demo=1`，**200**）原文：

> The most influential nodes are either the ones with the highest betweenness centrality — appearing most often on the shortest path between any two randomly chosen nodes (i.e. linking the different distinct communities) — or the ones with the highest degree.
> **We use the Jenks elbow cutoff algorithm to select the top prominent nodes that have significantly higher influence than the rest.**

**⚠️ 未能验证（原报告明确标注）**：官方并未使用 "top 100 words"、"top 150 words" 这类字面表述。可验证的规模口径只有 "Max Nodes in the Graph = 500"（硬上限）与 `maxnodes` 参数（默认 150），加上 Jenks elbow cutoff 这套「自动挑出显著更重要的少数节点」的机制。

**最小可用文本量**（[tutorial/create-your-first-knowledge-graph](https://infranodus.com/tutorial/create-your-first-knowledge-graph)，**200**，原文）：

> …and paste any text (**about 100 words is enough to see structure**). InfraNodus converts the words into nodes and their co-occurrences into connections…

#### 4.4.2 标签显示机制：sigma.js v1.2.1 的 `labelThreshold`（缩放依赖）

InfraNodus 自托管渲染库为 **sigma.js v1.2.1**（`https://infranodus.com/javascripts/sigma/sigma.min.js` → **HTTP 200**，115,431 B），文件头原文：

```
/* sigma.js - A JavaScript library dedicated to graph drawing. - Version: 1.2.1
   - Author: Alexis Jacomy, Sciences-Po Médialab - License: MIT */
```

文件内默认渲染设置（原文照录）：

```js
defaultLabelSize:14, font:"arial", fontStyle:"", labelColor:"default",
labelSize:"fixed", labelSizeRatio:1, labelThreshold:8, ...
```

绘制标签的判定逻辑（压缩后原文照录）：

```js
if((c("forceLabels")||!(e<c("labelThreshold")))&&"string"==typeof a.label&&(...
```

即：**只有当节点在屏幕上的尺寸 `e` 不小于 `labelThreshold` 时才绘制其标签**。报告据此定性：这是「缩放依赖的标签显示」策略——缩小看全局时只出现大节点（高 betweenness centrality）的标签，放大后才逐步出现更多标签。

**⚠️ 同时确认（原报告标注）**：该 `sigma.min.js` 中检索 `labelGridCellSize`、`labelDensity`、`collide` 均为 **0 次命中**，即这个版本**没有**基于网格/碰撞检测的标签布局避让（那是更新版 sigma 的能力）。

InfraNodus 自己暴露的标签控制参数（前端代码 `graphdemo_1.html`）：

- 默认值：`let in_label_threshold = 8;`（第 1218 行），`graphOptions` 中另有 `in_label_threshold: 7`、`default_threshold: 7`。
- URL 参数覆盖（第 2105/2117 行附近）：`userFactory.setGraphOption('in_label_threshold', getUrlVars()["threshold"]);`
- 分享链接会带上标签参数（第 26233–26255 行等）：

  ```js
  graph_share_url += '&threshold=' + userFactory.getGraphOption('threshold')
  graph_share_url += '&labelsize=' + userFactory.getGraphOption('labelSize')
  graph_share_url += '&defaultlabelsize=' + userFactory.getGraphOption('defaultLabelSize')
  graph_share_url += '&labelsizeratio=' + userFactory.getGraphOption('labelSizeRatio')
  ```

  这解释了公开 demo URL 里为什么会出现 `threshold=5`、`threshold=7`、`threshold=8`、`labelsize=fixed`、`labelsize=proportional`、`labelsizeratio=2`、`defaultlabelsize=14` 这些参数。

**对用户可见的标签选项**（抓自 [docs/how-it-works](https://infranodus.com/docs/how-it-works)（**200**）与各公开 demo 图页面（**200**），原文照录）：

> **Show Labels for Nodes >**
> 0
> size:
> 0
> 0
> **Default Label Size:**
> 0
> 0
> 20
> **labels size = nodes size**
> **labels color = nodes color**
> Edges Type: Lines / Curves / Do Not Show
> Layout Type: Nodes + Text / Abstract / Word Cloud

参数上下限在代码中写死（`graphdemo_1.html` 第 25117–25143 行）：`proportional` 模式标签字号比例滑块 `max = 5`；`fixed` 模式 `max = 20`，默认 `defaultLabelSize = 14`。

**社区（簇）名称标签按簇规模缩放字号**（`graphdemo_2.html` 第 18016 行）：

```js
const clusterFont = Math.floor(maxClusterFont * (Math.pow(cluster.positions.length, 0.5) / Math.pow(maxCommunityLength, 0.5))) + 'px'
```

`maxClusterFont = 30`（第 18004 行）。节点多的簇名字更大。

**相机变化时重算"当前可见内容"**（`graphdemo_2.html` 第 17988–17995 行）：

```js
bindUpdateClusters: function() {
    if (!sigma.instances(0).camera) return;
    sigma.instances(0).camera.bind('coordinatesUpdated', function(n){
        graph().calculateClusters('onlyShown');
        graph().updateClustersPosition();
        graph().updateNodeHighlightsPosition();
        graph().updateGapHighlightPosition();
    })
}
```

（图谱界面中还有大量标注为 `focus on this context` 的动作按钮，抓自 demo 图页面 HTML。）

#### 4.4.3 标签避让：官方零文字承诺，代码里也没有网格/碰撞避让

- **⚠️ 未能验证（原报告 5.1）**：在所有已抓取官方页面（首页、`/docs` 及子页、`/about/how-it-works`、`/tutorial/*`、`/use-case/*`、`/graphs`、`/knowledge-graphs`、`/showcase`）的正文文本中，**没有找到任何一句明确说明「标签避让」「文字不重叠」「label overlap avoidance」的文字**。这一项只能从实际交付的前端代码与 UI 选项来验证。
- 原报告 5.6 与第 8 节的结论：可验证的是它用 **sigma.js v1.2.1 + `labelThreshold`（默认 8）** 做「缩放依赖的标签显示」，并提供 UI 滑块与 URL 参数让用户自己调阈值与字号；**未能验证**的是——官方没有文字承诺「标签永不重叠」，所用 sigma 1.2.1 内部**不含**网格/碰撞式标签避让代码。

**对照结论**：InfraNodus 与本项目"动态标签池"是同源思路，但它靠的是**节点屏幕尺寸阈值**（节点级门槛），而不是**相机距离/缩放档位开关**（级别级门槛）。这个差别就是可直接落到代码里的改进点。

#### 4.4.4 可搬走的其他 InfraNodus 机制（均附原文依据）

- **社区着色 = Louvain 模块度**（[docs/text-network-analysis](https://infranodus.com/docs/text-network-analysis)，**200**）原文 "Run a **modularity algorithm (Blondel et al based on Louvain)**…"，阈值 "**>0.4 for medium, >0.65 for high modularity**"。
- **节点大小 = betweenness centrality**（[about/how-it-works](https://infranodus.com/about/how-it-works)，**200**）原文 "**The higher the measure of BC, the bigger is the node on the graph.**"
- **坐标 = Force Atlas**（[use-case/network-analysis-visualization](https://infranodus.com/use-case/network-analysis-visualization)，**200**）原文 "…apply **Force Atlas layout**…"；前端实测参数（`graphdemo_1.html` 第 1246–1253 行）`{worker:false, startingIterations:100, scalingRatio:0.1, slowDown:100, strongGravityMode:true}`，同处 `minnodesize: 5, maxnodesize: 16`（**尺寸区间只有 5→16，远比直觉小**）。
- **公开 JSON 字段与 URL 形态**：节点公开暴露 `degree`、`weighedDegree`、`bc`、`community`、`x/y/z`（实测无需 key）；实测确认**不存在** `https://infranodus.com/graph/<id>` 形式的图查看器 URL，公开图谱 URL 形式为 `https://infranodus.com/{context}/{graphname}?demo=1&...`。官方文档页内嵌 iframe 的硬证据（[docs/network-analysis](https://infranodus.com/docs/network-analysis) 页面 HTML 原文照录），实测源地址 → **HTTP 200**，684,016 B：

  ```html
  <iframe width='100%' height='700' style='height: 700px'
    src='https://infranodus.com/chinesemedicine/organs?background=dark&&most_influential=bc&&maxnodes=150&hide_always=1&analytics=1&link_hashtags=1&background=dark&maxnodes=150'
    frameborder='0' allowfullscreen>
  ```

- 报告第 6 节标题为「官方截图 / 图片直链（全部 curl 实测 200，共 40 个）」，例：[infranodus-overview.webp](https://infranodus.com/images/front/infranodus-overview.webp)（200，76,140 B）、[infranodus-network-analysis-tool-app.webp](https://infranodus.com/images/front/infranodus-network-analysis-tool-app.webp)（200，353,386 B）。

---

### 4.5 两份报告中全部「未能验证」项（原样列出）

#### 4.5.1 来自 `3d-kg-visual-research.md`

**⚠️ 反爬拦截（链接真实，服务器对脚本请求返回 403）**：

- [graphistry.com](https://www.graphistry.com/)（Graphistry）
- [onlinelibrary.wiley.com/doi/abs/10.1111/tgis.13053](https://onlinelibrary.wiley.com/doi/abs/10.1111/tgis.13053)（GeoGraphViz，DOI `10.1111/tgis.13053`）—— 原报告标注："**这是一篇专门讲 3D 力导向知识图谱的论文，与你的题目最贴近，强烈建议在校园网下载全文。**"
- npm 包页 `three-spritetext` / `troika-three-text`（包本身已通过 jsDelivr 验证存在）

**❓ 未验证 / 已下线**：

- `https://arxiv.org/pdf/1910.06825.pdf` —— 链接出自 TU Wien 官方论文页，但本机直连 arxiv.org 被网络策略阻断，**未能独立确认**（原报告第 203 行同此标注）。
- `http://wiki.polyfra.me/`（WikiGalaxy）—— **已下线**，本轮请求无响应，仅作历史参考。
- [invisiblecities.io](https://invisiblecities.io/)（Christian Marc Schmidt 的 3D 网络艺术项目）—— 本轮请求无响应，**未验证**。
- `https://feltron.com/`（Nicholas Felton）—— 本轮请求无响应，**未验证**；如需引用请先自行确认其现用域名。

**其它未能确认的事实**：deck.gl 社区 `deck.gl-community/graph-layers` 仓库**未能确认存在**（原报告第 102 行"诚实说明"）。

#### 4.5.2 来自 `InfraNodus-事实核查报告.md`（第 7 节，7 条）

1. **`semantic zoom` 这一术语**：官方全部被抓取页面正文中 **零命中**。官方使用的是 "zoom in on any group of concepts"、"shift attention between scales (zooming in and out)"、"Topics view / Concepts view"、"focus on this context" 等表述。
2. **标签避让 / 文字不重叠的官方文字说明**：未找到。只能从前端代码（sigma 1.2.1 的 `labelThreshold` 机制）与 UI 选项侧证。
3. **「只显示 top N 词」的字面说法**：未找到。可验证的是 `Max Nodes in the Graph = 500`、URL 参数 `maxnodes`（默认 150）、以及 Jenks elbow cutoff 自动挑选显著节点。
4. **`https://infranodus.com/graph/...` 形式的图查看器 URL**：不存在。`/graph`（404）、`/knowledge-graph`（404）。`/graph/public/nodes/...` 只是 JSON 数据端点前缀。
5. **`https://infranodus.com/videos`**：**404**。可用的只有 `https://infranodus.com/videos/text-analysis-ai`（200）。
6. **首页 `/graphs` 上提到的 `https://keywordgraph.com`**：未做验证（非 infranodus.com 域名）。
7. 本报告未调用任何需要 API key 的接口；`/graph/public/nodes/...` 为公开只读端点，实测无需鉴权。

另：实测 **404**、不能作为公开链接使用的还有 `https://infranodus.com/videos`、`https://infranodus.com/graph`、`https://infranodus.com/knowledge-graph`。

---

### 4.6 对本项目的可执行建议

> 现状：原生 three.js 单文件页面 `kg-nebula.html`，无构建链、无框架；已有 18 社区星云球概览层、社区内实体云、`CSS2DRenderer` 文字标签、`OrbitControls`、FBM shader 星云幕、社区间纤维连线、证据面板，以及按缩放级别动态取标签池的避让方案。凡与现有做法冲突之处均已标注。

**1｜把"按缩放档位取标签池"细化为"节点级屏幕尺寸门槛 + 重要性排序"。**
依据：InfraNodus sigma.js v1.2.1 的判定逻辑 `if((c("forceLabels")||!(e<c("labelThreshold")))...`——门槛落在**单个节点的屏幕尺寸**上，不是相机档位；配合 Jenks elbow cutoff"自动挑出显著更重要的少数节点"。落地：每帧算候选节点的投影屏幕半径，过门槛者进池，池内按重要性取前 N。**不冲突**：现有方案是级别级开关（近了一整批同时出现），节点级门槛能让高 BC 大节点在远处先出标签，避免小节点抢占位置。

**2｜`CSS2DRenderer` 必须自己补深度遮挡剔除。**
依据：标签渲染表第 2 行明确写出 CSS2D 代价——**"不参与深度遮挡（球后面的标签会透出来）"**；2.2 第 5 条给出做法"对每个标签做一次 raycast 或用深度纹理判断它前面是否有节点，被挡住就降透明度"，并提示"避免依赖 CSS2D 的遮挡关系"。落地：只对已进池的少量标签做 `Raycaster` 检测，被遮挡者降 opacity 到 0.15–0.3。**不冲突，是必要补强**；但会与现有避让方案叠加开销，两者应共用同一次投影计算。

**3｜标签一律加描边 / 半透明圆角底板，并做距离衰减。**
依据：2.2 第 3 条"没有描边，浅色节点前的白字会直接消失"（three-spritetext 用 `strokeWidth`）；第 4 条"透明度随距离衰减"。落地：CSS2D 侧用多层 `text-shadow` + 半透明底板，按相机距离 clamp 透明度。**与 FBM 星云幕有冲突风险**——星云幕是低对比弥散背景，白字落在亮雾上会糊掉，此处描边/底板是必需项而非可选项。

**4｜给标签数量设硬预算，不要把标签池做成"尽量多显示"。**
依据：InfraNodus 官方口径 `Max Nodes in the Graph = 500`（三档订阅一律 500），Cons 明写 **"Not suitable for graphs with more than 500 nodes"**，实际公开 demo 一律 `maxnodes=150`（实测 `nodes = 150, edges = 1455`）；Neo4j Bloom 的解法是"始终可见但字号极小、低对比，形成纹理而不是噪音"，而原报告点名的**反面案例就是"标签又大又亮"**；100,000 Stars 则"只在 hover / 靠近时出现"。落地：概览层 18 个球名可全给（它就是导航层）；进入社区后 40 个实体**只给 10–16 个**配额，其余靠 hover 与证据面板补足。**与 FBM 星云幕 + 纤维连线叠加时冲突明显**：背景越漂亮，前景文字越要克制，否则视觉噪声是平方级上升。

**5｜星云球内部用 Fibonacci 球面采样粒子，展开时把同一批粒子 tween 到力导向位置，不销毁重建。**
依据：3.2 第 1 条（"最优雅"、天然实现层级连续性）+ 3.3 第 2 条力模拟热启动（`alpha(1)` 升温，无需手写关键帧）。**与 FBM 星云幕不冲突**（幕在背景层，粒子是前景 `Points`）；但若星云球当前是 FBM/`ShaderMaterial` 实心 mesh，需改成"半透明 fresnel rim 外壳 + 内部粒子"两层结构（3.2 第 6 条），否则粒子藏在不透明球里看不见——这是真实改造点。

**6｜FBM 星云幕不要和节点共用一条无差别的 bloom 通道。**
依据：原报告第四章「建议 2：视觉分层严格三级」原文——"**避免对边和背景也开 bloom**。bloom 是全屏阈值后处理，边一多整屏泛白，层级感直接消失"；`bloom-effect` 示例的正确姿势是"**阈值调高只让节点溢光，背景保持纯黑**"。落地：引入 `UnrealBloomPass` 时把 FBM 幕放到 bloom 之后合成或对其亮度做遮罩，只让球壳边缘、内部粒子、高 BC 节点进 bloom。**与现有 FBM 星云幕直接相关**：当前若没有 EffectComposer，FBM 直接画在背景上是安全的；一旦加 bloom，全屏后处理会把星云亮雾一起抬起来，必须分层，否则整屏被"星云幕"吞成灰白。

**7｜不要再叠一层力导向标签避让布局。**
依据：2.2 第 6 条"节点数少时可省略；一旦超过 30 个标签重叠就会出现"；InfraNodus 侧同证——sigma 1.2.1 中检索 `labelGridCellSize`、`labelDensity`、`collide` **均为 0 次命中**，没有网格/碰撞避让也仍是可用的产品。落地：现有"动态标签池"就是正确层级；处理残余重叠优先做**屏幕空间贪心剔除**（按重要性排序，逐个检查与已放置标签的矩形交叠，冲突即丢弃），而不是每帧跑力模拟。**与现有动态标签池方案一致，是对"再加一层避让"的明确否决**——这正是该省性能的地方。

**8｜半径用 `sqrt` / `log` 映射并设硬上下限；展开动画按"相机先行"的时序做。**
依据：3.2 第 2 条"**必须用 `sqrt` 或 `log`，不能线性**"；3.3 时序（相机 500–700ms 先到位 → 力模拟 `alpha(1)` → 球 impostor `scale` 到 1.10–1.15 并 opacity 1→0 用 300–500ms → 边用 `drawRange` 300–600ms 渐入 → `easeInOutCubic`，总长 500–900ms，返回约 0.6 倍时长，末尾留 overshoot）；并采纳收敛后 `fix nodes` 冻结坐标，原文"静止的星云比抖动的星云好看十倍"。**与现有相机 / `OrbitControls` 有轻微冲突**：动画期间需临时接管 controls（禁用输入或与目标值加权混合），否则会和相机 tween 抢控制权。

---

## 五、待修正项（由本次调研暴露）

> 2026-09-12 更新：原第 3 条「相似度阈值统一为 0.8」与本次实体归一化调研的
> 结论冲突，已改写。原文把"Neo4j 官方默认 0.8"当成了合并线，
> 而官方 Labs 的 `neo4j-labs/agent-memory` 用**三档**阈值，0.8/0.85 都不该直接删节点。

1. **跨标签合并过于激进**：应改为 FalkorDB 式 LLM 验证，而非标签优先级裁决。
2. **`_names_are_duplicates` 应换用 RapidFuzz**（性能 4.4×），但必须保留长度保护
   ——官方 `FuzzyMatchResolver` 用的 `fuzz.WRatio` 对短缩写没有保护，
   实测 `UI` vs `build graph.py` 得 0.900，超过 0.8 会被错误合并。
3. ~~相似度阈值统一为 **0.8**（Neo4j 官方默认）~~
   → **改为三档**：**自动合并 0.95** / **人工复核 0.85** / **候选池 0.8**。
   依据 `neo4j-labs/agent-memory` 的 `DeduplicationConfig`
   （`auto_merge_threshold=0.95`、`flag_threshold=0.85`），
   0.85–0.95 之间只建 `SAME_AS {status:'pending'}` 而不删节点。
   **0.8 只能作为"进入候选池"的门槛，不能作为销毁节点的门槛**（§1.15 第 1 条）。
4. **不要调用 `apoc.refactor.mergeNodes` 做常规消歧**：它合并到列表首节点、
   返回值里没有任何原节点引用（`cloneNodes` 才有 `(input, output, error)`），
   且未在 config 里点名的属性一律被覆盖。可逆性必须在数据模型层自建
   （tombstone + 别名，见 §1.6）。
5. **"不合并"这个决策也要留痕**：应写成 `status:'rejected'` 的 `SAME_AS` 边
   而不是直接丢弃，否则无法回答"当初为什么没合并"。
6. **评测口径改用 ROC + ground truth 簇列**，而不是单个 F1
   （Splink 官方示例的做法，有 FCDS 2024 在 670,214 条记录上的第三方背书）。
7. **实体聚类层的判据要改**：现有 L2 用"任一成员的类型"判类型冲突，
   导致 196 组被跳过，其中 183 组只是简单复数、且低频变体的类型标注本身是噪声。
   应改为**频次加权类型**。
8. **补一层方向校验（L7）**：见 §2.4 与 `docs/L5_SIX_LAYER_REPORT.md` §4.5。
