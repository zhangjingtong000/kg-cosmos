# -*- coding: utf-8 -*-
"""验证「图谱分析面板」（T6.5）的渲染与关键数字。

断言以 DOM 实际内容与数据文件为准，而不是截图观感：
面板上的每个数字都必须能在 data/quality/ 的产物里找到出处，
否则就是手工写死的、会随数据变化而失真的假数字。

用法：
  python tools/cdp_verify_analytics.py
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websocket

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
URL = 'http://127.0.0.1:8098/web/analytics.html'
QUAL = Path(r'D:\kg-demo\data\quality')


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


def main():
    port = 9900 + (int(time.time() * 1000) % 90)
    proc = subprocess.Popen([
        CHROME, '--headless=new', '--enable-unsafe-swiftshader', '--disable-gpu',
        '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
        f'--remote-debugging-port={port}', '--remote-allow-origins=*',
        '--window-size=1400,1100', URL,
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
        ws = websocket.create_connection(page['webSocketDebuggerUrl'], timeout=60)
        cdp(ws, 'Page.enable')
        cdp(ws, 'Runtime.enable')

        ok = False
        for _ in range(300):
            if ev(ws, 'window.__ready === true') is True:
                ok = True
                break
            time.sleep(0.1)
        print('页面就绪:', ok)
        if not ok:
            fails.append('页面未就绪')

        # 1. 各区块的条目数
        counts = ev(ws, """({
          kpi: document.querySelectorAll('#kpis .kpi').length,
          layers: document.querySelectorAll('#layers .layer').length,
          gov: document.querySelectorAll('#gov .gcard').length,
          types: document.querySelectorAll('#types .bar').length,
          hits: document.querySelectorAll('#hits .bar').length,
          err: document.querySelector('#lede .err') ? 1 : 0,
        })""")
        print(f'[1] 区块条目数: {counts}')
        if counts.get('err'):
            fails.append('页面显示了数据加载错误')
        if counts.get('layers') != 6:
            fails.append(f'六层应为 6 行，实为 {counts.get("layers")}')
        if counts.get('gov') != 3:
            fails.append(f'治理对比应为 3 张卡片，实为 {counts.get("gov")}')
        if counts.get('hits') != 3:
            fails.append(f'端点命中应为 3 行，实为 {counts.get("hits")}')

        # 2. 面板上的数字必须与数据文件一致（防止写死）
        six = json.loads((QUAL / 'l5-six-layer.json').read_text(encoding='utf-8'))
        cur = json.loads((QUAL / 'relation-verify-all.json').read_text(encoding='utf-8'))
        want = {
            'L5': round(six['L5_triplet']['all'] * 100, 1),
            'L4': round(six['L4_predicate']['all'] * 100, 1),
            'L3': round(six['L3_endpoints']['all'] * 100, 1),
        }
        got = ev(ws, """(() => {
          const o = {};
          document.querySelectorAll('#layers .layer').forEach(el => {
            const id = el.querySelector('.nm').textContent.trim().slice(0, 2);
            o[id] = el.querySelector('.val').textContent.trim();
          });
          return o;
        })()""")
        print(f'[2] 页面六层数值: {got}   （数据文件 L3/L4/L5: {want}）')
        for k, v in want.items():
            if f'{v}%' != str(got.get(k, '')).replace(' ', ''):
                fails.append(f'{k} 页面显示 {got.get(k)!r}，数据文件是 {v}%')

        # 3. 关系类型行的通过率是否与该类型实际一致
        t = ev(ws, """(() => {
          const el = document.querySelector('#types .bar');
          return [el.querySelector('.nm').textContent.trim(),
                  el.querySelector('.n').textContent.trim(),
                  el.querySelector('.r').textContent.trim()];
        })()""")
        from collections import Counter
        buckets = Counter()
        passed = Counter()
        for e in cur['edges']:
            buckets[e['type']] += 1
            if e.get('pass') in (True, 'True'):
                passed[e['type']] += 1
        name = t[0]
        real_n = buckets.get(name)
        real_r = f'{passed.get(name, 0) / real_n * 100:.1f}%' if real_n else None
        print(f'[3] 首个关系类型行: {t}   实际 n={real_n} 通过率={real_r}')
        if str(real_n) != t[1]:
            fails.append(f'关系类型 {name} 的边数不符：页面 {t[1]} vs 实际 {real_n}')
        if real_r != t[2]:
            fails.append(f'关系类型 {name} 的通过率不符：页面 {t[2]} vs 实际 {real_r}')

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
    print('全部通过：面板上的数字均可追溯回 data/quality/ 产物')
    return 0


if __name__ == '__main__':
    sys.exit(main())
