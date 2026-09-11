# -*- coding: utf-8 -*-
"""
用 CDP 精确控制截图时机。

    python tools/cdp_shot.py <url> <输出png> [宽 高]

为什么不直接用 `chrome --screenshot`：它的抓帧时机不确定。页面里有
requestAnimationFrame 循环和顶层 await 时，实测同一个 URL 会在
"概览层 / 空背景 / 内部视图" 三种状态之间随机取到一种，没法用来判断
改动是否生效。

这里改成：页面在渲染完成后把 window.__ready 置为 true，脚本轮询这个
标志，确认后再调 Page.captureScreenshot。
"""
import base64
import json
import subprocess
import sys
import time
import urllib.request

import websocket

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'


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


def main():
    url = sys.argv[1]
    out = sys.argv[2]
    w = sys.argv[3] if len(sys.argv) > 3 else '1600'
    h = sys.argv[4] if len(sys.argv) > 4 else '900'
    port = 9333 + (int(time.time() * 1000) % 500)

    proc = subprocess.Popen([
        CHROME, '--headless=new', '--enable-unsafe-swiftshader', '--disable-gpu',
        '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
        f'--remote-debugging-port={port}', '--remote-allow-origins=*',
        f'--window-size={w},{h}', url,
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

        ready = False
        for _ in range(400):
            try:
                r = cdp(ws, 'Runtime.evaluate',
                        {'expression': 'window.__ready === true', 'returnByValue': True})
                if r.get('result', {}).get('result', {}).get('value') is True:
                    ready = True
                    break
            except Exception:
                pass
            time.sleep(0.1)

        time.sleep(0.35)
        shot = cdp(ws, 'Page.captureScreenshot', {'format': 'png'})
        data = base64.b64decode(shot['result']['data'])
        with open(out, 'wb') as f:
            f.write(data)
        print(f'{"ready" if ready else "TIMEOUT"} -> {out} ({len(data)} bytes)')
        ws.close()
        return 0 if ready else 2
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == '__main__':
    sys.exit(main())
