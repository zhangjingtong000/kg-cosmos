# -*- coding: utf-8 -*-
r"""方向判据守门：**全量自洽率不得低于当前水平**。

## 为什么值得一个正式回归位

`src/direction_witness.py` 被 `graph_sanitize` 的 `KG_SANITIZE=3` 用来
**翻转判为 `inverted` 的边** —— 判错的代价是**把方向本来正确的边改反**，
而且直接写进图里（数据损坏级）。

2026-09-19 实测过一个真 bug：模块 docstring 第 ① 处修正点名的原例
`X is listed as an author of Y` **一直判 `inverted`**（`COPULA_AGENT` 的
`(?:listed as|the|an?)?` 只能吃一个可选片段），同源的
`X is listed as one of the authors of Y` 也一样。

**这类退化是"静默"的**：没有异常、没有报错、单测里的合成样例也未必覆盖，
只有把**全量数据**重新跑一遍、看自洽率的变化才看得出来。
`tools/_probe/_verify_direction.py` 早就做了这件事，但它在 `_probe/` 下
**不会有人再跑** ⇒ 谁改回旧写法都没有东西会报。本脚本就是把它变成守门。

## 判据（三条，全部要满足）

① **自洽率 ≥ 80.0%**。
   来由是 2026-09-19 的**修复前后全量对照**（同一份 45708 条边、
   两端都在 description 里 27044 条）：

       修复前（旧正则） consistent 4188 / inverted 1665 / unknown 21191
                        ⇒ 自洽率 **71.55%**（4188/5853）
       修复后（当前）   consistent 5123 / inverted 1246 / unknown 20675
                        ⇒ 自洽率 **80.44%**（5123/6369）

   `listed as an author of` 在语料里是**主流形态**（233 个文件里出现
   1.4 万次），所以把正则改回旧写法会立刻掉回约 72% —— 80.0% 足够报红。
   ⚠️ **不写成 80.44**：那是四舍五入后的读数，真值是 80.4364%
   （5123/6369），判 `>= 80.44` 会「刚修好就红」。

② **相对"退化写法"的增益 ≥ 5.0pp**。
   脚本在**内存里**把 `COPULA_AGENT` 换回旧写法（并退回 `authored` 主动词形）
   重算一遍：只要当前实现是好的，它必须明显更高。
   实测增益约 +6.6pp（只回退系表）/ +8.9pp（再回退 `authored`），取 5.0 留余量。
   这一条是**代码哨兵**：数据再怎么变，只要"修复在起作用"，它就成立；
   若有人把正则改回旧写法，增益会掉到 0 ⇒ 报红。

③ **反转条数不许比退化写法多**（"零新增反转"）。
   修复只会把 `inverted` 变成 `consistent`，不会反向 ⇒ 当前条数必须 ≤ 退化版。
   若将来有人"顺手"扩正则把被动式也吃进主动系表，这条会先红。

## 耗时（决定它能不能进 verify_all）

全量 45708 条 + 退化对照各跑一遍，实测 **约 8 秒** ⇒
**不抽样**，直接进 `verify_all.py` 的 `DATA_CHECKS`（数据侧、不依赖浏览器）。

## 留档（不删）

与 `PRE-T3` 基线的逐字比对（证明 description 没被 T3.1 补丁改过）、
以及 `swapped / untouched` 分桶仍在 `tools/_probe/_verify_direction.py`。
本脚本**不读** PRE-T3（守门只需要自洽率，省掉一半 I/O）。

用法：
  python tools/check_direction_consistency.py
  python tools/check_direction_consistency.py --show 8   # 附带 inverted 样例
"""
import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

import direction_witness as DW  # noqa: E402

try:                                    # 输出里有中文；被 verify_all 捕获时按 UTF-8 解
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

DATA = ROOT / 'data' / 'quality' / 'relation-verify-all.json'

# 判据①的下限（见 docstring：71.55% 是旧写法，80.44% 是修复后读数）
RATE_MIN = 80.0
# 判据②的增益下限（百分点）
GAIN_MIN_PP = 5.0

# ── 退化写法（历史 bug 版本，只在对照里用，不进生产路径）─────────────────
# `(?:listed as|the|an?)?` 只能吃**一个**片段：吃下 `listed as` 之后就要求
# 紧跟 `author`，于是 `an author` / `one of the authors` 都命中不了主动系表
# ⇒ 落到 `PASSIVE_WITH_AUX`（`is ... listed`）⇒ 判反 ⇒ 正确的边被翻转。
_DEGRADED_COPULA_AGENT = re.compile(
    r"^(?:is|are|was|were)\s+"
    r"(?:listed as|the|an?)?\s*"
    r"(?:author|authors|co-?author|creator|creators|writer|writers|"
    r"developer|developers|designer|designers|owner|owners|maintainer|"
    r"contributor|contributors)\b", re.I)


def load_edges(path=DATA):
    d = json.loads(path.read_text(encoding='utf-8-sig'))
    if isinstance(d, dict):
        for k in ('edges', 'items', 'relations', 'data'):
            if isinstance(d.get(k), list):
                return d[k]
        return []
    return d


def measure(edges, show=0):
    """跑一遍全量判据；返回 `(计数, 可判数, 自洽率, inverted 样例)`。"""
    cnt = Counter()
    samples = []
    for e in edges:
        desc = e.get('description') or ''
        s, t = e.get('source'), e.get('target')
        if not desc or not s or not t:
            cnt['no_desc'] += 1
            continue
        if DW.split_desc(desc, s, t)[0] is None:
            cnt['endpoints_not_both_in_desc'] += 1
            continue
        v = DW.desc_verdict(desc, s, t)
        cnt[v] += 1
        if v == 'inverted' and len(samples) < show:
            samples.append(DW.witness(desc, s, t) | {
                'source': s, 'target': t, 'type': e.get('type')})
    judged = cnt['consistent'] + cnt['inverted']
    rate = 100.0 * cnt['consistent'] / judged if judged else 0.0
    return cnt, judged, rate, samples


def measure_degraded(edges):
    """把模块的正则**在进程内**换回旧写法再跑一遍（跑完必还原）。

    不复制 `desc_verdict` 的逻辑：换的正是它读的那两个模块级正则，
    所以走的是同一条代码路径 —— 对照才有意义。
    """
    keep = (DW.COPULA_AGENT, DW.AGENT_VERB)
    try:
        DW.COPULA_AGENT = _DEGRADED_COPULA_AGENT
        # 顺带退回 `authored` 主动词形（旧实现只有 `authored by`）
        DW.AGENT_VERB = re.compile(
            DW.AGENT_VERB.pattern.replace('authored|', '', 1), re.I)
        return measure(edges)
    finally:
        DW.COPULA_AGENT, DW.AGENT_VERB = keep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--show', type=int, default=0,
                    help='打印若干条 inverted 样例（原文施事与边方向相反）')
    a = ap.parse_args()

    if not DATA.exists():
        print(f'❌ 找不到 {DATA} —— 方向守门需要这份数据')
        return 1

    t0 = time.time()
    edges = load_edges()
    print(f'加载 {DATA.name}：{len(edges)} 条边')

    cnt, judged, rate, samples = measure(edges)
    print(f'① 自洽率 {rate:.2f}%（{cnt["consistent"]}/{judged}）'
          f'  consistent {cnt["consistent"]} / inverted {cnt["inverted"]}'
          f' / unknown {cnt["unknown"]}')

    d_cnt, d_judged, d_rate, _ = measure_degraded(edges)
    gain = rate - d_rate
    print(f'② 退化写法对照 {d_rate:.2f}%（{d_cnt["consistent"]}/{d_judged}）'
          f'  inverted {d_cnt["inverted"]} ⇒ 修复增益 {gain:+.2f}pp')

    if a.show and samples:
        print('③ inverted 样例（原文施事与边方向相反）：')
        for x in samples[:a.show]:
            print(f'   {x["source"]} —[{x["type"]}]→ {x["target"]}'
                  f'   见证：{x["witness_text"][:100]}')

    problems = []
    if rate < RATE_MIN:
        problems.append(f'自洽率 {rate:.2f}% < 下限 {RATE_MIN}%')
    if gain < GAIN_MIN_PP:
        problems.append(f'相对退化写法的增益只有 {gain:+.2f}pp < {GAIN_MIN_PP}pp'
                        f' ⇒ 方向判据疑似被改回旧写法')
    if cnt['inverted'] > d_cnt['inverted']:
        problems.append(f'反转条数 {cnt["inverted"]} 多于退化写法 {d_cnt["inverted"]}'
                        f' ⇒ 引入了新的判反')

    dt = time.time() - t0
    if problems:
        for p in problems:
            print(f'❌ {p}')
        print(f'❌ 方向判据守门未通过（{len(problems)} 项）· {dt:.1f}s')
        return 1
    print(f'✅ 自洽率 {rate:.2f}%（{cnt["consistent"]}/{judged}）'
          f' · 下限 {RATE_MIN}% · 相对退化写法 {gain:+.2f}pp'
          f' · 反转未增加 · 全量 {len(edges)} 条 · {dt:.1f}s')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
