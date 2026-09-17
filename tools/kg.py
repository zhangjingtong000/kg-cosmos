# -*- coding: utf-8 -*-
r"""J6 · 命令行工具 `kg`（面向研究者、可复现）。

## TODO 的原话

> | **J6** | ⬜ **可选：命令行工具**（`kg ask "问题" --graph xxx`，复用 `/api/qa/step`） |
> 「有没有别的用法」★。**与项目本体一致**（面向研究者、可复现） | **半天**

## ★ 一个与 TODO 不同的决定：**默认直连，不经过 HTTP**

TODO 写的是"复用 `/api/qa/step`"。但那意味着**必须先起后端**，
而命令行工具的价值恰恰在于"**不用起服务**"——
一个要在另一个终端先 `python src/app.py` 才能用的 CLI，
在脚本里、在远程 SSH 里、在批处理里都会变得别扭。

⇒ 所以：
· **默认**：**直连 `multi_agent_qa`**（`step_retrieve` / `step_agents` / `step_synth`），
  **不起任何服务**；
· **`--remote`**：走 `POST /api/qa/step`（**那条路仍然保留**，
  用在后端已经起着、或想验证 HTTP 那条链的时候）。

**两条路的接口是同一套**（`ask.html` 调的也是后者），
所以"复用"这件事仍然成立 —— 只是**默认那条更省事**。

## 子命令

    kg ask "问题" [--graph X] [--step] [--no-llm] [--remote]
    kg papers                        # 列已入库的论文（含规模）
    kg stats                         # 全库规模与关系分布
    kg evidence "实体名" [--graph X]   # 只列某个实体的证据（**不调模型**）
    kg export [--graph X] [--out Y]   # 复用 J7 的单文件导出
    kg verify [--fast]                # 复用 tools/verify_all.py

用法示例：
    python tools/kg.py ask "TransE 有什么局限" --graph 2504.09823v1 --no-llm
    python tools/kg.py ask "知识图谱嵌入的挑战" --step
    python tools/kg.py papers
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))

GRAPHS = ROOT / 'data' / 'corpus' / 'graphs'
INDEX = ROOT / 'web' / 'kg-data' / 'corpus-index.json'
DEFAULT_REMOTE = os.environ.get('KG_API', 'http://127.0.0.1:8000')


# ══════════════════════════════════════════════════════════════════════
# 数据侧（**不需要后端、不需要模型**）
# ══════════════════════════════════════════════════════════════════════
def load_one(gid):
    """读一篇的节点与边（**走与 pipeline 同一套字段修正**）。"""
    import graph_source
    # `graph_source.load_graph` 读全库；这里只需要一篇 ⇒ 用它的同一套规则
    # 但**不复制那份逻辑**：先整库读，再按 `_paper` 过滤。
    nodes, edges = graph_source.load_graph(governed=False, verbose=False)
    if not gid:
        return nodes, edges
    sub = [e for e in edges if e.get('_paper') == gid]
    if not sub:
        avail = sorted({str(e.get('_paper')) for e in edges if e.get('_paper')})
        raise SystemExit(f'❌ 没有 {gid} 这篇的边。可用：{avail}')
    names = {str(e['source']) for e in sub} | {str(e['target']) for e in sub}
    return [n for n in nodes if n['name'] in names], sub


def cmd_papers(a):
    if not INDEX.exists():
        print(f'  ❌ 缺 {INDEX}')
        return 1
    d = json.loads(INDEX.read_text(encoding='utf-8'))
    ps = [p for p in (d.get('papers') or []) if p.get('in_graph')]

    def year_of(p):
        """⚠️ `corpus-index.json` 里的 `year` 是**字符串**（`"2022"`），不是整数 ——
        第一版直接写 `-(x.get('year') or 0)`，于是 `TypeError: bad operand type
        for unary -: 'str'`。**又一次没看数据就写代码**（本项目栽过很多次）。"""
        try:
            return int(str(p.get('year') or 0).strip() or 0)
        except ValueError:
            return 0

    print(f'  已入库 {len(ps)} 篇（清单共 {len(d.get("papers") or [])} 篇）')
    print()
    print(f'  {"id":<18}{"年":<6}{"节点":>7}{"关系":>7}  标题')
    for p in sorted(ps, key=lambda x: (-year_of(x), x['id'])):
        print(f'  {p["id"]:<18}{str(p.get("year") or ""):<6}'
              f'{p.get("nodes") or 0:>7}{p.get("edges") or 0:>7}  '
              f'{(p.get("title") or "")[:52]}')
    return 0


def cmd_stats(a):
    from collections import Counter
    nodes, edges = load_one('')
    print(f'  全库 {len(nodes)} 节点 / {len(edges)} 边')
    c = Counter(str(e.get('type') or '') for e in edges)
    print()
    for k, v in c.most_common(a.top):
        print(f'    {v:>6}  {k}')
    return 0


def cmd_evidence(a):
    """**只列证据，不调模型** —— 这一条最符合"面向研究者"。"""
    nodes, edges = load_one(a.graph)
    hit = [e for e in edges
           if a.entity in str(e.get('source')) or a.entity in str(e.get('target'))]
    if not hit:
        print(f'  ❌ 图里没有提到「{a.entity}」的边'
              + (f'（限 {a.graph}）' if a.graph else ''))
        return 1
    print(f'  「{a.entity}」的证据 {len(hit)} 条'
          + (f'（限 {a.graph}）' if a.graph else '（全库）'))
    print()
    for e in hit[:a.limit]:
        ev = (e.get('evidence') or [{}])
        page = next((x.get('page') for x in ev if x.get('page')), None)
        quote = next((x.get('text') for x in ev if x.get('text')), '') or ''
        quote = ' '.join(str(quote).split())[:150]
        print(f'    [{e.get("type")}] {str(e["source"])[:38]} → {str(e["target"])[:38]}')
        print(f'        {e.get("_paper") or "?"}'
              + (f' 第 {page} 页' if page else '')
              + (f'  「{quote}」' if quote else '  （无引文）'))
    if len(hit) > a.limit:
        print(f'    …另有 {len(hit) - a.limit} 条（--limit 调整）')
    return 0


# ══════════════════════════════════════════════════════════════════════
# 问答
# ══════════════════════════════════════════════════════════════════════
def ask_local(question, gid, step, use_llm):
    import multi_agent_qa as QA
    nodes, edges = load_one(gid)
    print(f'  图：{gid or "全库"} · {len(nodes)} 节点 / {len(edges)} 边 · '
          f'模型：{"用" if use_llm else "**不用**（--no-llm）"}')
    print()
    if not step:
        out = QA.answer(question, nodes, edges, use_llm=use_llm)
        print(out if isinstance(out, str) else json.dumps(out, ensure_ascii=False,
                                                          indent=1)[:4000])
        return 0
    # 分步（与 `ask.html` 调的是同一套）
    #
    # ⚠️ **返回结构不是平的**：`seeds` / `n_nodes` / `n_edges` 在 `retrieval` 里，
    #    而 `evidence` 在顶层。第一版写 `r.get('seeds')` ⇒ 恒为 0，
    #    于是打出"**0 个种子 · 0 节点 · 18 条证据**"这种自相矛盾的一行。
    #    （又一次没看返回结构就写代码。）
    r = QA.step_retrieve(question, nodes, edges)
    ret = r.get('retrieval') or {}
    ev = r.get('evidence') or []
    print(f'  ① 检索：{len(ret.get("seeds") or [])} 个种子 · '
          f'{ret.get("n_nodes") or 0} 节点 · {ret.get("n_edges") or 0} 边 · '
          f'{len(ev)} 条证据')
    if ret.get('question_used') and ret.get('rewritten_from'):
        print(f'     追问补全：{ret["rewritten_from"]!r} → {ret["question_used"]!r}')
    if ret.get('reason'):
        print(f'     理由：{str(ret["reason"])[:120]}')
    if not use_llm:
        print('     （--no-llm：到这里为止，不调模型）')
        return 0
    # `step_agents` / `step_synth` 的正文在 `text` 里（不是整个 dict）
    s = QA.step_agents(question, ev, 'support')
    print(f'  ② 支持方：{str(s.get("text") or s)[:300]}')
    o = QA.step_agents(question, ev, 'oppose')
    print(f'  ③ 反对方：{str(o.get("text") or o)[:300]}')
    y = QA.step_synth(question, s.get('text') or '', o.get('text') or '')
    print(f'  ④ 综合：{str(y.get("text") or y)[:400]}')
    return 0


def ask_remote(question, gid, step, use_llm):
    import urllib.request
    def post(payload):
        req = urllib.request.Request(
            DEFAULT_REMOTE.rstrip('/') + '/api/qa/step',
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    gid = gid or '2012.00456v1'
    print(f'  走 HTTP：{DEFAULT_REMOTE}/api/qa/step · graph_id={gid}')
    print()
    try:
        r = post({'graph_id': gid, 'question': question, 'step': 'retrieve'})
    except Exception as e:
        print(f'  ❌ 连不上后端（{type(e).__name__}）—— 先起 `python src/app.py`，'
              f'或者去掉 --remote 用直连模式')
        return 1
    ev = r.get('evidence') or []
    print(f'  ① 检索：{len(ev)} 条证据')
    if not use_llm:
        return 0
    s = post({'graph_id': gid, 'question': question, 'step': 'support',
              'evidence': ev})
    print(f'  ② 支持方：{str(s)[:300]}')
    o = post({'graph_id': gid, 'question': question, 'step': 'oppose',
              'evidence': ev})
    print(f'  ③ 反对方：{str(o)[:300]}')
    y = post({'graph_id': gid, 'question': question, 'step': 'synth',
              'support': s, 'oppose': o})
    print(f'  ④ 综合：{str(y)[:400]}')
    return 0


def cmd_ask(a):
    if a.remote:
        return ask_remote(a.question, a.graph, a.step, not a.no_llm)
    return ask_local(a.question, a.graph, a.step, not a.no_llm)


def cmd_export(a):
    args = [sys.executable, str(ROOT / 'tools' / 'export_single_html.py')]
    if a.graph:
        args += ['--graph', a.graph]
    if a.out:
        args += ['--out', a.out]
    return subprocess.call(args, cwd=str(ROOT))


def cmd_verify(a):
    args = [sys.executable, str(ROOT / 'tools' / 'verify_all.py')]
    if a.fast:
        args.append('--fast')
    return subprocess.call(args, cwd=str(ROOT))


def main():
    ap = argparse.ArgumentParser(
        prog='kg', description='KG Cosmos 命令行（面向研究者、可复现）')
    sub = ap.add_subparsers(dest='cmd')

    p = sub.add_parser('ask', help='就一篇论文提问（**默认直连，不必起服务**）')
    p.add_argument('question')
    p.add_argument('--graph', default='', help='论文 id；省略 = 全库')
    p.add_argument('--step', action='store_true', help='分步显示四个 Agent')
    p.add_argument('--no-llm', action='store_true', help='**只检索证据，不调模型**')
    p.add_argument('--remote', action='store_true',
                   help='走 HTTP（复用 /api/qa/step；需先起后端）')
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser('papers', help='列已入库的论文')
    p.set_defaults(fn=cmd_papers)

    p = sub.add_parser('stats', help='全库规模与关系分布')
    p.add_argument('--top', type=int, default=16)
    p.set_defaults(fn=cmd_stats)

    p = sub.add_parser('evidence', help='列某个实体的证据（**不调模型**）')
    p.add_argument('entity')
    p.add_argument('--graph', default='')
    p.add_argument('--limit', type=int, default=12)
    p.set_defaults(fn=cmd_evidence)

    p = sub.add_parser('export', help='导出单文件 HTML（复用 J7）')
    p.add_argument('--graph', default='')
    p.add_argument('--out', default='')
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser('verify', help='跑检查（复用 verify_all）')
    p.add_argument('--fast', action='store_true')
    p.set_defaults(fn=cmd_verify)

    a = ap.parse_args()
    if not getattr(a, 'fn', None):
        ap.print_help()
        return 0
    return a.fn(a)


if __name__ == '__main__':
    sys.exit(main())
