"""把 5 份调研报告的提炼稿合并进 docs/MAINSTREAM_SOLUTIONS_SURVEY.md。

背景：T7.1 要求合并调研报告，但原文档只写了第一节（实体消歧 1.1–1.4），
第二/三/四节一直是"（待 subagent 调研结果补充）"占位。三份提炼稿已完成：
  E:\\fab\\survey-er-normalization-fragment.md   实体归一化（编号 1.5–1.15，天然衔接）
  E:\\fab\\kg-quality-eval-digest.md             质量评测框架
  E:\\fab\\标签与聚合-调研素材.md                 可视化：标签与聚合

做法：
  - 片段 1 的编号与现有 1.1–1.4 连续，直接接上，只去掉它自带的顶层标题
  - 片段 2 的 `### N.` 重编号为 `### 3.N`
  - 片段 3 的 `## 一、…## 六、` 降级为 `### 4.1…### 4.6`，其内部三级标题
    按映射表降为 `####`（显式映射，不做通用正则，避免改错编号）
  - "二、关系抽取质量"与"五、待修正项"由本脚本写入实质内容
    （前者是本项目视角，后者需修正原文与调研结论的冲突）

用法：
  python tools/merge_survey_docs.py [--apply]
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import re
import sys
from pathlib import Path

DOCS = ROOT / 'docs'
MAIN = DOCS / 'MAINSTREAM_SOLUTIONS_SURVEY.md'
F1 = Path(r'E:\fab\survey-er-normalization-fragment.md')
F2 = Path(r'E:\fab\kg-quality-eval-digest.md')
F3 = Path(r'E:\fab\标签与聚合-调研素材.md')
APPLY = '--apply' in sys.argv

# 片段 3 的三级标题映射：原编号 → 合并后的编号（显式，不用正则推断）
F3_MAP = {
    '### A 类｜': '#### A 类｜',
    '### B 类｜': '#### B 类｜',
    '### 2.1 ': '#### 4.2.1 ',
    '### 2.2 ': '#### 4.2.2 ',
    '### 3.1 ': '#### 4.3.1 ',
    '### 3.2 ': '#### 4.3.2 ',
    '### 3.3 ': '#### 4.3.3 ',
    '### 4.1 ': '#### 4.4.1 ',
    '### 4.2 ': '#### 4.4.2 ',
    '### 4.3 ': '#### 4.4.3 ',
    '### 4.4 ': '#### 4.4.4 ',
    '### 5.1 ': '#### 4.5.1 ',
    '### 5.2 ': '#### 4.5.2 ',
}
# 片段 3 的二级标题 → 合并后的三级编号
F3_H2 = [
    ('## 一、', '### 4.1 '),
    ('## 二、', '### 4.2 '),
    ('## 三、', '### 4.3 '),
    ('## 四、', '### 4.4 '),
    ('## 五、', '### 4.5 '),
    ('## 六、', '### 4.6 '),
]

SEC2 = """## 二、关系抽取质量

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
**2026-09-12 已实施**：15 类约束表在 `src/ontology.py::RELATION_ENDPOINT_CONSTRAINTS`，
逐类实测与可判性分级见 `docs/L7_ENDPOINT_CONSTRAINTS.md`。
要点：只有 `CREATED_BY`/`PRODUCES`/`LOCATED_AT` 能由端点类型判方向，
`CREATED_BY` 方向合规 98.0%、检出反转 120 条。

"""

SEC5 = """## 五、待修正项（由本次调研暴露）

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
"""


def main():
    main_txt = MAIN.read_text(encoding='utf-8')

    # 现有文档：只保留到第一节结束（## 二、之前）
    m = re.search(r'^## 二、关系抽取质量', main_txt, flags=re.M)
    if not m:
        raise SystemExit('未能在主文档里定位「## 二、关系抽取质量」')
    head = main_txt[:m.start()].rstrip() + '\n'

    # 更新引言
    head = head.replace(
        '> 调研方式：subagent 并行深挖四个方向 + 直接抓取 GitHub raw 源码。',
        '> 调研方式：subagent 并行深挖四个方向 + 直接抓取 GitHub raw 源码。\n'
        '>\n'
        '> 2026-09-12 合并：三份提炼稿（实体归一化 / 质量评测 / 可视化标签与聚合）\n'
        '> 并入本文，此前第二至四节一直是"待补充"占位。原始 5 份调研报告为\n'
        '> `neo4j-merge-research.md`、`research_evidence_kg_normalization.md`、\n'
        '> `kg-quality-eval-survey.md`、`3d-kg-visual-research.md`、\n'
        '> `InfraNodus-事实核查报告.md`。\n'
        '>\n'
        '> **约定**：标注「已抓取」= 本轮实测 HTTP 200 且读到内容；「已索引」=\n'
        '> 搜索结果或 OpenAlex 确认存在但未读正文；「未验证」= 未确认 URL。\n'
        '> 未验证项一律原样保留，不得当作业已确认。')

    # 片段 1：去掉自带的顶层标题，其余原样接在 1.4 之后
    f1 = F1.read_text(encoding='utf-8').split('\n')
    if not f1[0].startswith('## 实体消歧'):
        raise SystemExit('片段 1 的首行不是预期的顶层标题')
    f1_body = '\n'.join(f1[1:]).lstrip('\n').rstrip() + '\n'

    # 片段 2：顶层标题去掉，内部 ### N. → ### 3.N
    f2 = F2.read_text(encoding='utf-8')
    f2 = re.sub(r'^### (\d+)\. ', lambda mm: f'### 3.{mm.group(1)} ', f2, flags=re.M)
    f2 = f2.replace('## 质量评测框架：主流方案调研\n', '', 1).lstrip('\n').rstrip() + '\n'
    body2 = '## 三、质量评测框架\n\n' + f2

    # 片段 3：二级降为 ### 4.N，三级按映射表降为 ####
    f3 = F3.read_text(encoding='utf-8')
    for old, new in F3_MAP.items():
        f3 = f3.replace('\n' + old, '\n' + new)
    if f3.startswith('### ') or f3.startswith('## '):
        for old, new in F3_MAP.items():
            if f3.startswith(old):
                f3 = new + f3[len(old):]
                break
    for old, new in F3_H2:
        f3 = f3.replace('\n' + old, '\n' + new)
        if f3.startswith(old):
            f3 = new + f3[len(old):]
    f3 = f3.lstrip('\n').rstrip() + '\n'
    body4 = '## 四、可视化与交互\n\n' + f3

    out = (head + '\n' + f1_body + '\n---\n\n'
           + SEC2 + '---\n\n' + body2 + '\n---\n\n' + body4 + '\n---\n\n' + SEC5)

    # 完整性自检：不得残留占位符，关键小节必须都在
    for must in ('（待 subagent 调研结果补充）', '## 质量评测框架：主流方案调研'):
        if must in out:
            raise SystemExit(f'输出里仍残留 {must!r}')
    for must in ('### 1.15 ', '### 3.9 ', '### 4.6 ', '## 二、关系抽取质量',
                 '## 五、待修正项'):
        if must not in out:
            raise SystemExit(f'输出缺少 {must!r}')

    print(f'原文档 {len(main_txt)} 字符 → 合并后 {len(out)} 字符')
    print(f'  片段1 {len(f1_body)} · 片段2 {len(f2)} · 片段3 {len(f3)}'
          f' · 自写二/五 {len(SEC2)}/{len(SEC5)}')
    # 标题清单，便于人工核对层级
    print('\n合并后的一级/二级标题：')
    for line in out.split('\n'):
        if line.startswith('## '):
            print('  ' + line)
    if APPLY:
        MAIN.write_text(out, encoding='utf-8')
        print(f'\n已写出 {MAIN}')
    else:
        print('\n[dry-run] 未写出（加 --apply 生效）')


if __name__ == '__main__':
    main()
