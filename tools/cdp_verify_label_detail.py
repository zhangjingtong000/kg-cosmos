# -*- coding: utf-8 -*-
"""验证两个视觉细节改进：节点级标签门槛 + 概览层深度遮挡。

这两条都来自可视化调研（见 docs/MAINSTREAM_SOLUTIONS_SURVEY.md §4），
共同点是"只看截图分不出对错"，所以都以可测的量做断言：

  · 节点级门槛：进社区后，有资格拿标签的节点数应当**远小于**节点总数
    （旧实现按全局缩放档位发预算，与节点自身屏幕尺寸无关）；
  · 深度遮挡：概览层应当**存在被压暗的社区标签**——一个都没有说明遮挡没生效，
    全部都被压暗说明半径设得过大。

用法：
  python tools/cdp_verify_label_detail.py
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
    port = 9520 + (int(time.time() * 1000) % 60)
    # 先停在概览层
    proc = subprocess.Popen([
        CHROME, '--headless=new', '--enable-unsafe-swiftshader', '--disable-gpu',
        '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
        f'--remote-debugging-port={port}', '--remote-allow-origins=*',
        '--window-size=1400,900', f'{BASE}/web/kg-nebula.html',
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

        ok = False
        for _ in range(400):
            if ev(ws, 'window.__ready === true') is True:
                ok = True
                break
            time.sleep(0.1)
        print('页面就绪:', ok)
        if not ok:
            fails.append('页面未就绪')

        # 让遮挡计算跑几轮（每 5 帧一次）
        time.sleep(1.2)

        # ── 1. 概览层深度遮挡 ──
        n_small = ev(ws, 'KGNebula.labelMinRadius()')
        occ = ev(ws, 'KGNebula.occludedCount()')
        hist = ev(ws, 'JSON.stringify(KGNebula.labelOpacityHistogram())')
        total_labels = ev(ws, "document.querySelectorAll('.label').length")
        print(f'[1] 概览层：社区标签 {total_labels} 个，被遮挡压暗 {occ} 个')
        print(f'    透明度直方图: {hist}')
        print(f'    节点级门槛阈值: {n_small} px')
        if occ is None:
            fails.append('occludedCount() 不可用')
        elif occ == 0:
            fails.append('没有任何标签被遮挡压暗 —— 遮挡剔除没有生效')
        elif occ == total_labels:
            fails.append('全部标签都被压暗 —— 遮挡半径设得过大')
        else:
            print(f'    OK 遮挡生效且克制（{occ}/{total_labels}）')

        # ── 2. 进社区，验证节点级门槛 ──
        ev(ws, 'KGNebula.enter(0)')
        time.sleep(2.5)
        eligible = ev(ws, 'KGNebula.eligibleLabelCount()')
        node_total = ev(ws, "KGNebula._internals().ud ? KGNebula._internals().ud.data.nodes.length : null")
        shown = ev(ws, "document.querySelectorAll('.label').length")
        print(f'[2] 内部视图：社区 0 共 {node_total} 个节点')
        print(f'    按节点级屏幕尺寸门槛，有资格拿标签的: {eligible}')
        print(f'    标签池 DOM 元素总数: {shown}')
        if not node_total or eligible is None:
            fails.append('内部视图数据不可用')
        else:
            if eligible >= node_total:
                fails.append(f'有资格的节点数 {eligible} 等于总数 {node_total} —— 门槛没起作用')
            elif eligible == 0:
                fails.append('没有任何节点达到标签门槛 —— 阈值设得过大')
            else:
                pct = eligible / node_total * 100
                print(f'    OK 门槛筛掉了 {100-pct:.1f}% 的节点（{eligible}/{node_total}）')
            if shown and eligible > shown:
                # 标签池本身有上限（150），这是预期的
                print(f'    （标签池上限 {shown}，故实际同时显示不超过它）')

        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    print('\n' + '=' * 58)
    if fails:
        print(f'未通过 {len(fails)} 项：')
        for f in fails:
            print('  · ' + f)
        return 1
    print('全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
