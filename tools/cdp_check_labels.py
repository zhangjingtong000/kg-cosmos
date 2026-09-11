# -*- coding: utf-8 -*-
"""
验证 CSS2D 标签的 DOM 是否泄漏。

    python tools/cdp_check_labels.py

背景：CSS2DObject 的 DOM 是手动插进页面的，CSS2DRenderer 只会更新仍在场景
树里的对象。若清理时只做 overlay.remove(o) 而不移除 DOM，反复进出社区会让
标签越积越多，表现就是"同一段文字出现在好几处、且不在自己位置上"。

判据：反复进出后 .label 元素数量应保持恒定。
"""
import json
import subprocess
import sys
import time
import urllib.request

import websocket

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
URL = 'http://127.0.0.1:8098/web/kg-nebula.html?c=17'
ROUNDS = 4


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
    return r.get('result', {}).get('result', {}).get('value')


def main():
    port = 9800 + int(time.time() * 1000) % 100
    proc = subprocess.Popen([
        CHROME, '--headless=new', '--enable-unsafe-swiftshader', '--disable-gpu',
        '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
        '--remote-allow-origins=*', f'--remote-debugging-port={port}',
        '--window-size=1600,900', URL,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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

        for _ in range(400):
            if ev(ws, 'window.__ready === true'):
                break
            time.sleep(0.1)

        counts = [ev(ws, 'window.KGNebula.labelCount()')]
        for _ in range(ROUNDS):
            ev(ws, 'window.KGNebula.exit()')
            time.sleep(0.5)
            ev(ws, 'window.KGNebula.enter(17)')
            time.sleep(1.0)
            counts.append(ev(ws, 'window.KGNebula.labelCount()'))

        print(f'每轮进出后的 .label 元素数量：{counts}')
        ok = len(set(counts)) == 1 and counts[0]
        print('判定：' + ('通过（无泄漏）' if ok else '失败（DOM 在累积）'))
        ws.close()
        return 0 if ok else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == '__main__':
    sys.exit(main())
