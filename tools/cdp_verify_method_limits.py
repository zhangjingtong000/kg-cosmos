# -*- coding: utf-8 -*-
"""验证「方法能力与局限」视图（T6.6 替代方案）的筛选、搜索与详情交互。

为什么不用肉眼看截图：搜索与筛选是纯状态逻辑，看一张静态图无法区分
"筛对了"和"筛了但没生效（DOM 没重绘）"。这里以 DOM 的实际节点数与
过滤结果的内容为准做断言。

用法：
  python tools/cdp_verify_method_limits.py
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request

import websocket

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
URL = 'http://127.0.0.1:8098/web/method-limits.html'


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
        return {'__error__': res['exceptionDetails'].get('text', 'exception')}
    return res.get('result', {}).get('value')


def main():
    port = 9800 + (int(time.time() * 1000) % 150)
    proc = subprocess.Popen([
        CHROME, '--headless=new', '--enable-unsafe-swiftshader', '--disable-gpu',
        '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
        f'--remote-debugging-port={port}', '--remote-allow-origins=*',
        '--window-size=1600,1000', URL,
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
        if not targets:
            print('CDP 未就绪')
            return 1
        page = next(t for t in targets if t.get('type') == 'page')
        ws = websocket.create_connection(page['webSocketDebuggerUrl'], timeout=60)
        cdp(ws, 'Page.enable')
        cdp(ws, 'Runtime.enable')

        ok = False
        for _ in range(300):
            if ev(ws, 'window.__ready === true') is True:
                ok = True
                break
            time.sleep(0.1)
        print('页面就绪:', ok)
        if not ok:
            fails.append('页面未就绪')

        total = ev(ws, "document.querySelectorAll('#grid .card').length")
        print(f'[1] 初始卡片数: {total}')
        if total != 243:
            fails.append(f'初始卡片数应为 243，实为 {total}')

        # 筛选下拉是否按出现次数降序、且带计数
        opts = ev(ws, """[...document.getElementById('dimSel').options]
                         .slice(1, 4).map(o => o.text)""")
        print(f'[2] 维度下拉前三项: {opts}')
        if not opts or not all('(' in str(o) for o in opts):
            fails.append('维度下拉未带计数')

        # 搜索
        n = ev(ws, """(() => {
          const q = document.getElementById('q');
          q.value = 'scalability';
          q.dispatchEvent(new Event('input', {bubbles: true}));
          return document.querySelectorAll('#grid .card').length;
        })()""")
        hit_all = ev(ws, """[...document.querySelectorAll('#grid .card')]
          .every(c => c.textContent.toLowerCase().includes('scalability'))""")
        print(f'[3] 搜索 scalability → {n} 张卡片，全部命中: {hit_all}')
        if n == 0 or n >= total:
            fails.append(f'搜索未生效：{total} → {n}')
        if hit_all is not True:
            fails.append('搜索结果里存在不含关键词的卡片')

        # 清空搜索
        ev(ws, """(() => { const q = document.getElementById('q');
          q.value = ''; q.dispatchEvent(new Event('input', {bubbles: true})); })()""")

        # 维度筛选
        n_dim, dim_name, expect = ev(ws, """(() => {
          const s = document.getElementById('dimSel');
          s.value = s.options[1].value;
          s.dispatchEvent(new Event('change', {bubbles: true}));
          return [document.querySelectorAll('#grid .card').length, s.value,
                  Number(s.options[1].text.match(/\\((\\d+)\\)/)[1])];
        })()""")
        print(f'[4] 筛选维度「{dim_name}」→ {n_dim} 张卡片（下拉声明 {expect}）')
        if n_dim != expect:
            fails.append(f'维度筛选结果与下拉计数不符：{n_dim} vs {expect}')

        # 重置
        n_reset = ev(ws, """(() => {
          document.getElementById('reset').click();
          return document.querySelectorAll('#grid .card').length;
        })()""")
        print(f'[5] 重置 → {n_reset} 张卡片')
        if n_reset != total:
            fails.append(f'重置未恢复全量：{n_reset} vs {total}')

        # 点卡片上的维度胶囊应触发筛选
        n_pill = ev(ws, """(() => {
          const pill = document.querySelector('#grid .dim');
          const name = pill.dataset.dim;
          pill.click();
          return [document.querySelectorAll('#grid .card').length,
                  document.getElementById('dimSel').value, name];
        })()""")
        print(f'[6] 点击维度胶囊「{n_pill[2]}」→ {n_pill[0]} 张，下拉值={n_pill[1]}')
        if n_pill[1] != n_pill[2]:
            fails.append('点击维度胶囊未同步下拉值')

        # 详情展开（必须在重置回全量后统计，否则测的是上一步筛选剩下的几张）
        ev(ws, "document.getElementById('reset').click();")
        time.sleep(0.2)
        n_ev = ev(ws, "document.querySelectorAll('#grid details.ev').length")
        opened = ev(ws, """(() => {
          const d = document.querySelector('#grid details.ev');
          if (!d) return null;
          d.open = true;
          return d.querySelector('blockquote') ? d.querySelector('blockquote').textContent.length : 0;
        })()""")
        print(f'[7] 全量下引用折叠块 {n_ev} 个（共 {total} 张卡片）；'
              f'首个展开后正文 {opened} 字符')
        if not n_ev or n_ev < total * 0.9:
            fails.append(f'带原文依据的卡片过少：{n_ev}/{total}')
        if not opened:
            fails.append('原文依据折叠块为空')

        # 截图（重置到全量）
        ev(ws, "document.getElementById('reset').click();")
        time.sleep(0.4)
        os.makedirs('output/verify', exist_ok=True)
        shot = cdp(ws, 'Page.captureScreenshot', {'format': 'png'})
        with open('output/verify/method-limits.png', 'wb') as f:
            f.write(base64.b64decode(shot['result']['data']))
        print('\n截图已更新 output/verify/method-limits.png')

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
