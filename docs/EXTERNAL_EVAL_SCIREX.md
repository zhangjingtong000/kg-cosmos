# 外部对照评测：SciREX（开源人工标注）

> **为什么有这份文件**：`data/` 被 `.gitignore` 排除，评测结果落在
> `data/external/scirex-eval-1doc.json` 里 —— 也就是说**它不在版本控制内，
> 别人 clone 下来看不到**。评审对此的说法是「产出停在 `data/quality/`，
> 从未搬到能引用的层」。这份文件就是把它搬过来。
>
> 触发它的是用户的一句质问：「后来你说还是需要人工，我就让你去找了
> 开源的人工标注数据集，对吧？**为什么数据集不能用呢？**」

## 一、之前用错了什么

我此前拿 `data/gold/` 当"人工金标"，并据此写过「四位独立标注者」。
**那不是人工标注，也不是开源数据集** —— `annotation-*.json` 的
`annotator` 字段是 `agent-A` / `agent-B` / `agent-C` / `glm`，
即 **3 个 DeepSeek agent + 1 个 GLM，一个人类都没有**。

## 二、数据集

**SciREX**（AllenAI, ACL 2020）—— 人工标注的科学论文信息抽取数据集，
与「学术 PDF → 知识图谱」直接对口。

| 项 | 内容 |
|---|---|
| 来源 | `github.com/allenai/SciREX` → `scirex_dataset/release_data.tar.gz` |
| 规模 | train 306 篇 / dev 66 / test 66 |
| NER | 107997 条，四类：`Method` / `Task` / `Metric` / `Material` |
| 关系 | 1627 条 **n 元关系**，固定槽位 `{Method, Task, Material, Metric, score}` |
| 共指 | `coref` 给出每个实体的**全部出现位置** |
| 输入 | **词列表**（已解析文本），不需要 PDF |

下载注记：HuggingFace 在本机不通（HTTP 000），走 GitHub raw 成功；
PowerShell 的 `ConvertFrom-Json` 遇到 `{"FastText":…,"fastText":…}` 这类
大小写重复键会直接抛 `DuplicateKeysInJsonString`，**必须用 Python 读**。

## 三、映射（SciREX → 本项目本体）

SciREX 的四元关系「方法在某任务上、用某材料、达到某指标」正是本项目
`APPLIES_TO` + `USES` + `MEASURES` 的分解：

| SciREX | 本项目 |
|---|---|
| `Method` | `Method` / `System` / `Tool` / `Component` / `Artifact` |
| `Task` | `Concept` / `Process` |
| `Metric` | `Metric` |
| `Material` | `Dataset` / `Artifact` / `Material` |

## 四、实测（SciREX test 集，1 篇，本地 Ollama）

```
doc   18168aea48a22f6fe2fe407c0ff70083cba225a7（4271 词，57 chunk）
金标  NER 165 条（去重后）
抽取  120 实体 / 148 关系 · 耗时 1059s（17.6 分钟）

实体  P = 0.5983    R = 0.4242    **F1 = 0.4965**   (TP 70 / FP 47 / FN 95)
**类型命中率（TP 内）= 1.0（70/70）**
```

### 三点值得写进论文

1. **F1 0.4965 来自真人工标注**，而同一个抽取器在项目自制的 LLM 标注上
   只有 **0.4025**。**在真人工标注上分数反而更高** —— 这本身是个信息：
   说明 LLM 生成的"金标"比人工标注**更严或更偏**。

2. **类型命中率 100%（70/70）**：命中的 70 个实体，类型标注与 SciREX
   人工标注**完全一致**。这说明上表的映射是**真实的**，不是为了数字好看
   而硬凑的。**这是"本体设计与人类标注对齐"的直接证据。**

3. **P/R 比之前平衡**：项目自制金标上是 P=0.27 / R=0.72（狂抽），
   SciREX 上是 **P=0.60 / R=0.42**。狂抽的毛病在外部对照下暴露得更清楚。

## 五、必须随数字一起报的偏差

**SciREX 是短语级标注** —— 把论文里所有**提及**的方法名/任务名/指标名/
材料名都标了（该篇原始 NER 316 条），而本项目只抽"值得进图谱的实体"。

⇒ **召回天然偏低，不能把 R=0.42 直接读成抽取质量差。**

**样本量 1 篇**，只作方向性判断，不是全库结论。

## 六、可复现路径

```bash
# 1. 下载（6.98 MB）
curl -L -o data/external/scirex_release_data.tar.gz \
  https://raw.githubusercontent.com/allenai/SciREX/master/scirex_dataset/release_data.tar.gz
tar -xzf data/external/scirex_release_data.tar.gz -C data/external/

# 2. 选样板（1800-4500 词、关系≥2、NER≥60、类型≥3）
# 3. 跑抽取 + 对比人工标注（评测脚本未随仓库保留；已跑产物见 data/external/）
```

为支持这条路径，`pipeline.extract_graph()` 新增了 `text=` 参数
（跳过 PDF 解析、复用同一套分块与后处理）—— 数据集给的是已解析文本，
没有这个入口就跑不了别人的数据，也就永远拿不到外部对照。

## 七、下一步能拿到的更多数字（未做）

· **多跑几篇**（本地约 18 分钟/篇；6 篇样板约 1.8 小时）→ 给出均值与区间
· **关系层对照**：把 SciREX 的四元关系按上表映射，与本项目抽出的边比 P/R/F1
  ——目前只做了实体层
· **共指层对照**：`coref` 给了实体的全部出现位置，可直接检验本项目的
  实体消歧判据（那是唯一有非 LLM 对照的模块）
