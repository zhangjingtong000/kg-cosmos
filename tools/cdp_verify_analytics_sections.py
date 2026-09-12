# -*- coding: utf-8 -*-
"""确认 analytics 面板的两个新区块真的渲染出来了（不是 hidden 状态）。

为什么要单独验：数据接进去了不等于**显示**得出来。
`<section id="l5vSec" hidden>` 只有在 `boot()` 里拿到 `l5_validity` 后才会
取消 hidden；如果字段名对不上，页面上什么都不会变，而数字验证仍会通过
（因为验证脚本读的是 `analytics.json`，不是 DOM）。
"""
import json
import subprocess
import sys
import time
import urllib.request

import websocket

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
URL = 'http://127.0.0.1:8098/web/analytics.html'
port = 9870

p = subprocess.Popen([
    CHROME, '--headless=new', '--enable-unsafe-swiftshader', '--disable-gpu',
    '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
    f'--remote-debugging-port={port}', '--remote-allow-origins=*',
    '--window-size=1400,2600', URL,
], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

EXPECT = [
    ("l5vSec 可见", "document.getElementById('l5vSec').hidden === false"),
    ("l7rSec 可见", "document.getElementById('l7rSec').hidden === false"),
    ("L5 KPI 有 5 个", "document.querySelectorAll('#l5v .kpi').length === 5"),
    # 注意 `:not(.strat)`：页面现在有两张 `.sens` 表（阈值表 + 策略对比表），
    # 不加限定会匹配到两张表的所有行。初版就因此误报"阈值表不是 5 行"。
    ("阈值表有 5 行",
     "document.querySelectorAll('#l5vSens table.sens:not(.strat) tbody tr').length === 5"),
    ("策略对比表有 >=6 行",
     "document.querySelectorAll('#l5vSens table.sens.strat tbody tr').length >= 6"),
    ("策略表标出了现行与最优",
     "/现行/.test(document.getElementById('l5vSens').innerText)"
     " && /最优/.test(document.getElementById('l5vSens').innerText)"),
    ("L7 归因 KPI 有 3 个", "document.querySelectorAll('#l7r .kpi').length === 3"),
    # L9 关系召回：比端点召回更根本的读数（约四成 vs 86%）
    ("l9rSec 可见", "document.getElementById('l9rSec').hidden === false"),
    ("L9 关系召回 KPI 有 3 个", "document.querySelectorAll('#l9r .kpi').length === 3"),
    ("关系召回区块含 86.2% 与 47.6%",
     "/86\\.2%/.test(document.getElementById('l9r').innerText)"
     " && /47\\.6%/.test(document.getElementById('l9r').innerText)"),
    ("L5 区块含 PPV 字样", "/PPV/.test(document.getElementById('l5v').innerText)"),
]

try:
    t = None
    for _ in range(80):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/json', timeout=2) as r:
                t = json.load(r)
            if any(x.get('type') == 'page' for x in t):
                break
        except Exception:
            pass
        time.sleep(0.4)
    pg = next(x for x in t if x.get('type') == 'page')
    ws = websocket.create_connection(pg['webSocketDebuggerUrl'], timeout=30)

    def ev(expr):
        ws.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate',
                            'params': {'expression': expr, 'returnByValue': True}}))
        while True:
            m = json.loads(ws.recv())
            if m.get('id') == 1:
                return m.get('result', {}).get('result', {}).get('value')

    for _ in range(80):
        if ev('window.__ready === true'):
            break
        time.sleep(0.25)
    time.sleep(1.0)

    fails = []
    for label, expr in EXPECT:
        ok = ev(expr)
        print(f"  {'✅' if ok else '❌'} {label}")
        if not ok:
            fails.append(label)

    # 顺带打印关键文案，确认不是空元素
    print('\n  L5 区块首行:', str(ev(
        "document.querySelector('#l5v .kpi b') ? "
        "document.querySelector('#l5v .kpi b').innerText : 'N/A'"))[:60])
    print('  阈值表首行:', str(ev(
        "document.querySelector('#l5vSens table.sens tbody tr') ? "
        "document.querySelector('#l5vSens table.sens tbody tr').innerText"
        ".replace(/\\s+/g, ' ') : 'N/A'"))[:80])
    print('  L7 区块:', str(ev(
        "document.querySelector('#l7r .kpi small') ? "
        "document.querySelector('#l7r .kpi small').innerText : 'N/A'"))[:60])
    ws.close()
finally:
    p.terminate()

print()
if fails:
    print('未通过：', fails)
    sys.exit(1)
print('全部通过：三个新区块已正确渲染')
