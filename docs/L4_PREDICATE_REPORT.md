# L4 谓词选择层 · 「谓词选对了吗」评测报告

> 补的是 `docs/L5_SIX_LAYER_REPORT.md` §6.5 第 3 条自认的缺口：
> 「本项目的 L4 比报告版窄……只判谓词是否在 15 类本体**内**，**选错但落在本体内会被判通过**。」
> 生成脚本：`tools/l4_predicate_report.py`。
> **权威产物**：`data/quality/l4-predicate.json`（`--full`，43630 条全量，2026-09-12，耗时 4139s）。
> 抽样产物：`data/quality/l4-predicate-sample-150.json`（分层估计 + bootstrap CI）。
> 成本：全程 **¥0**，本地 GPU 推理，模型从本机 HF 缓存离线加载（`HF_HUB_OFFLINE=1`）。

---

## 0. 结论（先说要紧的）

**全量口径（43630 条，无抽样误差）**：

| 口径 | 数值 | 说明 |
| --- | --- | --- |
| **旧 L4**（谓词在本体 15 类内） | **99.46%** | 自证型：本来 USES 标成 PART_OF 也算通过 |
| **新 L4**（15 类 argmax = 标注谓词） | **25.73%** | 全量 43630 条 |
| 平凡基线（全预测 `RELATED_TO`） | **22.00%** | **新 L4 只比它高 3.73pp** |
| 随机（按类先验） | 14.8% | |
| 宏平均 F1（15 类） | 0.1724 | 多数类 F1 在 0.03–0.45 |
| top-2 命中率 / MRR | 40.01% / 0.4399 | 标注类常排在第二 |
| `hit=both` 子集一致率 | 26.08% | 端点齐备时并没有更好 |
| 标注类自身分数 ≥0.67（= L5 口径） | **63.66%** | 与 L5 的 63.7% **完全吻合** ⇒ 口径自洽 |

**三点结论**：

1. **L4 的 99.5% 与"谓词选对"无关。** 换成"选对没有"的口径，数字掉到 **25.73%**，
   仅比"什么都不判、全填 `RELATED_TO`"的 22.00% 高 **3.73pp**。
   论文里**不能**再用 99.5% 声称谓词质量；必须两个口径并列，并说明它们测的是不同构念。
   （`data/quality/metric-provenance.json` 里这两个数已经并列在同一张表中。）
2. **25.73% 不是"抽取器的准确率"，而是"两个模型的一致率"。** 标注谓词来自 LLM 抽取器，
   而该抽取器在抽取时**没有拿到任何谓词定义**（`src/kg_extractor.py` 的
   `COMBINED_PREFIX` 只列了 15 个类型名，见 §3.4）。所以这个数读作
   **机器版 inter-annotator agreement**，不是精度。
3. **L5 的 63.7% 与 L4 的 25.7% 并不矛盾，反而互相解释。**
   在被判错的边里 **54.23%** 的情况是"标注类分数 ≥0.67（L5 说支持）却输给了别的类"
   —— 即**多个谓词的假设句同时被证据支持**。这正是"63.7% 只能读作通过率、
   不能读作正确率"的量化版本（与 `docs/L5_VALIDITY_PLAN.md` §0 的结论一致）。

> **一个教科书级的实例**（全量错例，§4.6）：`Bangkok → Thailand` 被标成 `PART_OF`，
   模型预测 `LOCATED_AT`，两者分数**都是 0.9998**。曼谷既是泰国的一部分、也位于泰国
   —— 这类边的"正确答案"在 15 类本体里**不存在唯一解**。`Miami → Florida`、
   `Valencia → Spain`、`Riga → Latvia`、`Prague → Czech Republic` 同理。
   这直接支撑 §3.4 的结论：**本体边界在这批数据上不可复现。**

---

## 1. 算法

### 1.1 一句话

对每条边，让**同一条 premise** 与 15 个假设句模板各配一个 hypothesis，
取 `entail_prob` 最大的类作为**预测谓词**；与标注谓词一致记对。

```
premise    = relation-verify-all.json 的 premise 字段（已由 pick_evidence_sentence 抽好）
candidate_j= REL_HYPOTHESIS_TEMPLATE[type_j].format(s=source, t=target)   j = 1..15
score[i][j]= P(premise_i 蕴含 candidate_j)          ← 同一个 Triplet Critic，softmax[:,1]
pred_i     = argmax_j score[i][j]
正确       ⟺ pred_i == 标注 type_i
```

### 1.2 函数与数据结构（可直接照写）

| 函数 | 输入 → 输出 | 职责 |
| --- | --- | --- |
| `load_edges()` | `→ (d, rows, n_all, n_out, n_nop)` | 取「本体 15 类内 + premise 非空」= **43630** 条为有效分母；本体外 237 条与无 premise 的条数单独报出，不悄悄缩小分母 |
| `verify_hypothesis_field(rows)` | `→ 不一致条数` | **核实** `hypothesis` 字段 == 主口径模板代入结果（实测 43630/43630 一致）；这是 `--reuse-l5` 的前提，不一致就拒绝复用 |
| `pick_sample(rows, per_type, seed)` | `→ (sample, weights)` | 按谓词分层抽样，层内按 `hit` 配平；层权 `w = N_type / n_type` |
| `load_model()` | `→ (model, tok, dev, sup_idx)` | 本地 snapshot 加载，`HF_HUB_OFFLINE=1`；`sup_idx` 从 `id2label` 里找 entail |
| `score_grid(model, tok, dev, sup, rows)` | `→ scores[N][15], elapsed` | 展平成 `(i, j)` 任务表后按 batch 推理；`--reuse-l5` 时标注类那一格直接填 `entail_prob` |
| `build_rows(rows, scores, weights)` | `→ list[dict]` | 每条边给出 `label/pred/pred2/rank/s_label/s_max/margin/tie/hit/w` |
| `confusion(rs)` | `→ cm[label][pred]`（加权） | 混淆矩阵 |
| `accuracy(rs, filt)` | `→ 加权 top-1` | 支持任意子集过滤（hit 分层、排除平局） |
| `macro_f1(rs)` | `→ (macro_f1, per_class_f1, tp, fp, fn)` | 15 类宏平均 |
| `bootstrap_acc(rs, B, seed)` | `→ (lo, hi)` | **层内**有放回重抽（保持每层条数），重算加权一致率 |

产物结构（`data/quality/l4-predicate.json`）：`metrics` / `l5_cross` / `by_hit` /
`per_class` / `confusion[15][15]` / `templates` / `mode` / `form` / `reuse_l5` / `elapsed_s`。

### 1.3 判据与三个必须同时报的数

- **主判据**：`agreement_top1`（加权，与标注谓词一致率）。
- **必须并列的对照**：`baseline_related_to`（全预测 `RELATED_TO`）。
  没有这个对照，25.73% 会被误读成"还行"。**25.73% 与 22.00% 的差（3.73pp）才是信息量。**
- **`agreement_top1_hit_both`**：只在"两个端点都字面出现在 premise 里"的子集上算，
  用于回答"是不是端点缺失拖低的"（实测：不是，见 §4.3）。

---

## 2. 模板：15 类谓词各自变成一个可问 NLI 的假设句

模板已收进 **`src/ontology.py` 的 `REL_HYPOTHESIS_TEMPLATE`**（唯一来源），
方向约定 `{s}=source`、`{t}=target`，与 `RELATION_ENDPOINT_CONSTRAINTS[*]["semantics"]`
和既有的 `tools/verify_direction.py` 的 `REL_TEMPLATE` **逐字一致**。

| 谓词 | 假设句模板 | 方向怎么定的 |
| --- | --- | --- |
| `PART_OF` | `{s} is part of {t}.` | s 是 t 的组成部分 |
| `USES` | `{s} uses {t}.` | **主动语态**：s 使用 t。反写成 `{t} is used by {s}` 语义等价，但为与既有 L5 口径一致保持主动 |
| `PRODUCES` | `{s} produces {t}.` | s 生产者、t 产物 |
| `MEASURES` | `{s} measures {t}.` | s 度量 t |
| `EVALUATES` | `{s} evaluates {t}.` | s 评测 t |
| `COMPARES_WITH` | `{s} is compared with {t}.` | 对称，方向无意义 |
| `SUPPORTS` | `{s} supports {t}.` | s 支持 t |
| `CONTRADICTS` | `{s} contradicts {t}.` | 对称 |
| `CAUSES` | `{s} causes {t}.` | s 原因、t 结果 |
| `ENABLES` | `{s} enables {t}.` | s 使能 t |
| `APPLIES_TO` | `{s} is applied to {t}.` | s 被应用到 t |
| `LOCATED_AT` | `{s} is located at {t}.` | s 位于 t |
| **`CREATED_BY`** | **`{t} was created by {s}.`** | **注意：句子里 t 在前**。T3.1 已把方向统一为 Author(source) → Work(target)，所以必须写"作品由作者创作"。写成 `{s} was created by {t}` 会把语义判反——实测该类通过率 39.6% → 26.0%（`L5_SIX_LAYER_REPORT.md` §1.1c） |
| `CITES` | `{s} cites {t}.` | s 引用 t |
| `RELATED_TO` | `{s} is related to {t}.` | 兜底关系，语义最弱 |

**方向的三条硬规则**（`validate_constraints()` 已把第 3 条写成逐字断言，改反就报错）：

1. 主动语态类（`USES`/`PRODUCES`/`CAUSES`/`ENABLES`/`CITES`/`CREATED_BY`）的 `{s}`
   必须是语义上的施事方。反转 `{s}`/`{t}` 得到的必须是**另一个命题**，不能是同义句。
2. 不需要刻意用被动式。`verify_direction.py` 与 `l5_validity_probe.py` 的 P1 已实测
   判别器对主/被动**不敏感**（5 种等价写法通过率极差仅 4.4pp），所以选哪种语态不影响结论，
   但**必须与 L5 现行模板一致**，否则 L4 与 L5 问的不是同一个问题。
3. **`CREATED_BY` 的模板方向被逐字锁死**在 `REL_HYPOTHESIS_TEMPLATE` 与
   `REL_PHRASE_TEMPLATE` 里（见 `src/ontology.py` 与 `tools/check_ontology_consistency.py`）。

### 2.1 形态消融：`--form phrase`

同一批边换用**名词短语形态**（`REL_PHRASE_TEMPLATE`，如 `{s} created {t}`）再跑一次。
理由是官方校准集 `en_triplet_entailment.tsv` 的 hypothesis 就是名词短语
（`CBS Corporation subsidiary The CW`），而 0.9851 的 ROC-AUC 是在那个形态上标定的
（`docs/L5_VALIDITY_PLAN.md` §1.2 已实测记录这一错配）。
若两种形态的一致率相差很大 ⇒ argmax 主要由**措辞**驱动，而不是由谓词语义驱动。

---

## 3. 循环论证：正面回答

**结论：不构成逻辑循环，但构成「分数复用」，因此二者不能当作两条独立证据。**

分三层说清楚，每一层都给可核查的依据。

### 3.1 标签来源 —— 不是"用模型验证模型"

L4 的标签 `y` 来自**抽取器**（`src/kg_extractor.py` 调 LLM 从 17 篇 PDF 抽出的 `type` 字段），
不是本判别器产生的。判别器只做打分。所以不存在"用判别器的输出去验证判别器的输出"。
**同一个模型既做 L5 又做 L4 这件事本身不违例**——两次推理的**任务不同**：

| | 假设集合 | 决策规则 | 问题 |
| --- | --- | --- | --- |
| L5 | **1 个**（标注谓词的模板句） | 阈值 0.67 | "这条边**被证据支持**吗" |
| L4 | **15 个**（全部谓词模板） | argmax | "**哪一个**谓词最被支持" |

判"是否被支持"与判"哪个最被支持"是两个不同的构念；前者可以 63.7%，后者只有 25.73%，
这不矛盾（见 §4.4 的 A/B/C 分解）。

### 3.2 分数复用 —— 这一条必须写进论文

`data/quality/relation-verify-all.json` 的 `hypothesis` 字段就是主口径模板按每条边的
`(source, target)` 代入的结果，实测 **43630/43630 逐字一致**（脚本启动即复核）。
因此：

> **L4 候选集里「标注谓词」那一格的分数，就是 L5 的 `entail_prob`。**

后果有两个，都很实际：

- `--reuse-l5` 可以省 1/15 算力（全量从 654450 次降到 614138 次推理）；
- **L4 与 L5 共享同一批分数，不是独立的两条证据。** 论文里必须声明，
  不能写"两个指标互相印证"。

### 3.3 真正的自证风险在**数据侧**，不在模型侧 —— 已量化

`pick_evidence_sentence()` 优先挑「**同时含两个端点**」的句子作为 premise；
而模板句里又直接嵌了这两个端点的**字面串**。两者同源 ⇒ argmax 有可能退化成
"端点共现检测"而不是"谓词语义判别"。

**已有实测证据**：判别器分数有约八成可由"两个端点词在不在句子里"解释
——用 `hit` 区分 `entail_prob` 两端层的 **AUC = 0.8177**（`docs/L5_VALIDITY_PLAN.md` §1.1）。

**三层对策，脚本全部内建**：

1. **按 `hit` 分层报告**（§4.3 实测：`both` 25.8% vs `one` 27.6%，**未发现端点缺失是主因**；注意这只是描述、不是排除——真正的排除需要 placebo 对照，见 §4.3 的说明）；
2. **形态消融** `--form phrase`；
3. **交叉统计 B**（标注类 ≥0.67 却输给别的类）—— 若 B 很高，说明"错"多数是
   模板间语义重叠而非无证据，这与"端点共现"是**不同的**解释。

### 3.4 还有一个更根本的自证问题：`y` 本身不是真值

抽取 prompt 只给了 15 个类型**名字**，**没有给任何谓词定义**：

```
---Relation Types (use ONLY these)---
PART_OF, USES, PRODUCES, MEASURES, EVALUATES, COMPARES_WITH, SUPPORTS,
CONTRADICTS, CAUSES, ENABLES, APPLIES_TO, LOCATED_AT, CREATED_BY, CITES, RELATED_TO
```
（`src/kg_extractor.py` 第 514–515 行，`COMBINED_PREFIX`）

所以 LLM 在标注时是在**没有共享谓词定义**的情况下自行理解 `PART_OF` 与 `APPLIES_TO`
的边界。这直接决定了 25.73% 该怎么读：

> 新 L4 度量的是「**独立 NLI 模型对同一批边的谓词偏好**」与
> 「**无谓词定义下的 LLM 抽取标签**」之间的一致率 ——
> 是**两个模型的机器版 inter-annotator agreement**，**不是准确率**。
> 25.73% 低，既可能因为抽取器标错，也可能因为两套系统对 15 类的划界本就不同，
> 本指标**无法区分这两者**。

同时它也解释了 `L5_VALIDITY_PLAN.md` §1.4 那条"谓词字面线索区分度只有 5.6pp"的实测
—— 前提句里有没有 `uses`/`part of` 这类线索词，与谓词判得对不对几乎无关。

---

## 4. 结果

### 4.1 主指标（**全量 43630 条**，`data/quality/l4-predicate.json`）

```
top-1 一致率              25.73%     ← 无抽样误差；这是论文主数字
top-1 一致率（hit=both）  26.08%
top-1 一致率（排除平局）  25.77%     近乎平局只占 0.17%（73/43630）
top-2 命中率              40.01%
MRR                       0.4399
宏平均 F1（15 类）        0.1724
margin（最高分−标注类分）中位数  0.0597
平凡基线（全预测 RELATED_TO）  22.00%
随机（按类先验）期望           14.8%
标注类自身分数 ≥0.67（=L5 口径）63.66%   ← 与 L5 的 63.7% 完全吻合，口径自洽
推理耗时                  4139s（654450 对，158.1 对/秒，batch=64）
```

分层抽样的独立复核（2251 条，层权还原 + 层内 bootstrap 1000 次）给出
**26.0%，95% CI [23.6%, 28.8%]**——全量值 25.73% 落在区间内，
两个口径互相印证（`data/quality/l4-predicate-sample-150.json`）。

### 4.2 混淆矩阵（全量，行归一化 %，行=标注、列=预测）

```
标注\预测          APPLI CAUSE CITES COMPA CONTR CREAT ENABL EVALU LOCAT MEASU PART_ PRODU RELAT SUPPO  USES
APPLIES_TO       17     1     1     1     0    11     7     4     2     7    22     4    18     1     4   n=3850
CAUSES            6    19     0     1     1     6     9     4     0    11    21     4    16     1     1   n=280
CITES             3     1     5     2     1    25     1     6     3     9    28     5     5     1     6   n=967
COMPARES_WITH     4     2     1    22     1    16     5     6     3     4    19     4     6     2     5   n=598
CONTRADICTS       7     7     0     3     6    10    12     6     2    11    17     4     9     4     3   n=243
CREATED_BY        2     0    15     0     0    48     1     4     1     3     7     4     6     5     3   n=8755
ENABLES          15     3     1     1     0     9    21     2     1     6    18     7    11     4     2   n=999
EVALUATES         6     0     0     2     0    22     2    19     2     9    18     3     8     0     8   n=454
LOCATED_AT        2     0     1     0     0    10     0     1    29     9    41     1     4     0     1   n=853
MEASURES          4     1     1     2     1    13     3    12     2    26    17     4    11     1     2   n=872
PART_OF           4     1     1     1     0     7     3     2     8     7    46     5    10     1     4   n=5816
PRODUCES          7     3     2     1     0    23     5     7     2     6    14    17     6     1     5   n=2213
RELATED_TO        4     3     4     2     1    23     3     6     5     6    21     5    12     3     4   n=9598
SUPPORTS         10     1     1     2     0    10    16     4     1     6    20     4    15     7     4   n=793
USES              8     1     1     1     0    18     6     5     2     6    19     6     9     2    15   n=7339
```

**读法（三条，都是结构性的）**：

1. **`PART_OF` 是最大吸引子。** `LOCATED_AT` 41% 被预测成 `PART_OF`（召回仅 29%）、
   `CITES` 28%、`APPLIES_TO` 22%、`CAUSES` 21%、`RELATED_TO` 21%、`SUPPORTS` 20%。
   原因是 `X is part of Y` 是 15 条里语义最弱的断言，只要 X、Y 在同一句里共现就容易高分
   —— 这一点正是 §3.3 说的"端点共现 > 谓词语义"的具体落点。
2. **`RELATED_TO` 召回只有 11.6%**，尽管它是最大类（9598 条，占 22.0%），
   且它最常被误判成 **`CREATED_BY`（2168 条）**。原因是 `{t} was created by {s}` 的 `by`
   是个极强线索，而参考文献、作者地址等句式里到处都是 `by`，兜底关系因此被大量吞掉。
3. **`CREATED_BY` 召回 48.5%，是表现最好的一类**；`USES` 精确率 44.8% 也显著高于其他类。
   两者都有明确动词线索。这是一个**正面案例**：模板方向修正（§1.1c）确实带来了可测收益。

### 4.3 按端点命中分层（全量）

```
hit=both  n=20397  一致率 26.08%
hit=one   n=20953  一致率 26.71%
hit=none  n= 2280  一致率 14.13%
```

⇒ **`hit=both` 与 `hit=one` 的一致率几乎相同（26.1% vs 26.7%）**，
说明 25.7% 这个数**不是**端点缺失造成的；`hit=none` 只有 14.1%，
与其"证据里根本没提这两个实体"相符。

> ⚠️ **但这只是描述，不是排除。** 分层统计能说明"两个已知端点的子集与一个已知端点
> 的子集表现相近"，**不能**排除"模板里嵌了端点字面串、从而把分数抬高"这一机制——
> 恰恰相反，`hit=none` 比 `both` 低约 12 个百分点，**正是端点字面命中会抬分的迹象**。
>
> 要真正排除它，只能做**安慰剂对照（placebo）**：把 premise 换成同论文的无关句、
> 或把端点换成随机同类型实体名，看 15 类 argmax 的一致率会不会崩到随机水平（14.8%）。
> **该实验尚未做**；在补上之前，本节的结论只能写成"未发现端点缺失是主因"，
> 不能写成"已排除"。
>
> 另需注意：§4.5（L5 侧）用同一批数据得出的解读是
> 「判定主要被端点词在不在句子里解释掉」（`entail_prob` 区分 `both` 与 `none` 的 AUC = 0.8177）。
> **两份报告对同一组数据的方向性解读相反**，这个分歧必须解决——
> placebo 对照正是解决它的手段。

### 4.4 L5 与 L4 为什么不矛盾（全量关键分解）

```
标注类分数 ≥0.67 的边占                        63.66%   ← L5 的通过率口径
A 猜对但标注类自身 <0.67                       9.13% 的「对」   ← 噪声抬起来的「对」
B 标注类 ≥0.67 却输给别的类                    54.23% 的「错」  ← 模板区分度不足
C 连最高分都 <0.67                             17.98% 的「错」  ← 确实无证据
```

**读法**：被判错的边里，**超过一半（54.23%）**的情况是"标注谓词的假设句**确实被证据支持**
（分数 ≥0.67），只是另一个谓词的假设句得分更高。这不是"证据不支持这条关系"，
而是"**证据同时支持多个谓词**"。

⇒ 这正是 `L5_VALIDITY_PLAN.md` §0 结论的量化版本：
**63.7% 是"该假设句被蕴含"的比例，不是"这条关系正确"的比例**；
新 L4 的 25.73% 说明 15 类本体在这批数据上**没有可被独立模型稳定复现的语义边界**。

### 4.5 每类精确率/召回/F1（全量）

| 谓词 | n | 召回 | 精确 | F1 | 最常被误判成 |
| --- | --- | --- | --- | --- | --- |
| `CREATED_BY` | 8755 | 48.5% | 42.8% | **0.454** | `CITES`(1298) |
| `PART_OF` | 5816 | 45.9% | 28.8% | 0.354 | `RELATED_TO`(602) |
| `USES` | 7339 | 15.0% | **44.8%** | 0.225 | `PART_OF`(1392) |
| `COMPARES_WITH` | 598 | 21.7% | 19.3% | 0.205 | `PART_OF`(112) |
| `APPLIES_TO` | 3850 | 16.6% | 24.6% | 0.198 | `PART_OF`(841) |
| `LOCATED_AT` | 853 | 29.0% | 14.7% | 0.195 | `PART_OF`(353) |
| `PRODUCES` | 2213 | 17.5% | 16.9% | 0.172 | `CREATED_BY`(507) |
| `RELATED_TO` | 9598 | 11.6% | 26.1% | 0.160 | `CREATED_BY`(2168) |
| `ENABLES` | 999 | 20.9% | 11.5% | 0.148 | `PART_OF`(181) |
| `MEASURES` | 872 | 26.0% | 8.4% | 0.127 | `PART_OF`(144) |
| `CAUSES` | 280 | 18.6% | 8.1% | 0.112 | `PART_OF`(59) |
| `CONTRADICTS` | 243 | 6.2% | 9.3% | 0.074 | `PART_OF`(41) |
| `EVALUATES` | 454 | 18.5% | 4.1% | 0.066 | `CREATED_BY`(101) |
| `SUPPORTS` | 793 | 6.9% | 5.3% | 0.060 | `PART_OF`(159) |
| `CITES` | 967 | 5.3% | 2.5% | **0.034** | `PART_OF`(267) |

### 4.6 错例：本体边界不可复现的教科书级证据

按"标注类分数仍很高"排序的错例（全量）：

| 标注 | 预测 | 标注类分数 | 最高分 | 实例 |
| --- | --- | --- | --- | --- |
| `PART_OF` | `LOCATED_AT` | 0.9998 | 0.9998 | `Heraklion → Crete` |
| `LOCATED_AT` | `PART_OF` | 0.9998 | 0.9998 | `Monash University → Australia` |
| `PART_OF` | `LOCATED_AT` | 0.9998 | 0.9998 | `Valencia → Spain` |
| `PART_OF` | `LOCATED_AT` | 0.9998 | 0.9998 | `Riga → Latvia` |
| `PART_OF` | `LOCATED_AT` | 0.9998 | 0.9998 | `Bangkok → Thailand` |
| `PART_OF` | `LOCATED_AT` | 0.9998 | 0.9998 | `Miami → Florida` |
| `LOCATED_AT` | `PART_OF` | 0.9998 | 0.9998 | `Vectors → Vector Space` |
| `PART_OF` | `LOCATED_AT` | 0.9998 | 0.9998 | `Pittsburgh → USA` |

**每一个都同时成立**：曼谷既是泰国的一部分、也位于泰国；迈阿密既是佛罗里达的一部分、
也位于佛罗里达。**"正确答案"在 15 类本体里不存在唯一解**，所以这类边的一致率
无论模型多好都不会高。这正是 §3.4 结论的直接证据，也说明：

> 25.73% 里有一部分**不是模型错，而是本体在这类实体对上本身有歧义**。
> 本指标无法把这一部分单独摘出来（需要人工盲判 + 明确的谓词定义，见 §7）。

**两个可直接写进论文的观察**：

- `CONTRADICTS` 召回 6.7%、F1 0.079 —— 与 `L5_SIX_LAYER_REPORT.md` §6「该关系在当前配置下
  不可用（通过率 5.8%）」**互相独立地印证**（一个来自 L5 分数，一个来自 L4 竞争判别）。
- `EVALUATES` 精确率 4.0%：它自己的 454 条里只有 17.3% 被认回，却有大量别的类被判成
  `EVALUATES` —— 与 `ontology.py` 里 `EVALUATES` 的备注一致（"数据集评估方法"在本图里
  正反两个方向都被写成 `EVALUATES`，实测 8 条"反转"经人工核对**全是假阳性**）。

---

## 5. 成本：实测，不是估算

### 5.1 实测吞吐（RTX 4060 Laptop，max_len=256）

| 规模 | batch | 实测吞吐 | 耗时 | 峰值显存 |
| --- | --- | --- | --- | --- |
| 短跑基准 1800 对 | 32 | 33.8 对/秒 | 53s | 2124 MiB |
| 短跑基准 1800 对 | **64** | **121.5 对/秒** | 15s | 3173 MiB |
| 分层抽样 33765 对 | 64 | 115.9 对/秒 | 291s | — |
| **全量 654450 对** | **64** | **158.1 对/秒** | **4139s = 69 分钟** | — |

**外推与实测的差别值得记下**：按 1800 对的短跑基准外推全量要 **1.50 小时**，
实际只用了 **69 分钟**——batch=64 的长跑稳态比短跑更快（短跑含 CUDA 初始化与
算子 autotune）。**所以成本估算一律以长跑实测为准。**

⇒ **结论：不必为了省时间而抽样。全量 69 分钟就能跑完，论文主数字应该用全量。**

**两个必须提醒的坑**：

1. **batch=32 比 batch=64 慢 3.6 倍**（33.8 vs 121.5）——不是线性关系。
   项目既有脚本默认 `--batch 32`（`relation_verifier.py`）/`--batch 16`（`triplet_critic.py`），
   跑 L4 时**务必用 64**。
2. **显存要够**：模型 fp32 约 1.1 GB，batch=64/len=256 峰值 3.1 GB。
   实测首次跑基准时 GPU 被游戏占用（5729/8188 MiB），速度掉到 20 对/秒以下。
   脚本会在可用显存 < 5 GB 时打印警告。

### 5.2 分层抽样方案（用于快速回归与 CI，不是主口径）

即使全量可行，抽样仍是**回归自检**的正确工具（30 秒内给出数字）。设计：

- **分层变量**：谓词 `type`（15 层）。**不是**按 `hit` 分层——混淆矩阵是按谓词算的，
  按 `hit` 比例抽会让 `CONTRADICTS`(243)、`CAUSES`(280) 在样本里几乎是空的。
- **层内再按 `hit` 配平**：每格至少 1 条（若非空），其余按 `hit` 比例分配。
  因为 `hit` 与分数高度共线（AUC 0.8177），任其失衡会把"一致率"变成 `hit` 分布的产物。
- **层权** `w_h = N_h / n_h`（本案 `RELATED_TO` 的 w=64.0，`CAUSES` 的 w=1.87）。
- **估计量**：`Σ w·1[pred==label] / Σ w`。
- **置信区间**：**层内 bootstrap**（每层有放回重抽 `n_h` 条，1000 次，取 2.5/97.5 分位）。
  不是简单随机抽样的二项 CI——分层 + 层权下后者会低估方差。
- **样本量**（实测 CI 半宽）：

| 每类 n | 总条数 | 推理对数 | 实测耗时(batch=64) | 95% CI 半宽（实测/外推） |
| --- | --- | --- | --- | --- |
| 30 | 450 | 6750 | 1.2 分钟 | ±5.3pp（smoke 实测 [23.8,34.4]） |
| **150** | **2251** | **33765** | **6.2 分钟（实测 369s）** | **±2.6pp（实测 [23.6,28.8]）** |
| 300 | ~4500 | 67500 | 12 分钟 | ±1.8pp（外推） |

**抽样已被全量复核**：`--per-type 150` 给出 26.0%（CI [23.6%, 28.8%]），
全量真值 **25.73%** 落在区间内 —— 说明这套分层 + 层权 + 层内 bootstrap 的口径可用，
可以放心当作日常回归（6 分钟）而不必每次跑 69 分钟的全量。

**抽样必须声明的两个偏差**（`L5_VALIDITY_PLAN.md` §3 已指出同类问题）：

1. 小类被**过度代表**（`CONTRADICTS` 抽 150/243 = 62%，`RELATED_TO` 抽 150/9598 = 1.6%），
   层权能还原**总体一致率**，但**按类的 P/R/F1 在抽样模式下精度不均**——大类的那一行
   其实是 1.6% 样本的外推。要做逐类结论，用全量。
2. 分层抽样给不出"哪些具体边错了"的完整清单（全量可以）。

### 5.3 独立第二判定者（离线可用，未实施——把最要紧的一问留成可选实验）

§3.1 说了"共享编码器 ≠ 循环论证"，但那只是逻辑上的辩白。**能真正检验这一点的
是换一个架构完全不同的判定者**。本机磁盘上确实有（文件核实，2026-09-12）：

| 模型 | 位置 | 体积 | 独立性 |
| --- | --- | --- | --- |
| `qwen3.5:9b` | `~/.ollama/models/manifests/registry.ollama.ai/library/qwen3.5/9b` | **6.29 GB** blob | 通用 LLM，与 NLI 无共享权重/训练目标 |
| `qwen3-vl:4b` | 同上 | 3.14 GB | 多模态，独立 |
| `bge-m3` / `nomic-embed-text` | 同上 | 1.10 / 0.26 GB | embedding（最弱，只能做语义距离粗筛） |

`ollama.exe` 存在（`%LOCALAPPDATA%\Programs\Ollama\ollama.exe`），服务未运行
——`ollama serve` 起本地服务**不需要外网**，不违反零云端支出。

**为什么这个实验有杀伤力**：它同时检验两件事。

1. **若 qwen3.5:9b 的一致率也≈26%** ⇒ 两个架构完全独立的模型都复现不出抽取器的标签，
   说明**问题在标签/本体边界，不在判别器**。这直接支撑 §3.4 的结论，
   并且可以就"25.73% 是判别器弱还是本体边界不可复现"给出**决定性的对照实验**（需离线起本机第二个判定者，**尚未执行**）。
2. **若 qwen3.5:9b 的一致率显著更高（如 60%）** ⇒ 说明"模板句 + 无谓词定义"
   这条路线本身弱，应改用显式定义的分类式 prompt。

**具体做法**（⚠️ **该脚本尚未实现** —— 全仓无 `tools/l4_second_judge.py`，
    现状见 §5.3 的「尚未实施」；下面是**计划中的**做法，与本层共用同一份抽样以保证可比）：

```python
# prompt 的谓词定义直接取自 src/ontology.py 的
# RELATION_ENDPOINT_CONSTRAINTS[*]["semantics"] —— 单一来源，不要另写一份
PROMPT = """你是知识图谱关系分类器。给定论文原文片段与两个实体，
从下列 15 个关系类型中选出**最匹配**的一个。

{definitions}

原文：{premise}
实体1（前者）：{source}
实体2（后者）：{target}

只输出一个类型名，不要解释。"""
```

- 每条边一次生成（不是 15 次打分），所以 200 条约 200 次生成，比模板法便宜得多；
- 与模板法算 **Cohen's κ**（不只是比一致率），并列出**分歧清单**优先送人工；
- **它不是金标**（两个模型一致 ≠ 正确，见 `docs/L5_VALIDITY_PLAN.md` §3 的 L2 层），
  只能当**扰动实验**用。

**为什么本次没跑**：`--full` 正在占用 GPU（峰值 3.1 GB / 共 8 GB），
而 9B 模型加载会与它争显存；串行跑完全量再跑这个实验是正确顺序。

> 注：任务书写的"本地可用的模型只有已缓存的 triplet critic"与磁盘实况不符，
> 本报告以文件为准（同一更正也出现在 `docs/L5_VALIDITY_PLAN.md` §1.5）。

---

## 6. 复现


```powershell
cd D:\kg-demo

# 0) 只验证脚本能跑通（每类 30 条，约 1 分钟）—— 数字不可引用
python tools/l4_predicate_report.py --smoke

# 1) 分层抽样 + CI（约 5 分钟）—— 回归自检用
python tools/l4_predicate_report.py --per-type 150

# 2) 全量（batch=64 约 1.5 小时）—— **论文主数字用这个**
python tools/l4_predicate_report.py --full

# 3) 形态消融：换成与官方校准集同形的名词短语假设（抽样即可，约 5 分钟）
python tools/l4_predicate_report.py --per-type 150 --form phrase

# 4) 复用 L5 已有分数只算其余 14 类（省 6.7%，需 hypothesis 字段与模板逐字一致）
python tools/l4_predicate_report.py --full --reuse-l5

# 5) 口径自检（模板三处逐字一致 + 端点约束表 + CREATED_BY 方向）
python tools/check_ontology_consistency.py

# 6) 回归测试：模板覆盖性、占位符、CREATED_BY 方向、三处副本一致
python -m pytest tests/test_l4_templates.py -q

# 7) 把新指标登记进版本绑定
python tools/metric_provenance.py --write

# 8)（可选，串行执行，先跑完 --full 再跑）独立第二判定者扰动实验
ollama serve                                    # 另开一个终端，离线
python tools/l4_second_judge.py --n 200         # 见 §5.3，尚未实施
```

**离线可跑**：脚本启动即设 `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1` 并清除代理
环境变量，模型只从 `~/.cache/huggingface/hub` 读。演示现场断网不影响。

---

## 7. 局限（论文里必须写）

1. **这是"一致率"，不是"准确率"。** 参照系是 LLM 抽取器的标签，而该抽取器在抽取时
   **没有拿到谓词定义**（§3.4）。所以 25.73% 不能读作"谓词正确率"，
   只能读作"两个独立系统在无共享定义下的一致程度"。
   要得到真正的一致率，**唯一路径是人工盲判**（同 `L5_VALIDITY_PLAN.md` §3 的 L3 层）。
2. **它与 L5 共享分数，不独立**（§3.2）。不能写成"两个指标互相印证"。
3. **`RELATED_TO` 的定义性劣势。** 兜底关系在竞争判别里必然吃亏（召回 14%），
   这既反映真实的语义困难，也反映"兜底类本来就不该参与竞争"这一设计问题。
   若要更公平，应把 `RELATED_TO` 视为**弃权类**：另报"排除 `RELATED_TO` 后 14 类的
   条件一致率"（脚本的 `--reuse-l5` 与混淆矩阵可直接算出，本次未单列）。
4. **模板的选词是一个自由度。** 本报告的主口径刻意采用**与 L5 现行模板逐字一致**的那一套，
   目的是"可比"，代价是"未择优"。`--form phrase` 与 `l5_validity_probe.py` 的 P1
   已证明措辞对判别器影响很小（极差 4.4pp），但这不等于"所有措辞都不影响 argmax"。
5. **近乎平局不是主因（描述性结论，非排除）。** 实测近乎平局仅约 0.2%
   （`margin` p50 = 0.083），说明 argmax 的低一致率**不是**数值并列造成的任意性
   （初版脚本曾把"pred==label"误算成平局，得 26.2% 的错误结论，已修）。

---

## 8. 与既有六层的关系（论文里怎么放）

`L5_SIX_LAYER_REPORT.md` §6.5 已声明"本项目 L4 比报告版窄"。本层补上后建议这样改写：

> 报告版的第 4 层是「谓词**类型是否匹配**」，本项目的 L4 原先只判「谓词是否在 15 类
> 本体**内**」（本体依从率 99.5%），选错但落在本体内会被判通过。
> 现补上谓词选择评测（`tools/l4_predicate_report.py`）：用同一条证据句与 15 类模板
> 分别计算 NLI 蕴含概率、取最大者为预测谓词，在 43630 条边上得到
> **一致率 25.73%**（全量 43630 条；分层抽样独立复核 26.0%，95%CI 23.6–28.8），
> 与"全预测 `RELATED_TO`"的平凡基线 22.00% 仅差 3.73pp。
> 该数字的参照系是**抽取器标签**而非人工金标，且抽取时未提供谓词定义，
> 故它度量的是**两个模型的一致率**，不是谓词准确率。
> 与之对照，同一批边的 L5 通过率为 63.66%——被判错的边里 54.23% 是
> "标注谓词**被证据支持**（分数 ≥0.67）但输给了另一个谓词"，
> 说明该图上的主要问题是**多个谓词的假设同时被证据支持**（本体边界不可复现），
> 而不是"证据不支持关系"。

**登记进版本绑定**：`tools/metric_provenance.py` 的 `METRICS` 已加入
`L4_predicate_agreement` / `L4_predicate_agreement_excl_ties` / `L4_predicate_macro_f1` /
`L4_predicate_baseline_related_to`，指向 `data/quality/l4-predicate.json`
（即 `--full` 的产物）。

---

## 9. 工具函数索引

| 文件 | 关键函数 / 入口 | 作用 |
| --- | --- | --- |
| `tools/l4_predicate_report.py` | `load_edges` `pick_sample` `verify_hypothesis_field` `score_grid` `build_rows` `confusion` `macro_f1` `bootstrap_acc` `main` | 本层全部逻辑 |
| `src/ontology.py` | `REL_HYPOTHESIS_TEMPLATE` `REL_PHRASE_TEMPLATE` `validate_constraints()` | 模板唯一来源 + 自检（含 `CREATED_BY` 方向逐字断言） |
| `tools/check_ontology_consistency.py` | `parse_str_dict()` | 把 `relation_verifier.py` / `verify_direction.py` 的 `REL_TEMPLATE` 与 ontology **逐字相减** |
| `tests/test_l4_templates.py` | 11 个用例 | 回归：模板覆盖性、占位符合法性、`CREATED_BY` 方向逐字锁定、三处副本一致（`python -m pytest tests` 现为 **85 项全通过**） |
| `tools/metric_provenance.py` | `METRICS` | 新指标的版本绑定 |

## 10. 尚未做（如实列出）

| 项 | 说明 |
| --- | --- |
| 人工盲判锚定 | 与 `L5_VALIDITY_PLAN.md` §2 的 200 条盲判**共用一份抽样**即可同时锚定 L5 与 L4（给同一条边的 `type` 加一列"谓词对不对"）。这是唯一能给出真一致率的路径 |
| **独立第二判定者**（§5.3） | `qwen3.5:9b` 在盘上、`ollama serve` 可离线跑。prompt 与做法已在 §5.3 给出，**脚本未落地、未执行**（`--full` 占用 GPU；且这是可选实验，不影响本报告结论）。它是检验"25.73% 是判别器弱还是本体边界不可复现"的决定性实验 |
| `RELATED_TO` 弃权口径 | 把兜底类单列为弃权后，14 类上的条件一致率（脚本数据已够，尚未单列成表） |
| 模板择优 | 本报告用"与 L5 一致"的模板求可比性，未做"哪套模板 argmax 更准"的搜索 |
| 端点类型约束辅助 | `src/ontology.py` 的 `RELATION_ENDPOINT_CONSTRAINTS`（L7 那层）可作为 argmax 的**先验**（角色不允许的组合降权）。这是把 L4 与 L7 耦合的可行方向，但会引入新的调参自由度，未擅自实施 |
| 三处 `REL_TEMPLATE` 彻底收敛为单一 import | 目前靠 `check_ontology_consistency.py` **逐字相减** + `tests/test_l4_templates.py` 守住。彻底改为 `from ontology import REL_HYPOTHESIS_TEMPLATE as REL_TEMPLATE` 需要动主线脚本 `relation_verifier.py`（会触发 `ontology → kg_extractor` 的模块级导入），零风险优先，留给后续 |
| 逐类结论用全量而非抽样 | 抽样中小类被过度代表（`CONTRADICTS` 抽 62%、`RELATED_TO` 抽 1.6%），按类的 P/R/F1 精度不均；`--full` 的产物无此问题 |
