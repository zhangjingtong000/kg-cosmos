# -*- coding: utf-8 -*-
r"""清洗 `data/distill/` 的蒸馏数据集 —— H5 的第一步。

## 探查发现的三个问题（都不是猜的，是 `_probe_distill_data.py` 量出来的）

**① 断行**（`entities.jsonl` 4 条 · `relations.jsonl` 1 条）：
某行的 `response` 里含**真实换行**，而写 jsonl 时没转义 ⇒ 一条记录被拆成两行。
```
{'e<|>KG-Aware<|>Concept<|>Knowledge graph-aware learning.\n<|COMPLETE|>", "meta":'
```
⇒ **判据**：**不以 `{` 开头的行 ⇒ 拼回上一行。**

**② 空 `response`**：`entities` 里 **940/4137（22.7%）** 的 `response` 是空的，
`relations` 里 2/3181。**那是失败的调用** ⇒
**必须过滤** —— 否则微调出来的模型会学会"输出空"，
而那种失败**在训练时不会报错、只在评测时表现为"什么都不输出"**。

**③ 版本混杂的可能**：`meta` 里有 `model`/`mode`/`kind`，
⇒ 要统计一下，**不能把不同来源的输出混在一起训练而不说明**。

## 只读输入，另写输出

输入 `data/distill/*.jsonl` **一个字节都不改**（按 `ARCHIVE_POLICY`），
清洗结果写到 `data/distill/clean/`。

用法：
    python tools/clean_distill_data.py                # 只统计
    python tools/clean_distill_data.py --write        # 另写清洗后的 jsonl
"""
import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / 'data' / 'distill'
OUTDIR = D / 'clean'


def parse_args():
    ap = argparse.ArgumentParser(description='清洗蒸馏数据集（H5）')
    ap.add_argument('--write', action='store_true', help='写出清洗后的 jsonl')
    ap.add_argument('--min-response', type=int, default=1,
                    help='response 至少多少字符才算有效（默认 1，即非空）')
    return ap.parse_args()


ARGS = parse_args()


def read_records(path):
    """读 jsonl，修复**能修的**、丢弃**修不了的**，并如实报告。

    ## 损坏形态（都是在这份数据上量出来的，不是假想）

    ① **断成两行**：`response` 里的换行没转义 ⇒ 一条记录被拆开。
       判据：**一行不以 `{` 开头 ⇒ 它是上一行的延续**。
    ② **粘在一行**：两条记录挤在同一行 ⇒ `Extra data: line 2 column 1`。
       判据：用 `raw_decode` 把一行里的多个 JSON 依次切出来。

    ## 还有一种**修不了的**，本次数据里真有（6 条）

    实测 `entities.jsonl` 的 `L2660/L2661`：
    ```
    L2660 len=3410  '{"task": "entities", "prompt": "...'   ← 单独看是「合法 JSON + 尾部残留」
    L2661 len= 165  'e<|>KG-Aware<|>...<|COMPLETE|>", "meta": {...'  ← 半条的尾部
    L2662 len=2839  '{"task": "entities", ...'               ← 下一条正常
    ```
    **中间那段（真正的 response 主体）已经丢了。**
    ⇒ **为 6 条数据写"缝合半条记录"的启发式，风险大于收益** ——
    拼错了会把垃圾喂进训练集，而且**不会报错**。
    ⇒ **整条丢弃，并报告条数与样本**。这符合"宁可少，不可错"。

    返回 `(records, bad, n_fixed)`。
    """
    raw = path.read_text(encoding='utf-8-sig', errors='replace').splitlines()

    # 第一步：把"不以 { 开头"的行拼回上一行
    joined, n_join = [], 0
    for line in raw:
        s = line.rstrip('\n')
        if not s.strip():
            continue
        if s.lstrip().startswith('{'):
            joined.append(s)
        elif joined:
            joined[-1] += '\n' + s
            n_join += 1
        else:
            joined.append(s)

    # 第二步：逐条切（一行可能含多个 JSON）
    recs, bad, n_split = [], [], 0
    dec = json.JSONDecoder()
    for i, s in enumerate(joined, 1):
        rest, objs = s.strip(), []
        while rest:
            try:
                obj, end = dec.raw_decode(rest)
            except Exception as e:
                # **切不干净 ⇒ 这整段丢弃**（不做缝合）
                bad.append({'line': i, 'why': str(e)[:70], 'sample': s[:110]})
                objs = []
                break
            objs.append(obj)
            rest = rest[end:].lstrip()
        if objs:
            if len(objs) > 1:
                n_split += len(objs) - 1
            recs.extend(objs)
    return recs, bad, n_join + n_split


def clean_one(name):
    p = D / f'{name}.jsonl'
    if not p.exists():
        return None
    recs, bad, n_join = read_records(p)
    kept, dropped = [], Counter()
    for r in recs:
        resp = r.get('response')
        if not isinstance(resp, str) or len(resp.strip()) < ARGS.min_response:
            dropped['空 response'] += 1
            continue
        if not (r.get('prompt') or '').strip():
            dropped['空 prompt'] += 1
            continue
        kept.append(r)
    meta_models = Counter((r.get('meta') or {}).get('model') or '?' for r in kept)
    meta_modes = Counter((r.get('meta') or {}).get('mode') or '?' for r in kept)
    tasks = Counter(r.get('task') or '?' for r in kept)

    print('=' * 84)
    print(f'{name}.jsonl')
    print('=' * 84)
    print(f'  原始 {p.stat().st_size / 1024:.0f} KB')
    print(f'  拼接断行 {n_join} 处 ⇒ 解析出 {len(recs)} 条 · **坏行 {len(bad)} 条**')
    for b in bad[:3]:
        print(f"      ⚠️ L{b['line']}: {b['why']}")
        print(f"         {b['sample']!r}")
    print(f'  清洗后 **保留 {len(kept)} 条** · 丢弃 {sum(dropped.values())} 条'
          f'（{dict(dropped)}）')
    print(f'  来源模型 {dict(meta_models)} · 通道 {dict(meta_modes)} · 任务 {dict(tasks)}')
    if kept:
        pl = sorted(len(r['prompt']) for r in kept)
        rl = sorted(len(r['response']) for r in kept)
        print(f'  prompt   长度 中位 {int(statistics.median(pl))} · p95 {pl[int(len(pl)*.95)]} · 最大 {pl[-1]}')
        print(f'  response 长度 中位 {int(statistics.median(rl))} · p95 {rl[int(len(rl)*.95)]} · 最大 {rl[-1]}')
        # **一个必要的检查**：response 是不是"空行凑数"（有很多 \n 但内容很少）
        thin = sum(1 for r in kept if len(r['response'].strip()) < 20)
        print(f'  response 去空白后 < 20 字符的：{thin} 条'
              + ('  ⚠️ 这些几乎是空输出' if thin else ''))
    print()
    return {'name': name, 'raw': len(recs), 'joined': n_join, 'bad': len(bad),
            'kept': len(kept), 'dropped': dict(dropped),
            'models': dict(meta_models), 'modes': dict(meta_modes),
            'records': kept}


def main():
    print()
    stats = []
    for name in ('entities', 'relations', 'combined'):
        s = clean_one(name)
        if s:
            stats.append(s)

    if not ARGS.write:
        print('（只统计。加 --write 才写出清洗后的 jsonl）')
        return 0

    OUTDIR.mkdir(parents=True, exist_ok=True)
    for s in stats:
        out = OUTDIR / f'{s["name"]}.jsonl'
        with out.open('w', encoding='utf-8') as f:
            for r in s['records']:
                # **`ensure_ascii=False` + 默认转义** ⇒ 换行会写成 `\n` 而不是真换行，
                # 从根上避免"一条记录被拆成两行"再次发生
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
        print(f'  已写 {out.relative_to(ROOT)}（{s["kept"]} 条 · '
              f'{out.stat().st_size / 1024:.0f} KB）')

    # 汇总写一份元数据，供训练脚本与文档引用
    meta = {
        'generated_from': 'data/distill/*.jsonl（**只读，未改动**）',
        'problems_found': {
            'broken_lines': '某行 response 含真实换行 ⇒ 一条记录被拆成两行；'
                            '已按"不以 { 开头就拼回上一行"修复',
            'empty_response': '失败的调用留下的空输出 ⇒ **必须过滤**，'
                              '否则微调出的模型会学会"输出空"，'
                              '而那种失败训练时不报错、只在评测时表现为什么都不输出',
        },
        'per_file': [{k: v for k, v in s.items() if k != 'records'} for s in stats],
    }
    mp = OUTDIR / '_cleaning-report.json'
    mp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'  已写 {mp.relative_to(ROOT)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
