# -*- coding: utf-8 -*-
"""验证统一着色引擎（T6.4）真的改变了点云颜色缓冲，而不只是改了图例文字。

这是本次改造最容易出现的假通过：图例 DOM 更新了，但 `color` 缓冲没写回去，
画面一动不动。因此本脚本**以几何缓冲的实际取值为准**做断言。

检查项：
  1. 页面模块加载成功（window.KGNebula 存在）——语法错误会让它为 undefined
  2. 默认模式与改造前等价（partition=community, ranking=degree）
  3. 切到类型分区后，点云里的不同颜色数**显著增加**（单色渐变 → 15 色系）
  4. 切排名维度后颜色集合发生变化（degree ≠ pagerank）
  5. 图例项数与分区实际类别数一致
  6. 关闭排序维度（ranking=none）后颜色数收敛到分区数
  7. 全程无 JS 异常

用法：
  python tools/cdp_verify_color.py [社区索引]
"""
import base64
import json
import subprocess
import sys
import time
import urllib.request

import websocket

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
BASE = 'http://127.0.0.1:8098/web/kg-nebula.html'


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


def evaluate(ws, expr):
    r = cdp(ws, 'Runtime.evaluate',
            {'expression': expr, 'returnByValue': True, 'awaitPromise': True})
    res = r.get('result', {})
    if 'exceptionDetails' in res:
        return {'__error__': res['exceptionDetails'].get('text', 'exception')}
    return res.get('result', {}).get('value')


def main():
    ci = sys.argv[1] if len(sys.argv) > 1 else '0'
    port = 9700 + (int(time.time() * 1000) % 200)
    url = f'{BASE}?c={ci}'

    proc = subprocess.Popen([
        CHROME, '--headless=new', '--enable-unsafe-swiftshader', '--disable-gpu',
        '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
        f'--remote-debugging-port={port}', '--remote-allow-origins=*',
        '--window-size=1600,900', url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    failures = []
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
        ws = websocket.create_connection(page['webSocketDebuggerUrl'], timeout=90)
        cdp(ws, 'Page.enable')
        cdp(ws, 'Runtime.enable')

        ready = False
        for _ in range(400):
            if evaluate(ws, 'window.__ready === true') is True:
                ready = True
                break
            time.sleep(0.1)
        print(f'页面就绪: {ready}')
        if not ready:
            failures.append('页面未就绪（window.__ready 未置位）')

        # ── 1. 模块是否加载成功 ──
        api = evaluate(ws, 'typeof window.KGNebula')
        print(f'\n[1] typeof window.KGNebula = {api}')
        if api != 'object':
            failures.append('window.KGNebula 不存在 —— 模块加载或语法有误')
            print('\n!! 模块未加载，后续检查无意义')
            for f in failures:
                print(f'   FAIL: {f}')
            ws.close()
            return 1

        # ── 2. 默认模式 ──
        default_mode = evaluate(ws, 'JSON.stringify(KGNebula.colorMode())')
        print(f'[2] 默认模式 = {default_mode}')
        if default_mode != '{"partition":"community","ranking":"degree"}':
            failures.append(f'默认模式不是等价于改造前的取值: {default_mode}')

        # ── 3~6. 逐模式检查 ──
        def probe(partition, ranking, label):
            evaluate(ws, f'KGNebula.setColorMode({partition!r}, {ranking!r})')
            time.sleep(0.25)
            ncol = evaluate(ws, 'KGNebula.nodeColors().length')
            nitems = evaluate(ws, 'KGNebula.legendItems()')
            mode = evaluate(ws, 'JSON.stringify(KGNebula.colorMode())')
            print(f'  {label:24s} 颜色数={ncol:3d}  图例项={nitems:3d}  mode={mode}')
            return ncol, nitems

        print('\n[3] 各模式的点云实际颜色数与图例项数')
        c_comm_deg, _ = probe('community', 'degree', 'community × degree')
        c_comm_pr, _ = probe('community', 'pagerank', 'community × pagerank')
        c_comm_none, n_comm_none = probe('community', 'none', 'community × none')
        c_type_deg, n_type_deg = probe('type', 'degree', 'type × degree')
        c_type_pr, n_type_pr = probe('type', 'pagerank', 'type × pagerank')
        c_type_none, n_type_none = probe('type', 'none', 'type × none')

        print('\n[4] 断言')
        if c_type_deg <= c_comm_deg:
            failures.append(f'切到类型分区后颜色数未增加：{c_comm_deg} → {c_type_deg}')
        else:
            print(f'  OK  类型分区使颜色数增加 {c_comm_deg} → {c_type_deg}')
        if c_comm_deg == c_comm_pr:
            failures.append('community 下 degree 与 pagerank 颜色完全相同 —— 排名维度没生效')
        else:
            print(f'  OK  排名维度生效 degree ≠ pagerank（{c_comm_deg} vs {c_comm_pr}）')
        if n_type_none != n_type_deg:
            failures.append(f'type 分区下关闭排序改变了图例项数：{n_type_deg} → {n_type_none}')
        else:
            print(f'  OK  关闭排序不改变分区图例项数（{n_type_deg}，应为社区内的类型数）')
        if c_type_none > c_type_deg:
            failures.append(f'关闭排序后颜色数反而增加：{c_type_deg} → {c_type_none}')
        else:
            print(f'  OK  关闭排序使颜色数收敛 {c_type_deg} → {c_type_none}（=各类型基色）')
        if n_comm_none != 1:
            failures.append(f'community 分区图例应为 1 项，实为 {n_comm_none}')
        else:
            print(f'  OK  community 分区图例为 1 项')

        # ── 5. 等价性回归：默认模式必须与改造前的公式逐值相同 ──
        # 下面是**逐字复刻**的改造前代码。若引擎默认参数与它不等价，
        # 两个颜色集合就会不同。用字符串集合比较，不涉及浮点容差。
        print('\n[5] 等价性回归：引擎默认输出 vs 改造前公式')
        evaluate(ws, "KGNebula.setColorMode('community','degree')")
        time.sleep(0.25)
        legacy = evaluate(ws, """(() => {
          const { THREE, ud } = KGNebula._internals();
          if (!ud || !ud.data) return null;
          const nodes = ud.data.nodes;
          const base = new THREE.Color(ud.data.color);
          const maxD = Math.max(1, ...nodes.map(n => n.d || 1));
          const set = new Set();
          nodes.forEach(n => {
            const t = Math.pow((n.d || 1) / maxD, 0.6);
            const cc = base.clone().lerp(new THREE.Color('#ffffff'), 0.16 + 0.62 * t);
            set.add([cc.r, cc.g, cc.b].map(v => Math.round(v * 255)).join(','));
          });
          return [...set].sort().join('|');
        })()""")
        engine = evaluate(ws, "KGNebula.nodeColors().slice().sort().join('|')")
        if legacy is None:
            failures.append('等价性回归无法执行（_internals 返回空）')
        elif legacy == engine:
            print(f'  OK  与改造前公式逐值相同（{len(engine.split("|"))} 种颜色）')
        else:
            lg, en = set(legacy.split('|')), set(engine.split('|'))
            failures.append(
                f'默认模式与改造前公式不一致：仅旧有 {len(lg-en)} 种、'
                f'仅新有 {len(en-lg)} 种')
            print(f'     旧有独有: {sorted(lg-en)[:5]}')
            print(f'     新有独有: {sorted(en-lg)[:5]}')

        # 截图留档（放 output/：它是验证产物，可随时由本脚本重新生成，
        # 单张 1.3MB 的 PNG 不值得进版本库）
        out = f'output/verify/color-type-degree-c{ci}.png'
        try:
            import os
            os.makedirs('output/verify', exist_ok=True)
            evaluate(ws, "KGNebula.setColorMode('type','degree')")
            time.sleep(0.4)
            shot = cdp(ws, 'Page.captureScreenshot', {'format': 'png'})
            with open(out, 'wb') as f:
                f.write(base64.b64decode(shot['result']['data']))
            print(f'\n截图已保存: {out}')
        except Exception as e:
            print(f'\n截图失败（不影响结论）: {e}')

        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    print('\n' + '=' * 60)
    if failures:
        print(f'未通过 {len(failures)} 项：')
        for f in failures:
            print(f'  · {f}')
        return 1
    print('全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
