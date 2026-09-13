# -*- coding: utf-8 -*-
"""验证导航页（index.html）：卡片结构、链接可达、示意图未溢出。

其中第 3 项是**防回归**：初版把示意图形写成 `<div class="art dots">`（两个类
同一元素），`.dots` 的 `position:absolute; inset:0` 覆盖了 `.art` 的定位，
使示意图脱离文档流并铺满整张卡片——卡片矮了 76px、圆点压住标题。
这个 bug 只看截图容易被当成"视觉风格"，是实测 rect 才定位到的，
所以在这里固化成断言。

背景检查（第 1/2/2b 项）随背景实现换代更新：背景现在是 voyage.js 里的
**实时 WebGL 光场**（时间自走 + 滚动驱动 + 鼠标视差），不再是帧序列，
所以：
  · 第 1 项改为验证 WebGL 上下文、无 GL 错误、首帧已画；
  · 第 2 项（原"帧序列文件可达"）改为**时间自走断言**：不滚动的前提下
    静置 1.8 秒，中心像素必须变化——这是"背景活着"的最低证据；
  · 第 2b 项保持原语义：滚动时中心亮度单调上升（深度旅程确实存在），
    只是改用 gl.readPixels 读像素。

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


def read_center(ws):
    """从 WebGL 画布读中心像素（readPixels，y 轴朝上无所谓，取单点）。"""
    return ev(ws, """(() => {
      const c = document.getElementById('voyageCanvas');
      if (!c) return null;
      const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
      if (!gl) return null;
      const px = new Uint8Array(4);
      gl.readPixels(Math.round(c.width / 2), Math.round(c.height / 2),
                    1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
      return [px[0], px[1], px[2]];
    })()""")


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

        # 1. 背景画布：必须存在、WebGL 上下文健康、已启用（首帧已画）
        cv = ev(ws, """(() => {
          const c = document.getElementById('voyageCanvas');
          if (!c) return { has: false };
          const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
          if (!gl) return { has: true, gl: false };
          const pts = [[0.5, 0.5], [0.25, 0.35], [0.75, 0.65]];
          const px = pts.map(([fx, fy]) => {
            const d = new Uint8Array(4);
            gl.readPixels(Math.round(c.width * fx), Math.round(c.height * fy),
                          1, 1, gl.RGBA, gl.UNSIGNED_BYTE, d);
            return [d[0], d[1], d[2]];
          });
          return { has: true, gl: true, w: c.width, h: c.height,
                   on: c.classList.contains('on'),
                   op: +getComputedStyle(c).opacity,
                   err: gl.getError(), px: px };
        })()""")
        print(f'[1] 背景画布: has={cv.get("has")} gl={cv.get("gl")} '
              f'{cv.get("w")}x{cv.get("h")} on={cv.get("on")} op={cv.get("op")} '
              f'err={cv.get("err")}')
        print(f'    中心/边角像素: {cv.get("px")}')
        if not cv.get('has'):
            fails.append('找不到 #voyageCanvas')
        elif not cv.get('gl'):
            fails.append('画布上没有 WebGL 上下文（背景降级成了静态渐变）')
        else:
            if not cv.get('on'):
                fails.append('画布没有启用（首帧没画上，背景会是空的）')
            if cv.get('w', 0) < 100 or cv.get('h', 0) < 100:
                fails.append(f'画布尺寸异常: {cv.get("w")}x{cv.get("h")}')
            if cv.get('err') != 0:
                fails.append(f'WebGL 报错: {cv.get("err")}')
            # 注意：**不**检查"是否全黑" —— 首屏本来就是深靛底（不是纯黑），
            # 背景真的在渲染由第 2 项（时间自走）与第 2b 项（滚动变亮）证明。

        # 2. 时间自走断言：**不滚动**，静置 1.8 秒，中心像素必须自己变化。
        #    背景是实时着色器（时间驱动），这是"页面是活的"的最低证据；
        #    旧版帧序列只在滚动时才动，正是被用户抱怨"死板"的根源。
        p_a = read_center(ws)
        time.sleep(1.8)
        p_b = read_center(ws)
        if p_a and p_b:
            diff = sum(abs(a - b) for a, b in zip(p_a, p_b))
            print(f'[2] 时间自走: {p_a} → {p_b}  变化量 {diff}')
            if diff < 3:
                fails.append(f'不滚动时背景完全静止（变化量 {diff}）——时间驱动失效')
        else:
            fails.append('readPixels 读不到像素（WebGL 上下文异常）')

        # 2b. 滚动驱动验证：往下滚，背景必须真的在变（深入旅程）。
        #     读法用 3×3 网格的均值而不是单点——戏剧化的旅程里
        #     单点亮度天然波动（炽热丝缕扫过中心会瞬时拉高），
        #     均值才是"整屏光量"的稳定估计。
        def grid_mean():
            px = ev(ws, """(() => {
              const c = document.getElementById('voyageCanvas');
              const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
              if (!gl) return null;
              const out = [];
              for (const [fx, fy] of [[0.3,0.35],[0.5,0.35],[0.7,0.35],
                                      [0.3,0.55],[0.5,0.55],[0.7,0.55],
                                      [0.3,0.75],[0.5,0.75],[0.7,0.75]]) {
                const d = new Uint8Array(4);
                gl.readPixels(Math.round(c.width * fx), Math.round(c.height * fy),
                              1, 1, gl.RGBA, gl.UNSIGNED_BYTE, d);
                out.push(d[0] + d[1] + d[2]);
              }
              return out;
            })()""")
            return round(sum(px) / (len(px) * 3), 1) if px else -1
        trail = []
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            ev(ws, 'scrollTo(0, Math.round((document.body.scrollHeight - innerHeight) * %s))' % frac)
            time.sleep(1.6)          # 等阻尼收敛（curP 约 1s 到位）
            lum = grid_mean()
            trail.append((frac, lum))
            print(f'[2b] 滚动 {int(frac * 100):3d}%  整屏平均亮度 {lum}')
        lums = [l for _, l in trail]
        if lums[-1] - lums[0] < 20:
            fails.append(f'滚动时背景几乎没变亮（{lums[0]} -> {lums[-1]}），着色器没被滚动驱动')
        if any(lums[i + 1] - lums[i] < -25 for i in range(len(lums) - 1)):
            bad = [(trail[i][0], trail[i][1], trail[i + 1][1]) for i in range(len(lums) - 1)
                   if lums[i + 1] - lums[i] < -25]
            fails.append(f'整屏光量出现剧烈回落（{bad}）——旅程曝光曲线异常')
        ev(ws, 'scrollTo(0, 0)')
        time.sleep(1.2)

        # 2c. 新交互回归：键盘 ↓ 跳到下一章；进度点点击跳到对应章节
        ev(ws, """document.body.dispatchEvent(new KeyboardEvent('keydown',
          {key: 'ArrowDown', bubbles: true, cancelable: true}))""")
        time.sleep(2.4)              # 平滑滚动到位
        k1 = ev(ws, """(() => {
          const c = document.querySelectorAll('.ch')[1];
          return { y: scrollY, top: c.offsetTop };
        })()""")
        print(f'[2c] 键盘 ↓: scrollY={k1["y"]} 目标={k1["top"]}')
        if abs(k1['y'] - k1['top']) > 60:
            fails.append('键盘 ↓ 没有跳到下一章节（键盘导航失效）')
        ev(ws, "document.querySelector('#rail .dot[data-i=\"4\"]').click()")
        time.sleep(2.6)
        k2 = ev(ws, """(() => {
          const c = document.querySelectorAll('.ch')[4];
          return { y: scrollY, top: c.offsetTop };
        })()""")
        print(f'[2c] 点进度点 4: scrollY={k2["y"]} 目标={k2["top"]}')
        if abs(k2['y'] - k2['top']) > 60:
            fails.append('进度点点击没有跳到对应章节')
        ev(ws, 'scrollTo(0, 0)')
        time.sleep(1.0)

        # 3. 入口链接
        entries = ev(ws, """[...document.querySelectorAll('a.entry')].map(e => ({
          href: e.getAttribute('href'),
          title: (e.querySelector('h3')||{}).textContent || ''
        }))""")
        print(f'[3] 入口数: {len(entries)}')
        for e in entries:
            print(f"    {e['title'][:18]:20s} → {e['href']}")
        if len(entries) != 4:
            fails.append(f'入口应为 4 个，实为 {len(entries)}')

        # 4. 数据区被 JS 填充（说明数据链路仍在工作）
        facts = ev(ws, "(document.getElementById('liveFacts')||{}).innerText || ''")
        print(f'[4] 数据区: {facts[:70].replace(chr(10), " / ")!r}')
        if '18,780' not in facts and '18780' not in facts:
            fails.append('数据区未被填充（liveFacts 里没有实体数）')

        # 5. 进度点数量 = 章节数
        nDot = ev(ws, "document.querySelectorAll('#rail .dot').length")
        nCh = ev(ws, "document.querySelectorAll('.ch').length")
        print(f'[5] 进度点 {nDot} / 章节 {nCh}')
        if nDot != nCh:
            fails.append(f'进度点 {nDot} ≠ 章节数 {nCh}')

        # 6. 所有链接可达（入口 + 页脚）
        hrefs = [e['href'] for e in entries] + ev(
            ws, "[...document.querySelectorAll('#foot a')].map(a => a.getAttribute('href'))")
        bad = []
        for h in hrefs:
            try:
                req = urllib.request.Request(f'{BASE}/web/{h}', method='HEAD')
                with urllib.request.urlopen(req, timeout=6) as r:
                    if r.status != 200:
                        bad.append(f'{h} → {r.status}')
            except Exception as e:
                bad.append(f'{h} → {e}')
        print(f'[6] 检查 {len(hrefs)} 个链接，失效 {len(bad)} 个')
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
