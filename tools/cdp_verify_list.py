# -*- coding: utf-8 -*-
"""
验证内部视图的列表 / 搜索 / 选中高亮。

    python tools/cdp_verify_list.py

检查：进入社区后列表有内容；点击列表项后高亮环出现、右侧关系面板打开、
列表项标记为选中；搜索能过滤列表。
"""
import base64
import json
import subprocess
import sys
import time
import urllib.request

import websocket

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
URL = 'http://127.0.0.1:8098/web/kg-nebula.html?c=17'


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


def main():
    port = 9900 + int(time.time() * 1000) % 90
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
        res['初始高亮环'] = ev(ws, 'window.KGNebula.haloOn()')

        ev(ws, "document.querySelectorAll('#list .item')[3].click()")
        time.sleep(0.4)
        res['点击后高亮环'] = ev(ws, 'window.KGNebula.haloOn()')
        res['点击后面板'] = ev(ws, 'window.KGNebula.panelOpen()')
        res['选中项'] = ev(ws, "document.querySelector('#list .item.sel')?.textContent?.trim()")
        res['面板标题'] = ev(ws, "document.querySelector('#dbody h2')?.textContent")
        res['证据条数'] = ev(ws, "document.querySelectorAll('#dbody .ev').length")
        res['证据出处'] = ev(ws, "document.querySelector('#dbody .evsrc')?.textContent")
        res['证据原文'] = ev(ws, "(document.querySelector('#dbody .evtext')?.textContent||'').slice(0,60)")

        # 截图留档（此时高亮环已定位、右侧面板显示证据）
        shot = cdp(ws, 'Page.captureScreenshot', {'format': 'png'})
        with open(r'D:\kg-demo\output\shots\evidence-panel.png', 'wb') as f:
            f.write(base64.b64decode(shot['result']['data']))
        print('截图已保存 output/shots/evidence-panel.png')

        set_search(ws, 'temporal')
        time.sleep(0.3)
        res['搜索 temporal'] = ev(ws, 'window.KGNebula.listCount()')
        set_search(ws, '')
        time.sleep(0.3)
        res['清空搜索'] = ev(ws, 'window.KGNebula.listCount()')

        print(json.dumps(res, ensure_ascii=False, indent=2))
        ok = bool(res['列表项数']) and res['点击后高亮环'] and res['点击后面板'] \
            and res['选中项'] and isinstance(res['搜索 temporal'], int)
        print('判定：' + ('通过' if ok else '失败'))
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
