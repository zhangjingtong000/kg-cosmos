# -*- coding: utf-8 -*-
r"""J7 · **导出为单文件 HTML**（数据内嵌，双击即看、可点证据）。

## TODO 的原话

> | **J7** | ⬜ **可选：导出为单文件 HTML**（数据内嵌，双击即看、可点证据） |
> 「能发给别人看吗」★★。**这是最适合本项目的"另一端"**
> （比硬塞小程序方向对） | **1 天**

**"最适合本项目的另一端"** 这句说的是：
这个项目的卖点是「**每条结论都能回溯到原文那一句**」——
而**一个能双击打开、每条证据都能点开看原文的 HTML**，
正是那个卖点**最直接的交付形态**（比让人 clone 仓库、起服务、跑 pipeline 现实得多）。

## ★ 三个设计决定

**① 数据要**精简投影**，不能搬原始 JSON。**
实测单篇图 1.07–12.73 MB（21 篇 `data/corpus/graphs/*.json`，最小 `jsjkx2025-kg-completion-llm`、
最大 `2302.05019v1`）、全库合计 88.76 MB —— 直接内嵌会得到一个"能打开但很慢"的文件。
（原写「单篇图 1.8–13 MB、全库 77.6 MB」；2026-09-19 按目录实测更正。
 若嫌大，本脚本默认 `pick()` 取**最小的那一篇**。）
⇒ 只带**给人看**需要的字段：
```
节点: [name, type]
边:   [source, target, type, page, quote, paper]
```
（去掉 `chunk_idx` / 完整 `evidence` 数组 / 位置字段等程序才需要的部分。）

**② 引文截断，但**页码与论文名一定要留**。**
"可点证据"的**实质**是"**能核**"，而不是"能跳 PDF"。
⇒ 单文件里不带 PDF（那会让文件从几 MB 变成几十 MB），
但**每条证据都带论文名 + 页码 + 原文引文** —— 拿到文件的人**照页码能翻到**。
**页面上把这一点写明**，不然会被当成"证据不完整"。

**③ 自己带样式与脚本，零外部请求。**
断网、换机器、直接双击都要能看。
⇒ 不引 CDN、不引 `lib/`（那些都在仓库里，跟着文件走不了）。

用法：
    python tools/export_single_html.py                     # 默认取最小的那一篇
    python tools/export_single_html.py --graph 2012.00456v1
    python tools/export_single_html.py --papers 2012.00456v1,2504.09823v1
    python tools/export_single_html.py --out D:\分享\kg.html
"""
import argparse
import html
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPHS = ROOT / 'data' / 'corpus' / 'graphs'
DEFAULT_OUT = ROOT / 'output' / 'kg-single-file.html'

QUOTE_LIMIT = 220       # 引文截断长度（够看懂这句话在说什么）


def parse_args():
    ap = argparse.ArgumentParser(description='J7 导出单文件 HTML')
    ap.add_argument('--graph', default='', help='一篇的 id（不含 .json）')
    ap.add_argument('--papers', default='', help='多篇，逗号分隔')
    ap.add_argument('--out', default=str(DEFAULT_OUT))
    ap.add_argument('--max-edges', type=int, default=0,
                    help='每篇最多导出多少条边（0 = 不限）')
    return ap.parse_args()


def pick(a):
    ids = [x.strip() for x in a.papers.split(',') if x.strip()]
    if a.graph:
        ids.append(a.graph)
    files = {f.stem: f for f in GRAPHS.glob('*.json')}
    if ids:
        missing = [i for i in ids if i not in files]
        if missing:
            print(f'  ❌ 找不到：{missing}')
            print(f'     可用：{sorted(files)[:8]} …')
            sys.exit(1)
        return [files[i] for i in sorted(set(ids))]
    # 不指定 ⇒ **取最小的那一篇**（默认产出小、打开快）
    return [min(files.values(), key=lambda p: p.stat().st_size)]


def main():
    a = parse_args()
    t0 = time.time()
    files = pick(a)
    print('=' * 92)
    print('J7 · 导出单文件 HTML')
    print('=' * 92)
    print(f'  导出 {len(files)} 篇：{[f.stem for f in files]}')

    papers = []
    tot_edges = 0
    for f in files:
        try:
            d = json.loads(f.read_text(encoding='utf-8-sig'))
        except Exception as e:
            print(f'  ⚠️ {f.stem} 读失败：{type(e).__name__}')
            continue
        nodes = [{'n': str(n.get('name') or n.get('id')),
                  't': str(n.get('type') or 'Unknown')}
                 for n in (d.get('nodes') or []) if (n.get('name') or n.get('id'))]
        edges = []
        for e in (d.get('edges') or []):
            ev = (e.get('evidence') or [{}])
            page = next((x.get('page') for x in ev if x.get('page')), None)
            quote = next((x.get('text') for x in ev if x.get('text')), '') or ''
            quote = ' '.join(str(quote).split())[:QUOTE_LIMIT]      # **截断**
            edges.append({
                's': str(e.get('source') or '')[:90],
                't': str(e.get('target') or '')[:90],
                'r': str(e.get('type') or '')[:40],
                'p': page,
                'q': quote,
            })
        if a.max_edges:
            edges = edges[:a.max_edges]
        tot_edges += len(edges)
        papers.append({'id': f.stem, 'nodes': nodes, 'edges': edges})
        print(f'    {f.stem:<16} {len(nodes):>5} 节点 · {len(edges):>5} 边')

    # 统计（页面上给个概览）
    from collections import Counter
    rels = Counter()
    n_with_quote = 0
    for p in papers:
        for e in p['edges']:
            rels[e['r']] += 1
            if e['q']:
                n_with_quote += 1
    print(f'\n  合计 {tot_edges} 边 · 其中 {n_with_quote} 条带引文'
          f'（{n_with_quote / max(1, tot_edges):.0%}）· {len(rels)} 种关系类型')

    payload = {'papers': papers,
               'stats': {'papers': len(papers), 'edges': tot_edges,
                         'with_quote': n_with_quote,
                         'relations': dict(rels.most_common())},
               'thresholds': {'quote_limit': QUOTE_LIMIT}}

    page = TEMPLATE.replace('/*__DATA__*/', json.dumps(payload, ensure_ascii=False))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding='utf-8')
    print(f'\n  ✅ 已写出 {out}')
    print(f'     {out.stat().st_size / 1024 / 1024:.2f} MB · '
          f'{time.time() - t0:.1f}s · **零外部依赖，双击即看**')
    return 0


# ── 模板：所有 CSS/JS 内联，**不引任何外部资源** ──────────────────────
TEMPLATE = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KG Cosmos · 单文件导出</title>
<style>
  :root{--bg:#0b0d12;--fg:#dfe2e8;--dim:#8b95a8;--line:rgba(193,195,201,.18);
        --hot:#e0a469;--cool:#7cc0b6;--warn:#e8c9a4}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:14px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
  header{padding:18px 22px 10px;border-bottom:1px solid var(--line)}
  h1{margin:0;font-size:18px;font-weight:600}
  .sub{color:var(--dim);font-size:12.5px;margin-top:6px;max-width:1000px}
  .sub b{color:var(--warn)}
  main{padding:14px 22px 60px;max-width:1180px}
  .bar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:6px 0 14px}
  input[type=search],select{background:rgba(18,20,26,.9);color:var(--fg);
    border:1px solid var(--line);border-radius:8px;padding:7px 10px;font-size:13px;
    font-family:inherit}
  input[type=search]{min-width:260px}
  .card{border:1px solid var(--line);border-radius:11px;padding:12px 15px;margin:12px 0}
  .card h2{margin:0 0 4px;font-size:15.5px}
  .meta{color:var(--dim);font-size:12.5px;margin-bottom:8px}
  .rel{display:inline-block;border:1px solid rgba(193,195,201,.3);border-radius:6px;
       padding:1px 7px;font-size:11.5px;color:var(--dim);margin:2px 4px 2px 0;
       cursor:pointer;user-select:none}
  .rel.on{border-color:rgba(224,164,105,.8);color:var(--warn)}
  table{border-collapse:collapse;width:100%;font-size:12.5px;margin-top:8px}
  th,td{border-bottom:1px solid var(--line);padding:7px 8px;text-align:left;
        vertical-align:top}
  th{color:var(--dim);font-weight:500;font-size:11.5px}
  tr.edge{cursor:pointer}
  tr.edge:hover{background:rgba(255,255,255,.035)}
  .r{color:var(--cool);font-family:ui-monospace,Consolas,monospace;font-size:11.5px}
  .pg{color:var(--dim);font-size:11.5px;white-space:nowrap}
  .quote{display:none;color:var(--warn);font-size:12.5px;line-height:1.7;
         padding:8px 10px;margin:2px 0 8px;border-left:2px solid rgba(224,164,105,.5);
         background:rgba(224,164,105,.06)}
  tr.edge.open + tr.q td{padding:0 8px}
  tr.edge.open + tr.q .quote{display:block}
  .empty{color:var(--dim);font-size:12.5px;padding:6px 0}
  .note{color:var(--dim);font-size:12.5px;margin-top:18px;border-top:1px solid var(--line);
        padding-top:12px}
  .note b{color:var(--warn)}
  code{color:var(--warn)}
</style>
</head>
<body>
<header>
  <h1>KG Cosmos · 单文件导出</h1>
  <div class="sub" id="sub">载入中…</div>
</header>
<main>
  <div class="bar">
    <input type="search" id="q" placeholder="搜实体或关系（如 TransE / USES）">
    <select id="paper"><option value="">全部论文</option></select>
    <span style="color:#8b95a8;font-size:12.5px" id="cnt"></span>
  </div>
  <div id="rels" class="bar"></div>
  <div id="out"></div>
  <p class="note">
    <b>⚠️ 这个文件里没有 PDF。</b>但每一条关系都带
    <b>论文名 + 页码 + 原文引文</b> ——
    <b>点任意一行</b>就能展开那句话，<b>照页码能翻回原文核对</b>。<br>
    <b>证据可核</b>才是这一页的意义：它不是一个"好看的关系图"，
    而是一张<b>每条边都能查回去</b>的清单。<br>
    数据来自 <code>data/corpus/graphs/</code>；抽取有漏有错（L3 端点完整性 <b>96.17%</b>），
    所以<b>它是线索，不是定论</b>。<br>
    <span id="src"></span>
  </p>
</main>
<script>
'use strict';
const DATA = /*__DATA__*/;
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let relOn = new Set(), kw = '', paperSel = '';

function render() {
  const rels = Object.keys(DATA.stats.relations || {});
  $('#rels').innerHTML = rels.map(r =>
    `<span class="rel${relOn.has(r) ? ' on' : ''}" data-r="${esc(r)}">`
    + `${esc(r)} <span style="opacity:.6">${DATA.stats.relations[r]}</span></span>`).join('');

  let shown = 0;
  $('#out').innerHTML = DATA.papers.filter(p => !paperSel || p.id === paperSel).map(p => {
    const rows = p.edges.filter(e => {
      if (relOn.size && !relOn.has(e.r)) return false;
      if (kw) {
        const hay = (e.s + ' ' + e.t + ' ' + e.r).toLowerCase();
        if (hay.indexOf(kw) < 0) return false;
      }
      return true;
    });
    shown += rows.length;
    if (!rows.length) return '';
    return `<div class="card"><h2>${esc(p.id)}</h2>
      <div class="meta">${p.nodes.length} 个实体 · 命中 ${rows.length} 条关系</div>
      <table><thead><tr><th>来源</th><th>关系</th><th>目标</th><th>出处</th></tr></thead>
      <tbody>${rows.map(e => `<tr class="edge"><td>${esc(e.s)}</td>
        <td class="r">${esc(e.r)}</td><td>${esc(e.t)}</td>
        <td class="pg">${e.p ? '第 ' + esc(e.p) + ' 页' : '—'}</td></tr>
        <tr class="q"><td colspan="4"><div class="quote">${
          e.q ? '「' + esc(e.q) + '」' : '（这条边在原文里没有留下引文）'
        }</div></td></tr>`).join('')}</tbody></table></div>`;
  }).join('') || '<div class="empty">没有匹配的关系。</div>';
  $('#cnt').textContent = `显示 ${shown} 条`;
  window.__exportState = { shown: shown, papers: DATA.papers.length,
                           rels: relOn.size, kw: kw, paper: paperSel };
}

document.addEventListener('click', ev => {
  const tr = ev.target.closest('tr.edge');
  if (tr) { tr.classList.toggle('open'); return; }
  const rel = ev.target.closest('.rel');
  if (rel) {
    const r = rel.dataset.r;
    if (relOn.has(r)) relOn.delete(r); else relOn.add(r);
    render();
  }
});
$('#q').addEventListener('input', e => { kw = e.target.value.trim().toLowerCase(); render(); });
$('#paper').addEventListener('change', e => { paperSel = e.target.value; render(); });

(function boot() {
  $('#paper').innerHTML = '<option value="">全部论文</option>'
    + DATA.papers.map(p => `<option value="${esc(p.id)}">${esc(p.id)}</option>`).join('');
  const s = DATA.stats;
  $('#sub').innerHTML = `<b>${s.papers}</b> 篇论文 · <b>${s.edges}</b> 条关系 · `
    + `<b>${s.with_quote}</b> 条带原文引文（${(s.with_quote / Math.max(1, s.edges) * 100).toFixed(0)}%）· `
    + `<b>点任意一行</b>展开它引用的原文那一句`;
  render();
  window.__exportReady = true;
})();
</script>
</body>
</html>
'''


if __name__ == '__main__':
    sys.exit(main())
