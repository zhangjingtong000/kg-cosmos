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

        # 1. 十二张背景图层：都必须有 data-src，且都已加载成功
        # （2026-09：从 5 张各自独立生成的星云图，换成一条成片上抽出的 12 帧；
        #   渲染机制不变 —— 常驻图层，只改 opacity/transform，不重新解码。）
        skies = ev(ws, """[...document.querySelectorAll('#voyageFrames .frm')].map(el => ({
          src: el.dataset.src || '',
          bg: (el.style.backgroundImage || '').slice(0, 60),
          op: +getComputedStyle(el).opacity
        }))""")
        print(f'[1] 星云图层: {len(skies)} 张')
        for i, s in enumerate(skies):
            print(f"    #{i}  {s['src']:34s} bg={s['bg'][:34]}")
        if len(skies) != 5:
            fails.append(f'背景图层应为 5 张，实为 {len(skies)}')
        for i, s in enumerate(skies):
            if not s['src']:
                fails.append(f'第 {i} 张星云图层缺少 data-src')
            if 'url(' not in s['bg']:
                fails.append(f'第 {i} 张星云图层的 background-image 未设置')
        # 图片是否真的加载成功（加载失败时 opacity 永远为 0 / 背景是黑）
        okimg = ev(ws, """(() => {
          const els = [...document.querySelectorAll('#voyageFrames .frm')];
          return els.map(el => {
            const m = (el.style.backgroundImage||'').match(/url\("?([^")]+)"?\)/);
            if (!m) return false;
            // 用 Image 再取一次，命中缓存时是同步完成的
            const im = new Image(); im.src = m[1];
            return im.complete && im.naturalWidth > 0;
          });
        })()""")
        bad = [i for i, v in enumerate(okimg or []) if not v]
        if bad:
            fails.append(f'以下星云图加载失败: {bad}')
        else:
            print('[2] OK 五张星云图均已加载')

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
