# -*- coding: utf-8 -*-
"""验证「离线守卫」：屏蔽外部域名后，依赖 CDN 的页面必须给出明确提示而非白屏。

断言的是**提示真的出现在 DOM 里**（`#net-guard`），而不是"代码里有这段逻辑"。

用法：
  python tools/cdp_verify_net_guard.py            # 测全部 5 个页面
"""
import json
import subprocess
import sys
import time
import urllib.request

import websocket

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
BASE = 'http://127.0.0.1:8098'
PAGES = [
    'community-atlas.html', 'graph-2d.html', 'force-explorer.html',
    'force-graph.html', 'official-3-text.html',
]
EXTERNAL = ['*esm.sh*', '*unpkg.com*', '*jsdelivr.net*', '*cdnjs*']


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
    fails = []
    for page in PAGES:
        port = 9640 + (int(time.time() * 1000) % 60)
        proc = subprocess.Popen([
            CHROME, '--headless=new', '--enable-unsafe-swiftshader', '--disable-gpu',
            '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
            f'--remote-debugging-port={port}', '--remote-allow-origins=*',
            '--window-size=1200,800', 'about:blank',
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            targets = None
            for _ in range(60):
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/json', timeout=2) as r:
                        targets = json.load(r)
                    if any(t.get('type') == 'page' for t in targets):
                        break
                except Exception:
                    pass
                time.sleep(0.4)
            page_t = next(t for t in targets if t.get('type') == 'page')
            ws = websocket.create_connection(page_t['webSocketDebuggerUrl'], timeout=60)
            cdp(ws, 'Page.enable')
            cdp(ws, 'Runtime.enable')
            cdp(ws, 'Network.enable')
            cdp(ws, 'Network.setBlockedURLs', {'urls': EXTERNAL})
            cdp(ws, 'Page.navigate', {'url': f'{BASE}/web/{page}'})
            # net-guard 的兜底判定在 4.2s，留足时间
            time.sleep(6.5)
            guard = ev(ws, """(() => {
              const g = document.getElementById('net-guard');
              return g ? g.textContent.slice(0, 90) : null;
            })()""")
            canvas = ev(ws, "document.querySelectorAll('canvas').length")
            leak = ev(ws, """performance.getEntriesByType('resource')
              .filter(r => r.name.startsWith('http') && !r.name.includes('127.0.0.1')).length""")
            ok = guard is not None
            print(f"{'✅' if ok else '❌'} {page:26s} canvas={canvas} 外部请求={leak} "
                  f"提示={'有' if ok else '无'}")
            if ok:
                print(f"     {guard!r}")
            else:
                fails.append(f'{page} 断网时没有出现「需联网」提示（仍是白屏）')
            ws.close()
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

    print('\n' + '=' * 60)
    if fails:
        print(f'未通过 {len(fails)} 项：')
        for f in fails:
            print('  · ' + f)
        return 1
    print(f'全部通过：{len(PAGES)} 个页面断网时都会给出明确提示')
    return 0


if __name__ == '__main__':
    sys.exit(main())
