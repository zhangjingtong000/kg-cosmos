# -*- coding: utf-8 -*-
r"""验证「只看分歧」（P7 前端）—— 三个可断言的点。

## 这个功能是什么

主视图进入某个社区后，可点「⚠️ 只看分歧」把非 `CONTRADICTS`（矛盾）的关系变暗，
只留分歧边高亮。数据来自 `comm-*.json` 的 `edges[i][2]`。

## 为什么需要这个测试

它的**数据依赖曾经是断的**：`build_community_details.py:145` 原先把边类型
**硬编码成 `"RELATED"`**，于是 18 个社区 16569 条边**全是同一个类型**，
`CONTRADICTS` 根本不存在（详见 `docs/P6_SCIREX_RELATION_ANALYSIS.md` 同期修的
另一个 bug）。**所以这个功能"看起来做了但其实没数据"是很容易发生的事** ——
必须有断言盯着"真的有条数、真的会变色"。

## 三个断言

  1. **有分歧的社区**（**现算**：`CONTRADICTS` 最多的那个 `comm-*`）：
     按钮**可见**、文字**含条数**、点击后 `aria-pressed` 变 `true`、
     **边颜色数组真的变了**、再点能恢复；
  2. **无分歧的社区**（**现算**：`CONTRADICTS` == 0 的第一个）：
     按钮**必须隐藏** —— 不给一个点了没反应的控件；
  3. **恢复后颜色回到初值** —— 防止"只能进不能出"。

用法：
  python tools/cdp_verify_contradicts.py
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


def pick_communities():
    """动态挑「有分歧」与「无分歧」的社区各一个（现算 `comm-*.json` 的 `CONTRADICTS` 数）。

    ⚠️ 2026-09-19 改（原来写死 `HAS = (0, 24)` / `NONE = (1, 0)`）：

    加中文语料后社区划分重排 ⇒ 分歧边整体挪了位置
    （实测新 `comm-1` 有 22 条，而 `comm-0` 变成 0 条）⇒ **判据没错，但目标过期了**。

    **断言强度不变**：仍然是"有分歧的必须看得见、条数对得上、点了真变色、再点能恢复"
    与"没分歧的必须把按钮藏起来"。
    """
    counts = {}
    for f in sorted(WEB.glob('comm-*.json')):
        try:
            ci = int(f.stem.split('-')[1])
            d = json.loads(f.read_text(encoding='utf-8'))
        except Exception:
            continue
        counts[ci] = sum(1 for e in (d.get('edges') or [])
                         if len(e) > 2 and e[2] == 'CONTRADICTS')
    has = sorted((c for c, n in counts.items() if n > 0), key=lambda c: -counts[c])
    none = sorted(c for c, n in counts.items() if n == 0)
    if not has or not none:
        return None
    return (has[0], counts[has[0]]), (none[0], 0)


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
    port = 9560 + (int(time.time() * 1000) % 40)
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
            raise SystemExit(1)

        # ── 1. 有分歧的社区 ──────────────────────────────────────────────
        picked = pick_communities()
        if not picked:
            fails.append('挑不出社区（没有"有分歧"或没有"无分歧"的社区）')
            raise SystemExit(1)
        (ci, want), (ni, _) = picked
        print(f'目标社区（现算）：有分歧 comm-{ci}（{want} 条）· 无分歧 comm-{ni}')
        ev(ws, f'KGNebula.enter({ci})')
        time.sleep(2.5)

        st_n = ev(ws, 'KGNebula._internals().ud.contra ? '
                      'KGNebula._internals().ud.contra.n : null')
        hidden = ev(ws, "document.getElementById('onlyContra').hidden")
        label = ev(ws, "document.getElementById('onlyContra').textContent")
        print(f'[1] 社区 {ci}：contra.n={st_n}（期望 {want}）· 按钮隐藏={hidden} · 文字={label!r}')
        if st_n != want:
            fails.append(f'社区 {ci} 的 contra.n 应为 {want}，实为 {st_n}')
        if hidden:
            fails.append(f'社区 {ci} 有分歧，按钮不该隐藏')
        if label and str(want) not in label:
            fails.append(f'按钮文字里应含条数 {want}：{label!r}')

        # 点击前后的颜色首值（第 0 条边的 r 分量）
        before = ev(ws, 'KGNebula._internals().ud.contra.attr.array[0]')
        ev(ws, "document.getElementById('onlyContra').click()")
        time.sleep(0.4)
        on = ev(ws, 'KGNebula._internals().ud.contra.on')
        pressed = ev(ws, "document.getElementById('onlyContra').getAttribute('aria-pressed')")
        after = ev(ws, 'KGNebula._internals().ud.contra.attr.array[0]')
        op = ev(ws, 'KGNebula._internals().ud.contra.mat.opacity')
        print(f'[2] 点击后：on={on} · aria-pressed={pressed} · '
              f'颜色首值 {before} → {after} · opacity={op}')
        if on is not True:
            fails.append('点击后 contra.on 应为 true')
        if pressed != 'true':
            fails.append(f'点击后 aria-pressed 应为 "true"，实为 {pressed!r}')
        if before == after:
            fails.append('点击后边颜色数组没有变化 —— 筛选没有真的生效')
        if op is None or op <= 0.30:
            fails.append(f'筛选态的 opacity 应大于默认 0.30，实为 {op}')

        # ── 3. 再点应恢复 ────────────────────────────────────────────────
        ev(ws, "document.getElementById('onlyContra').click()")
        time.sleep(0.4)
        off = ev(ws, 'KGNebula._internals().ud.contra.on')
        back = ev(ws, 'KGNebula._internals().ud.contra.attr.array[0]')
        op2 = ev(ws, 'KGNebula._internals().ud.contra.mat.opacity')
        print(f'[3] 再点恢复：on={off} · 颜色首值回到 {back}（初值 {before}）· opacity={op2}')
        if off is not False:
            fails.append('再点后 contra.on 应回到 false')
        if back != before:
            fails.append(f'再点后颜色应回到初值 {before}，实为 {back}')

        # ── 2. 无分歧的社区：按钮必须隐藏 ────────────────────────────────
        ev(ws, 'KGNebula.exit()')
        time.sleep(1.2)
        ev(ws, f'KGNebula.enter({ni})')
        time.sleep(2.5)
        st_n2 = ev(ws, 'KGNebula._internals().ud.contra ? '
                       'KGNebula._internals().ud.contra.n : null')
        hidden2 = ev(ws, "document.getElementById('onlyContra').hidden")
        print(f'[4] 社区 {ni}（无分歧）：contra.n={st_n2} · 按钮隐藏={hidden2}')
        if st_n2 != 0:
            fails.append(f'社区 {ni} 的 contra.n 应为 0，实为 {st_n2}')
        if not hidden2:
            fails.append(f'社区 {ni} 没有分歧，按钮应隐藏（不给点了没反应的控件）')

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
    print('全部通过：「只看分歧」有数据、能变色、能恢复、无数据时不显示')
    return 0


if __name__ == '__main__':
    sys.exit(main())
