"""针对性重抽：只重跑"漏抽集中"的那几年论文。

【为什么需要这个脚本，而不是直接全量重跑】
  L9 关系召回分析发现漏抽**不是均匀的**：

      2023 及以前  20 条金标，漏 6 条（30%）
      2024 及以后  22 条金标，漏 16 条（**73%**）

  差了 43 个百分点，而且漏抽集中在 4 篇（`2406.08223v2`、`2510.20345v1`、
  `2401.04507v1`、`2501.03566v1`，漏率都是 100%）。

  也就是说：**问题不在"抽取器整体不行"，而在"较新的论文没抽好"**。
  （可推测的原因：新论文大量使用 `LLMs enable three key mechanisms…`
   这类表述，而抽取 prompt 是在更早的语料上调的。）

  全量重抽是 21 篇 × 约 2 小时 ≈ **42 小时**；
  只重抽 2024+ 的 8 篇约 **6.4 小时**。**成本差 5 倍。**
  （原写「全量重抽是 **17** 篇 × 约 2 小时 ≈ **34** 小时」——
   那是 `data/corpus/graphs/` 还只有 17 篇英文快照时的读数；
   现为 21 篇（17 篇 arXiv + 4 篇中文期刊出图），篇数与时长同步更正。
   ⚠️ 「2024+ 的 8 篇」仍是**当时的篇数**，现 2024+ 已不止 8 篇，需重数。）

【为什么默认不真跑】
  · 需要本机 Ollama（`qwen3.5:9b`）在线，实测要 ~6.1 GB 显存；
  · 这是**本地零成本**的，但会占用机器数小时；
  · 所以默认 dry-run，列出"会重抽哪些、预计多久"，由人决定何时启动。

用法：
  python tools/re_extract_recent.py                # dry-run：列出目标与耗时
  python tools/re_extract_recent.py --since 2024   # 换个年份阈值
  python tools/re_extract_recent.py --apply        # 真的跑（需 Ollama）
"""
import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / 'data' / 'corpus'
GRAPHS = CORPUS / 'graphs'
BACKUP = ROOT / 'data' / 'quality' / 'backup'

# 实测：Ollama qwen3.5:9b 抽取约 20.9 秒/chunk（见早先的评审记录 §1）
SEC_PER_CHUNK = 20.9


def year_of(stem: str):
    m = re.match(r'^(\d{2})\d{2}\.', stem)
    return 2000 + int(m.group(1)) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', type=int, default=2024, help='重抽这一年及以后的论文')
    ap.add_argument('--apply', action='store_true', help='真的跑（需本机 Ollama）')
    ap.add_argument('--batch-chars', type=int, default=500)
    args = ap.parse_args()

    snaps = sorted(GRAPHS.glob('*.json'))
    targets = []
    for f in snaps:
        y = year_of(f.stem)
        if y is None:
            continue
        d = json.loads(f.read_text(encoding='utf-8'))
        chunks = ((d.get('stats') or {}).get('chunks')) or 0
        targets.append({'stem': f.stem, 'year': y, 'chunks': chunks,
                        'path': str(f),
                        'est_min': round(chunks * SEC_PER_CHUNK / 60)})

    recent = [t for t in targets if t['year'] >= args.since]
    old = [t for t in targets if t['year'] < args.since]

    print(f'快照共 {len(targets)} 篇；阈值 {args.since}+\n')
    print(f'{"论文":18s} {"年":>5s} {"chunk":>6s} {"预计":>7s}')
    print('-' * 44)
    for t in sorted(recent, key=lambda x: -x['chunks']):
        print(f'  {t["stem"]:16s} {t["year"]:5d} {t["chunks"]:6d} {t["est_min"]:5d} 分')
    tot_chunks = sum(t['chunks'] for t in recent)
    tot_min = sum(t['est_min'] for t in recent)
    print(f'\n  目标 {len(recent)} 篇 / {tot_chunks} chunk，'
          f'预计 **{tot_min} 分钟 ≈ {tot_min/60:.1f} 小时**')
    print(f'  对照：全量 {len(targets)} 篇 / {sum(t["chunks"] for t in targets)} chunk，'
          f'约 {sum(t["est_min"] for t in targets)/60:.1f} 小时')
    print(f'  ⇒ 省下约 {1 - tot_min/max(1,sum(t["est_min"] for t in targets)):.0%} 的时间')

    print('\n【前置条件】')
    print('  · 本机 Ollama 服务在跑（`ollama serve`），模型 qwen3.5:9b 已 pull')
    print('  · 显存需约 6.1 GB 空闲（实测 8 GB 卡上与其他进程会冲突）')
    print('  · 全程本地，**零 API 成本**')

    if not args.apply:
        print('\n[dry-run] 加 --apply 才会真的重抽。')
        print('重抽会覆盖 data/corpus/graphs/<论文>.json，脚本会先整目录备份。')
        return 0

    # ── 真跑 ──
    try:
        import urllib.request
        with urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=4) as r:
            tags = json.load(r)
    except Exception as e:
        print(f'\n❌ Ollama 不可达（{type(e).__name__}）：请先 `ollama serve`。'
              f'本次不做任何改动。')
        return 1

    names = [m.get('name', '') for m in (tags.get('models') or [])]
    if not any('qwen3.5' in n for n in names):
        print(f'\n❌ 没找到 qwen3.5 模型（现有：{names}）。本次不做任何改动。')
        return 1

    ts = time.strftime('%Y%m%d-%H%M%S')
    bak = BACKUP / f'graphs-{ts}-preReextract'
    bak.mkdir(parents=True, exist_ok=True)
    for t in recent:
        shutil.copy2(t['path'], bak / f'{t["stem"]}.json')
    print(f'\n已备份 {len(recent)} 个快照到 {bak}')

    sys.path.insert(0, str(ROOT / 'src'))
    from pipeline import extract_graph  # noqa: E402

    ok = fail = 0
    for t in sorted(recent, key=lambda x: -x['chunks']):
        pdf = CORPUS / f'{t["stem"]}.pdf'
        if not pdf.exists():
            print(f'  ⏭ {t["stem"]}：找不到 PDF，跳过')
            fail += 1
            continue
        outdir = ROOT / 'data' / f'_reextract_{t["stem"]}'
        outdir.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        print(f'  ▶ {t["stem"]}（{t["chunks"]} chunk，预计 {t["est_min"]} 分）…',
              flush=True)
        try:
            extract_graph(str(pdf), str(outdir), t['stem'], f'{t["stem"]}.pdf',
                          mode='local', max_chars=args.batch_chars)
            dt = (time.time() - t0) / 60
            print(f'     ✅ 完成，用时 {dt:.1f} 分')
            ok += 1
        except Exception as e:
            print(f'     ❌ 失败：{type(e).__name__}: {e}')
            fail += 1

    print(f'\n重抽完成：成功 {ok} / 失败 {fail}')
    print(f'备份在 {bak}（如需回滚，把里面的文件拷回 data/corpus/graphs/）')
    print('\n⚠️ 重抽只改了快照；**还要按 RUNBOOK §6 重跑下游**才有意义：')
    print('   python tools/freshness_report.py --fix   # 自动重建可自动化的部分')
    print('   python tools/relation_verifier.py --all  # L5 重新打分')
    return 0


if __name__ == '__main__':
    sys.exit(main())
