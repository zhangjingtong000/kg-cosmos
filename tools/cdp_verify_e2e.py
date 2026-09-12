# -*- coding: utf-8 -*-
"""端到端测试：后端 API ↔ 前端 是否真的连通，且静态回退仍然可用。

【背景（对抗评审的 blocker）】
  `src/app.py` 有 5 个 API 端点，而前端全部读预生成的静态 JSON，
  **一个端点都没被调用过** —— 也就是"系统"其实只有展示侧在跑。
  修法是把前端数据面暴露成 `/api/kg/*` 并让前端优先走它、
  失败回退静态文件（保持「离线演示铁律」）。

  但仅仅"能跑"不够：必须有一条**可执行的证据**证明两端真的连上了，
  而不是回退路径在默默兜底。所以本脚本用 CDP 读页面里的
  `KGData.source`，断言它确实是 'api'。

【测三件事】
  ① 后端起来后 /health 与数据面端点可用；
  ② 通过**后端端口**打开页面时，页面确实走了 API（KGData.usedApi() === true）；
  ③ 通过**纯静态端口**打开页面时，自动回退且功能不缺（usedApi() === false，
     但页面照常渲染）——这一条守的是「离线演示铁律」。

用法：
  python tools/cdp_verify_e2e.py
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

import websocket

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
STATIC = 'http://127.0.0.1:8098'
BACKEND = 'http://127.0.0.1:8000'


def http_json(url, timeout=8):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def cdp_open(url, port_base=9870, wait_ready=True, timeout_s=30):
    """起一个 headless Chrome 打开 url，返回 (proc, ws, ev)。"""
    port = port_base + (int(time.time() * 1000) % 50)
    proc = subprocess.Popen([
        CHROME, '--headless=new', '--enable-unsafe-swiftshader', '--disable-gpu',
        '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
        f'--remote-debugging-port={port}', '--remote-allow-origins=*',
        '--window-size=1400,900', url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    targets = None
    for _ in range(90):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/json', timeout=2) as r:
                targets = json.load(r)
            if any(t.get('type') == 'page' for t in targets):
                break
        except Exception:
            pass
        time.sleep(0.4)
    if not targets:
        proc.terminate()
        raise RuntimeError('CDP 未就绪')
    page = next(t for t in targets if t.get('type') == 'page')
    ws = websocket.create_connection(page['webSocketDebuggerUrl'], timeout=60)

    def ev(expr):
        ws.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate',
                            'params': {'expression': expr, 'returnByValue': True,
                                       'awaitPromise': True}}))
        while True:
            m = json.loads(ws.recv())
            if m.get('id') == 1:
                res = m.get('result', {}).get('result', {})
                return res.get('value')

    if wait_ready:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if ev('window.__ready === true') is True:
                break
            time.sleep(0.25)
    return proc, ws, ev


def main():
    fails = []

    # ── ① 后端可用性 ──
    print('=== ① 后端数据面 ===')
    try:
        h = http_json(f'{BACKEND}/health')
        print(f'  /health ok={h.get("ok")} kg_data_files={len(h.get("kg_data_files", []))} '
              f'quality_files={h.get("quality_files")}')
        if not h.get('ok'):
            fails.append('/health 未返回 ok')
    except Exception as e:
        fails.append(f'后端不可用（{type(e).__name__}: {e}）——请先起 python src/app.py')
        print('  ❌', fails[-1])
        print('\n后端没起来，后续测试无法进行。')
        return 1

    for name in ('analytics.json', 'filaments.json', 'comm-0.json'):
        try:
            http_json(f'{BACKEND}/api/kg/{name}')
            print(f'  /api/kg/{name} 200')
        except Exception as e:
            fails.append(f'/api/kg/{name} 取不到：{e}')
            print(f'  ❌ /api/kg/{name} {e}')

    try:
        q = http_json(f'{BACKEND}/api/quality/l9-recall.json')
        print(f'  /api/quality/l9-recall.json 200（endpoint_recall={q.get("endpoint_recall")}）')
    except Exception as e:
        fails.append(f'/api/quality 取不到：{e}')
        print(f'  ❌ /api/quality {e}')

    # 路径穿越必须被挡住
    try:
        http_json(f'{BACKEND}/api/kg/..%2F..%2Frequirements.txt')
        fails.append('路径穿越没有被拦截')
        print('  ❌ 路径穿越未被拦截')
    except urllib.error.HTTPError as e:
        print(f'  路径穿越被拦（HTTP {e.code}）✅')
    except Exception as e:
        print(f'  路径穿越：{type(e).__name__}')

    # ── ② 页面通过后端端口打开 → 必须走 API ──
    print('\n=== ② 经后端端口打开页面（应走 API）===')
    for page in ('analytics.html', 'kg-nebula.html'):
        proc, ws, ev = cdp_open(f'{BACKEND}/{page}', port_base=9880)
        try:
            src = ev('JSON.stringify(window.KGData ? KGData.source : null)')
            used = ev('window.KGData ? KGData.usedApi() : null')
            print(f'  {page:18s} source={src}  usedApi={used}')
            if used is not True:
                fails.append(f'{page} 通过后端打开却没有走 API（source={src}）')
        finally:
            ws.close()
            proc.terminate()

    # ── ③ 页面通过静态端口打开 → 必须回退且功能不缺 ──
    print('\n=== ③ 经纯静态端口打开页面（应回退，守离线演示）===')
    for page in ('analytics.html', 'kg-nebula.html'):
        proc, ws, ev = cdp_open(f'{STATIC}/web/{page}', port_base=9920)
        try:
            src = ev('JSON.stringify(window.KGData ? KGData.source : null)')
            used = ev('window.KGData ? KGData.usedApi() : null')
            ready = ev('window.__ready === true')
            print(f'  {page:18s} ready={ready} source={src} usedApi={used}')
            if used is not False:
                fails.append(f'{page} 在纯静态站下没有正确回退（usedApi={used}）')
            if ready is not True:
                fails.append(f'{page} 在纯静态站下未能就绪')
        finally:
            ws.close()
            proc.terminate()

    print('\n' + '=' * 60)
    if fails:
        print(f'未通过 {len(fails)} 项：')
        for f in fails:
            print('  · ' + f)
        return 1
    print('全部通过：前后端已连通，且静态回退仍然可用')
    return 0


if __name__ == '__main__':
    sys.exit(main())
