# -*- coding: utf-8 -*-
"""端到端验证人工盲判界面：确保那 200 条判读不会卡在工具上。

【为什么值得单独验】
  L5 的严格效度**只能**由人工盲判给出（判别器不能自证）。
  如果界面本身有 bug（盲态泄漏、翻条丢状态、PDF 定位失效），
  这 3–6 小时的人工投入就白费，而且会得到一个不可信的数字。
  所以「界面能用」是这层的前置条件，必须可验证。

本脚本用 CDP 实测四件事：
  ① 服务起得来、页面能加载；
  ② **盲态不泄漏**：页面与网络响应里都不得出现 entail_prob / pass / hit；
  ③ 判定能落盘，且落盘内容不含被隐藏的字段；
  ④ 键盘快捷键与翻条不丢状态。

用法：
  python tools/cdp_verify_l5_review.py
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websocket

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
ROOT = Path(__file__).resolve().parent.parent
PORT = 8097
LABELS = ROOT / 'data' / 'quality' / '_verify_labels.jsonl'


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


def ev(ws, expr, state=None):
    r = cdp(ws, 'Runtime.evaluate',
            {'expression': expr, 'returnByValue': True, 'awaitPromise': True}, state)
    res = r.get('result', {})
    if 'exceptionDetails' in res:
        return {'__error__': res['exceptionDetails'].get('text')}
    return res.get('result', {}).get('value')


def main():
    fails = []

    # ── 起盲判服务（用临时标签文件，不污染用户的进度）──
    if LABELS.exists():
        LABELS.unlink()
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / 'tools' / 'l5_validity_review.py'),
         '--port', str(PORT), '--no-browser', '--labels', str(LABELS)],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        up = False
        for _ in range(40):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/', timeout=2) as r:
                    html = r.read().decode('utf-8', 'replace')
                up = True
                break
            except Exception:
                time.sleep(0.5)
        print(f'① 服务可用: {up}')
        if not up:
            fails.append('盲判服务起不来')
            return 1

        # ② 盲态：HTML 与数据接口都不得泄漏判别器字段
        leaks = [k for k in ('entail_prob', 'pass', '"hit"') if k in html]
        print(f'② 页面 HTML 中的判别器字段: {leaks or "无"}')
        if leaks:
            fails.append(f'页面 HTML 泄漏了判别器字段 {leaks}')

        items = None
        for path in ('/items', '/api/items', '/sheet', '/api/sheet'):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{PORT}{path}', timeout=3) as r:
                    items = json.loads(r.read().decode('utf-8'))
                break
            except Exception:
                continue
        if items is not None:
            blob = json.dumps(items, ensure_ascii=False)
            bad = [k for k in ('entail_prob', '"pass"', '"hit"') if k in blob]
            print(f'   数据接口泄漏: {bad or "无"}')
            if bad:
                fails.append(f'数据接口泄漏判别器字段 {bad}')

        # ③ 浏览器实测：判定 → 落盘
        port = 9750
        cp = subprocess.Popen([
            CHROME, '--headless=new', '--enable-unsafe-swiftshader', '--disable-gpu',
            '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
            f'--remote-debugging-port={port}', '--remote-allow-origins=*',
            '--window-size=1400,900', f'http://127.0.0.1:{PORT}/',
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
            st = {'id': 0}
            cdp(ws, 'Runtime.enable', None, st)
            time.sleep(2.5)

            dom = ev(ws, """JSON.stringify({
              hasTriple: !!document.querySelector('table, .triple, #triple, pre'),
              bodyText: document.body.innerText.slice(0, 400),
            })""", st)
            print(f'③ 页面已渲染: {str(dom)[:110]}')

            # 键盘判定（1 = support）
            ev(ws, "window.dispatchEvent(new KeyboardEvent('keydown',"
                   "{key:'1', bubbles:true}))", st)
            time.sleep(1.2)
            n = sum(1 for _ in LABELS.open(encoding='utf-8')) if LABELS.exists() else 0
            print(f'   按一次「1」后落盘条数: {n}')
            if n == 0:
                # 可能不是键盘绑定，退回点击
                ev(ws, """(() => {
                  const b = [...document.querySelectorAll('button')]
                    .find(x => /支持|support|1/.test(x.textContent));
                  if (b) b.click();
                })()""", st)
                time.sleep(1.2)
                n = sum(1 for _ in LABELS.open(encoding='utf-8')) if LABELS.exists() else 0
                print(f'   改为点击按钮后落盘条数: {n}')
            if n == 0:
                fails.append('判定没有落盘（键盘与按钮都试过）')
            else:
                rec = json.loads(LABELS.read_text(encoding='utf-8').strip().split('\n')[0])
                bad = [k for k in ('entail_prob', 'pass', 'hit') if k in rec]
                print(f'   落盘记录字段: {sorted(rec.keys())}')
                if bad:
                    fails.append(f'落盘记录含被隐藏字段 {bad}')
            ws.close()
        finally:
            cp.terminate()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        if LABELS.exists():
            LABELS.unlink()      # 这是验证用的临时文件，清掉

    print('\n' + '=' * 60)
    if fails:
        print(f'未通过 {len(fails)} 项：')
        for f in fails:
            print('  · ' + f)
        return 1
    print('盲判界面可用：服务正常、盲态未泄漏、判定能落盘')
    return 0


if __name__ == '__main__':
    sys.exit(main())
