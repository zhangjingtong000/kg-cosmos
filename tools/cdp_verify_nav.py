# -*- coding: utf-8 -*-
"""验证导航页（index.html）：卡片结构、链接可达、示意图未溢出。

其中第 3 项是**防回归**：初版把示意图形写成 `<div class="art dots">`（两个类
同一元素），`.dots` 的 `position:absolute; inset:0` 覆盖了 `.art` 的定位，
使示意图脱离文档流并铺满整张卡片——卡片矮了 76px、圆点压住标题。
这个 bug 只看截图容易被当成"视觉风格"，是实测 rect 才定位到的，
所以在这里固化成断言。

用法：
  python tools/cdp_verify_nav.py
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
        return {'__error__': res['exceptionDetails'].get('text')}
    return res.get('result', {}).get('value')


def main():
    port = 9460 + (int(time.time() * 1000) % 40)
    proc = subprocess.Popen([
        CHROME, '--headless=new', '--enable-unsafe-swiftshader', '--disable-gpu',
        '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
        f'--remote-debugging-port={port}', '--remote-allow-origins=*',
        '--window-size=1400,1000', f'{BASE}/web/index.html',
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
        time.sleep(1.0)

        cards = ev(ws, """[...document.querySelectorAll('a.card')].map(c => {
          const art = c.querySelector('.art');
          const inner = art ? art.firstElementChild : null;
          const b = (el) => { const r = el.getBoundingClientRect();
                              return {top: +r.top.toFixed(1), h: +r.height.toFixed(1)}; };
          return {
            href: c.getAttribute('href'),
            title: c.querySelector('h2').textContent,
            cardH: +c.getBoundingClientRect().height.toFixed(1),
            art: art ? b(art) : null,
            innerClass: inner ? inner.className : null,
            innerH: inner ? b(inner).h : null,
          };
        })""")
        print(f'[1] 卡片数: {len(cards)}')
        for c in cards:
            print(f"    {c['title']:14s} → {c['href']:22s} 卡片高 {c['cardH']:6.1f}"
                  f"  art {c['art']['h'] if c['art'] else 0:.0f}px  内层 {c['innerClass']}")
        if len(cards) != 5:
            fails.append(f'主卡片应为 5 张，实为 {len(cards)}')

        # 2. 防回归：示意图容器必须是 .art 的**子元素**，且高度等于 art
        for c in cards:
            if not c['art'] or c['innerH'] is None:
                fails.append(f"{c['title']} 缺少示意图容器")
                continue
            if abs(c['innerH'] - c['art']['h']) > 1:
                fails.append(
                    f"{c['title']} 的示意图高度 {c['innerH']} ≠ .art 高度 {c['art']['h']}"
                    f"（说明它脱离了 .art 的定位上下文）")
            if c['art']['h'] != 62:
                fails.append(f"{c['title']} 的 .art 高度为 {c['art']['h']}，应为 62")
        if not fails:
            print('[2] OK 示意图都嵌在 .art 内，高度与 .art 一致（未脱离文档流）')

        # 3. 同行卡片等高（grid 的 stretch 生效）
        tops = {}
        for c in cards:
            tops.setdefault(round(c['art']['top']), []).append(c['cardH'])
        for top, hs in tops.items():
            if len(hs) > 1 and max(hs) - min(hs) > 1:
                fails.append(f'同一行卡片高度不一致: {hs}')
        if not any('同一行' in f for f in fails):
            print(f'[3] OK 同一行卡片等高（行数 {len(tops)}）')

        # 4. 所有链接可达
        hrefs = [c['href'] for c in cards] + ev(
            ws, "[...document.querySelectorAll('.legacy a')].map(a => a.getAttribute('href'))")
        bad = []
        for h in hrefs:
            try:
                req = urllib.request.Request(f'{BASE}/web/{h}', method='HEAD')
                with urllib.request.urlopen(req, timeout=6) as r:
                    if r.status != 200:
                        bad.append(f'{h} → {r.status}')
            except Exception as e:
                bad.append(f'{h} → {e}')
        print(f'[4] 检查 {len(hrefs)} 个链接，失效 {len(bad)} 个')
        if bad:
            fails.extend(bad)

        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    print('\n' + '=' * 56)
    if fails:
        print(f'未通过 {len(fails)} 项：')
        for f in fails:
            print('  · ' + f)
        return 1
    print('全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
