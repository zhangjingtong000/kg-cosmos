# 金标标注规范（M2 实验）

> 目的：为抽取质量实验提供人工标注的 ground truth，计算 Precision / Recall / F1
> 与错误类型分布。标注人：论文作者本人。**标注时不得查看系统抽取输出**（防锚定），
> 只对照 PDF 原文。

## 1. 语料与工作量

- 标注文献：3-5 篇（第一篇：attention-is-all-you-need，已附页级工作表）
- 每篇：实体 50-100 条、关系 30-60 条，覆盖正文前 60%（方法与实验部分信息密度最高）
- 工作表：`data/gold/<paper_id>.json`（结构见 §4）

## 2. 标注规则

**实体**：论文中明确命名且承载语义的对象——方法名、系统名、数据集名、指标名、
模型组件等，类型限定 15 类本体（Concept/Method/System/Component/Process/Artifact/
Dataset/Metric/Tool/Material/Person/Organization/Location/Event/Document）。

- 记录**规范名**（论文中首次正式命名，如 "multi-head attention" 而非 "attention"）
- 记录**首次出现的页码**与**原文引用句**（截取 ≤200 字符，用于核验）
- 同一实体的不同指称（MHA / multi-head attention）记一条，规范名取全称
- 排除：泛指词（"the model"）、纯编号（Table 1）、仅在参考文献列表出现的条目

**关系**：论文明确陈述的二元关系，限 15 类（PART_OF/USES/PRODUCES/MEASURES/
EVALUATES/COMPARES_WITH/SUPPORTS/CONTRADICTS/CAUSES/ENABLES/APPLIES_TO/
LOCATED_AT/CREATED_BY/CITES/RELATED_TO）。

- 两端必须是本篇已标注实体；关系必须**在句中有明确文字依据**（引用该句）
- 无把握的隐含关系不标——金标只标"确定知道的"，宁可缺不可错

## 3. 评分口径（论文实验章直接引用）

- **实体匹配**：规范名归一化（小写、去标点）后完全相等记命中；另报告 ≥0.93 相似度
  的宽松口径作次要指标（与抽取去重阈值一致）
- **Precision** = 命中的抽取实体数 / 抽取实体总数
- **Recall** = 命中的金标实体数 / 金标总数；F1 为二者调和平均
- **关系**：三元组 (源名, 目标名, 类型) 归一化后全等记命中；源目标互换记**方向错误**
- **错误类型分析**（每条非命中抽取项归类）：
  1. 幻觉——原文该页无此实体/关系依据
  2. 类型错误——名字命中但类型不符
  3. 边界错误——实体名切分不当（多词/少词）
  4. 遗漏——金标有而抽取无（计入 Recall，不计入 Precision 分母）

## 4. 工作表结构

```json
{
  "paper_id": "1703.08098v9",
  "annotator": "作者",
  "entities": [
    {"name": "multi-head attention", "type": "Method",
     "page": 3, "quote": "Multi-head attention allows the model to..."}
  ],
  "relations": [
    {"source": "Transformer", "target": "multi-head attention", "type": "USES",
     "page": 3, "quote": "...the Transformer employs multi-head attention..."}
  ]
}
```

## 5. 与四通道实验的咬合

同一份金标对所有通道（本地零样本 / 本地蒸馏 / 云端 / Schema 消融开与关）统一评分，
不做任何通道的特殊化处理。D1 双代理审计的金标验证（红灯密度 vs 错误密度列联表）
也复用本表。
