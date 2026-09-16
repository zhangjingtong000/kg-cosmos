# A4 · 三个模块接入图这一层（2026-09-16）

> **配套**：`src/graph_sanitize.py`（新增）·
> `tools/_probe/_probe_sanitize_impact.py`（只读量化）·
> `data/quality/sanitize-impact.json` · `tests/test_graph_sanitize.py`（12 例）
>
> **一句话**：三个「只报不改」的模块被接成**一处决定、全部记账**的图级净化；
> 而**"级联硬删"这个第一版设计在量完全库之后被推翻** ——
> 改成**默认软删（标记不删）**，硬删变成显式开关。

---

## 一、A4 的原话与它的真实难点

> 三个模块**都只在 `tools/_probe/` 里被引用**，没进 `pipeline.py`。不接就没有实际收益。
> 接之前**先设计"被过滤实体所连的边怎么办"**（悬空边）——这一层**现在没有设计**。

**读过实现之后，问题的位置变了**：

```
entity_filter.filter_entities(nodes)         → (kept, dropped)，**不修改入参**
entity_absorb.apply_absorptions(nodes, plan) → **不删节点**，只加 absorbed_into
direction_witness.witness(desc, s, t, type)  → 只返回判定
```

⇒ **三个模块都是"只报不改"**，悬空边**不会在它们内部产生**。
⇒ 它产生在**接入时**：一旦你把节点集换成 `kept`，**原来那些边就有一端指向不存在的节点**。
⇒ 所以"设计"这一步就是新建的 `src/graph_sanitize.py` 的职责：
**把节点的取舍与边的取舍放在同一个地方决定，并记账。**

---

## 二、三个动作各不相同（理由比动作重要）

| 模块 | 对节点 | **对边的动作** | 为什么是这个动作 |
|---|---|---|---|
| `entity_filter` | 丢弃 | **默认软删（打标）；硬删要显式开** | 见 §三 —— 硬删的第一版设计**被数据推翻** |
| `entity_absorb` | 标 `absorbed_into` | **重指向 + 去重** | `A→X` 应变 `B→X`，**不是删边**；重指后可能与已有边撞上 ⇒ **必须去重** |
| `direction_witness` | 不动 | **只在高置信（`inverted`）时翻转** | 判不出就不动；**改错方向比漏改一个更糟** |

---

## 三、★★ 被推翻的第一版设计：级联硬删 → 软删

### 第一版做了什么

实体被过滤 ⇒ 它连的边**一律级联硬删**。逻辑上很顺：
"参考文献作者是噪声 ⇒ 它连的边也是噪声"。

### 量完全库之后，这一刀太大

`tools/_probe/_probe_sanitize_impact.py`（只读全库 17 篇图）实测：

```
2012.00456v1    过滤 120 个实体  ⇒  **删掉 570 条边**
2002.00388v4    过滤  15 个实体  ⇒  **删掉 1072 条边**
1703.08098v9    过滤  23 个实体  ⇒  **删掉 560 条边**
...
全库：过滤 667 个实体 ⇒ 删掉约 9000 条边，而全库约 45000 条 ⇒ **20%**
```

**平均每过滤 1 个实体删掉 24 条边。**

### 根因

被过滤的是**参考文献作者**（`Smith, J.` 这种形态），
而它们在**图里度数极高** —— 连着一堆论文、机构、合作者、被引关系。
**"实体是噪声" 推不出 "它的每一条边都是噪声"。**

### 新设计：软删（标记不删）

```
默认                      边的两端只要有一端被过滤 ⇒ 打标
                          e['src_filtered'] = 原始名字
                          e['tgt_filtered'] = 原始名字
                          **一条边都不删**

KG_SANITIZE_DROP_EDGES=1  才真的级联硬删（并断言不留悬空边）
```

**这与 `entity_absorb` 的 `absorbed_into` 是同一哲学**：
**模块只"报"，不擅自"改"**；真要改，由调用方显式要求。
⇒ 信息不丢，下游（前端筛选、统计、六层评测）可以**按需**排除。

### 修正后的影响面

```
指标                 lvl=1 只吸收   lvl=2 +过滤   lvl=3 全开
吸收的实体对               23          23          23
重指向的边                 48          48          48
重指后合并的重复边          211         211         312
★ 被过滤的实体               0         754         754
★ 级联删除的边               0           0           0      ← 软删
★ 翻转方向的边               0           0       1,560
判不出方向的边                0           0      38,115
未知 verdict                0           0           0      ← 取值约定没漂
```

**方向翻出率 = 1560 / 39675 = 3.9%** —— 而 `L5_SIX_LAYER_REPORT.md §4.5` 记着
NLI 通道对 2348 条真实反转**只能拦住 23.9%**。
⇒ 3.9% 正是"**高置信那一小部分**"，**这个比例本身说明判据是紧的**（合理）。

---

## 四、记账（本模块存在的一半理由）

`sanitize()` 的 `report` **每一个被丢掉或改动的东西都有数字**：

```
absorbed_pairs · rewired_edges · merged_after_rewire
dropped_entities · dropped_by_reason · dropped_edges_dangling · marked_edges
direction_flipped · direction_uncertain · direction_unexpected
nodes_before/after · edges_before/after
```

**为什么记得这么细**：本项目反复栽在"改了东西却没注意副作用"上 ——
`backfill_missing_endpoints.py` 把 L1 从 92.8% 抬到 94.4%，
**而那个副作用是后来才发现的**。

---

## 五、默认关闭，分四档（不是布尔）

```
KG_SANITIZE=0   不净化（**默认** ⇒ 既有绿灯与全部历史产物口径**完全不变**）
KG_SANITIZE=1   只吸收（最保守：只加字段）
KG_SANITIZE=2   吸收 + 过滤（软删标记）
KG_SANITIZE=3   全开（再加方向见证）
```

**为什么分级**：三件事的风险差很多（吸收只加字段、过滤会改节点集），
一个布尔开关会把它们绑死，**没法只开风险最低的那个**。

**默认路径零开销**：`lvl=0` 时 `sanitize()` **原样返回同一个对象**（连浅拷贝都不做），
`test_level_0_is_a_noop` 用 `is` 断言钉住了这点。

---

## 六、⚠️ 过程中发现的两个真问题

### ① 我假设了 `witness` 的返回约定，而它是错的

第一版我按 `verdict == 'reversed'` 写方向翻转。实际约定是：

```
desc_verdict → "consistent" / "inverted" / "unknown"
witness      → 判不出返回 **None**；否则 {'verdict': ...}
```

⇒ `'reversed'` **永远不命中**，而 `'inverted'` 又落不进任何一个分支 ⇒ **静默丢失**。

**修法**：改成 `'inverted'` 才翻转，`'consistent'` 不动，
**并新增 `direction_unexpected` 计数器** —— 将来约定再漂，会**暴露**而不是静默。

（这是本项目**第二次**犯"假设返回约定而不先读实现"：A5 时我假设
`tokens()` 会保留点，实际它把点与括号都去掉了。）

### ② 我的测试自己也写错了一条

`test_direction_only_flips_on_inverted` 第一版用了
`witness('RotatE was authored by TransE', 'TransE', 'RotatE')`
并断言"翻转或判不出至少一个"。实际它返回 **`consistent`** ——
因为 `RotatE` 在句首（`order=T_S`）而 `authored by` 是"无助动词的被动"（`sem=('T','S')`），
`sem == graph` ⇒ **一致**。**那是对的结果，而我的断言没给 `consistent` 留位置。**

⇒ 要造出 `inverted`，必须让**施事出现在句首**：
`'TransE was authored by RotatE'` 配边 `TransE → RotatE`。
现在这条测试**断言 `direction_flipped == 1` 且端点确实互换了**。

---

## 七、与已有的 `dangling` 检查的关系（别混淆）

`pipeline.py` **早就有**一处"删边 + 记账"：

```python
# 端点无法解析到实体 ⇒ 丢弃，并计数 dangling_edges_dropped
```

**它与 A4 的净化不是同一件事**：

| | 判据 | 由来 |
|---|---|---|
| **已有的 `dangling`** | **名字对不上**（端点在节点集里找不到） | 抽取器的端点规范化不完整 |
| **A4 的净化** | **节点被判为噪声而删除**（名字能对上，但节点不该存在） | 实体过滤器 |

**两者的处理模式一致（丢弃 + 计数）**，这是刻意的 ——
`graph_sanitize` 的 `dropped_edges_dangling` 与 `pipeline` 的同名概念对齐。

---

## 八、诚实的边界

| # | 边界 | 影响 |
|---|---|---|
| **1** | **`lvl>=1` 尚未在真实抽取上端到端跑过** | 本轮的量化是**对已有图快照做变换**，不是"重新抽取一遍"。⇒ 只测了净化本身，**没测它对抽取流程的副作用** |
| **2** | **六层指标在净化后没有重算** | 尤其 L3（关系端点）与 L4（谓词合规）**会被净化影响**，但重算要跑完整评测链 |
| **3** | **`raw` 与 `lvl=1` 的端到端金标 F1 差没有实测** | 模块 docstring 记着 +0.0051，那是**模块级**的旧实测，不是接入后的 |
| **4** | **软删的标记还没有下游消费者** | `src_filtered` / `tgt_filtered` 已写进边，但**前端与评测还没读它** |
| **5** | **方向翻转只覆盖 `description` 通道** | `witness` 的覆盖面是"description 里有谓词语义"的那部分（1560/39675） |

**第 1、3 条是最该补的**：需要一次"打开 `KG_SANITIZE=1` 重新抽一篇并算金标 F1"的端到端实验。

---

## 九、复现

```powershell
# 只读量化（三档在全库 17 篇图上的影响面）
python tools/_probe/_probe_sanitize_impact.py

# 端到端开启（注意：会改变抽取产物的口径）
$env:KG_SANITIZE='2'                      # 吸收 + 过滤（软删）
$env:KG_SANITIZE_DROP_EDGES='1'           # 再加：真删被过滤实体连的边
```
