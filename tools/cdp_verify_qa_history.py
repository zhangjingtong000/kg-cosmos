# -*- coding: utf-8 -*-
r"""K8 问答历史 + K9 答案导出。

## 为什么这两件事值得回归

**K8**：历史存在 `localStorage` 里，**"存进去了"和"刷新后还在"是两件事** ——
只测前者会漏掉"键名写错、被别的代码清掉"这类问题。

**K9**：TODO 对它的定位是「**写论文最直接的用途** —— 现在只能截图」。
导出内容里**必须带引文与页码**，否则它相对截图的唯一优势就没了。
而"带没带"这件事很好断言 —— `toMarkdown()` 是**纯函数**，
可以直接喂一份假响应检查输出，**不需要后端**。

（真实提交路径由 `tests/test_qa_history.py` 覆盖端点；这里只管界面与格式化。）
"""
import json
import subprocess
import sys
import time
import urllib.request

import websocket

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
BASE = 'http://127.0.0.1:8098'


def cdp(ws, method, params=None, state=None):
    if state is None:
        state = {'id': 0}
    state['id'] += 1
    mid = state['id']
    ws.send(json.dumps({'id': mid, 'method': method, 'params': params or {}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get('id') == mid:
            return msg


def ev(ws, expr):
    r = cdp(ws, 'Runtime.evaluate',
            {'expression': expr, 'returnByValue': True, 'awaitPromise': True})
    res = r.get('result', {})
    if 'exceptionDetails' in res:
        return {'__error__': str(res['exceptionDetails'])[:300]}
    return res.get('result', {}).get('value')


# 一份最小的假响应 —— 字段名与 `/api/qa/step` 的返回一致
FAKE = {
    'question': 'TransE 有什么局限？',
    'retrieval': {'seeds': ['TransE', 'RotatE'],
                  'reason': '种子 2 个，2 跳扩展至 12 个节点、20 条边（纯规则，可复现）',
                  'question_used': 'TransE 有什么局限？', 'rewritten_from': None},
    'evidence': [
        {'source': 'TransE', 'target': 'knowledge graph', 'type': 'APPLIES_TO',
         'citations': [{'src': '2302.xxxx.pdf', 'page': 4,
                        'quote': 'TransE 难以处理一对多关系。'}]},
        {'source': 'TransE', 'target': 'link prediction', 'type': 'USES',
         'citations': [{'src': '2302.xxxx.pdf', 'page': 7,
                        'quote': 'We evaluate on link prediction benchmarks.'}]},
        {'source': 'RotatE', 'target': 'TransE', 'type': 'COMPARES_WITH',
         'citations': []},
    ],
    'n_evidence': 3, 'n_with_quote': 2,
    'support': '支持方的意见。', 'oppose': '反对方的意见。',
    'conclusion': '综合结论。', 'confidence': '中',
}


def main():
    port = 9850 + (int(time.time() * 1000) % 30)
    proc = subprocess.Popen([
        CHROME, '--headless=new', '--enable-unsafe-swiftshader', '--disable-gpu',
        '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
        f'--remote-debugging-port={port}', '--remote-allow-origins=*',
        '--window-size=1400,900', f'{BASE}/web/ask.html',
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    fails = []
    try:
        targets = None
        for _ in range(80):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/json', timeout=2) as r:
                    targets = json.load(r)
                if any(t.get('type') == 'page' for t in targets):
                    break
            except Exception:
                pass
            time.sleep(0.4)
        page = next(t for t in targets if t.get('type') == 'page')
        ws = websocket.create_connection(page['webSocketDebuggerUrl'], timeout=60)
        cdp(ws, 'Runtime.enable')
        for _ in range(300):
            if ev(ws, 'window.__ready === true') is True:
                break
            time.sleep(0.1)

        # 每次跑都从干净状态开始（否则上一次跑留下的历史会干扰断言）
        ev(ws, "try{localStorage.removeItem('kg-qa-history')}catch(e){}; renderHist();")

        # ── K8 ①：没有历史时面板是隐藏的 ──
        hidden = ev(ws, "document.getElementById('histsec').hidden")
        print(f'[1] 无历史时 histsec.hidden = {hidden}（应 True）')
        if hidden is not True:
            fails.append('没有历史时历史面板不该显示')

        # ── K8 ②：histPush 之后出现，并写进 localStorage ──
        entry = {'q': '测试问题', 'a': '答案摘要', 't': 1757000000000,
                 'gid': 'ad690dfb', 'resp': FAKE}
        ev(ws, 'histPush(' + json.dumps(entry, ensure_ascii=False) + ')')
        n_rows = ev(ws, "document.querySelectorAll('#hist .hq').length")
        n_ls = ev(ws, "JSON.parse(localStorage.getItem('kg-qa-history')||'[]').length")
        sec_hidden = ev(ws, "document.getElementById('histsec').hidden")
        print(f'[2] 写入后 .hq {n_rows} 行 · localStorage {n_ls} 条 · hidden {sec_hidden}')
        if n_rows < 1 or n_ls < 1 or sec_hidden:
            fails.append(f'写入历史后面板没出现（行 {n_rows} · ls {n_ls} · hidden {sec_hidden}）')

        # ── K8 ③：**刷新后仍在**（"存进去"和"刷新还在"是两件事）──
        cdp(ws, 'Page.enable')
        cdp(ws, 'Page.navigate', {'url': f'{BASE}/web/ask.html'})
        for _ in range(300):
            if ev(ws, 'window.__ready === true') is True:
                break
            time.sleep(0.1)
        time.sleep(0.4)
        n_after = ev(ws, "document.querySelectorAll('#hist .hq').length")
        print(f'[3] 刷新后 .hq {n_after} 行（应 >= 1）')
        if n_after < 1:
            fails.append('刷新后历史没了 —— 没有真正持久化')

        # ── K9 ①：toMarkdown 必须带引文与页码 ──
        fake_entry = {'q': 'TransE 有什么局限？', 't': 1757000000000,
                      'gid': 'ad690dfb', 'resp': FAKE}
        md = ev(ws, 'toMarkdown(' + json.dumps(fake_entry, ensure_ascii=False) + ')')
        if isinstance(md, dict) and '__error__' in md:
            fails.append(f'toMarkdown 抛错：{md["__error__"][:160]}')
            md = ''
        checks = [
            ('含问题', 'TransE 有什么局限？' in md),
            ('含引文原文', '一对多关系' in md),
            ('含页码', '第 4 页' in md and '第 7 页' in md),
            ('含来源 PDF', '2302.xxxx' in md),
            ('含关系类型', 'APPLIES_TO' in md),
            ('含支持方', '支持方' in md),
            ('含反对方', '反对方' in md),
            ('含结论与置信度', '综合结论' in md and '置信度' in md),
            ('是 Markdown 表格', '| # | 源 | 关系 | 目标 | 原文出处 |' in md),
        ]
        print(f'[4] toMarkdown 产出 {len(md)} 字符：')
        for label, ok in checks:
            print(f'      {"OK  " if ok else "FAIL"} {label}')
            if not ok:
                fails.append(f'导出内容缺「{label}」')
        # 没有引文的那条不该被塞进引文段（避免"引文段里混进空引文"）
        quote_block = md.split('### 引文')[-1] if '### 引文' in md else ''
        if 'COMPARES_WITH' in quote_block:
            fails.append('没有引文的那条边混进了引文段')

        # ── K9 ②：导出按钮存在、点得动 ──
        has_btn = ev(ws, "!!document.getElementById('expMd')")
        print(f'[5] 导出按钮存在 = {has_btn}')
        if not has_btn:
            fails.append('导出按钮不存在')
        # 没问过问题时点它要有提示（而不是静默无反应）
        ev(ws, "LAST_ENTRY = null;"
               "document.getElementById('expMd').click();")
        time.sleep(0.2)
        tip = ev(ws, "(document.getElementById('expMsg')||{}).textContent || ''")
        print(f'[6] 无记录时点击的提示 = {tip!r}')
        if '先问一个问题' not in (tip or ''):
            fails.append(f'无记录时点导出没有提示，实际 {tip!r}')

    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass

    print()
    print('=' * 60)
    if fails:
        print(f'❌ {len(fails)} 项失败：')
        for f in fails:
            print(f'   · {f}')
        return 1
    print('全部通过：历史可持久化并回看；导出含引文与页码')
    return 0


if __name__ == '__main__':
    sys.exit(main())
