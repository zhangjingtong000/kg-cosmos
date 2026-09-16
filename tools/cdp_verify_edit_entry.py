# -*- coding: utf-8 -*-
r"""K1 的前端入口：实体详情里的「改名并提交改图计划」。

## 为什么它需要独立断言

这条路径替换的是原先"**只记录，不自动改图**"的终态 ——
`flagbar` 三个按钮走 `/api/flag`，把人工判断记进 `human-flags.json`，
**然后就没有下文了**。K1 给它接上出口：写一份计划文件，由
`tools/apply_edit_plan.py` 执行。

**三种失效都是静默的**，所以必须断言：
  1. **入口没渲染出来** —— 页面上什么都没有，用户以为"这功能不存在"；
  2. **`data-edit-node` 绑错了实体** —— 提交的计划会改错东西，**这最危险**；
  3. **空输入时的提示丢失** —— 用户点了没反应，不知道该填什么。

## 只测"不需要后端"的部分

静态站（8098）没有 `/api/kg/edit-plan`。所以这里**只验证界面与交互提示**：
真实提交路径由 `tests/test_edit_plan_api.py` 用 TestClient 覆盖
（那样才是在测后端，而不是测"静态站的 404 页面"）。
"""
import json
import subprocess
import sys
import time
import urllib.request

import websocket

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
BASE = 'http://127.0.0.1:8098'
# 实测：社区 0 的 nodes[6] 是 TransE（与 cdp_verify_node_detail.py 同一个目标）
CI, NODE_IDX = 0, 6


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
        return {'__error__': str(res['exceptionDetails'])[:200]}
    return res.get('result', {}).get('value')


def main():
    port = 9800 + (int(time.time() * 1000) % 30)
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
        for _ in range(400):
            if ev(ws, 'window.__ready === true') is True:
                break
            time.sleep(0.1)

        ev(ws, f'KGNebula.enter({CI})')
        time.sleep(2.5)
        ev(ws, f'KGNebula.select({NODE_IDX})')
        time.sleep(1.0)

        node = ev(ws, f'KGNebula._internals().ud.data.nodes[{NODE_IDX}].id')
        n_row = ev(ws, "document.querySelectorAll('#dbody .editrow').length")
        bound = ev(ws, "(()=>{const r=document.querySelector('#dbody .editrow');"
                       "return r && r.dataset ? r.dataset.editNode : null})()")
        has_input = ev(ws, "!!document.querySelector('#dbody .editrow input')")
        has_btn = ev(ws, "!!document.querySelector('#dbody .editrow button[data-edit]')")
        print(f'[1] 实体 = {node!r} · .editrow {n_row} 个 · 输入框 {has_input} · 按钮 {has_btn}')
        print(f'[2] data-edit-node = {bound!r}（应等于实体名）')

        if not n_row:
            fails.append('详情面板里没有改图计划入口（.editrow）—— 功能不可见')
        if not has_input or not has_btn:
            fails.append('入口缺输入框或提交按钮')
        if bound != node:
            fails.append(f'data-edit-node 绑错了：{bound!r} ≠ 实体名 {node!r} —— '
                         f'这会导致计划改错东西')

        # 空输入时的提示（**不需要后端**）
        ev(ws, "document.querySelector('#dbody .editrow button[data-edit]').click()")
        time.sleep(0.5)
        tip = ev(ws, "(document.querySelector('#dbody .editrow .editmsg')||{}).textContent || ''")
        print(f'[3] 空输入点击后的提示 = {tip!r}')
        if '新名字' not in (tip or ''):
            fails.append(f'空输入时没有提示"请先填新名字"，实际 {tip!r}')

        # 输入原名（= 没改动）时的提示
        ev(ws, "(()=>{const i=document.querySelector('#dbody .editrow input');"
               f"i.value={json.dumps(node)};"
               "document.querySelector('#dbody .editrow button[data-edit]').click()})()")
        time.sleep(0.5)
        tip2 = ev(ws, "(document.querySelector('#dbody .editrow .editmsg')||{}).textContent || ''")
        print(f'[4] 填原名点击后的提示 = {tip2!r}')
        if '一样' not in (tip2 or ''):
            fails.append(f'填了与原名相同的值时没有提示，实际 {tip2!r}')

    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass

    print()
    print('=' * 58)
    if fails:
        print(f'❌ {len(fails)} 项失败：')
        for f in fails:
            print(f'   · {f}')
        return 1
    print('全部通过：改图计划入口可见、绑定正确、空输入与同值都有提示')
    return 0


if __name__ == '__main__':
    sys.exit(main())
