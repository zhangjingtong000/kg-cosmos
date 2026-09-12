# -*- coding: utf-8 -*-
"""
在概览层扫描出一个能触发社区悬浮面板的位置，停在那里并截图。

    python tools/cdp_shot_hover.py <输出png> [宽] [高]

headless 下没法用鼠标，所以这里按网格派发 pointermove，找到第一个让
#detail 打开的位置（同时报告命中的社区名）。
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import base64
import json
import subprocess
import sys
import time
import urllib.request

import websocket

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
URL = 'http://127.0.0.1:8098/web/kg-nebula.html'
OUT = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / 'output/shots/hover.png')
W = sys.argv[2] if len(sys.argv) > 2 else '1673'
H = sys.argv[3] if len(sys.argv) > 3 else '984'

SCAN = """(function(){
  var cv = document.querySelector('canvas');
  var det = document.getElementById('detail');
  for (var y = 130; y < innerHeight - 40; y += 32) {
    for (var x = 250; x < innerWidth - 380; x += 32) {
      cv.dispatchEvent(new PointerEvent('pointermove',
        {clientX: x, clientY: y, bubbles: true}));
      if (det.classList.contains('open')) {
        var h = document.querySelector('#dbody h2');
        return {x: x, y: y, name: h ? h.textContent : null};
      }
    }
  }
  return null;
})()"""


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
    port = 9500 + int(time.time() * 1000) % 90
    proc = subprocess.Popen([
        CHROME, '--headless=new', '--enable-unsafe-swiftshader', '--disable-gpu',
        '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
        '--remote-allow-origins=*', f'--remote-debugging-port={port}',
        f'--window-size={W},{H}', URL,
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

        hit = ev(ws, SCAN)
        print('悬浮命中:', hit)
        time.sleep(0.6)
        shot = cdp(ws, 'Page.captureScreenshot', {'format': 'png'})
        with open(OUT, 'wb') as f:
            f.write(base64.b64decode(shot['result']['data']))
        print('saved', OUT)
        ws.close()
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == '__main__':
    sys.exit(main())
