# -*- coding: utf-8 -*-
r"""P3 · 协议阶梯实验：把「可标性取决于协议结构化程度」从**两臂对照**做成**单调曲线**。

## 这是什么

主研究点说「**可标性取决于标注协议的结构化程度**」。原先只有两臂对照
（SciREX 槽位式+人类 vs 我方开放式+LLM），**而且两个变量同时变了**，
所以只能说"观察"。

**本实验只动一个变量：协议的结构化程度。** 标注者、语料、单元全都不变，
只把指令从"完全开放"逐级收紧到"槽位式"，看标注者间一致性 α 是否单调上升。

## 四档协议

    档 1  完全开放   「列出这段文本里的所有关系」—— 不限定谓词
    档 2  给类型表   「从这 16 类里选一个」—— 限定谓词空间
    档 3  类型+方向  「选一类，并判断方向」—— 再限定方向
    档 4  槽位式     「在什么任务上、用什么材料、达到什么指标」（SciREX 形态）

## 为什么"大部分不需要人类"

每一档用**同一批 LLM 标注者**跑两遍（不同 seed ⇒ 两个"标注者"），
就能得到该档的 α。**人类只需标首尾两档**即可校准"LLM 曲线是否可信"。

## 口径（与 recompute_agreement.py 一致，很重要）

标注者被要求"列出本文的全部关系" ⇒ **对任意候选单元，未列出 == 明确判定不属于**。
**所以填 0 是正确口径，不是缺失值。**
（本脚本同时跑 `nan` 口径作对照，以展示两种口径的差异 —— 与既有脚本保持一致。）

用法：
    python tools/protocol_ladder.py --dry-run      # 只打 4 档 prompt，不跑模型
    python tools/protocol_ladder.py                # 跑全部（约 25 分钟）
    python tools/protocol_ladder.py --protocols 2,4
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

GOLD = ROOT / 'data/gold'
OUT = ROOT / 'data/quality/protocol-ladder.json'

MODEL = os.environ.get('KG_QA_MODEL', 'qwen3.5:9b')
OLLAMA = os.environ.get('OLLAMA_HOST', 'http://127.0.0.1:11434')


def parse_args():
    ap = argparse.ArgumentParser(description='协议阶梯：看 α 是否随结构化程度单调上升')
    ap.add_argument('--paper', default='2012.00456v1')
    ap.add_argument('--source', default=None, help='默认 SOURCE-<paper>-p1-12.txt')
    ap.add_argument('--protocols', default='1,2,3,4', help='跑哪几档，逗号分隔')
    ap.add_argument('--reps', type=int, default=2, help='每档几个"标注者"（不同 seed）')
    # 2026-09-15：全量 42723 字符的 prompt 要跑 440s/次（而且几乎顶到 num_ctx），
    # 8 次就一个多小时。实测**前 3000 字符**已能产出 15 条关系，
    # 所以默认截断，保证这个实验"跑得完"。
    ap.add_argument('--chars', type=int, default=3000,
                    help='源文本截取多少字符（0=全量；全量约 440s/次）')
    ap.add_argument('--dry-run', action='store_true', help='只打 prompt，不跑模型')
    return ap.parse_args()


ARGS = parse_args()

# ══════════════════════════════════════════════════════════════════════════
# 四档协议 —— **只改指令的结构化程度，别的一律不变**
# ══════════════════════════════════════════════════════════════════════════
#
# 每档都要求**逐条给出原文引用**，理由：
#   · 与既有 `annotation-*.json` 的字段（page/block/quote）对齐；
#   · 逼模型"看着原文说"，而不是凭记忆编。

_TYPES = ('RELATED_TO, PART_OF, USES, APPLIES_TO, CREATED_BY, PRODUCES, ENABLES, '
          'CITES, MEASURES, LOCATED_AT, SUPPORTS, COMPARES_WITH, EVALUATES, CAUSES, '
          'CONTRADICTS, PUBLISHED_IN')

_TAIL = """
只输出 JSON，形如：
{"relations": [{"source": "...", "target": "...", "type": "...", "quote": "原文中的一句话"}]}
不要输出任何解释文字。最多 40 条。
"""

PROTOCOLS = {
    1: (
        '完全开放',
        '你是知识图谱标注者。下面是论文正文。'
        '请**列出这段文本里出现的全部关系**：任意两个实体之间，只要原文有明确表述，就算一条。'
        '**用你自己认为合适的词来描述关系类型**（不限于某个固定词表）。'
        + _TAIL,
    ),
    2: (
        '给类型表',
        '你是知识图谱标注者。下面是论文正文。'
        '请**列出这段文本里出现的全部关系**，并为每条关系**从下面这个固定词表里选一个类型**：\n'
        + _TYPES +
        '\n如果确实都不合适，用 RELATED_TO。'
        + _TAIL,
    ),
    3: (
        '类型 + 方向',
        '你是知识图谱标注者。下面是论文正文。'
        '请**列出这段文本里出现的全部关系**，每条要给出三件事：\n'
        '  ① source 与 target（必须是原文里出现过的实体名）；\n'
        '  ② **从下面这个固定词表里选一个类型**；\n'
        '  ③ **判断方向**：source 是动作的发出者、target 是接受者。'
        '若你不确定方向，就把它标成 RELATED_TO（无方向）。\n'
        '词表：' + _TYPES
        + _TAIL,
    ),
    4: (
        '槽位式（SciREX 形态）',
        '你是学术论文的实验结果标注者。下面是论文正文。'
        '请**只标注"实验装置"式的四元组**，即：\n'
        '  **某方法（Method）在某任务（Task）上、用某材料/数据集（Material）、达到某指标（Metric）**。\n'
        '把每个四元组拆成三条关系输出，**type 只允许这三种**：\n'
        '  · APPLIES_TO （Method → Task）\n'
        '  · USES       （Method → Material）\n'
        '  · MEASURES   （Method → Metric）\n'
        '**只要不是"方法—任务/材料/指标"这种结构，就一律不要输出。**\n'
        '（宁可少标，也不要凑数。）'
        + _TAIL,
    ),
}


def norm(n):
    """实体名归一：与 recompute_agreement.py 的 `norm` 保持一致。"""
    return re.sub(r'[^a-z0-9]+', '', (n or '').lower())


def call_ollama(prompt, seed, timeout=900):
    """直接打 Ollama /api/generate（不经过项目的抽取器 —— 这是**标注**任务，不是抽取）。

    `seed` 用来造出"两个不同的标注者"：同一个 prompt + 不同 seed
    ⇒ 输出的差异**只来自模型的采样噪声**，这正是"标注者间不一致"的下界。

    ## ⚠️ 刻意**不传** `format='json'`（2026-09-15 实测踩到的坑）

    第一版传了 `format: 'json'`，结果**四档全部返回 0 条**。
    诊断（`tools/_probe/_probe_ladder_fail.py`）三组对照：

        ① 短输入 · 无 format      440s · 输出 3440 字符 · **15 条关系**  ✅
        ② 短输入 · format=json     81s · 输出 **0 字符**（eval_count=1589）❌
        ③ 全量   · format=json     27s · 输出 **0 字符**（eval_count=50） ❌

    ⇒ **`qwen3.5:9b` 是 reasoning 模型**（`eval_count` 上千说明它"想"了很久），
    **JSON 约束模式与它的思考输出冲突，导致 final 输出为空。**

    **项目主路径没踩这个坑是运气**：`src/kg_extractor.py` 的 `call_ollama_json`
    **从未设置过 `payload["format"]`**（这正是台账里记的那条）。

    ⇒ 改用"让模型自由输出、由 `parse_relations()` 从文本里抠 JSON"。
    """
    import urllib.request
    body = json.dumps({
        'model': MODEL, 'prompt': prompt, 'stream': False,
        # **不传 'format'** —— 见 docstring
        'options': {'temperature': 0.8, 'seed': seed, 'num_ctx': 16384},
    }).encode('utf-8')
    req = urllib.request.Request(f'{OLLAMA}/api/generate', data=body,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8')).get('response', '')


def parse_relations(raw):
    """从模型输出里抠出关系列表。容错：模型有时会在 JSON 外包一层解释。"""
    try:
        d = json.loads(raw)
    except Exception:
        m = re.search(r'\{.*\}', raw, re.S)
        if not m:
            return []
        try:
            d = json.loads(m.group(0))
        except Exception:
            return []
    out = []
    for r in (d.get('relations') or []):
        if not isinstance(r, dict):
            continue
        s, t = norm(r.get('source')), norm(r.get('target'))
        ty = str(r.get('type') or '').strip().upper()
        if s and t and s != t and ty:
            out.append({'s': s, 't': t, 'type': ty,
                        'quote': str(r.get('quote') or '')[:120]})
    return out


def build_matrix(units_by_annotator, fill):
    """→ (M_coders × N_units, units)。`fill=0` 表示"未列出 == 不属于"。

    与 `recompute_agreement.py` 同口径。
    """
    import numpy as np
    all_units = sorted({u for us in units_by_annotator.values() for u in us})
    anns = sorted(units_by_annotator)
    M = np.full((len(anns), len(all_units)), fill, dtype=float)
    for i, a in enumerate(anns):
        for j, u in enumerate(all_units):
            v = units_by_annotator[a].get(u)
            if v is not None:
                M[i, j] = v
    return M, all_units


def alpha_of(units_by_annotator, fill):
    """Krippendorff α（nominal）。单元值 = 关系类型的整数编码，0 = 未列出。"""
    try:
        import krippendorff
        import numpy as np
    except ImportError:
        return None
    M, units = build_matrix(units_by_annotator, fill)
    if M.shape[1] < 2 or M.shape[0] < 2:
        return None
    if fill != 0:
        # nan 口径：把"未列出"当缺失
        M = np.where(M == fill, np.nan, M)
    return float(krippendorff.alpha(reliability_data=M, level_of_measurement='nominal'))


def main():
    src = Path(ARGS.source) if ARGS.source else GOLD / f'SOURCE-{ARGS.paper}-p1-12.txt'
    if not src.exists():
        raise SystemExit(f'找不到源文本：{src}')
    text = src.read_text(encoding='utf-8', errors='replace')
    if ARGS.chars:
        text = text[:ARGS.chars]
    print(f'源文本：{src.name}（截取 {len(text)} 字符'
          f'{"，全量" if not ARGS.chars else ""}）· 模型：{MODEL}')
    print()

    want = [int(x) for x in ARGS.protocols.split(',') if x.strip()]

    if ARGS.dry_run:
        for k in want:
            name, p = PROTOCOLS[k]
            print('=' * 78)
            print(f'档 {k} · {name}   （prompt {len(text) + len(p)} 字符）')
            print('=' * 78)
            print(p[:700])
            print()
        return 0

    results = {}
    for k in want:
        name, instruction = PROTOCOLS[k]
        prompt = instruction + '\n\n【论文正文】\n' + text
        by_ann = {}
        t0 = time.time()
        for rep in range(ARGS.reps):
            ann = f'P{k}-rep{rep}'
            try:
                raw = call_ollama(prompt, seed=1000 * k + rep)
            except Exception as e:
                print(f'  档 {k} rep{rep} ❌ {type(e).__name__}: {str(e)[:120]}')
                continue
            rels = parse_relations(raw)
            # 单元 = (源, 目标)，值 = 类型码。同一对出现多次时取第一个（保守）
            d = {}
            for r in rels:
                u = (r['s'], r['t'])
                d.setdefault(u, r['type'])
            by_ann[ann] = d
            print(f'  档 {k} rep{rep}: {len(rels)} 条 → {len(d)} 个单元'
                  f'（{time.time() - t0:.0f}s）')

        if len(by_ann) < 2:
            print(f'  档 {k} 有效标注者不足 2 个，跳过')
            continue

        # 把类型映射成整数（0 保留给"未列出"）
        types = sorted({v for d in by_ann.values() for v in d.values()})
        code = {t: i + 1 for i, t in enumerate(types)}
        coded = {a: {u: code[v] for u, v in d.items()} for a, d in by_ann.items()}
        a0 = alpha_of(coded, fill=0)
        a1 = alpha_of(coded, fill=-1)
        n_units = len({u for d in by_ann.values() for u in d})
        n_types = len(types)
        print(f'  ★ 档 {k}「{name}」：α(填0) = {a0 if a0 is None else round(a0, 4)} · '
              f'α(nan) = {a1 if a1 is None else round(a1, 4)} · '
              f'单元 {n_units} · 用到的类型 {n_types}')
        print()
        results[k] = {'name': name, 'alpha_fill0': a0, 'alpha_nan': a1,
                      'units': n_units, 'types_used': n_types,
                      'annotators': {a: len(d) for a, d in by_ann.items()},
                      'sample': [{'s': u[0], 't': u[1], 'type': v}
                                 for d in by_ann.values() for u, v in list(d.items())[:5]][:12]}

    if results:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({
            'source': str(src.relative_to(ROOT)).replace('\\', '/'),
            'model': MODEL, 'reps': ARGS.reps,
            'caliber': '单元=(源,目标)对；值=关系类型编码，0=未列出（"未列出==明确判定不属于"，'
                       '与 tools/recompute_agreement.py 同口径）',
            'results': results,
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'已写 {OUT.relative_to(ROOT)}')
        print()
        print('=' * 78)
        print('协议阶梯（α 是否随结构化程度单调上升）')
        print('=' * 78)
        for k in sorted(results):
            r = results[k]
            a = r['alpha_fill0']
            # ⚠️ α 可能是 None（模型没产出可解析的关系，或单元不足 2 个）——
            # 第一版这里直接 `round(a, 4):<8` 抛了 TypeError: NoneType.__format__。
            atxt = 'n/a' if a is None else f'{round(a, 4)}'
            bar = '' if a is None else ('█' * max(0, int((a + 0.2) * 20)))
            print(f'  档 {k} {r["name"]:<18} α={atxt:<8} {bar}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
