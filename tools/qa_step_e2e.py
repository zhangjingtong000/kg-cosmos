# -*- coding: utf-8 -*-
r"""B1 · 分步问答的 2/3/4 步**端到端实测**。

## 为什么单独做这件事

TODO 的原话：
> `POST /api/qa/step` 的 `support`/`oppose`/`synth` 三步**从未端到端跑过**。
> 它们与 `answer()` 共用同一套 prompt，但**未经实测不能当它能跑**。

`ask.html` 是**分四次请求**驱动四个 Agent 的（这样界面上能逐行点亮）。
而项目此前只验证过第 1 步（`retrieve`，纯规则秒回）——
**第 2/3/4 步要真调本地模型，从没跑完过。**
⇒ 这一页是整个项目的**主看点**，而它的第 2/3/4 步**没有一次成功记录**。

## 顺带验证一件更要紧的事：支持与反对**真的独立吗**

设计上它们是**两次独立调用、互不可见**（`step_agents(q, ev, role)` 两次），
而综合方只看两方交上来的东西。
**如果两次调用返回一模一样的文本，那"独立取证"就是假的** ——
所以本脚本会比对两方输出的相似度。

用法：
    python tools/qa_step_e2e.py                    # 默认问一个关于 TransE 的问题
    python tools/qa_step_e2e.py --question "..." --out data/quality/qa-step-e2e.json
"""
import argparse
import difflib
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = 'http://127.0.0.1:8000'


def parse_args():
    ap = argparse.ArgumentParser(description='B1 分步问答端到端实测')
    ap.add_argument('--base', default=BASE)
    ap.add_argument('--graph-id', default='ad690dfb')
    ap.add_argument('--question', default='TransE 有什么局限？')
    ap.add_argument('--out', default=str(ROOT / 'data' / 'quality' / 'qa-step-e2e.json'))
    ap.add_argument('--timeout', type=int, default=300)
    return ap.parse_args()


ARGS = parse_args()


def post(path, payload, timeout=None):
    body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(ARGS.base + path, data=body,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout or ARGS.timeout) as r:
        return json.load(r)


def short(s, n=160):
    s = ' '.join(str(s or '').split())
    return s[:n] + ('…' if len(s) > n else '')


def main():
    t0 = time.time()
    print('=' * 88)
    print('B1 · 分步问答端到端实测（retrieve → support → oppose → synth）')
    print('=' * 88)
    print(f'  后端 {ARGS.base} · 图 {ARGS.graph_id}')
    print(f'  问题：{ARGS.question!r}')
    print()

    steps = []

    # ── ① retrieve（纯规则，秒回）────────────────────────────────────
    print('  ① retrieve（纯规则，不调模型）…')
    t = time.time()
    try:
        r1 = post('/api/qa/step', {'graph_id': ARGS.graph_id,
                                   'question': ARGS.question, 'step': 'retrieve'})
    except Exception as e:
        print(f'     ❌ 失败：{type(e).__name__}: {e}')
        return 1
    el = time.time() - t
    ret = r1.get('retrieval') or {}
    n_ev = r1.get('n_evidence') or len(r1.get('evidence') or [])
    print(f'     ✅ {el:.2f}s · 种子 {len(ret.get("seeds") or [])} 个 · '
          f'节点 {ret.get("n_nodes")} · 边 {ret.get("n_edges")} · 证据 {n_ev} 条')
    steps.append({'step': 'retrieve', 'ok': True, 'elapsed_s': round(el, 2),
                  'seeds': ret.get('seeds'), 'n_evidence': n_ev,
                  'question_used': ret.get('question_used'),
                  'rewritten_from': ret.get('rewritten_from')})
    if not n_ev:
        print('     ⚠️ 没有证据 ⇒ 2/3/4 步无法进行（换一个问句）')
        return 1
    evidence = r1.get('evidence') or []

    # ── ②③ support / oppose（**两次独立调用**）───────────────────────
    outs = {}
    for step in ('support', 'oppose'):
        print(f'  {"②" if step == "support" else "③"} {step}（调本地模型）…')
        t = time.time()
        try:
            r = post('/api/qa/step', {'graph_id': ARGS.graph_id,
                                      'question': ARGS.question,
                                      'step': step, 'evidence': evidence})
        except Exception as e:
            el = time.time() - t
            print(f'     ❌ 失败（{el:.1f}s）：{type(e).__name__}: {e}')
            steps.append({'step': step, 'ok': False, 'elapsed_s': round(el, 2),
                          'error': f'{type(e).__name__}: {e}'})
            continue
        el = time.time() - t
        txt = r.get('text') or ''
        outs[step] = txt
        print(f'     {"✅" if r.get("ok") else "⚠️"} {el:.1f}s · '
              f'ok={r.get("ok")} · {len(txt)} 字符')
        print(f'        {short(txt, 200)}')
        steps.append({'step': step, 'ok': bool(r.get('ok')), 'elapsed_s': round(el, 2),
                      'chars': len(txt), 'preview': short(txt, 400),
                      'agent': r.get('agent'), 'note': r.get('note')})

    # ── 独立性：两次调用的输出不能雷同 ────────────────────────────────
    indep = None
    if outs.get('support') and outs.get('oppose'):
        ratio = difflib.SequenceMatcher(None, outs['support'], outs['oppose']).ratio()
        indep = {'similarity': round(ratio, 4),
                 'verdict': ('⚠️ 太像了 —— "独立取证"可能只是形式' if ratio > 0.95
                             else '✅ 明显不同 —— 两次调用确实是独立的')}
        print()
        print(f'  ★ 支持 vs 反对 的文本相似度 = {ratio:.4f}')
        print(f'     {indep["verdict"]}')

    # ── ④ synth（只看两方交上来的东西）──────────────────────────────
    print()
    print('  ④ synth（只看两方交上来的意见）…')
    t = time.time()
    try:
        r4 = post('/api/qa/step', {'graph_id': ARGS.graph_id,
                                   'question': ARGS.question, 'step': 'synth',
                                   'support': outs.get('support', ''),
                                   'oppose': outs.get('oppose', '')})
    except Exception as e:
        el = time.time() - t
        print(f'     ❌ 失败（{el:.1f}s）：{type(e).__name__}: {e}')
        steps.append({'step': 'synth', 'ok': False, 'elapsed_s': round(el, 2),
                      'error': f'{type(e).__name__}: {e}'})
        r4 = {}
    else:
        el = time.time() - t
        txt = r4.get('text') or ''
        print(f'     {"✅" if r4.get("ok") else "⚠️"} {el:.1f}s · '
              f'ok={r4.get("ok")} · 置信度 {r4.get("confidence")!r} · {len(txt)} 字符')
        print(f'        {short(txt, 240)}')
        steps.append({'step': 'synth', 'ok': bool(r4.get('ok')), 'elapsed_s': round(el, 2),
                      'chars': len(txt), 'preview': short(txt, 400),
                      'confidence': r4.get('confidence'),
                      'agent': r4.get('agent'), 'note': r4.get('note')})

    # ── 汇总 ─────────────────────────────────────────────────────────
    all_ok = all(s.get('ok') for s in steps) and len(steps) == 4
    total = time.time() - t0
    print()
    print('=' * 88)
    print('结果')
    print('=' * 88)
    for s in steps:
        mark = '✅' if s.get('ok') else '❌'
        extra = (f'证据 {s["n_evidence"]} 条' if s['step'] == 'retrieve'
                 else (f'{s.get("chars", 0)} 字符' if 'chars' in s else s.get('error', '')))
        print(f'  {mark} {s["step"]:<9} {s["elapsed_s"]:>7.2f}s   {extra}')
    print()
    print(f'  四步总耗时 {total:.1f}s · '
          + ('**四步全部成功**' if all_ok else '**有步骤失败**'))

    payload = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'purpose': 'B1：分步问答 2/3/4 步的端到端实测（此前从未跑过）',
        'graph_id': ARGS.graph_id, 'question': ARGS.question,
        'all_ok': all_ok, 'total_s': round(total, 1),
        'independence': indep,
        'steps': steps,
    }
    Path(ARGS.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                              encoding='utf-8')
    print(f'  已写 {Path(ARGS.out).name}')
    return 0 if all_ok else 1


if __name__ == '__main__':
    sys.exit(main())
