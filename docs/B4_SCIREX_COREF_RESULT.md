# B4 · SciREX 人工共指 vs 本项目消歧判据（2026-09-16）

> **配套**：`tools/_probe/_scirex_coref_eval.py`（可复跑）·
> `data/external/scirex-coref-eval.json`（产物）·
> `data/external/scirex_samples.jsonl`（6 篇人工标注，ACL 2020）
>
> **一句话**：**召回率 100%，但精确率只有 64.6%（假阳性 27.4%）——
> 而 FP 的模式与 A5 完全一致：「长名包含短名」不足以判定同指。**
> **同一个根因出现在两个不同模块里**（抽取期消歧 + 后处理吸收）。

---

## 一、结果

```
正例（同物异写）1200 对 · 负例（人工认为不同）2400 对

混淆矩阵： TP 1200 · FP 658 · TN 1742 · FN 0
  精确率 P = 0.6459
  召回率 R = 1.0000
  F1      = 0.7848

★ 假阳性率（把不同实体合成一个）= 27.42%
```

**召回率 100% 是好的**（**没放过任何一个同物异写**）；
**但合并不可逆，27.4% 的假阳性率是这份判据最要紧的问题。**

---

## 二、正负例是怎么造的（这决定了结果可不可信）

### 先看清 SciREX 标注长什么样

```
gold_ner:  316 条 {name, span:[起,止], type}
           人工把**同一个实体的每次提及都写成同一个 name**
           （'denoising' 25 次 · 'super - resolution' 19 次）
gold_coref: {'super - resolution': [[38,41],[301,304],…19 个位置], …}
```

**⇒ "同名即同实体"是人工给定的** ⇒ **同名配对是平凡正例，测不出判据好坏**。
⇒ 所以两边的取法都要绕开这一点：

| | 怎么造 | 为什么这样造 |
|---|---|---|
| **负例** | **人工认为不同**的实体两两配对，**优先取互为子串 / 词重叠高的** | 随机配对大多平凡（`'PSNR'` vs `'denoising'` 谁都不会合）；**子串对才是判据的边界** |
| **正例** | 把人工名做**三类排版扰动**：连字符去空格 / 加空格 / 大小写 | **这三类在 SciREX 自己的 `name` 里都真实出现过**（`'super - resolution'`、`'De - convolutional layers'`）⇒ 是**真实存在的同物异写**，不是我编的 |

### ⚠️ 一个必须先修掉的坑：**人工标注自己的大小写不统一**

第一版**没有排除"只差大小写"的负例**，结果 FP 里塞满了：

```
'Convolution' ≡ 'convolution'          ← 判 True 是**完全正确的**
'Denoising'   ≡ 'denoising'
'Image Restoration' ≡ 'image restoration'
```

**根因**：同一篇里 `gold_ner` **同时有** `Convolution` 和 `convolution` ——
**那是标注噪声，不是"人工认为这是两个实体"**。
把它们当负例 = **拿标注噪声去罚判据，测出来的 FP 率是假的**。

⇒ 修法：负例里 `if la == lb: continue`。
**修完 FP 从 687 降到 658（只少 29，占 4%）** ——
**说明大小写不是主因**（判据本来就有大小写归一），**剩下的才是真问题**。

---

## 三、★ 核心发现：FP 的模式与 A5 是**同一个根因**

修完之后，FP 样本长这样：

```
'Image super - resolution'  ≡  'super - resolution'
        ← **人工明确认为这是两个不同实体**（一个是任务、一个是更泛的说法）

'Skip connections'          ≡  'Symmetric Skip Connections'      （'Symmetric ' 是限定词）
'30 layer networks'         ≡  'layer networks'                  （'30 ' 是数量）
'Image Restoration'         ≡  'image restoration topics'        （' topics' 是载体）
'convolutional and deconvolutional' ≡ '… kernels' / '… layers'   （加名词）
'De - convolutional layers' ≡  'convolutional and de - convolutional layers'（加限定）
'pixel - wise prediction'   ≡  'pixel - wise prediction problems'
```

**⇒ 全都是"长名 = 短名 + 若干词"，而多出来的词改变了所指。**

这与 **A5（1C 吸收判据）** 的结论**逐字相同**：

> A5 的实测：`Structured Data` → `Semi-Structured Data`（**`Semi-` 改了含义**）、
> `Question Answering` → `Knowledge Graph Question Answering`（**上义词**）
> ⇒ 根因：**「长名包含短名」在有名词短语上不足以判定同指**。

**⇒ 两个模块犯了同一条**：

| 模块 | 阶段 | 判据 | 本次实测 |
|---|---|---|---|
| **`_names_are_duplicates`** | **抽取期消歧** | 长名包含短名 ⇒ 同指 | **假阳性 27.4%**（B4 本次） |
| **`entity_absorb.find_absorptions`** | **后处理吸收** | 长名包含短名 ⇒ 同指 | 全库 1404 对里抽样 **0 对该合**（A5） |

**⇒ 这是"根因诊断"，不是"两个孤立的 bug"。**
A5 当时只修了后处理那一侧（`extra_is_person_form` 收紧成人名），
**B4 说明同一个根因在抽取期那一侧**还**原样存在**。

---

## 四、为什么这次"只报不改"

`_names_are_duplicates` 在 `deduplicate_entities` 里**直接决定节点合不合并** ——
改它等于改**抽取阶段的图规模**，要重跑全部 17 篇、重算六层指标、
连同 H6 的 KGE、H4 的嵌入、B6 的社区摘要全部重做。
**那是数小时的机器时间，而且要按 ARCHIVE_POLICY 留 before/after 双口径。**

⇒ **本轮把它定位清楚并量化，改法留到有整块机器时间时做。**
**这份评测就是那个 before。**

**建议的改法（与 A5 一致）**：把「长名包含短名」从**判据**降级为**候选生成**，
再对候选加一道"多出来的词是否改变所指"的判据 ——
A5 在**人名**上已经做成了（`extra_is_person_form`），
**同样的思路可以搬到抽取期，只是候选的类型分布更杂。**

---

## 五、诚实的边界

| # | 边界 | 影响 |
|---|---|---|
| **1** | **只有 6 篇** | 1200 正 / 2400 负，但都来自 6 篇同一领域（CV+NLP 混合）；**换领域比例会变** |
| **2** | **正例是"扰动造出来的"，不是人工标的** | 扰动类型**在数据里真实出现过**（已核），但**它们的比例是我定的**（三类各占 1/3）⇒ **P 的绝对值受此影响，R 不受** |
| **3** | **负例偏难**（优先取子串对） | ⇒ **27.4% 是"边界情形上的 FP 率"，不是随机配对的 FP 率**；随机抽样会低得多 |
| **4** | **只测了 `_names_are_duplicates` 一个函数** | `deduplicate_entities` 里还有别的规则（类型一致性等）**未纳入** |
| **5** | **改法未实施** | 见 §四 —— 需要整块机器时间与 before/after 双口径 |

**第 3 条要特别说清**：**这个 27.4% 不能读成"判据有 27% 的时候是错的"**，
它是**"在最容易错的那批配对上的错误率"**。
**但恰恰是这批配对决定了图会不会被错误合并** —— 所以它是要紧的那个数。

---

## 六、复现

```powershell
python tools/_probe/_scirex_coref_eval.py
python tools/_probe/_scirex_coref_eval.py --seed 1     # 换采样种子
```

产物：`data/external/scirex-coref-eval.json`
（含混淆矩阵、假阳性率、以及**逐条 FP/FN 样本** —— 没有样本就没法改）
