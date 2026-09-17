# -*- coding: utf-8 -*-
r"""B5 · **prompt A/B 多 seed** —— 把「−4.5pp」拆成"真效应"与"seed 噪声"。

## TODO 的原话

> | **B5** | ⬜ **prompt A/B 多 seed** |
> A/B 是混合结果（碎片 −58% ✓、关系 F1 +39% ✓、**实体 F1 −4.5pp** ✗）。
> **只有一篇、没做多 seed**，−4.5pp 里真效应占多少分不出来 |
> 对新旧 prompt 各跑多个 seed，看均值与方差 | 数小时

## ★ 为什么要做：**单次实验分不清"效应"与"噪声"**

`OPTIMIZATION_PLAN.md` 的实测（`2012.00456v1`，一次）：

| 指标 | 旧 prompt | 新 prompt | 变化 |
|---|---|---|---|
| **实体 F1** | 0.3918 | 0.3471 | **−0.0447** |
| 关系 F1 | 0.0128 | 0.0178 | +0.0050（相对 +39%） |
| **长标题碎片** | 24 | 10 | **−14（−58%）** |

**⇒ 结论写成"混合"，而它的局限那一行才是关键**：
> 只有一篇、未做多 seed，**−4.5pp 里有多少是真效应分不出来**。

**⇒ 本脚本就是去分那 4.5pp。** 判据很直接：

```
若 新 prompt 的 3 个 seed 的 F1 全都 < 旧 prompt 的 3 个 seed 的最小值
   ⇒ **效应大于噪声**（区间不重叠）
若两者区间大幅重叠
   ⇒ **那 4.5pp 很可能只是 seed 噪声** —— 而"混合结果"这个结论要改写
```

## ★★ 为什么默认 `--dry-run`：这一项真的要数小时

`2012.00456v1` 一次抽取 **36 分钟**（`B3_SCIREX_RELATION_RESULT.md:151` 实测 1900 秒）。
⇒ **2 个 prompt × 3 个 seed = 6 次抽 ≈ 3.6 小时**，**而且它独占 GPU 与 Ollama**。

⇒ **这一项不是"点一下"的量级** ⇒ 默认只打印计划与预计时间。

## ★ 它复用什么（**不重写抽取**）

- 抽取：`src/pipeline.py` 的 `extract_graph`（**与 pipeline 同一条路径**）；
- 金标：`data/gold/gold-2012.00456v1.json`（**该篇有金标**，这是它能算 F1 的原因）；
- 指标：`tools/l5_report.py` 口径的 P/R/F1（**不另造一套**）；
- 碎片计数：`OPTIMIZATION_PLAN.md` 里那个"长标题碎片"的判据。

## ⚠️ 一个必须先说清的边界

**"多个 seed"在这里意味着什么**：本地 Ollama 的采样带随机性（`temperature>0`），
⇒ 同一个 prompt 跑多次会得到不同的图。**本脚本通过设 `seed` 环境变量/参数来固定或改变它。**
**但 Ollama 的 seed 支持取决于版本** —— 若它忽略 seed，那"多个 seed"实际上
只是**重复采样**，**方差仍然可测**（那正是我们要的），但**不能声称"复现了同一 seed"**。
**脚本会在输出里如实标注这一点。**

用法：
    python tools/prompt_ab_multiseed.py                    # dry-run：计划 + 预计时间
    python tools/prompt_ab_multiseed.py --seeds 3 --apply   # 真跑（约 3.6 小时）
    python tools/prompt_ab_multiseed.py --report            # 只看已有的结果
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / 'data' / 'gold' / 'gold-2012.00456v1.json'
PDF = ROOT / 'data' / 'corpus' / '2012.00456v1.pdf'
OUT = ROOT / 'data' / 'quality' / 'prompt-ab-multiseed.json'
RUNS = ROOT / 'data' / 'quality' / '_prompt-ab-runs'
PY = sys.executable

# 实测（`B3_SCIREX_RELATION_RESULT.md:151`：`elapsed_s` 1900 秒）
MIN_PER_RUN = 36
# 旧 prompt 那一次的实测（`OPTIMIZATION_PLAN.md`）—— **作为 only-one-seed 的基线**
BASELINE = {'entity_f1': 0.3918, 'relation_f1': 0.0128, 'fragments': 24,
            'source': 'OPTIMIZATION_PLAN.md（旧 prompt，1 次）'}
NEW_ONE_SHOT = {'entity_f1': 0.3471, 'relation_f1': 0.0178, 'fragments': 10,
                'source': 'OPTIMIZATION_PLAN.md（新 prompt，1 次）'}


def env_for(seed):
    """抽取的环境：**显式传 seed 与 prompt 版本**。"""
    e = dict(os.environ)
    e['KG_PROMPT_VARIANT'] = 'new' if seed.get('variant') == 'new' else 'old'
    e['KG_SEED'] = str(seed.get('seed', 0))
    # 让 Ollama 的采样也换种子（**若版本不支持，会被忽略 —— 见 docstring 的边界**）
    e['OLLAMA_SEED'] = str(seed.get('seed', 0))
    return e


def main():
    ap = argparse.ArgumentParser(description='B5 prompt A/B 多 seed（默认 dry-run）')
    ap.add_argument('--seeds', type=int, default=3, help='每个 prompt 跑几个 seed')
    ap.add_argument('--variants', default='old,new', help='跑哪几个 prompt 版本')
    ap.add_argument('--apply', action='store_true', help='真跑（默认只打印计划）')
    ap.add_argument('--report', action='store_true', help='只看已有结果')
    a = ap.parse_args()
    t0 = time.time()
    print('=' * 92)
    print('B5 · prompt A/B 多 seed（**把 −4.5pp 拆成真效应与 seed 噪声**）')
    print('=' * 92)

    if a.report or OUT.exists():
        if OUT.exists():
            d = json.loads(OUT.read_text(encoding='utf-8'))
            print(f'  已有结果（{d.get("generated")}）')
            for k, v in (d.get('summary') or {}).items():
                print(f'      {k}: {json.dumps(v, ensure_ascii=False)[:110]}')
            return 0
        if a.report:
            print('  还没有结果（没跑过）')

    variants = [x.strip() for x in a.variants.split(',') if x.strip()]
    runs = [{'variant': v, 'seed': s}
            for v in variants for s in range(a.seeds)]
    print(f'  金标：{GOLD.relative_to(ROOT) if GOLD.exists() else "❌ 缺"}')
    print(f'  PDF ：{PDF.relative_to(ROOT) if PDF.exists() else "❌ 缺"}')
    if not GOLD.exists() or not PDF.exists():
        print('  ❌ 缺金标或 PDF ⇒ **这一项做不了**（没有金标就算不出 F1）')
        return 1
    print()
    print(f'  计划：{len(variants)} 个 prompt × {a.seeds} 个 seed = **{len(runs)} 次抽取**')
    for r in runs:
        print(f'      {r["variant"]:<5} seed={r["seed"]}')
    print(f'  ⇒ 按 **{MIN_PER_RUN} 分钟/次** 估：**{len(runs) * MIN_PER_RUN} 分钟'
          f'（{len(runs) * MIN_PER_RUN / 60:.1f} 小时）**')
    print()
    print('  ★ 判据（跑完之后才知道结论）：')
    print('      · 新 prompt 的 F1 区间**与旧的完全不重叠** ⇒ **效应大于噪声**')
    print('      · 两者**大幅重叠** ⇒ 那 4.5pp 很可能只是 seed 噪声，')
    print('        「混合结果」这个结论**要改写**')
    print()
    print('  ⚠️ 边界：**Ollama 是否真的支持 seed 取决于版本**。')
    print('      若它忽略 seed，那"多 seed"实际只是**重复采样** ——')
    print('      **方差仍然可测**（那正是我们要的），但**不能声称"复现了同一 seed"**。')
    print('      脚本会在结果里如实标注这一点。')

    if not a.apply:
        print()
        print('  （**dry-run** —— 没有动任何东西。真跑请加 `--apply`）')
        print('  ⚠️ 这一项**独占 GPU 与 Ollama 约 %d 小时**，'
              '且需要 Ollama 在跑。' % (len(runs) * MIN_PER_RUN // 60))
        return 0

    # ── 真跑 ──────────────────────────────────────────────────────
    RUNS.mkdir(parents=True, exist_ok=True)
    results = []
    for i, r in enumerate(runs):
        tag = f'{r["variant"]}-s{r["seed"]}'
        print(f'\n  ▶ [{i + 1}/{len(runs)}] {tag}（约 {MIN_PER_RUN} 分钟）')
        out = RUNS / f'{tag}.json'
        e = env_for(r)
        e['KG_ONLY'] = '2012.00456v1'
        t1 = time.time()
        # **复用 pipeline 的批抽取**（不重写抽取逻辑）
        p = subprocess.run(
            [PY, str(ROOT / 'tools' / 'ingest_corpus.py'), str(ROOT / 'data' / 'corpus'),
             os.environ.get('KG_INGEST_MODE', 'local')],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            cwd=str(ROOT), env=e)
        dt = time.time() - t1
        tail = ((p.stdout or '') + (p.stderr or '')).strip().splitlines()[-3:]
        for ln in tail:
            print('      ' + ln[:104])
        results.append({'variant': r['variant'], 'seed': r['seed'],
                        'rc': p.returncode, 'seconds': round(dt, 1),
                        'tag': tag})
        print(f'      ⇒ {dt / 60:.1f} 分钟 · rc={p.returncode}')

    payload = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'purpose': 'B5：prompt A/B 多 seed —— 把 −4.5pp 拆成真效应与 seed 噪声',
        'subject': '2012.00456v1（**该篇有金标**，这是能算 F1 的原因）',
        'design': {
            'why': '单次实验分不清"效应"与"噪声"：TODO 自己写着'
                   '「只有一篇、未做多 seed，−4.5pp 里有多少是真效应分不出来」。'
                   '**本脚本就是去分那 4.5pp**：看两个区间是否重叠。',
            'reuses_pipeline':
                '抽取复用 `tools/ingest_corpus.py` / `src/pipeline.py`（**同一条路径**），'
                '**不重写抽取** —— 否则 A/B 比的就不是 prompt 了。',
            'seed_caveat':
                '⚠️ **Ollama 是否真的支持 seed 取决于版本**。若它忽略 seed，'
                '那"多 seed"实际只是**重复采样** —— **方差仍然可测**（那正是我们要的），'
                '但**不能声称"复现了同一 seed"**。',
            'cost': f'实测 **{MIN_PER_RUN} 分钟/次**（`B3_SCIREX_RELATION_RESULT.md:151`），'
                    f'{len(runs)} 次 ≈ **{len(runs) * MIN_PER_RUN} 分钟**，'
                    '且**独占 GPU 与 Ollama**',
        },
        'baseline_one_shot': {'old': BASELINE, 'new': NEW_ONE_SHOT},
        'runs': results,
        'summary': {
            'n_runs': len(results),
            'note': 'F1 的均值与方差需要**从各次产物重算** —— '
                    '用 `tools/l5_report.py` 与碎片计数脚本，'
                    '**本脚本只负责"跑出多次"，不自己造一套指标口径**',
        },
        'elapsed_s': round(time.time() - t0, 1),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n  已写出 {OUT.relative_to(ROOT)}')
    print('  ★ 下一步（**本脚本刻意不做**）：用 `l5_report` 口径重算每次的 F1，'
          '再看两个区间是否重叠 —— **那才是 B5 的结论**。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
