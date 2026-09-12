# L7 端点类型约束表（本体 15 类关系）

> 唯一来源：`src/ontology.py::RELATION_ENDPOINT_CONSTRAINTS`
> 审计脚本：`tools/l7_relation_constraints_audit.py`
> 精度抽查：`tools/l7_constraint_precision_sample.py`
> 实测数据：`data/quality/relation-verify-all.json`（43867 条边，2026-09-12）
> 结果文件：`data/quality/l7-relation-constraints.json`

---

## 0. 结论摘要

> ### ⚠️ 必读：本层在 `CREATED_BY` 上**不是独立验证**
>
> `CREATED_BY` 报出的 98.0% 高合规率里，有**很大一部分是循环论证的必然结果**，
> 不能当作 L7 的独立成果来陈述：
>
> · T3.1 的 `tools/apply_created_by_fix.py` 已经用**与 L7 完全同一套**
>   `AUTHOR`/`CREATABLE` 规则，反转了 **2348 条** `CREATED_BY` 边、并把 165 条
>   重标为 `CONTRIBUTED_TO`（后者已从图中删除）；
> · 这 2348 条在本层必然判 `compliant_directed`，占 5819 的 **40.3%**；
> · 换成任何一套自洽的规则，只要"改动层"和"校验层"共用同一份定义，
>   都会得到同样的高合规率——**它检验的是两边有没有漂移，不是方向对不对**。
>
> **本层对 `CREATED_BY` 唯一的独立产出，是那 120 条反转与 302 条角色违规**
> ——它们要么不在 T3.1 的改动范围内，要么因 `CITATIONISH` 被跳过。
> 论文里引用 98.0% 时必须同时说明这一点，否则就是"用补丁的假设验证补丁"。
>
> 至于 `PRODUCES` 与 `LOCATED_AT`，改动层从未触碰过，**它们的数字是独立的**——
> 但代价是覆盖率很低（47.4% / 48.0%）。

15 类关系已各有一条端点类型约束，写进 `src/ontology.py`，可直接被校验层与改动层 import。

**但其中只有 3 类真能由端点类型判方向**：

| 可判性 | 关系类型 | 条数 |
| --- | --- | --- |
| 方向可判（有方向指纹） | `CREATED_BY`、`PRODUCES`、`LOCATED_AT` | 3 |
| 语义对称，按定义不判方向 | `COMPARES_WITH`、`CONTRADICTS`、`RELATED_TO` | 3 |
| 明确不可判定（有方向但类型判不了） | `MEASURES` | 1 |
| 方向敏感但类型判不了向（只做角色检查） | 其余 8 类 | 8 |

这不是没做完，而是这套图的真实性质：要让类型决定方向，两侧的允许角色必须**几乎不相交**，
而 15 类里只有「创作者 vs 被创作物」「场所 vs 非场所」这一组半能做到。论证见 §4。

三条硬结论：

1. **`CREATED_BY` 是唯一可以直接用的方向判据**（但见上方循环论证声明）。
   可判定 6241 条里方向合规 **98.0%**，检出反转 120 条。
   人工抽查 20 条中 **17 条**描述明确是「被创作物 → 创作者」
   （`Word2Vec was created by Mikolov et al.` 一类）、**2 条**描述字段只有 `CREATED_BY`
   无任何信息、**1 条是谓词误用而非方向问题**
   （`Cluster Modification Algorithm → Gu et al.`，描述实为"Gu et al. 使用了该算法"，
   本该是 `USES`）。**真阳性率 ≈ 85%**，不是 100%。
   另需注意：这 120 条里约 117 条会因命中 `apply_created_by_fix.py` 的
   `CITATIONISH` 规则（`et al` / `(2019)` / `, 2019`）被跳过，
   **实际能自动反转的只有约 3 条**。所以 L7 对 `CREATED_BY` 的产出定位是
   **报告，而不是修复**。
2. **`EVALUATES` 与 `APPLIES_TO` 的方向指纹被主动撤销**。前者按 `{Method,Document,Person}
   → {Dataset,Concept}` 判出的 8 条「反转」经逐条核对**全部是假阳性**；后者判出的 248 条里
   `Concept→Process` 98 条是「知识图谱用于问答」这类完全合法的边。撤销比留着好看得多。
3. **`tools/l7_direction_verify.py` 的违规分类此前被 `Unknown` 端点污染**，已修：
   120（方向颠倒）+ 92（引文式端点）+ 210（其他）= 422，正好等于实际违规数；
   修正前三者之和是 2847，多出来的 2425 条绝大多数是类型无从得知的反建节点。

---

## 1. 判据：四类裁决，两个指标

L7 要回答的是「方向对不对」。但现有原型把三种完全不同的东西混进了同一个数字：

1. 方向真的反了；
2. 端点类型扮演了不可能的角色（`Person` 被当成「被创作物」）；
3. 端点类型是 `Unknown`（`tools/backfill_missing_endpoints.py` 反建出来的节点，类型本就无从得知）。

实测把 ③ 当成违规，会让 `CREATED_BY` 的合规率从 93.2% 假摔到 67.1%。所以本层拆成四类裁决：

| 裁决 | 含义 | 算不算方向错 |
| --- | --- | --- |
| `unjudgeable` | 端点不在节点表内，或类型为 `Unknown` | 不判 |
| `compliant_directed` | 正向命中方向指纹 | 否 |
| `reversed` | **反向**命中方向指纹 | **是** |
| `compliant_role` | 未命中指纹，但两端类型都在允许集内 | 否 |
| `role_violation` | 端点类型不在允许集内 | 否（是端点角色问题，交 L1/L3） |

判定顺序（`tools/l7_relation_constraints_audit.py::verdict`）：

```
端点缺失或类型 Unknown        → unjudgeable
kind == unconstrained        → unconstrained（不判）
kind == symmetric            → 只看允许集，不看方向
kind == directed 且有方向指纹 → 正向命中 / 反向命中 / 否则看允许集
kind == directed 无方向指纹   → 只看允许集
```

两个必须分开读的指标：

| 指标 | 定义 | 怎么读 |
| --- | --- | --- |
| `direction_compliance` | `compliant_directed / (compliant_directed + reversed)` | L7 真正要报的数，**只在方向指纹覆盖到的边上有意义** |
| `direction_coverage` | `(compliant_directed + reversed) / 可判定边` | 低于 30% 就别拿上面那个比例下结论 |
| `role_compliance` | `(compliant_directed + compliant_role) / 可判定边` | 端点角色合理率。**接近 100% 说明该约束没有判别力，不是质量好** |

字段含义（表内每一类都有）：

| 字段 | 含义 |
| --- | --- |
| `semantics` | 一句话语义。`source` 是 `verify_direction.py::REL_TEMPLATE` 里的 `{s}`，`target` 是 `{t}` |
| `kind` | `directed` / `symmetric` / `unconstrained` |
| `source_core`、`target_core` | 方向指纹（窄）。两者不相交时方向由类型唯一决定；为空集 = 判不了方向 |
| `source_allowed`、`target_allowed` | 角色允许集（宽）。只判角色是否合理，**不判方向** |
| `basis` | `semantics`（语义常识）/ `convention`（既有约定）/ `data`（数据佐证） |
| `evidence` | 定这条约束时的数据依据 |
| `reversal` | `judgeable` / `unjudgeable` |
| `template_symmetric` | L5 假设句模板反转后语义是否不变（口径与 `verify_direction.py` 的 `SYMMETRIC` 一致） |

---

## 2. 15 类约束表（主交付）

### 2.1 方向指纹（只有这 3 类有）

| 关系 | `semantics`（source → target 角色） | `source_core` | `target_core` | `source_allowed` | `target_allowed` | 依据 |
| --- | --- | --- | --- | --- | --- | --- |
| `CREATED_BY` | 创作者 → 被创作物（沿用 T3.1 的 Author→Work） | `Person, Organization` | `Document, Method, System, Tool, Artifact, Dataset, Component, Concept, Process, Metric, Material` | 同 core | 同 core | 既有约定 |
| `PRODUCES` | 生产者 → 产物 | `Method, System, Process, Person` | `Concept, Artifact` | `ALL − {Location, Event, Metric, Material}` | `ALL − {Person, Organization, Location, Event}` | 语义 + 数据 |
| `LOCATED_AT` | 有位置的实体 → 场所 | `ALL − {Location, Organization, Event}` | `Location, Organization, Event` | `ALL` | `ALL − {Person, Method, Metric, Material}` | 语义 |

`ALL` = 本体 15 类实体。注意 `CREATED_BY` 的 `source_allowed` / `target_allowed` **与 T3.1 的
`AUTHOR` / `CREATABLE` 逐字相同**（现由表派生，不再是两份字面量），改动它会同时改变
`tools/apply_created_by_fix.py` 的行为。

### 2.2 其余 12 类

| 关系 | `kind` | `semantics`（source → target 角色） | `source_allowed` | `target_allowed` | 方向 |
| --- | --- | --- | --- | --- | --- |
| `PART_OF` | directed | 部分 → 整体 | 全部 15 类 | `ALL − {Person}` | 判不了 |
| `USES` | directed | 使用者 → 被使用的手段/数据/组件 | `ALL − {Location, Event, Material}` | `ALL − {Person}` | 判不了 |
| `APPLIES_TO` | directed | 被应用的方法/技术 → 应用对象或领域 | `ALL − {Location, Event, Material}` | `ALL` | 判不了 |
| `SUPPORTS` | directed | 支持者（证据/方法）→ 被支持的论断或流程 | `ALL − {Location, Material}` | `ALL − {Location, Material}` | 判不了 |
| `CAUSES` | directed | 原因 → 结果 | `ALL − {Location, Material}` | `ALL − {Location, Person}` | 判不了 |
| `ENABLES` | directed | 使能者（条件/能力）→ 被使能者 | `ALL − {Person, Organization, Location, Event, Material}` | `ALL − {Person, Organization, Location, Event, Material, Document}` | 判不了 |
| `CITES` | directed | 引用者 → 被引用的文献/方法/作者 | `ALL − {Location, Material}` | `ALL − {Location, Material}` | 判不了 |
| `EVALUATES` | directed | 评测者/评测手段 → 被评测对象 | `ALL − {Location, Event, Material}` | `ALL − {Person, Location, Event, Material, Organization}` | 判不了（指纹已撤销） |
| `COMPARES_WITH` | symmetric | 互相比对，无左右之分 | `ALL − {Location, Event, Material}` | 同左 | 对称 |
| `CONTRADICTS` | symmetric | 互相矛盾，矛盾无方向 | `ALL − {Location, Material, Person, Organization}` | 同左 | 对称 |
| `MEASURES` | unconstrained | 度量/刻画，本图四种读法混用 | `ALL − {Person, Organization, Location, Event, Material}` | 同左 | 不可判定 |
| `RELATED_TO` | unconstrained | 兜底关系，无方向语义 | 全部 15 类 | 全部 15 类 | 不可判定 |

---

## 3. 实测结果（2026-09-12，43867 条边）

| 关系 | 可判定 | 合规(向) | 合规(角色) | 反转 | 角色违规 | 不可判 | 方向合规 | 方向覆盖 | 角色合规 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `RELATED_TO` | 6625 | – | – | – | – | 2973 | n/a | – | n/a |
| `CREATED_BY` | 6241 | 5819 | 0 | **120** | 302 | 2514 | **98.0%** | 95.2% | 95.1% |
| `USES` | 4857 | – | 4815 | – | 42 | 2482 | n/a | – | 99.1% |
| `PART_OF` | 4001 | – | 3983 | – | 18 | 1815 | n/a | – | 99.6% |
| `APPLIES_TO` | 2521 | – | 2520 | – | 1 | 1329 | n/a | – | 100.0% |
| `PRODUCES` | 1519 | 684 | 776 | **36** | 23 | 694 | **95.0%** | 47.4% | 98.4% |
| `ENABLES` | 739 | – | 734 | – | 5 | 260 | n/a | – | 99.3% |
| `CITES` | 639 | – | 638 | – | 1 | 328 | n/a | – | 99.8% |
| `MEASURES` | 537 | – | – | – | – | 335 | n/a | – | n/a |
| `LOCATED_AT` | 590 | 283 | 306 | **0** ※ | 1 | 263 | **100.0%** ※ | 48.0% | 99.8% |
| `SUPPORTS` | 560 | – | 560 | – | 0 | 233 | n/a | – | 100.0% |
| `COMPARES_WITH` | 374 | – | 374 | – | 0 | 224 | n/a | – | 100.0% |
| `EVALUATES` | 302 | – | 296 | – | 6 | 152 | n/a | – | 98.0% |
| `CAUSES` | 196 | – | 193 | – | 3 | 84 | n/a | – | 98.5% |
| `CONTRADICTS` | 176 | – | 176 | – | 0 | 67 | n/a | – | 100.0% |
| **合计** | **29877** | 6786 | 15371 | **156** | 402 | 13753 | | | |

对账：29877 + 13753（不可判）+ 237（本体外关系类型）= 43867 ✅

> **※ `LOCATED_AT` 的 100% 是构造性的，不能当作"方向全对"汇报。**
> 它的反向触发条件是「source ∈ {Location, Organization, Event} 且 target 非此类」，
> 而这类边在这 43867 条里**一次都没有出现**——判据的触发条件与实际抽取模式不相交，
> 于是 `reversed` 恒为 0、"方向合规"恒为 100%。
> 换句话说：这个 100% 说明的是**没有可判的反例**，不是**方向都对**。
>
> 另有两类同源现象：
> · 3 类对称关系（`COMPARES_WITH`、`CONTRADICTS`、`RELATED_TO`）的
>   `reversed = 0` 是**设计行为**（按定义不判方向），不是检出结果；
> · 其余 8 类因 kind 不是 directed，同样不产出 `reversed`。
>
> 因此下表里 `reversed` 真正非零的**只有 `CREATED_BY` 与 `PRODUCES` 两类**
> ——这也正是 156 条反转的全部来源。

**与 `tools/l7_direction_verify.py` 原型逐项对账**（同一批边、同一张表）：

| 项 | 本表 | 原型 | |
| --- | ---: | ---: | --- |
| 可判定 | 6241 | 6241 | ✅ |
| 合规 | 5819 | 5819 | ✅ |
| 反转 | 120 | 120 | ✅ |
| 报告口径 | 方向合规 **98.0%** | 合规率 93.2% | 口径不同 |

93.2% 与 98.0% 的差不是谁算错了：93.2% 的分母是全部可判定边，把 302 条
**端点角色问题**（如 `C. Zhang CREATED_BY SIGKDD` —— 谓词该是 `CONTRIBUTED_TO`，
方向并没有反）也算进了「不合规」。L7 的判据是方向，应报 98.0%；
302 条角色问题另报 `role_compliance = 95.1%`。

`LOCATED_AT` 检出 0 条反转。它的指纹覆盖 283 条（48.0%）且合规 100% ——
这是「在覆盖到的范围内没发现问题」，不等于「这一类方向都对」。

---

## 4. 为什么 15 类里只有 3 类能判方向

要让端点类型决定方向，需要 `source_core` 与 `target_core` **不相交**，否则
「反向命中」会被「正向命中」先吃掉，`reversed` 恒为 0，看起来一切正常、实际什么都没查
（`validate_constraints()` 专门守这条不变量）。

逐类看数据，能形成不相交指纹的只有：

- **`CREATED_BY`**：`Person/Organization` 对 11 类产物。数据支持极强——可判定边里
  source 侧 `Person` 占 93.6%，target 侧落 11 类产物占 92.0%。
- **`LOCATED_AT`**：场所（`Location/Organization/Event`）对非场所。语义上唯一一套
  天然的「容器 vs 内容」二分。
- **`PRODUCES`**：`Method/System/Process/Person` 对 `Concept/Artifact`。取法是量化的：
  某类型进指纹要求**本侧占比 ≥ 15%，或达到对侧占比的 2 倍以上**。据此 `Component`
  （6.8% vs 4.5%）、`Document`、`Tool`、`Dataset` 两侧都不进；`Concept` 在 target 侧
  是 source 侧的 3 倍，只进 target。

其余类的两端类型在数据里双向都显著，典型如：

- `APPLIES_TO`：`Concept` 占 source 侧 25.2%、target 侧 56.4%；`Method` 41.2% / 10.2%；
  `Process` 9.0% / 16.8%。任何切法都会误杀另一半。
- `USES`：`Method→Concept` 821 条与 `Concept→Method` 168 条并存。
- `PART_OF`：最高格 `Concept→Concept` 仅 17.3%；`Document→Event` 305 条实为「发表于某会议」，
  `Person→Organization` 99 条实为 affiliation，而「成员是机构的一部分」在语义上也成立。
- `MEASURES`：`Metric→Concept` 55 与 `Concept→Metric` 43 几乎相等，
  `Method→Metric` 54 与 `Method→Concept` 54 并列，且至少混了「指标度量概念」
  「方法取得指标值（has-value）」「指标的定义式」三种语义。

**为什么不用统计法自动推导**：`tools/l7_direction_audit.py` 已证伪——
16 类的「主导端点类型对」占比全部 < 60%（最高 `CREATED_BY` 40.2%）。
本表的指纹是**按语义手工定的**，数据只用来事后核对覆盖率与误杀率。

---

## 5. 精度抽查：反转判定准不准

`direction_compliance` 只说明「检出多少」，不说明「检出得准不准」。
端点类型是抽取出来的标签，本身有噪声，所以精度只能人工读 `description` 判断。
`tools/l7_constraint_precision_sample.py` 用固定 seed 把样例固定下来，使这个判断可复核。

| 关系 | 判为反转 | 抽 20 条人工判读 | 真阳性率 | 处置 |
| --- | ---: | --- | ---: | --- |
| `CREATED_BY` | 120 | **17 条**描述明确是「被创作物 → 创作者」（`Word2Vec was created by Mikolov et al.`）；**2 条**描述字段只有 `CREATED_BY`（无信息）；**1 条是谓词误用**（`Cluster Modification Algorithm → Gu et al.`，描述为 "Gu et al. apply a cluster modification algorithm"，本该是 `USES`） | **≈ 85%** | **只出报告，不自动改**：这 120 条里约 117 条会命中 `apply_created_by_fix.py` 的 `CITATIONISH` 规则被跳过，实际可自动反转的约 3 条 |
| `PRODUCES` | 36 | 12 条真反转（`CNN produces a score` 被写成 `Score → CNN`），8 条是谓词误用或类型噪声，集中在 `Concept→Process` | ≈ 60% | **只进人工队列，不自动改** |
| `LOCATED_AT` | 0 | 无可抽 | 无法估计 | 保持观察 |
| `EVALUATES`（已撤销） | 8 | **0 条真反转**：`FewRel dataset was built to evaluate …`、`Evaluation assesses the edge prior method` 方向本身没错 | 0% | 指纹已撤销 |

判读口径：读 `description` 里谁是主动方，与表的 `semantics` 比对；描述为空或只有关系名的记「无信息」。

**已做人工/独立判读的类（共 8 类，两组样本合计 69 条）**：

| 组 | 类 | 判什么 | 条数 |
|---|---|---|---|
| 精度抽查 | `CREATED_BY`、`PRODUCES`、`EVALUATES` | 反转判定准不准 | 48 |
| role_violation 第一批 | `USES`、`PRODUCES`、`PART_OF` | 角色违规是约束太严还是真错 | 58 |
| role_violation 第二批 | `APPLIES_TO`、`CAUSES`、`CITES`、`ENABLES`、`LOCATED_AT` | 同上 | 11 |

**仍未判读的类（7 类）**：`SUPPORTS`、`MEASURES`、`COMPARES_WITH`、`CONTRADICTS`、
`RELATED_TO`、`CAUSES` 的部分样本未覆盖、`ENABLES` 的其余样本。
**但要注意**：这 7 类里有 5 类的 `role_violation` **本来就是 0**
（`SUPPORTS` / `MEASURES` / `COMPARES_WITH` / `CONTRADICTS` / `RELATED_TO`），
**没有样本可判**。而"零违规"要分两种含义，见 §5.2——不能直接读成"约束正确"。

### 5.1 补充判读：`role_violation` 到底在说什么

抽取 `role_violation` 最多的三类（`USES` 42 / `PRODUCES` 23 / `PART_OF` 18）各抽 20/18 条，
交给**独立于约束设计者**的判定者判读（固定 seed=7，可复现）。
判读口径事先写好：

| 裁决 | 含义 |
|---|---|
| `true_violation` | 组合确实说不通，约束判得对 |
| `false_positive` | 组合其实说得通 ⇒ **约束定得太严** |
| `predicate_wrong` | 组合说不通，但根因是**谓词选错**（不是端点类型的错） |

实测（58 条）：

| 关系 | true_violation | predicate_wrong | false_positive | 合计 |
|---|---:|---:|---:|---:|
| `USES` | 5 | 15 | 0 | 20 |
| `PRODUCES` | 3 | 12 | 5 | 20 |
| `PART_OF` | 11 | 7 | 0 | 18 |
| **合计** | **19（32.8%）** | **34（58.6%）** | **5（8.6%）** | 58 |

**两条结论（都会影响论文口径）**：

1. **约束的假阳性率只有 8.6%** —— 允许集定得**不算太严**，这张表基本可用。
   这比"未验证"要好，也比"约束很严会误伤"的担心要轻。

2. **但 `role_violation` 里 58.6% 其实是谓词选错**，不是端点类型不对。
   **这个指标名有误导性**：它捕捉的主要是"关系类型选错了"，
   而不是"端点类型违规"。报告与论文里应把它读作
   **"端点类型或谓词问题"**，不能写成"端点类型违规率"。

**`PRODUCES` 那 5 条假阳性的成因很集中**，提示允许集可以这样放宽：
  · **NER → 人名/地名**：命名实体识别的产出就是被识别出的实体，
    `target` 允许集应纳入 `Person` / `Location`；
  · **会议 → 论文**：会议发表论文，`source` 允许集应纳入 `Event`；
  · **Loss → Gradient**：损失函数产生梯度，`source` 允许集应纳入 `Metric`。

（这些是**建议**而非已改动：改允许集会改变 `role_compliance` 的数字，
需要连同 §3 表格一起重算。本轮只给出证据。）

### 5.2 第二批：另外 5 类（n=11）——结论与第一批**差一个数量级**

第一批的三类（`USES` / `PRODUCES` / `PART_OF`）假阳性只有 8.6%，
看起来约束表整体是可靠的。补判另外 5 类后，这个印象被推翻：

| 关系 | 真违规 | 谓词错 | **假阳性** |
|---|---:|---:|---:|
| `APPLIES_TO` | 0 | 0 | **1** |
| `CAUSES` | 0 | 1 | **2** |
| `CITES` | 1 | 0 | 0 |
| `ENABLES` | 0 | 0 | **5** |
| `LOCATED_AT` | 1 | 0 | 0 |
| **合计** | 2 | 1 | **8 / 11 = 72.7%** |

**两批的假阳性率相差一个数量级（8.6% vs 72.7%）**，
说明 15 类约束表的**内部质量不均**：
`USES` / `PRODUCES` / `PART_OF` 基本可用，而 **`ENABLES` / `CAUSES` / `APPLIES_TO` 太严**。

**`ENABLES` 尤其明确**：它的 5 条样本**全部**是假阳性，且成因完全一致——
允许集把 `Person` / `Document` / `Material` 从两端都排掉了，而这三类恰好是
「人被使能（`enabled researchers to…`）」「文献被使能」「物质被使能」的正常用法。
`CAUSES` 的 `target` 排除 `Person` 也误伤了「某事对人是挑战（`poses a challenge for humans`）」
这类边；`APPLIES_TO` 把 `Material` 排除出 `source`，挡住了「数据/物料作为被处理对象」。

**建议放宽**（同样本轮只给证据、未改动）：
  · `ENABLES`：两端纳入 `Person` / `Document` / `Material`
  · `CAUSES`：`target` 纳入 `Person`
  · `APPLIES_TO`：`source` 纳入 `Material`

**限度**：第二批只有 11 条（因为这 5 类各自的 `role_violation` 本来就很少：
1 / 3 / 1 / 5 / 1），比例估计的置信区间很宽。
但「`ENABLES` 五条全是假阳性且成因一致」这一点是明确的——
它不是抽样噪声，而是允许集设计的问题。

### 5.3 "零 `role_violation`" 的两种含义（必须区分）

有 5 类关系的 `role_violation` 是 **0**，看起来"类型用得很规范"。但要分清：

| 含义 | 类 | 说明 |
|---|---|---|
| **恒等式**（约束永不触发） | `MEASURES`、`RELATED_TO` | 两侧允许集都是**全集**，角色检查根本不触发 ⇒ 100% 是构造性的，**不提供质量信息** |
| **确实没违规** | `SUPPORTS`（13 类允许）、`COMPARES_WITH`（12）、`CONTRADICTS`（11） | 有具体限制，100% 是真的判出来的 |

这一检查已做成常驻（`check_l7_role_violation.py` 的 `tautological_constraints` 段）。
**审计表里 `MEASURES` / `RELATED_TO` 显示的是 `n/a` 而非 `100%`，这一点报告处理正确。**

同类陷阱本项目已栽过一次：`LOCATED_AT` 的 `direction_compliance = 100%` 是构造性的
（反向触发条件在 43867 条边里命中 0 次），见 §3 的 ※ 脚注。

---

## 6. 已知失效模式与缓解

### 6.1 `Event` 在这套图里装的是发表 venue，不是事件

实测：`PART_OF` 的 `target=Event` 588 条、`LOCATED_AT` 241 条、`CITES` 104 条，
描述几乎全是 `The paper was published in ICANN / AAAI / NIPS / ICLR / ACL`。

`target` 类型为 `Event` 的边共 **1910 条**，端点实体名 Top6：
`AAAI` **169**、`EMNLP` **165**、`ACL` **150**、`ICLR` **79**、`COLING` **63**、`IJCAI` **62**
（口径：`relation-verify-all.json` 全部 43867 条边，按边计次；
节点表 19427 个名字中 `Event` 类型共 **355** 个）。

> 修正记录：本文档旧版这里写的是 `AAAI(9)`、`International Conference on
> Machine Learning(9)`、`WWW(8)`、`ICLR(8)`，**与主数据差一个数量级、无法复现**，
> 已按上述口径更正。

这不是方向问题，是**本体缺一个 `Venue` 类**（15 类里没有）。

缓解（**更正**）：`Event` **并非**在所有关系的允许集里一律保留——实测它被排除在
**7 类**的 `source_allowed` 之外（`CREATED_BY`、`PRODUCES`、`EVALUATES`、
`APPLIES_TO`、`USES`、`ENABLES`、`COMPARES_WITH`）、**5 类**的 `target_allowed` 之外
（`CREATED_BY`、`PRODUCES`、`EVALUATES`、`ENABLES`、`COMPARES_WITH`）。
被排除的后果是这些边落入 `role_violation`（角色违规）而**不是** `reversed`（方向反转）
——例如 `CREATED_BY` 上因此产生 87 条角色违规（`Person→Event` 34、`Event→Organization` 29、
`Event→Person` 24）。这些是 §6.4 记录的**谓词误用**，不污染方向指标。
报告与论文里应把它们单列，不当作方向错误。

> 修正记录：旧版这里写「`Event` 在所有关系的允许集里一律保留」，与代码事实相反，已更正。

### 6.2 端点类型标签本身有噪声

`docs/L5_SIX_LAYER_REPORT.md` §5.1 记录过：低频变体的类型标注不可信
（`Link Predictions` 只出现一次、被标成 `Concept`）。缓解措施有四条：

1. **允许集取宽、指纹取窄**：允许集只排除语义上铁定不可能的角色，
   指纹只覆盖两侧占比悬殊的类型。宁可少判，不制造假阳性。
2. **撤销比保留优先**：`EVALUATES`、`APPLIES_TO` 的指纹都因为抽查出假阳性而撤销，
   表里保留撤销理由（见各自 `evidence` 字段）。
3. **`role_violation` 与 `reversed` 分开计数**：类型噪声通常表现为前者，
   它不会污染方向指标。
4. **精度抽查常态化**：`l7_constraint_precision_sample.py` 固定 seed，
   改表后重跑即可对比同一批样例。

### 6.3 31.4% 的边不可判定

13753 / 43867 = 31.4% 的边因端点不在节点表内或类型为 `Unknown` 而无法判定。
其中 4304 个节点是 `tools/backfill_missing_endpoints.py` 反建的（类型标 `Unknown`）。
高频不可判端点：`Relations`(91)、`KGEM`(76)、`Zero-Shot Image Classification`(48)、
`Knowledge`(45)。

这些边**不是方向合规的**，也不是违规的——它们不参与任何比例的分母。
要提升 L7 的覆盖，得先解决 L3 的端点完备性（当前 68.4% 抽取口径），不是放宽约束。

### 6.4 谓词误用会被记成角色问题，不是方向问题

`CREATED_BY` 的 302 条 `role_violation` 里大量是这样：

```
C. Zhang(Person) → SIGKDD(Event)            C. Zhang is an author of a paper published in SIGKDD 2018
2009 IEEE Conference(Event) → IEEE(Org)     The 2009 IEEE Conference ... was published by IEEE
Mehwish Alam(Person) → The Semantic Web     Edited The Semantic Web
```

方向没反，是谓词选错了（该用 `CONTRIBUTED_TO`，而该类已按 `docs/L5_SIX_LAYER_REPORT.md` §4
列为待清理项）。把它们记成方向错误会误导后续修复，所以本层只归入角色问题。

### 6.5 L5 不能用来做方向判定

`docs/L5_SIX_LAYER_REPORT.md` §4.5 已确认：阈值 0.67 下 L5 只能拦住 23.9% 的方向反转；
对 T3.1 的真实反转，反转前后平均分 0.7205 → 0.7217（Δ +0.0011），
2179 条配对里 94.3% 分数持平。L7 必须独立于 L5，本表就是那一层。

---

## 7. 复现与维护

```bash
# 1. 契约自检（表结构 + 与 verify_direction.py 的对称划分是否漂移）
python tools/check_ontology_consistency.py

# 2. 15 类全量审计（零模型，只读本地图快照与 L5 结果）
python tools/l7_relation_constraints_audit.py
#    → data/quality/l7-relation-constraints.json

# 3. 只看某一类
python tools/l7_relation_constraints_audit.py --by-type CREATED_BY

# 4. CREATED_BY 原型（与上面对账用，口径必须一致）
python tools/l7_direction_verify.py
#    → data/quality/l7-direction-verification.json

# 5. 精度抽查（固定 seed，改表后重跑对比）
python tools/l7_constraint_precision_sample.py
#    → data/quality/l7-constraint-samples.json
```

改动本表时要动的与不用动的：

| 改动 | 需要同步的地方 |
| --- | --- |
| 改 `CREATED_BY` 的允许集 | 会同时改变 `tools/apply_created_by_fix.py` 的修复行为——先跑它的 `--dry-run` |
| 改对称关系的划分 | 同步 `tools/verify_direction.py::SYMMETRIC`（`check_ontology_consistency.py` 会报不一致） |
| 本体新增关系类型 | `validate_constraints()` 会报「表中缺失」；同时要改 `kg_extractor.RELATION_TYPES` 与 `ontology.RELATION_TYPES` |
| 给某类加方向指纹 | 自检要求 `source_core ∩ target_core = ∅`、指纹 ⊆ 允许集、`reversal` 与指纹一致 |
| 只改 `evidence` 文本 | 无副作用 |

自检覆盖的静默失败（`validate_constraints()`）：

- 关系类型缺失或多余；
- 集合里写了拼错的类型名（约束会静默失效、永远判合规）；
- 指纹两侧相交（`reversed` 恒为 0，看起来正常实际没查）；
- 指纹不在允许集内（命中后会掉进 `role_violation`）；
- `SYMMETRIC_RELATIONS` 与表中 `template_symmetric` 不一致。
