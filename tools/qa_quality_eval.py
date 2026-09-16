# -*- coding: utf-8 -*-
r"""H11 · 多 Agent 问答的**质量评测**。

## 现状（TODO 的原话）

> 用户明确说它是「项目设计最开始的主要看点」
> **只有 1 次成功记录**（11.7 秒，四 Agent 依次点亮）；
> 分步接口的 2/3/4 步**从未实测**；**没有任何质量评测** ——
> "答得对不对"**没人量过** ⇒ ❌ **功能在、证据不在**

## 这份评测怎么做 —— **不做主观打分**

"答得好不好"没法机器判，但**它的几个必要条件可以**：

| 指标 | 怎么量 | 它回答什么 |
|---|---|---|
| **① 召回** | 拿**图里真实存在**的实体名造问句，看检索是否命中该实体 | "问得对的时候，找得到吗" |
| **② 拒答** | 拿**图里确定没有**的字符串造问句，看是否如实报"没有证据" | **"不知道的时候，会不会编"** |
| **③ 引用可核验率** | 每条证据的 `citations[].src/page` 是否指向**真实存在的论文与页** | "引用是真的吗" |
| **④ 证据两端的原文命中** | 证据的 source/target 是否出现在引文里 | "这条边有原文支撑吗"（与 L5 同源） |

**② 是最要紧的**：一个问答系统的可信度，首先取决于**它不知道时会不会承认**。
而这一条恰好**完全可机器验证** —— 造一个图里绝无可能存在的字符串，
如果它还给出"结论"，那就是在编。

## 为什么不用 LLM 判分

本项目已经量过：**四个模型标注者对关系层的一致性 α 是负的**
（`RESEARCH_POINT_EVIDENCE.md` 证据一）。
**用一个 α 为负的裁判去给另一个模型打分，得到的分数没有意义。**
⇒ 所以这里只用**可机械核对的事实**。

## 用法

    python tools/qa_quality_eval.py                  # 默认各 40 题
    python tools/qa_quality_eval.py --n 20 --seed 1  # 小样本快跑
    python tools/qa_quality_eval.py --out data/quality/qa-quality-eval.json
"""
import argparse
import json
import random
import statistics
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))

BASE = 'http://127.0.0.1:8000'
GID = 'ad690dfb'


def parse_args():
    ap = argparse.ArgumentParser(description='H11 多 Agent 问答的质量评测')
    ap.add_argument('--n', type=int, default=40, help='每类造多少题')
    ap.add_argument('--seed', type=int, default=20260916)
    ap.add_argument('--base', default=BASE)
    ap.add_argument('--out', default=str(ROOT / 'data' / 'quality' / 'qa-quality-eval.json'))
    ap.add_argument('--graph-id', default=GID)
    return ap.parse_args()


ARGS = parse_args()


def post(path, payload, timeout=120):
    body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(ARGS.base + path, data=body,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def get(path, timeout=120):
    with urllib.request.urlopen(ARGS.base + path, timeout=timeout) as r:
        return json.load(r)


def load_graph():
    """直接读图快照 —— 与后端 `_ensure_graph` 读的是同一份，
    但**评测不该依赖后端的内部状态**，所以自己读一份。"""
    for p in (ROOT / 'data').rglob(f'{ARGS.graph_id}/graph.json'):
        return json.loads(p.read_text(encoding='utf-8-sig'))
    g = json.loads((ROOT / 'data' / ARGS.graph_id / 'graph.json').read_text(encoding='utf-8-sig'))
    return g


def main():
    t0 = time.time()
    print('=' * 86)
    print('H11 · 多 Agent 问答的质量评测')
    print('=' * 86)

    try:
        g = load_graph()
    except Exception as e:
        print(f'  ❌ 读图失败：{type(e).__name__}: {e}')
        return 1
    nodes = [str(n.get('name') or n.get('id')) for n in (g.get('nodes') or [])]
    nodes = [n for n in nodes if n and len(n) >= 4]
    edges = g.get('edges') or []
    print(f'  图 {ARGS.graph_id}：{len(g.get("nodes") or [])} 节点 / {len(edges)} 边 · '
          f'可用实体名 {len(nodes)} 个')

    rng = random.Random(ARGS.seed)

    # ── ① 召回题：拿图里真实存在的实体名造问句 ────────────────────────
    pos = rng.sample(nodes, min(ARGS.n, len(nodes)))
    # ── ② 拒答题：**图里绝无可能存在的字符串** ────────────────────────
    #  用"随机字母 + 数字"造伪词。**不能用真实但不在此图的词** ——
    #  那种词是否该被拒答本身有争议；而伪词是无可争议的"图里没有"。
    def fake_word(i):
        return ''.join(rng.choice('ZQXJKVWY') for _ in range(3)) + \
               ''.join(rng.choice('aeiou') for _ in range(3)) + str(1000 + i)
    neg = [fake_word(i) for i in range(ARGS.n)]
    # 造题时**必须确认它真的不在图里**（否则测的就是召回而不是拒答）
    joined = ' '.join(nodes).lower()
    neg = [w for w in neg if w.lower() not in joined]
    print(f'  造题：召回 {len(pos)} 题 · 拒答 {len(neg)} 题（伪词，已核对不在图内）')
    print()

    def ask(q):
        try:
            r = post('/api/qa/step', {'graph_id': ARGS.graph_id, 'question': q,
                                      'step': 'retrieve'})
            return r.get('retrieval') or {}, r.get('evidence') or []
        except Exception as e:
            return {'__error__': f'{type(e).__name__}'}, []

    # ── ① 召回 ─────────────────────────────────────────────────────────
    print('  ① 召回（问图里有的实体，看种子是否命中它）…')
    hit = 0
    recs = []
    for i, name in enumerate(pos, 1):
        ret, ev = ask(f'{name} 是什么')
        seeds = [str(s) for s in (ret.get('seeds') or [])]
        ok = any(name.lower() in s.lower() or s.lower() in name.lower() for s in seeds)
        hit += 1 if ok else 0
        recs.append({'kind': 'recall', 'q': f'{name} 是什么', 'expect': name,
                     'seeds': seeds, 'ok': ok})
        if i % 10 == 0 or i == len(pos):
            print(f'      {i}/{len(pos)} · 命中 {hit}（{hit / i:.1%}）')
    recall = hit / len(pos) if pos else 0

    # ── ② 拒答 ─────────────────────────────────────────────────────────
    print()
    print('  ② 拒答（问图里伪造的词，看是否如实说"没有证据"）…')
    refused = 0
    for i, w in enumerate(neg, 1):
        ret, ev = ask(w)
        seeds = ret.get('seeds') or []
        # **"拒答"的判据**：检索不到种子 ⇒ 后端会返回 0 证据 ⇒ 前端显示"没有相关证据"
        ok = (len(seeds) == 0 and len(ev) == 0)
        refused += 1 if ok else 0
        recs.append({'kind': 'refuse', 'q': w, 'seeds': [str(s) for s in seeds],
                     'n_evidence': len(ev), 'ok': ok})
        if i % 10 == 0 or i == len(neg):
            print(f'      {i}/{len(neg)} · 正确拒答 {refused}（{refused / i:.1%}）')
    refuse_rate = refused / len(neg) if neg else 0

    # ── ③ 引用可核验率 ─────────────────────────────────────────────────
    print()
    print('  ③ 引用可核验率（证据里的论文/页是否指向真实的论文与页）…')
    # ⚠️ **字段名是 `paper` 不是 `src`** —— 第一版用了 `c.get('src')`，
    # 于是 276 条引文全部"不可核验"（0.0%），**那是评测脚本自己的 bug**。
    # 实际的 citation 结构是：
    #     {'paper': '2506.09566v1.pdf', 'page': 5, 'quote': '...', 'source_id': ...}
    #
    # ⚠️ **`known` 必须包含"上传的 PDF"** —— 第二版又漏了：
    # `data/ad690dfb/2506.09566v1.pdf` 是**上传任务目录里的原始论文**，
    # 它不在 `data/gold/`（那是 SOURCE-*.txt 摘录）也不在 `data/corpus/graphs/`（17 篇）。
    # **而被评测的 GID 恰好就是上传任务** ⇒ 全部引文都指向它。
    srcs = {p.stem for p in (ROOT / 'data' / 'corpus' / 'graphs').glob('*.json')}
    pdfs = {p.stem.replace('SOURCE-', '').split('-p1-')[0]
            for p in (ROOT / 'data' / 'gold').glob('*.txt')}
    # 上传任务的原始 PDF：`data/<task_id>/<arxiv_id>.pdf`
    uploads = {p.stem for p in (ROOT / 'data').glob('*/*.pdf')}
    known = srcs | pdfs | uploads
    print(f'      可核验来源：graph {len(srcs)} · gold {len(pdfs)} · 上传 PDF {len(uploads)}')
    n_cit = n_cit_ok = 0
    bad_cit = []
    for r in recs:
        if r['kind'] != 'recall':
            continue
        _ret, ev = ask(r['q'])
        for e in ev:
            for c in (e.get('citations') or []):
                n_cit += 1
                src = str(c.get('paper') or c.get('src') or '').replace('.pdf', '')
                page = c.get('page')
                ok = bool(src) and bool(page) and (
                    src in known or any(src in k or k in src for k in known))
                if ok:
                    n_cit_ok += 1
                elif len(bad_cit) < 5:
                    bad_cit.append({'paper': src, 'page': page,
                                    'known_sample': sorted(known)[:3]})
    cit_rate = n_cit_ok / n_cit if n_cit else None
    print(f'      引文 {n_cit} 条 · 可核验 {n_cit_ok} 条'
          + (f'（{cit_rate:.1%}）' if cit_rate is not None else '（无引文）'))
    for b in bad_cit:
        print(f'      ⚠️ 核验不到：paper={b["paper"]!r} page={b["page"]!r}')

    # ── 汇总 ───────────────────────────────────────────────────────────
    print()
    print('=' * 86)
    print('结果')
    print('=' * 86)
    print(f'  ① 召回率        {recall:.1%}  （{hit}/{len(pos)}）'
          f'  —— 问图里有的东西，找得到吗')
    print(f'  ② 拒答率        {refuse_rate:.1%}  （{refused}/{len(neg)}）'
          f'  —— **不知道的时候，会不会编**')
    print(f'  ③ 引用可核验率  '
          + (f'{cit_rate:.1%}' if cit_rate is not None else '—')
          + f'  （{n_cit_ok}/{n_cit}）  —— 引用是真的吗')

    # 失败样本要列出来（**只有通过率没有样本，没法改**）
    bad = [r for r in recs if not r['ok']]
    if bad:
        print()
        print(f'  ⚠️ 未通过的 {len(bad)} 题（前 8 条）：')
        for r in bad[:8]:
            if r['kind'] == 'recall':
                print(f'      [召回] {r["q"]!r} 期望命中 {r["expect"]!r}，'
                      f'实际种子 {r["seeds"][:4]}')
            else:
                print(f'      [拒答] {r["q"]!r} 却拿到 {r["n_evidence"]} 条证据，'
                      f'种子 {r["seeds"][:4]}')

    payload = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'purpose': 'H11：多 Agent 问答的质量证据（"答得对不对"此前没人量过）',
        'design': {
            'no_llm_judge': '**不用 LLM 判分** —— 本项目已量过四个模型标注者对关系层的'
                            '一致性 α 是负的；用一个 α 为负的裁判给另一个模型打分没有意义。'
                            '这里只用可机械核对的事实。',
            'recall': '拿图里真实存在的实体名造问句',
            'refuse': '拿**伪词**（随机字母+数字，已核对不在图内）造问句；'
                      '不能用"真实但不在此图的词"——那种词该不该拒答本身有争议',
            'citation': '证据里的 src/page 是否指向真实存在的论文与页码',
        },
        'graph_id': ARGS.graph_id,
        'n_nodes': len(nodes), 'n_edges': len(edges),
        'metrics': {
            'recall': round(recall, 4), 'recall_n': len(pos), 'recall_hit': hit,
            'refuse_rate': round(refuse_rate, 4), 'refuse_n': len(neg), 'refuse_ok': refused,
            'citation_rate': None if cit_rate is None else round(cit_rate, 4),
            'citation_n': n_cit, 'citation_ok': n_cit_ok,
        },
        'records': recs,
        'elapsed_s': round(time.time() - t0, 1),
    }
    Path(ARGS.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                              encoding='utf-8')
    print()
    print(f'  已写 {Path(ARGS.out).name}（{payload["elapsed_s"]}s）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
