# -*- coding: utf-8 -*-
"""验证「问题子图」（T6.3 PathQA）的检索与渲染。

断言以 DOM 与内部状态为准（`window.__lastAsk`）：
种子是否命中、子图规模、SVG 里实际画了几个节点、侧栏统计是否一致。

用法：
  python tools/cdp_verify_pathqa.py
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request

import websocket

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
URL = 'http://127.0.0.1:8098/web/pathqa.html'


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
        return {'__error__': res['exceptionDetails'].get('text', 'exception')}
    return res.get('result', {}).get('value')


def ask(ws, q, wait=0.7):
    ev(ws, f"""(() => {{
      const el = document.getElementById('q');
      el.value = {json.dumps(q)};
      document.getElementById('go').click();
    }})()""")
    time.sleep(wait)
    return ev(ws, "JSON.stringify(window.__lastAsk)")


def main():
    port = 9600 + (int(time.time() * 1000) % 120)
    proc = subprocess.Popen([
        CHROME, '--headless=new', '--enable-unsafe-swiftshader', '--disable-gpu',
        '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
        f'--remote-debugging-port={port}', '--remote-allow-origins=*',
        '--window-size=1600,1000', URL,
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
        if not targets:
            print('CDP 未就绪')
            return 1
        page = next(t for t in targets if t.get('type') == 'page')
        ws = websocket.create_connection(page['webSocketDebuggerUrl'], timeout=90)
        cdp(ws, 'Page.enable')
        cdp(ws, 'Runtime.enable')

        ok = False
        for _ in range(500):
            if ev(ws, 'window.__ready === true') is True:
                ok = True
                break
            time.sleep(0.1)
        print('页面就绪:', ok)
        if not ok:
            fails.append('页面未就绪（可能是 nebula.json 没加载完）')

        head = ev(ws, "document.getElementById('hint2').textContent.trim().slice(0,60)")
        print(f'[1] 初始提示: {head}')

        # 命中：一个明确存在的实体
        r = ask(ws, 'TransE 的局限')
        d = json.loads(r) if isinstance(r, str) else r
        print(f'[2] 问「TransE 的局限」→ {d}')
        if not d or not d.get('seeds'):
            fails.append('「TransE 的局限」没有命中任何种子实体')
        else:
            if 'TransE' not in d['seeds']:
                fails.append(f'种子中没有 TransE: {d["seeds"]}')
            if d['nodes'] < 5:
                fails.append(f'子图过小（{d["nodes"]} 个节点），检索可能没展开')

        # SVG 实际画出的圆点数应与子图规模一致（.n 是节点本体；.ring 是种子外圈）
        paints = ev(ws, """({
          circles: document.querySelectorAll('#graph svg circle.n').length,
          rings: document.querySelectorAll('#graph svg circle.ring').length,
          lines: document.querySelectorAll('#graph svg line').length,
          texts: document.querySelectorAll('#graph svg text').length,
          seedRows: document.querySelectorAll('#seeds .row').length,
        })""")
        print(f'[3] SVG 元素: {paints}')
        if paints.get('circles') != d['nodes']:
            fails.append(f'SVG 圆点数 {paints.get("circles")} 与子图节点数 {d["nodes"]} 不符')
        if paints.get('rings') != len(d['seeds']):
            fails.append(f'种子外圈数 {paints.get("rings")} 与种子数 {len(d["seeds"])} 不符')
        if paints.get('lines') != d['links']:
            fails.append(f'SVG 连线数 {paints.get("lines")} 与子图关系数 {d["links"]} 不符')
        if paints.get('seedRows') != len(d['seeds']):
            fails.append('侧栏种子行数与实际种子数不符')

        # 子串污染：TransE 不应额外命中 Tran 这类无意义片段
        if any(s != 'TransE' and 'transe'.startswith(s.lower()) for s in d['seeds']):
            fails.append(f'种子中残留 TransE 的子串: {d["seeds"]}')

        # 多词问题
        d2 = json.loads(ask(ws, 'knowledge graph embedding'))
        print(f'[4] 问「knowledge graph embedding」→ 种子 {d2["seeds"][:3]}… '
              f'子图 {d2["nodes"]} 节点 / {d2["links"]} 边')
        if not d2.get('seeds'):
            fails.append('多词问题没有命中种子')
        if d2['nodes'] > 70:
            fails.append(f'子图节点数 {d2["nodes"]} 超过上限 70')
        # 被更长命中包含的短名字应被剔除
        low = [s.lower() for s in d2['seeds']]
        for i, a in enumerate(low):
            for j, b in enumerate(low):
                if i != j and b in a:
                    fails.append(f'种子 {a!r} 是被 {b!r} 包含的短名字，本应剔除')

        # 匹配不到时应当如实为空，而不是猜
        d3 = json.loads(ask(ws, 'q w e r t y'))
        empty_txt = ev(ws, "document.getElementById('hint2') ? document.getElementById('hint2').textContent : ''")
        print(f'[5] 无命中问题 → 种子 {d3["seeds"]}，画布提示: {str(empty_txt)[:44]!r}')
        if d3['seeds']:
            fails.append(f'无关问题不应命中种子，实际: {d3["seeds"]}')
        if '没有命中' not in str(empty_txt):
            fails.append('无命中时未给出"没有命中"的明确提示')

        # 示例按钮
        ex = ev(ws, """(() => {
          const b = document.querySelector('.ex');
          b.click();
          return [b.dataset.q, JSON.stringify(window.__lastAsk)];
        })()""")
        print(f'[6] 点击示例「{ex[0]}」→ {ex[1][:90]}')
        if 'TransE' not in ex[1]:
            fails.append('示例按钮未触发检索')

        # 截图留档
        ask(ws, 'knowledge graph embedding')
        time.sleep(0.4)
        os.makedirs('output/verify', exist_ok=True)
        shot = cdp(ws, 'Page.captureScreenshot', {'format': 'png'})
        with open('output/verify/pathqa.png', 'wb') as f:
            f.write(base64.b64decode(shot['result']['data']))
        print('\n截图已保存 output/verify/pathqa.png')

        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    print('\n' + '=' * 58)
    if fails:
        print(f'未通过 {len(fails)} 项：')
        for f in fails:
            print('  · ' + f)
        return 1
    print('全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
