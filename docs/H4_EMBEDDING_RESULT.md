# H4 · 语义嵌入（bge-m3）结果：**"用余弦替代字符重叠"不成立**（2026-09-16）

> **配套**：`src/embeddings.py`（正式接口）·
> `tools/embedding_threshold_eval.py`（可复跑的标定）·
> `data/quality/embedding-threshold-eval.json`（产物）
>
> **一句话**：**"该拒"的最高分（0.8502）远高于"该合"的最低分（0.4074）——
> 两类完全交叠，任何单一阈值都会判错（最佳 F1 只有 0.696）。
> 余弦只能作为高分区（≥ 0.93）的补充通道，不能替代字符判据。**

---

## 一、标定集（20 对，每对都有人工判断）

覆盖五类：拼写/空白变体 · 缩写↔全称 · 子串陷阱 · 同缩写不同全称 · 完全不相关。

```
  余弦      该合   类别                    A ↔ B
  0.9655   ✅    单复数                  'knowledge graph' ↔ 'knowledge graphs'
  0.9379   ✅    拼写/空白               'OpenRefine' ↔ 'Open Refine'
  0.9244   ✅    大小写/连字符            'multi-head attention' ↔ 'Multi-Head Attention'
  0.8502   ❌    子串陷阱                'graph' ↔ 'graph neural network'      ← ★
  0.7862   ❌    子串陷阱                'time' ↔ 'timestamp'
  0.7618   ✅    版本后缀                'tabula' ↔ 'Tabula PDF'
  0.7477   ❌    子串陷阱                'Tag' ↔ 'Tagger'                     ← ★
  0.7130   ✅    带括注                  'TransE' ↔ 'TransE (Bordes 2013)'
  0.6250   ✅    版本后缀                'BERT' ↔ 'BERTLarge'
  0.5510   ❌    同缩写不同全称           'AD' ↔ 'A. Doan'                     ← ★
  0.5051   ❌    碰巧首字母对齐           'RDF' ↔ 'Reference Data File'
  0.4873   ❌    完全不相关               'AI' ↔ 'Semantic Scholar'
  0.4681   ✅    缩写↔全称               'AD' ↔ 'Anomaly Detection'
  0.4669   ✅    缩写↔全称               'GNN' ↔ 'Graph Neural Networks'
  0.4437   ❌    完全不相关               'UI' ↔ 'build graph.py'
  0.4240   ❌    完全不相关               'Crossref' ↔ 'Semantic Scholar'
  0.4181   ❌    独立品牌 vs 表面全称      'ORKG' ↔ 'Open Research Knowledge Graph'
  0.4074   ✅    缩写↔全称               'KG' ↔ 'Knowledge Graph'
  0.3961   ❌    同缩写不同全称           'AD' ↔ 'Action Dropout'
  0.3462   ❌    完全不相关               'Tabula' ↔ 'GROBID'
```

---

## 二、★ 核心发现：两类**完全交叠**

```
该合 9 对：最低 0.4074 · 中位 0.7130 · 最高 0.9655
该拒 11 对：最低 0.3462 · 中位 0.4873 · 最高 **0.8502**

交叠区间：0.4074 … 0.8502
```

**三个最能说明问题的对照**：

| 该拒（余弦更高） | | 该合（余弦更低） |
|---|---|---|
| `graph` ↔ `graph neural network` | **0.8502** | |
| | | `BERT` ↔ `BERTLarge` **0.6250** |
| `Tag` ↔ `Tagger` | **0.7477** | |
| | | `KG` ↔ `Knowledge Graph` **0.4074** |
| `AD` ↔ `A. Doan` | **0.5510** | |
| | | `AD` ↔ `Anomaly Detection` **0.4681** |

**注意最后两行**：**同一个 `AD`**，与"该拒的 `A. Doan`"（0.5510）
比与"该合的 `Anomaly Detection`"（0.4681）**更像**。

**判别分析**：扫遍 0.30–0.99 的所有阈值，**最佳单阈值 0.45 的 F1 只有 0.696**
（TP 8 · FP 6 · FN 1 · TN 5）。**这不是阈值没调好，是两类本来就叠在一起。**

---

## 三、为什么必然如此（这不是模型不好）

**bge-m3 编码的是"语义相近"，而我们要判的是"是否同一个实体"。**
这两件事在短名上**本来就不同**：

- `Tag` / `Tagger` — 语义**极近**（一个东西和它的施事），但**不是同一个实体**；
- `graph` / `graph neural network` — 语义**高度相关**（后者是前者的一种应用），
  但**是两个概念**；
- `AD` / `A. Doan` — 一个是缩写、一个是人名，**语义上无关，但都是两个字母起头**，
  嵌入模型对"极短字符串"的表征本身就不稳。

**⇒ 换更强的嵌入模型也不会解决** —— 因为要判的那个关系（同一性）
**不是嵌入空间里的距离**。

---

## 四、那它**能**用来做什么

按 `src/embeddings.py` 的三段口径（`HIGH=0.93` / `LOW=0.30`）：

```
能自动决定 2/20 条，其中对 2、错 0
剩 18 条落到"不可判" ⇒ 交给字符判据或人工
```

**⇒ 高分区是可靠的**（2/2 全对），而且它抓到的正是**字符判据容易漏的**：
`OpenRefine`/`Open Refine`（0.9379）、`multi-head attention`/`Multi-Head Attention`（0.9244）。

**所以正确的定位是：一条高置信度的补充通道，不是替代品。**

### 对 H4 两个具体用途的分别结论

| 用途 | 结论 |
|---|---|
| **E1 用向量余弦替代字符重叠** | ❌ **不成立** —— 交叠区间覆盖了 18/20 的样本。**字符判据仍是主力**；余弦只在 ≥0.93 时补充 |
| **PathQA 端点匹配失败时语义召回兜底** | ✅ **成立且有用** —— 那是"检索不到就放宽"，允许返回**候选**（不要求判对同一性）。⚠️ 但**必须给最低分阈值**：返回一个 0.3 分的"最像的"比返回空更危险（调用方会以为找到了） |

**⇒ 两个用途一否一立，这就是 H4 的结论。**

---

## 五、诚实的边界

| # | 边界 | 影响 |
|---|---|---|
| **1** | **标定集只有 20 对，且是我手写的** | 有选择偏差；但**交叠幅度（0.41–0.85）足够大**，结论不会因为再加几十对而翻转 |
| **2** | **只测了 `bge-m3:latest`** | 换模型可能改变具体分数；**但"同一性不是嵌入距离"这一点不随模型变** |
| **3** | **只测了"名字对"，没测"名字+上下文"** | 若把实体在论文里的**上下文描述**一起嵌入，同一性判断会显著改善 —— **那是另一条路线**（且它更接近 `_probe_bge_direction.py` 探过的方向） |
| **4** | **`HIGH=0.93` 是从这 20 对上定的** | **样本小**；要在更大标定集上重定。当前它的价值是"宁可不判"，不是"判得准" |

---

## 六、复现

```powershell
# 标定（约 15 秒，需要 Ollama 在跑且 bge-m3:latest 已拉取）
python tools/embedding_threshold_eval.py

# 在代码里用
python -c "import sys; sys.path.insert(0,'src'); from embeddings import embed, cosine, judge; ..."
```

产物：`data/quality/embedding-threshold-eval.json`
（**在 `data/` 下 ⇒ 入库要 `git add -f`**，见 `tools/check_artifacts_tracked.py`）
