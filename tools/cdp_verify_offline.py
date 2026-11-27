# -*- coding: utf-8 -*-
"""离线可用性验证：断网后页面还能不能跑起来。

背景：早先的评审记录 §1 第 6 条写着「**离线演示铁律**：
现场演示要有预构建缓存图谱 + 本地 Ollama 双保险」。
但 `web/kg-nebula.html` 的 three.js 是通过 importmap 从 unpkg CDN 加载的
（`https://unpkg.com/three@0.160.0/...`）。若现场没有网络，整个 3D 视图会直接白屏。

本脚本用 CDP 的 `Network.emulateNetworkConditions` 把浏览器置为 offline，
再导航到页面，看它是否还能完成渲染。

用法：
  python tools/cdp_verify_offline.py            # 测星云页
  python tools/cdp_verify_offline.py web/analytics.html
  python tools/cdp_verify_offline.py web/kg-nebula.html web/index.html   # 多个页面
"""
import json
import os
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
        return {'__error__': res['exceptionDetails'].get('text')}
    return res.get('result', {}).get('value')


def main():
    targets = sys.argv[1:] or ['web/kg-nebula.html']

    # 支持一次传多个页面：**每个页面起一个独立的 Chrome 进程**。
    # 为什么不是"一个浏览器里连续导航两次"：CDP 的 `Network.setBlockedURLs` 是
    # 会话级设置，第二个页面会继承第一个留下的屏蔽/缓存状态，
    # 容易把上一页的结果混进来。分开进程，读数才只属于那一页。
    # （2026-09-20 加：此前 verify_all 只测 `web/kg-nebula.html` 一页，
    #   而 GSAP 编排的主战场 `web/index.html` 根本不在离线覆盖范围内。）
    if len(targets) > 1:
        rc = 0
        for t in targets:
            print()
            print('#' * 72)
            print(f'# 离线检查：{t}')
            print('#' * 72)
            rc |= subprocess.call([sys.executable, os.path.abspath(__file__), t])
        return 1 if rc else 0

    target = targets[0]
    port = 9560 + (int(time.time() * 1000) % 40)
    proc = subprocess.Popen([
        CHROME, '--headless=new', '--enable-unsafe-swiftshader', '--disable-gpu',
        '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
        f'--remote-debugging-port={port}', '--remote-allow-origins=*',
        '--window-size=1400,900', 'about:blank',
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
        cdp(ws, 'Page.enable')
        cdp(ws, 'Runtime.enable')
        cdp(ws, 'Network.enable')

        # 只屏蔽**外部**域名，保留本地 8098 服务。
        #
        # 第一版用的是 `Network.emulateNetworkConditions({offline: true})`，
        # 那会把本地服务一起断掉——于是页面连自己的 html/js/data 都取不到，
        # 测试必然失败，但失败原因与"是否依赖 CDN"无关。
        # 现场演示的真实场景是「本地服务可用 + 无外网」，所以要按域名屏蔽。
        EXTERNAL = ['*unpkg.com*', '*esm.sh*', '*jsdelivr.net*', '*cdnjs.cloudflare.com*',
                    '*fonts.googleapis.com*', '*fonts.gstatic.com*', '*cdnjs*']
        cdp(ws, 'Network.setBlockedURLs', {'urls': EXTERNAL})
        print('已屏蔽外部域名（保留本地服务）：', ', '.join(EXTERNAL[:4]), '…')

        # 顺便把请求失败也记下来，便于定位是哪一个资源被挡了
        cdp(ws, 'Page.navigate', {'url': f'{BASE}/{target}'})
        time.sleep(4.0)

        # 页面的本地资源（同源）应能加载；CDN 资源必然失败
        ready = ev(ws, 'window.__ready === true')
        err = ev(ws, "document.getElementById('hint2') ? document.getElementById('hint2').textContent.slice(0,120) : (document.querySelector('header h1') ? document.querySelector('header h1').textContent : '?')")
        three = ev(ws, "typeof window.KGNebula")
        canvas = ev(ws, "document.querySelectorAll('canvas').length")
        print(f'  window.__ready   = {ready}')
        print(f'  typeof KGNebula  = {three}')
        print(f'  canvas 数量      = {canvas}')
        print(f'  页面提示          = {str(err)[:110]!r}')

        if target.endswith('kg-nebula.html'):
            if three != 'object' or ready is not True:
                fails.append('屏蔽外部域名后星云页无法启动（仍依赖 CDN 资源）')
        elif ready is True:
            print('  （该页自行置位了 window.__ready）')
        else:
            # 官方示例搬运过来的页面没有 __ready 约定，改看是否真的起了 WebGL 画布
            if not canvas:
                fails.append(f'屏蔽外部域名后 {target} 没有生成 canvas')
            else:
                print(f'  （该页无 __ready 约定，改以 canvas={canvas} 判定已渲染）')

        # 收集失败的请求，直接指出是哪几个 CDN 资源
        logs = ev(ws, """(() => {
          const e = performance.getEntriesByType('resource');
          return e.filter(r => r.name.startsWith('http') && !r.name.includes('127.0.0.1'))
                  .map(r => r.name);
        })()""")
        print(f'\n外部资源请求 {len(logs or [])} 个：')
        for u in (logs or [])[:8]:
            print('   ' + u[:110])
        if logs:
            fails.append(f'页面仍依赖 {len(logs)} 个外部资源，离线不可用')

        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    print('\n' + '=' * 60)
    if fails:
        print('离线检查未通过：')
        for f in fails:
            print('  · ' + f)
        return 1
    print('离线检查通过：页面不依赖任何外部资源')
    return 0


if __name__ == '__main__':
    sys.exit(main())
