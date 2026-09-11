# -*- coding: utf-8 -*-
"""
验证本轮 UI 细节改动。

    python tools/cdp_verify_ui.py [社区号]

检查：
  1. 列表全量（不再截断在 400 项）
  2. 顶部副标题：显示数与总数相同时不重复写
  3. 搜索框清除按钮：有内容时出现，点击后清空
  4. 空状态提示
  5. 在三维画面里点击实体 = 与点击列表项同种效果
  6. 再次点击同一处 = 取消选中
"""
import json
import subprocess
import sys
import time
import urllib.request

import websocket

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
CI = sys.argv[1] if len(sys.argv) > 1 else '0'
URL = f'http://127.0.0.1:8098/web/kg-nebula.html?c={CI}'


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


def set_search(ws, val):
    return ev(ws, "(function(){var s=document.getElementById('search');"
                 f"s.value={json.dumps(val)};"
                 "s.dispatchEvent(new Event('input'));return s.value;})()")


def click_at(ws, x, y):
    ev(ws, "(function(){var cv=document.querySelector('canvas');"
           f"var o={{clientX:{x},clientY:{y},bubbles:true}};"
           "cv.dispatchEvent(new PointerEvent('pointerdown',o));"
           "cv.dispatchEvent(new PointerEvent('pointerup',o));return 1;})()")


def main():
    port = 9700 + int(time.time() * 1000) % 90
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

        res = {}
        res['列表项数'] = ev(ws, 'window.KGNebula.listCount()')
        res['顶部副标题'] = ev(ws, 'window.KGNebula.subText()')

        set_search(ws, 'temporal')
        time.sleep(0.3)
        res['搜索后有清除按钮'] = ev(
            ws, "getComputedStyle(document.getElementById('searchClear')).display !== 'none'")
        res['搜索命中数'] = ev(ws, 'window.KGNebula.listCount()')

        ev(ws, "document.getElementById('searchClear').click()")
        time.sleep(0.3)
        res['清除后搜索值'] = ev(ws, "document.getElementById('search').value")
        res['清除后列表'] = ev(ws, 'window.KGNebula.listCount()')

        set_search(ws, 'zzzqqq')
        time.sleep(0.3)
        res['空状态文本'] = ev(
            ws, "document.querySelector('#list .empty')?.textContent?.replace(/\\s+/g,' ').trim()")
        set_search(ws, '')
        time.sleep(0.3)

        scr = ev(ws, """(function(){
          for (var i = 0; i < 120; i++) {
            var s = window.KGNebula.nodeScreen(i);
            if (s && s.z > -1 && s.z < 1 &&
                s.x > 320 && s.x < innerWidth - 340 &&
                s.y > 90 && s.y < innerHeight - 60) return s;
          }
          return null;
        })()""")
        res['可点节点'] = scr and f"({round(scr['x'])},{round(scr['y'])})"
        if scr:
            click_at(ws, scr['x'], scr['y'])
            time.sleep(0.5)
            res['点三维节点后高亮环'] = ev(ws, 'window.KGNebula.haloOn()')
            res['点三维节点后面板'] = ev(ws, 'window.KGNebula.panelOpen()')
            res['点三维节点后列表选中'] = ev(ws, "!!document.querySelector('#list .item.sel')")
            click_at(ws, scr['x'], scr['y'])
            time.sleep(0.5)
            res['再点一次后高亮环'] = ev(ws, 'window.KGNebula.haloOn()')

        print(json.dumps(res, ensure_ascii=False, indent=2))
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
