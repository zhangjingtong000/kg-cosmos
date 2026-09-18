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
import re
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
    """抽取的环境。

    ⚠️⚠️ **警告：这里设的三个变量，目前没有任何代码读它们。**（2026-09-18 实测）

    ```
    grep 全仓库：KG_PROMPT_VARIANT / KG_SEED / OLLAMA_SEED 各出现 1 次 ——
                 都在本文件里（**写**），**没有一处**读**。
    ```

    ⇒ 所以上一次跑出来的六份产物，**用的是同一个 prompt** ——
      **"旧 vs 新"的对比根本没有发生**（详见 `docs/B5_PROMPT_AB_MULTISEED.md §〇`）。

    **⇒ 那个 bug 意外产出了一个更硬的结论**（同一配置重复六次，F1 极差 4.08pp），
      但**不能就这么留着** —— 见下面 `assert_variant_wired()`：
      **设了变量就要验证它真被读到，否则那是一次"看起来做了 A、实际做了 B"的运行。**

    **要真跑 A/B，先做这件事**：在 `src/kg_extractor.py` 里加一个分支
    （它已经有 `ENTITY_PROMPT` / `ENTITY_PROMPT_V2` / `ENTITY_PROMPT_V3` 三份现成的），
    读 `KG_PROMPT_VARIANT` 并把选中的那份打印出来。
    """
    e = dict(os.environ)
    e['KG_PROMPT_VARIANT'] = 'new' if seed.get('variant') == 'new' else 'old'
    e['KG_SEED'] = str(seed.get('seed', 0))
    e['OLLAMA_SEED'] = str(seed.get('seed', 0))
    return e


def variant_is_wired():
    """`KG_PROMPT_VARIANT` **真的**被读到了吗？

    **判据是"源码里有没有读它的地方"**，不是"变量设没设成功" ——
    **后者一定会成功，而那正是上一次踩的坑。**
    """
    src = ROOT / 'src'
    for f in list(src.glob('*.py')) + [Path(__file__)]:
        if f.resolve() == Path(__file__).resolve():
            continue                       # **自己的写入不算"被读到"**
        try:
            t = f.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        if re.search(r'os\.environ(?:\.get)?\s*[\(\[]\s*[\'"]KG_PROMPT_VARIANT', t) \
           or re.search(r'getenv\s*\(\s*[\'"]KG_PROMPT_VARIANT', t):
            return True, str(f.relative_to(ROOT))
    return False, None


def main():
    ap = argparse.ArgumentParser(description='B5 prompt A/B 多 seed（默认 dry-run）')
    ap.add_argument('--seeds', type=int, default=3, help='每个 prompt 跑几个 seed')
    ap.add_argument('--variants', default='old,new', help='跑哪几个 prompt 版本')
    ap.add_argument('--apply', action='store_true', help='真跑（默认只打印计划）')
    ap.add_argument('--report', action='store_true', help='只看已有结果')
    # ★ `--resume`：**跳过已经产出的那几次**（2026-09-18 加）。
    #   为什么需要：每次抽取约 36 分钟，而 6 次里可能已经跑完一两次；
    #   没有这个开关就只能**从头再来**（白烧 36 分钟/次 的 GPU 时间）。
    #   ⚠️ 判定是"`<tag>.json` 存在**且非空**" —— **不是"跑过就算"**
    #      （若上一次中途被杀，可能留下半截文件；那种情况必须重跑）。
    # --same-prompt：**只测采样方差**（不换 prompt）。
    #   本项已有的结论（同一配置重复六次 F1 极差 4.08pp）正来自这种跑法 ——
    #   它**不需要** `KG_PROMPT_VARIANT` 生效。
    ap.add_argument('--same-prompt', action='store_true',
                    help='只测采样方差（不换 prompt，不检查变体是否接通）')
    ap.add_argument('--resume', action='store_true',
                    help='跳过已有产物的那几次（按 <tag>.json 是否存在且非空判定）')
    a = ap.parse_args()
    t0 = time.time()
    print('=' * 92)
    print('B5 · prompt A/B 多 seed（**把 −4.5pp 拆成真效应与 seed 噪声**）')
    print('=' * 92)

    # ⚠️ **只有 `--report` 才读旧结果**。
    #
    # 第一版写的是 `if a.report or OUT.exists():` ⇒ **只要结果文件在就打印它并 `return 0`**
    # ⇒ 于是"重启 B5"实际是**把上次那 6 条失败记录又打印了一遍**
    #   （GPU 0%、无新产物，我一开始还以为它在慢慢跑）。
    # **⇒ 一个"看结果"的分支不该挡住"再跑一次"。**
    if a.report:
        if OUT.exists():
            d = json.loads(OUT.read_text(encoding='utf-8'))
            runs = d.get('runs') or []
            n_ok = sum(1 for r in runs if r.get('rc') == 0)
            print(f'  已有结果（{d.get("generated")}）：{len(runs)} 次，'
                  f'**成功 {n_ok} / 失败 {len(runs) - n_ok}**')
            if runs and n_ok == 0:
                print('  ⚠️ **上一次全都失败了** ⇒ 那些记录不能当结论用；'
                      '修完原因后直接重跑（**不带 `--report`**）。')
            for k, v in (d.get('summary') or {}).items():
                print(f'      {k}: {json.dumps(v, ensure_ascii=False)[:110]}')
        else:
            print('  还没有结果（没跑过）')
        return 0

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
    # ★★ **跑之前先验"变体真的生效"**（2026-09-18 加）。
    #   上一次跑完六次（3 小时）之后才发现"没人读那个变量" ⇒ **白跑**。
    #   ⇒ 把这个检查放在**开始之前**，而且**它失败就拒绝往下走**。
    wired, where = variant_is_wired()
    if not wired and len(variants) > 1 and not a.same_prompt:
        print()
        print('  ❌ **`KG_PROMPT_VARIANT` 没有任何代码读它** ⇒ 拒绝运行。')
        print('     为什么：那意味着**这几个变体会跑出同一份结果** ——')
        print('     而输出上完全看不出来（上一次就是这样白跑了 3 小时）。')
        print()
        print('     先做这一步：在 `src/kg_extractor.py` 里加分支，读 `KG_PROMPT_VARIANT`；')
        print('     （它已经有 `ENTITY_PROMPT` / `ENTITY_PROMPT_V2` / `ENTITY_PROMPT_V3` 三份现成的）')
        print('     并在抽第一份之前**打印所选 prompt 的前 40 字符** —— 那才是"生效"的证据。')
        print()
        print('     若你只是想测**采样方差**（不换 prompt），加 `--same-prompt` ——')
        print('     那仍然有意义：本项已有的结论正来自这种跑法。')
        return 2
    if wired:
        print(f'  ✅ `KG_PROMPT_VARIANT` 已被 {where} 读取')
    else:
        print('  ⚠️ **`KG_PROMPT_VARIANT` 无人读** —— 但只有 1 个变体 ⇒ 那是"测采样方差"，允许')
    # ★ `--resume`：把已有产物的那几次标掉（**在半截文件上必须重跑**）
    if a.resume:
        kept = []
        for r in runs:
            tag = f'{r["variant"]}-s{r["seed"]}'
            f = RUNS / f'{tag}.json'
            r['_done'] = f.exists() and f.stat().st_size > 1024
        kept = [r for r in runs if r.get('_done')]
        todo = [r for r in runs if not r.get('_done')]
        print(f'  **--resume**：已有产物 {len(kept)} 次（跳过）· 待跑 {len(todo)} 次')
        for r in kept:
            f = RUNS / f'{r["variant"]}-s{r["seed"]}.json'
            print(f'      ⏭  {r["variant"]}-s{r["seed"]}（{f.stat().st_size / 1024:.0f} KB）')
        runs = todo
        if not runs:
            print('  ✅ 六次都已完成 ⇒ 没有要跑的；直接用 `--report` 看结果。')
            return 0
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
    subject = '2012.00456v1'
    snapshot = ROOT / 'data' / 'corpus' / 'graphs' / f'{subject}.json'
    # ★★ **必须先让 ingest 愿意重抽**（2026-09-17 修）：
    #   `ingest_corpus.py` 的 `is_usable_snapshot` 认为"已有非空图 ⇒ 已完成"，
    #   于是六次全部打出 `[1/1] skip (done)` —— **一次都没真抽**，
    #   而 rc=0、看起来"成功"。**那是最容易被误读成"跑通了"的假象。**
    #   ⇒ 本脚本在每次抽取前把该篇的图**归档移走**（**按 `ARCHIVE_POLICY`：不删，留档**），
    #     抽完再恢复 —— 而每次的产物另存为 `<tag>.json` 供后续算 F1。
    archived = None
    if snapshot.exists():
        archived = RUNS / '_before.json'
        if not archived.exists():
            archived.write_bytes(snapshot.read_bytes())
        print(f'  已有图 {snapshot.name} ⇒ **归档到 {archived.name} 并移走**'
              f'（否则 ingest 会 skip）')

    def fresh_extract(tag, env):
        """**强制重抽**（`KG_FORCE=1`）→ 把产物另存为 `<tag>.json`。

        ⚠️ **第一版是"把原图移走再抽"** —— 而那样有一个很坏的副作用：
        `data/corpus/graphs/2012.00456v1.json` **在那段时间里不存在** ⇒
        · `pytest` 的 `test_paper_names_match_snapshots` **当场报红**
          （`papers` 里提到的名字在快照目录里找不到）；
        · 前端与 `verify_all` 里依赖那份图的检查也会红。
        **⇒ 一个跑实验的动作，把整个仓库变成了不一致状态。**

        ⇒ 改成 `KG_FORCE=1`：**强制重抽，但原图一直在**
        （`ingest_corpus.py` 会先把原图备份成 `.json.bak`，抽完覆盖它）。
        """
        e = dict(env)
        e['KG_FORCE'] = '1'
        p = subprocess.run(
            [PY, str(ROOT / 'tools' / 'ingest_corpus.py'),
             str(ROOT / 'data' / 'corpus'), os.environ.get('KG_INGEST_MODE', 'local')],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            cwd=str(ROOT), env=e)
        got = RUNS / f'{tag}.json'
        if snapshot.exists():
            got.write_bytes(snapshot.read_bytes())
        return p, got

    for i, r in enumerate(runs):
        tag = f'{r["variant"]}-s{r["seed"]}'
        print(f'\n  ▶ [{i + 1}/{len(runs)}] {tag}（约 {MIN_PER_RUN} 分钟）')
        e = env_for(r)
        e['KG_ONLY'] = subject
        t1 = time.time()
        p, got = fresh_extract(tag, e)
        dt = time.time() - t1
        tail = ((p.stdout or '') + (p.stderr or '')).strip().splitlines()[-3:]
        for ln in tail:
            print('      ' + ln[:104])
        n_nodes = 0
        if got.exists():
            try:
                n_nodes = len(json.loads(got.read_text(encoding='utf-8')).get('nodes') or [])
            except Exception:
                n_nodes = -1
        results.append({'variant': r['variant'], 'seed': r['seed'],
                        'rc': p.returncode, 'seconds': round(dt, 1),
                        'tag': tag, 'nodes': n_nodes,
                        'artifact': str(got.relative_to(ROOT)) if got.exists() else None})
        print(f'      ⇒ {dt / 60:.1f} 分钟 · rc={p.returncode} · 节点 {n_nodes}')

    # 恢复原图（**抽出来的那些已经另存了**）
    if archived and archived.exists() and not snapshot.exists():
        snapshot.write_bytes(archived.read_bytes())
        print(f'\n  ✅ 已恢复原图 {snapshot.name}（各次产物在 {RUNS.name}/）')

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
