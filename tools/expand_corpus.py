# -*- coding: utf-8 -*-
r"""M8 · **扩语料编排**（下载 → 抽取 → 刷新注册表），并**实测每篇耗时**。

## TODO 的原话

> | **M8** | ⬜ **扩语料计划**（28 → 100–300 篇） | **B 的直接面对** ★★★ |
> 本地 36 分钟/篇 ⇒ 100 篇约 60 小时，**可分批发**（夜间/空闲跑）。
> ⚠️ **前置**：**`arxiv.org` / `export.arxiv.org` 在当前网络全部 000（不通）**，
> 需本地代理 `127.0.0.1:7897`（见 N25） |

## ★ 实测：那个"前置"**已经不成立了**

```
https://export.arxiv.org/api/query?...  直连 ⇒ **HTTP 200 · 3.2 KB**
（走 127.0.0.1:7897 也是 200，但**不需要**）
```

⇒ **所以 M8 不必等代理。**（TODO 那句是**某个时间点之前**的状况。）
**⚠️ 这是"文档里的一句话，在某个时间点之后就不再为真"的又一例** ——
本项目已经栽过很多次（H10 的 +8.16pp、TODO 的 35 项 ⬜ 漂移、作者/脚本"缺失"）。

## ★ 两个脚本**早就有**，缺的只是"编排"

| 脚本 | 干什么 | 现状 |
|---|---|---|
| `tools/fetch_corpus.py` | 5 个检索式 × 12 篇，下载 PDF + 写 `_manifest.json` | **已有**（96 行） |
| `tools/ingest_corpus.py` | 批量抽取成图（`resume-safe`、`KG_ONLY` 可限篇、支持并行） | **已有**（142 行） |

**⇒ 本脚本做的是把它们串起来，并**实测每篇耗时**。**
TODO 里那个"36 分钟/篇"是**估的** —— 编排一次就能有**实测分布**。

## ⚠️ 为什么默认 `--dry-run`

抽取是**本地模型、约 36 分钟/篇** —— 那不是"跑一下试试"的量级。
⇒ 默认**只打印计划**（会下载什么、会抽什么、预计多久），**要 `--apply` 才真动**。

## ⚠️ 并行与显存的现实

`ingest_corpus.py` 支持 `KG_EXTRACT_WORKERS>1`，但本项目实测：
**`qwen3.5:9b` 常驻占 6496/8188 MiB = 79%**，只剩 1692 MiB（`docs/C3_OLLAMA_500_ROOT_CAUSE.md`）。
⇒ **并行度 >1 会让显存更紧**（C3 那次 HTTP 500 就怀疑是它）。
⇒ 本脚本**默认 workers=1**，并把这条写进输出。

用法：
    python tools/expand_corpus.py                      # dry-run：列出计划
    python tools/expand_corpus.py --papers 5           # 计划下 5 篇
    python tools/expand_corpus.py --apply --papers 3   # 真跑 3 篇（约 2 小时）
    python tools/expand_corpus.py --apply --only 2402.12001v1,2401.04507v1
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / 'data' / 'corpus'
GRAPHS = CORPUS / 'graphs'
MANIFEST = CORPUS / '_manifest.json'
REG = ROOT / 'web' / 'kg-data' / 'paper-registry.json'
OUT = ROOT / 'data' / 'quality' / 'expand-report.json'
PY = sys.executable

# 实测参考值（`B3_SCIREX_RELATION_RESULT.md:151` 的 `elapsed_s` 实测 1900 秒）
MIN_PER_PAPER = 36


def snapshots():
    return {f.stem for f in GRAPHS.glob('*.json') if f.stat().st_size > 0}


def pdfs():
    return {f.stem for f in CORPUS.glob('*.pdf')}


def main():
    ap = argparse.ArgumentParser(description='M8 扩语料编排（默认 dry-run）')
    ap.add_argument('--papers', type=int, default=0,
                    help='目标篇数（0 = 用 fetch_corpus 的默认 28）')
    ap.add_argument('--only', default='', help='只抽这几篇（逗号分隔的 stem）')
    ap.add_argument('--apply', action='store_true', help='真跑（默认只打印计划）')
    ap.add_argument('--skip-fetch', action='store_true', help='跳过下载，只抽取')
    ap.add_argument('--workers', type=int, default=1,
                    help='抽取并行度（**默认 1** —— 显存只剩 1692 MiB，见 docstring）')
    a = ap.parse_args()
    t0 = time.time()
    print('=' * 92)
    print('M8 · 扩语料编排（下载 → 抽取 → 刷新注册表）')
    print('=' * 92)

    have_pdf, have_snap = pdfs(), snapshots()
    print(f'  现状：PDF {len(have_pdf)} 篇 · 已抽取 {len(have_snap)} 篇')
    # **待抽取** = 有 PDF 但没图
    todo = sorted(have_pdf - have_snap)
    print(f'  **已下载但没抽取的：{len(todo)} 篇**')
    if todo:
        print(f'      {todo[:8]}{" …" if len(todo) > 8 else ""}')

    plan = []
    if a.only:
        want = [x.strip() for x in a.only.split(',') if x.strip()]
        missing = [x for x in want if x not in have_pdf]
        if missing:
            print(f'  ⚠️ 还没下载的：{missing} ⇒ 会被跳过（先跑下载）')
        plan = [x for x in want if x in have_pdf]
    elif todo:
        plan = todo
    print()
    print(f'  计划抽取 {len(plan)} 篇'
          + (f' ⇒ 预计 **{len(plan) * MIN_PER_PAPER} 分钟**'
             f'（按 {MIN_PER_PAPER} 分钟/篇）' if plan else ''))

    est_fetch = (a.papers or 28) - len(have_pdf)
    if est_fetch > 0 and not a.skip_fetch:
        print(f'  计划新增下载 ≤ {est_fetch} 篇 ⇒ 预计 **{est_fetch * 6} 秒**'
              f'（3 秒礼貌间隔 + 下载）')
    print()
    print('  ★ 显存现实（**这是"能不能并行"的答案**）：')
    print('      `qwen3.5:9b` 常驻 **6496 / 8188 MiB = 79%**，只剩 **1692 MiB**')
    print(f'      ⇒ 本脚本默认 **workers={a.workers}**；调高会让显存更紧')
    print('      （C3 那次的 HTTP 500 就怀疑是它：`docs/C3_OLLAMA_500_ROOT_CAUSE.md`）')

    if not a.apply:
        print()
        print('  （**dry-run** —— 没有动任何东西。真跑请加 `--apply`）')
        print('  ⚠️ 抽取是**本地模型、约 36 分钟/篇**，且**必须 Ollama 在跑**。')
        return 0

    # ── 真跑 ──────────────────────────────────────────────────────
    log = {'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
           'before': {'pdf': len(have_pdf), 'snapshots': len(have_snap)},
           'steps': []}

    if not a.skip_fetch:
        n = str(a.papers) if a.papers else '28'
        print(f'\n  ▶ 阶段 1 · 下载（`fetch_corpus.py {n}`）')
        t1 = time.time()
        p = subprocess.run([PY, str(ROOT / 'tools' / 'fetch_corpus.py'), n],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', cwd=str(ROOT))
        out = (p.stdout or '') + (p.stderr or '')
        for ln in out.strip().splitlines()[-6:]:
            print('      ' + ln[:110])
        log['steps'].append({'stage': 'fetch', 'rc': p.returncode,
                             'seconds': round(time.time() - t1, 1),
                             'tail': out.strip().splitlines()[-4:]})
        if p.returncode != 0:
            print('      ❌ 下载失败 ⇒ 停下（不带着半截状态往下走）')
            return 1

    if plan:
        print(f'\n  ▶ 阶段 2 · 抽取（`ingest_corpus.py`，workers={a.workers}）')
        print(f'      限这几篇：{plan[:6]}{" …" if len(plan) > 6 else ""}')
        print(f'      ⇒ **预计 {len(plan) * MIN_PER_PAPER} 分钟**（会按篇打印进度）')
        env = dict(os.environ, KG_ONLY=','.join(plan),
                   KG_EXTRACT_WORKERS=str(a.workers))
        t2 = time.time()
        p = subprocess.run([PY, str(ROOT / 'tools' / 'ingest_corpus.py'),
                            str(CORPUS), os.environ.get('KG_INGEST_MODE', 'local')],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', cwd=str(ROOT), env=env)
        out = (p.stdout or '') + (p.stderr or '')
        dt = time.time() - t2
        for ln in out.strip().splitlines()[-8:]:
            print('      ' + ln[:110])
        log['steps'].append({'stage': 'ingest', 'rc': p.returncode,
                             'seconds': round(dt, 1), 'planned': len(plan),
                             'tail': out.strip().splitlines()[-6:]})
        print(f'      ⇒ 实耗 **{dt / 60:.1f} 分钟**（计划时按 '
              f'{len(plan) * MIN_PER_PAPER} 分钟估）')

    # ── 刷新注册表 ────────────────────────────────────────────────
    print('\n  ▶ 阶段 3 · 刷新注册表（`build_paper_registry.py`）')
    p = subprocess.run([PY, str(ROOT / 'tools' / 'build_paper_registry.py')],
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace', cwd=str(ROOT))
    for ln in ((p.stdout or '') + (p.stderr or '')).strip().splitlines()[-3:]:
        print('      ' + ln[:110])
    log['steps'].append({'stage': 'registry', 'rc': p.returncode})

    after_pdf, after_snap = pdfs(), snapshots()
    log['after'] = {'pdf': len(after_pdf), 'snapshots': len(after_snap)}
    log['delta'] = {'pdf': len(after_pdf) - len(have_pdf),
                    'snapshots': len(after_snap) - len(have_snap)}
    print()
    print(f'  ★ 结果：PDF {len(have_pdf)} → **{len(after_pdf)}** · '
          f'已抽取 {len(have_snap)} → **{len(after_snap)}**')
    ing = [s for s in plan if s in after_snap]
    if plan:
        per = (time.time() - t0) / max(1, len(plan))
        log['per_paper_seconds'] = round(per, 1)
        print(f'  ★ **实测每篇 {per / 60:.1f} 分钟**'
              f'（TODO 里写的是 {MIN_PER_PAPER} 分钟）· 成功 {len(ing)}/{len(plan)}')
        print(f'      ⇒ 由此推算：抽 100 篇约 **{per * 100 / 3600:.0f} 小时**')

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n  已写出 {OUT.relative_to(ROOT)}  {time.time() - t0:.1f}s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
