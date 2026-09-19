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

**现算**（`pick_target()`）：遍历全部 `comm-*.json`，取「关系类型数最多、
其次关联数最多」的那个节点，并进入它所在的社区。
**用"该节点有 N 种类型"这个事实**就能区分"有没有截断"（旧代码只显示 5 种）。

⚠️ 2026-09-19：这里原来写死「`comm-0` 的 `nodes[6]` == `TransE`（13 种类型）」。
加中文语料后社区划分重排（新 `comm-0` 叫 `AAAI`，`TransE` 搬到了 `comm-1`），
下标 6 指向 `WWW` ⇒ 脚本自己报出「测试目标漂了」。
**判据没改，只是不再假设"目标一定在社区 0、一定在下标 6"。**

## 三个断言

  1. **该节点的每一种关系类型都要有区块**（旧代码上限 5 种）；
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
from pathlib import Path

import websocket

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
BASE = 'http://127.0.0.1:8098'
WEB = Path(__file__).resolve().parent.parent / 'web' / 'kg-data'


def pick_target():
    """动态选测试目标：**全部 `comm-*.json` 里关系类型最多的节点**。

    ⚠️ 2026-09-19 改（原来写死「`comm-0` 的 `nodes[6]` == `TransE`（13 种类型）」）：

    加中文语料后 `filaments`/`comm-*.json` 重建 ⇒ **社区划分重排**
    （新 `comm-0` 的社区名是 `AAAI`，节点最多只有 6 种类型；
    `TransE` 搬到了 `comm-1` 的 `nodes[6]`）⇒ 下标 6 指向 `WWW`，
    脚本自己报出「测试目标漂了：nodes[6] 现在是 'WWW'」。

    **判据一个字都没改**（旧代码上限：5 类 × 6 行）—— 只是不再假设
    「类型最丰富的节点一定在社区 0、一定在下标 6」。

    返回 `(社区号, 下标, 类型数, 关联数, 节点名)`。
    """
    best = None
    for f in sorted(WEB.glob('comm-*.json')):
        try:
            ci = int(f.stem.split('-')[1])
            d = json.loads(f.read_text(encoding='utf-8'))
        except Exception:
            continue
        ns, es = d.get('nodes') or [], d.get('edges') or []
        idx = {n.get('id'): i for i, n in enumerate(ns)}
        types, assoc = {}, {}
        for e in es:
            if len(e) < 3:
                continue
            for x in (e[0], e[1]):
                i = idx.get(x)
                if i is None:
                    continue
                types.setdefault(i, set()).add(e[2])
                assoc[i] = assoc.get(i, 0) + 1
        for i, ts in types.items():
            cand = (len(ts), assoc.get(i, 0), ci, i, ns[i].get('id'))
            if best is None or cand[:2] > best[:2]:
                best = cand
    return best


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

        picked = pick_target()
        if not picked:
            print('❌ 挑不出测试目标（comm-*.json 缺失或没有边？）')
            return 1
        n_types, n_assoc, CI, NODE_IDX, want_id = picked
        print(f'测试目标：comm-{CI} nodes[{NODE_IDX}] = {want_id!r}'
              f'（{n_types} 种关系类型 · {n_assoc} 条关联）')

        ev(ws, f'KGNebula.enter({CI})')
        time.sleep(2.5)

        # 确认测试目标还对得上（数据变了要立刻发现，而不是静默测了个别的节点）
        nid = ev(ws, f'KGNebula._internals().ud.data.nodes[{NODE_IDX}].id')
        if nid != want_id:
            fails.append(f'测试目标漂了：comm-{CI} nodes[{NODE_IDX}] 现在是 {nid!r}，'
                         f'期望 {want_id!r}')

        ev(ws, f'KGNebula.select({NODE_IDX})')
        time.sleep(1.0)

        body = ev(ws, "document.getElementById('dbody').innerText || ''")
        secs = ev(ws, "document.querySelectorAll('#dbody .sec').length")
        rows = ev(ws, "document.querySelectorAll('#dbody .row').length")
        print(f'[1] 详情面板：{len(body)} 字符 · `#dbody .sec` 区块 {secs} 个 · `.row` {rows} 行')

        # 断言 1：**该节点的每一种关系类型都要有区块**（旧代码上限是 5 类）
        # 注意：`.sec` 里除类型外还有"证据出处"/"出现在 N 篇论文"，
        # 所以实际数应当 ≥ n_types；若 ≤ 5 就直接说明截断还在。
        if secs is None or secs < n_types or secs <= 5:
            fails.append(f'类型区块只有 {secs} 个（该节点有 {n_types} 种类型 · '
                         f'旧代码上限 5）—— slice(0,5) 可能还在')
        else:
            print(f'    OK 区块数 {secs} ≥ 该节点类型数 {n_types}（且 > 5）'
                  f'⇒ 类型截断已去掉')

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
        #   目标节点有 n_assoc 条关联（现算出来的），去截断后 .row 应显著多于 30
        print(f'[3] `.row` 行数 {rows}（目标节点 {n_assoc} 条关联 · '
              f'旧代码理论上限：5 类 × 6 = 30 + 证据行）')
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
