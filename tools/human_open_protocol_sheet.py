# -*- coding: utf-8 -*-
r"""**臂 C / P2 档 1 的标注材料生成器** —— 给人填的「完全开放协议」标注表。

## 为什么只有这一件还需要人

`docs/早先的审计记录.md` 逐条核验后的结论：

```
P3 的判据是「人类只需标**首尾两档**即可校准"LLM 曲线是否可信"」
  档 1 **完全开放**  ← **只有这个要人**（没有公开数据集会标"完全开放"）
  档 4 **槽位式**    ← **SciREX 的 2148 条人类标注就是它**（已在 data/external/）
```

**⇒ 所以本脚本只做档 1 的材料。**

## ★ 设计要点

1. **材料与 P3 完全一致** —— 用**同一份** `data/gold/SOURCE-<paper>-p1-12.txt`、
   同 `--chars` 截断。**否则这一轮的 α 不能与 P3 的档 1 对照。**
2. **指令就是档 1 的原文**（「列出这段文本里的所有关系」，**不限定谓词**）——
   **一个字都不加**。加了就变成"给类型表"（档 2）了。
3. **⚠️ 不给任何模型输出** —— 那是**盲标**。L5 的盲判页（`l5-review.html`）
   也是这个纪律。看到模型的答案再标，测的就不是"人类能不能标"。
4. **目标只有 20–30 个单元** ——
   要的是"**人类曲线的形状**"，不是"精确的一致性系数"。
   旧文档写"1 小时 / 200 个单元"，那是把"算准 α"和"看形状"混为一谈了。

## 产出

    output/human-open-protocol/HUMAN-SHEET.md      ← **给人看的**（指令 + 文本 + 怎么填）
    output/human-open-protocol/human-sheet.jsonl   ← **给程序读的**（id + text，空的 relations）
    output/human-open-protocol/design.json         ← 这一轮的设计（seed/chars/source/sha）

**⚠️ 产出写 `output/`** —— 那是**给人填的临时材料**，不是研究产物；
填完之后再决定要不要 `git add -f` 进 `data/`。

用法：
    python tools/human_open_protocol_sheet.py                    # 默认
    python tools/human_open_protocol_sheet.py --chars 3000
    python tools/human_open_protocol_sheet.py --paper 2401.04507v1
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / 'data' / 'gold'
OUT = ROOT / 'output' / 'human-open-protocol'

# ★ **档 1 的指令原文**（`tools/protocol_ladder.py` 的 `## 四档协议`）——
#   **一个字都不加**。加了就变成档 2（给类型表）了。
INSTRUCTION = """列出这段文本里的所有关系。"""

# 给人填的示例（**不提示任何具体答案**，只示范格式）
FORMAT = """每一条关系写成一行，四个字段用 ` | ` 隔开：

    source | target | 关系名 | 原文里那句话

- `source` / `target`：**原文里出现过的名字**（照抄，别改写）；
- `关系名`：**你自己起的** —— 这一档**不限定谓词**，随便叫什么（中文英文都行）；
- `原文里那句话`：**能支撑这条关系的那句话**（照抄，便于核对）；
- **标不出来就写 `标不出来`** —— 那**也是数据**（它正是 α 为负的来源）。
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--paper', default='2012.00456v1')
    ap.add_argument('--chars', type=int, default=3000,
                    help='截多少字符（**P3 用的是 3000**）')
    ap.add_argument('--seed', type=int, default=20260912)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    src = GOLD / f'SOURCE-{a.paper}-p1-12.txt'
    print('=' * 88)
    print('臂 C / P2 档 1 · **给人填的「完全开放协议」标注表**')
    print('=' * 88)
    if not src.exists():
        cands = sorted(GOLD.glob('SOURCE-*-p1-*.txt'))
        print(f'  ❌ 缺 {src.name}')
        if cands:
            print('  可选的 SOURCE：')
            for c in cands:
                print('      ' + c.name)
        return 1
    text = src.read_text(encoding='utf-8', errors='replace')
    sha = hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]
    body = text[:a.chars]
    print(f'  来源 {src.name}（全文 {len(text)} 字 · sha {sha}）')
    print(f'  截取 **前 {len(body)} 字**（与 P3 的 `--chars` 一致）')
    print(f'  目标：**20–30 条关系单元**（约 15 分钟）')

    # ── 1) 给人看的表 ──────────────────────────────────────────────
    sheet = OUT / 'HUMAN-SHEET.md'
    sheet.write_text(
        f"""# 「完全开放协议」人工标注表（臂 C / P2 档 1）

> **你要做的事**：读下面这段论文原文，**列出里面所有的关系**。
> **大约 15 分钟**。**没有标准答案，也没有对错** —— 我们要的正是"你这样读，
> 别人那样读"这个事实本身。

---

## 一、指令（**就这一句，不加任何限定**）

> ## {INSTRUCTION}

**⚠️ 刻意不给你关系类型的清单** —— 那是这一档的定义。
（给了类型表就变成"档 2"了，那是另一个实验。）

## 二、怎么填

{FORMAT}
## 三、填在哪

把结果写进 **`human-sheet.jsonl`** 里那个 `relations` 数组，每项形如：

```json
{{"source": "…", "target": "…", "type": "…", "quote": "…"}}
```

**⚠️ 或者更简单**：直接在这个文件下面「四、你的答案」一节里一行一条，
**我（程序）来解析**。你选一种就行。

---

## 四、你的答案

<!-- 在这里一行一条，例如：
Knowledge Graph | Named Entity Recognition | 用到了 | "…原文那句…"
-->
（还没有填）

---

## 五、要标的文本（**{len(body)} 字，来自 `{src.name}`**）

```
{body}
```

---

## 六、这一轮的元信息（**填完不用管**）

| 项 | 值 |
|---|---|
| 论文 | `{a.paper}` |
| 来源文件 | `{src.name}` |
| 截断 | 前 {len(body)} 字 |
| 全文 sha256（前 12） | `{sha}` |
| 协议 | **档 1 · 完全开放**（不限定谓词） |
| 对照组 | P3 的档 1（**LLM** 两个 seed：α = −0.2977 / −0.1783） |
| 档 4 的对照组 | **SciREX 2148 条人类标注**（`data/external/`，**不需要你标**） |
""", encoding='utf-8')

    # ── 2) 给程序读的 jsonl（空的 relations，等人填）────────────────
    jl = OUT / 'human-sheet.jsonl'
    jl.write_text(json.dumps(
        {'id': f'{a.paper}#open-human', 'paper': a.paper,
         'protocol': 'tier1-open', 'instruction': INSTRUCTION,
         'chars': len(body), 'source_sha12': sha,
         'relations': []}, ensure_ascii=False) + '\n', encoding='utf-8')

    # ── 3) 设计（**为了可复现：这一轮到底用的哪份文本**）──────────────
    (OUT / 'design.json').write_text(json.dumps(
        {'generated_from': str(src.relative_to(ROOT)),
         'chars': a.chars, 'seed': a.seed, 'source_sha12': sha,
         'instruction': INSTRUCTION,
         'why_only_tier1':
             'P3 的档 4 就是 SciREX 的槽位式协议，而 `data/external/` 里已有 '
             '2148 条人类标注 ⇒ 档 4 不需要人；只有档 1（完全开放）没有公开数据。',
         'target_units': '20-30（要的是曲线的形状，不是精确的 α）',
         'blind': '⚠️ 不给任何模型输出 —— 那是盲标（与 web/l5-review.html 同一纪律）'},
        ensure_ascii=False, indent=1), encoding='utf-8')

    print()
    print(f'  ✅ 给人看的：{sheet.relative_to(ROOT)}')
    print(f'  ✅ 给程序读的：{jl.relative_to(ROOT)}')
    print(f'  ✅ 设计：{(OUT / "design.json").relative_to(ROOT)}')
    print()
    print('  ★ 打开上面那个 .md，读第五节那段文本，在第四节一行一条地写。')
    print('  ⚠️ **不要先看任何模型输出** —— 那是盲标。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
