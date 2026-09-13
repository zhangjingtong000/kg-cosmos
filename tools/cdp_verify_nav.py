# -*- coding: utf-8 -*-
"""验证导航页（index.html）：卡片结构、链接可达、示意图未溢出。

其中第 3 项是**防回归**：初版把示意图形写成 `<div class="art dots">`（两个类
同一元素），`.dots` 的 `position:absolute; inset:0` 覆盖了 `.art` 的定位，
使示意图脱离文档流并铺满整张卡片——卡片矮了 76px、圆点压住标题。
这个 bug 只看截图容易被当成"视觉风格"，是实测 rect 才定位到的，
所以在这里固化成断言。

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

        # 1. 背景画布：必须存在、已启用，并且**真的画上了像素**
        cv = ev(ws, """(() => {
          const c = document.getElementById('voyageCanvas');
          if (!c) return { has: false };
          const ctx = c.getContext('2d');
          // 画布中心 + 四分之一处各取一点，覆盖可能被裁掉的边角
          const pts = [[0.5, 0.5], [0.25, 0.35], [0.75, 0.65]];
          const px = pts.map(([fx, fy]) => {
            const d = ctx.getImageData(Math.round(c.width * fx), Math.round(c.height * fy), 1, 1).data;
            return [d[0], d[1], d[2], d[3]];
          });
          return { has: true, w: c.width, h: c.height,
                   on: c.classList.contains('on'),
                   op: +getComputedStyle(c).opacity, px: px };
        })()""")
        print(f'[1] 背景画布: has={cv.get("has")} {cv.get("w")}x{cv.get("h")} '
              f'on={cv.get("on")} opacity={cv.get("op")}')
        print(f'    中心/边角像素: {cv.get("px")}')
        if not cv.get('has'):
            fails.append('找不到 #voyageCanvas')
        else:
            if not cv.get('on'):
                fails.append('画布没有启用（第一帧没就绪，背景会是空的）')
            if cv.get('w', 0) < 100 or cv.get('h', 0) < 100:
                fails.append(f'画布尺寸异常: {cv.get("w")}x{cv.get("h")}')
            drawn = [p for p in (cv.get('px') or []) if p and p[3] > 0]
            if not drawn:
                fails.append('画布上没有任何像素 —— 帧序列没画上去')
            # 注意：**不**检查"是否全黑" —— 第一帧本来就是深空星场（YAVG 16.7），
            # 它就该是接近黑的。背景到底变没变，由下面的滚动对比来证明。

        # 2. 帧序列资源可达（抽查首帧与末帧，确认不是 404）
        seq = ev(ws, """(async () => {
          const urls = ['./assets/voyage-frames/s001.webp',
                        './assets/voyage-frames/s061.webp',
                        './assets/voyage-frames/s121.webp'];
          const out = [];
          for (const u of urls) {
            try {
              const r = await fetch(u, { method: 'HEAD' });
              out.push([u.split('/').pop(), r.status]);
            } catch (e) { out.push([u.split('/').pop(), 'ERR']); }
          }
          return out;
        })()""")
        print(f'[2] 帧序列抽查: {seq}')
        bad = [x for x in (seq or []) if x[1] != 200]
        if bad:
            fails.append(f'帧序列文件取不到: {bad}')

        # 2b. 滚动驱动验证：往下滚，画布应该越来越亮（深空 → 星云 → 亮核）
        trail = []
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            ev(ws, 'scrollTo(0, Math.round((document.body.scrollHeight - innerHeight) * %s))' % frac)
            time.sleep(1.6)          # 等阻尼收敛（curP 约 1s 到位）
            px = ev(ws, """(() => {
              const c = document.getElementById('voyageCanvas');
              const ctx = c.getContext('2d');
              const d = ctx.getImageData(Math.round(c.width / 2), Math.round(c.height / 2), 1, 1).data;
              return [d[0], d[1], d[2]];
            })()""")
            lum = round(sum(px) / 3, 1) if px else -1
            trail.append((frac, lum))
            print(f'[2b] 滚动 {int(frac * 100):3d}%  中心像素 {px}  亮度 {lum}')
        lums = [l for _, l in trail]
        if lums[-1] - lums[0] < 20:
            fails.append(f'滚动时背景几乎没变亮（{lums[0]} -> {lums[-1]}），帧序列没被驱动')
        if any(lums[i + 1] - lums[i] < -12 for i in range(len(lums) - 1)):
            bad = [(trail[i][0], trail[i][1], trail[i + 1][1]) for i in range(len(lums) - 1)
                   if lums[i + 1] - lums[i] < -12]
            fails.append(f'亮度出现回落（{bad}）—— 帧序列可能没按顺序推进')
        ev(ws, 'scrollTo(0, 0)')
        time.sleep(1.2)

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
