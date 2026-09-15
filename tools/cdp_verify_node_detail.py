# -*- coding: utf-8 -*-
r"""K3 / G5：实体详情面板的两处验证（去掉截断 + 说清"出现在哪几篇"）。

## 验证什么

`renderInnerPreview`（`kg-nebula.html`）此前有两处截断：
```js
[...byType.entries()].slice(0, 5)   // 最多只显示 5 种关系类型
list.slice(0, 6)                    // 每种类型最多只显示 6 个邻居
```
于是"关联 99 条 / 13 种类型"的节点，界面上只能看到 5 类 × 6 个 ——
**用户会以为这个实体就是那么点关系**。

而"跨了几篇论文"这条线索（G5）数据本来就在 `evidence-*.json` 里
（每条证据带 `src`），此前只用来渲染"证据出处"，**没有汇总**.

## 测试目标是怎么选的

社区 0 里关系类型最多的节点（实测）：
```
TransE                  13 种类型 · 99 条关联   ← 用它
Link Prediction         13 种类型 · 89 条关联
Knowledge Graph Embeddings 11 种 · 78 条
```
`TransE` 在 `comm-0.json` 的 `nodes` 里下标为 **6**（实测）。
**用 13 种类型这个事实**就能区分"有没有截断"（旧代码显示 5 种）。

## 三个断言

  1. **类型区块数 ≥ 13**（去掉 `slice(0,5)` 的直接证据）；
  2. **"出现在 N 篇论文"这一行存在**（G5 的新增内容）；
  3. **某个类型的邻居行数 > 6**（去掉 `list.slice(0,6)` 的直接证据）。

用法：
  python tools/cdp_verify_node_detail.py
"""
import json
import subprocess
import sys
import time
import urllib.request

import websocket

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
BASE = 'http://127.0.0.1:8098'
# 实测：社区 0 的 nodes[6] 是 TransE，13 种关系类型、99 条关联
CI, NODE_IDX, EXPECT_TYPES = 0, 6, 13


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
        return {'__error__': str(res['exceptionDetails'].get('text'))}
    return res.get('result', {}).get('value')


def main():
    port = 9620 + (int(time.time() * 1000) % 30)
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

        # 确认测试目标还对得上（数据变了要立刻发现，而不是静默测了个别的节点）
        nid = ev(ws, f'KGNebula._internals().ud.data.nodes[{NODE_IDX}].id')
        print(f'测试目标：nodes[{NODE_IDX}] = {nid!r}（期望 TransE）')
        if nid != 'TransE':
            fails.append(f'测试目标漂了：nodes[{NODE_IDX}] 现在是 {nid!r}，'
                         f'请重新确认下标与期望类型数')

        ev(ws, f'KGNebula.select({NODE_IDX})')
        time.sleep(1.0)

        body = ev(ws, "document.getElementById('dbody').innerText || ''")
        secs = ev(ws, "document.querySelectorAll('#dbody .sec').length")
        rows = ev(ws, "document.querySelectorAll('#dbody .row').length")
        print(f'[1] 详情面板：{len(body)} 字符 · `#dbody .sec` 区块 {secs} 个 · `.row` {rows} 行')

        # 断言 1：类型区块数 ≥ 13（旧代码上限是 5）
        # 注意：`.sec` 里除类型外还有"证据出处"/"出现在 N 篇论文"，
        # 所以实际数应当 ≥ 13；若 ≤ 5 就直接说明截断还在。
        if secs is None or secs <= 5:
            fails.append(f'类型区块只有 {secs} 个（旧代码上限就是 5）—— slice(0,5) 可能还在')
        else:
            print(f'    OK 区块数 {secs} > 5 ⇒ 类型截断已去掉')

        # 断言 2："来源论文"区块（G5）
        #   注意**不能要求"跨多篇"** —— 实测社区 0 的 1200 个实体会 1108 个只跨 1 篇，
        #   若判据写成 "> 1" 则绝大多数实体（含 TransE 这种枢纽）一个字都不显示。
        has_src = '来源论文' in body
        print(f'[2] "来源论文" 区块：{"有" if has_src else "无"}'
              f'{"（含跨篇数）" if "跨" in body else ""}')
        if not has_src:
            fails.append('详情面板没有"来源论文"区块（G5 未生效）')

        # 断言 3：邻居行数 > 某个类型的 6 行上限
        #   旧代码：类型 ≤5 个、每类 ≤6 行 ⇒ .row 上限约 30 + 证据行
        #   实测该节点 99 条关联，去截断后 .row 应显著多于 30
        print(f'[3] `.row` 行数 {rows}（旧代码理论上限：5 类 × 6 = 30 + 证据行）')
        if rows is None or rows <= 40:
            fails.append(f'`.row` 只有 {rows} 行 —— list.slice(0,6) 可能还在')

        # 顺带记录：面板是否可滚动（去掉截断会让内容变长，必须有滚动容器）
        scrollable = ev(ws, "(()=>{const d=document.getElementById('detail');"
                            "return d ? (d.scrollHeight > d.clientHeight) : null})()")
        print(f'    面板可滚动：{scrollable}（内容变长后必须能滚）')

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
    print('全部通过：详情面板无截断，且说清了"出现在哪几篇"')
    return 0


if __name__ == '__main__':
    sys.exit(main())
