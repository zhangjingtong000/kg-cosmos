# -*- coding: utf-8 -*-
"""验证证据深链（T6.2）：证据行 → 原文对应页，并**真的高亮那段证据**。

【这一版和上一版的区别】
  上一版用「Chrome 内置 PDF 查看器的 URL 片段」，无头环境**无法验证高亮**
  （`#search=` 不生效，只能断言链接构造得对）。
  现在改用 pdf.js 自绘 + 自建文本层，于是可以直接断言：
    · 文本层建起来了多少个 span；
    · 其中多少个被标成 `.hl`（高亮）；
    · 翻页后高亮是否会跟着重算。
  这才是"PDF 高亮已实现"的证据，而不只是"链接拼得对"。

用法：
  python tools/cdp_verify_pdflink.py
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
    port = 9700 + (int(time.time() * 1000) % 40)
    url = f'{BASE}/web/kg-nebula.html?c=0'
    proc = subprocess.Popen([
        CHROME, '--headless=new', '--enable-unsafe-swiftshader', '--disable-gpu',
        '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
        f'--remote-debugging-port={port}', '--remote-allow-origins=*',
        '--window-size=1600,900', url,
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
        ws = websocket.create_connection(page['webSocketDebuggerUrl'], timeout=90)
        cdp(ws, 'Page.enable')
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

        # 1. snippet 提取的边界行为
        snips = ev(ws, """[
          KGNebula.pdfSnippetOf('short'),
          KGNebula.pdfSnippetOf('Knowledge graphs (KGs) of real-world facts about entities and…'),
          KGNebula.pdfSnippetOf('  multi   space\\ttext with enough length to qualify  ')
        ]""")
        print(f'[1] pdfSnippetOf: {snips}')
        if snips[0] != '':
            fails.append('过短文本不应产生高亮片段')
        if '…' in (snips[1] or '') or '  ' in (snips[1] or ''):
            fails.append(f'snippet 未清理干净: {snips[1]!r}')

        # 2. 点证据行里的"在原文中查看"，等 pdf.js 渲染完
        res = ev(ws, """(async () => {
          const ud = KGNebula._internals().ud;
          if (!ud || !ud.data) return {err: 'no data'};
          for (let i = 0; i < ud.data.nodes.length; i++) {
            KGNebula.select(i);
            const a = document.querySelector('#detail .evlink');
            if (a) {
              const info = {node: ud.data.nodes[i].id, src: a.dataset.src,
                            page: a.dataset.page, snipLen: (a.dataset.snip || '').length};
              a.click();
              // 等 pdf.js 把文档与页面渲染完
              for (let t = 0; t < 80; t++) {
                await new Promise(r => setTimeout(r, 150));
                const st = KGNebula.pdfState();
                if (st && (st.error || st.spans)) { info.state = st; break; }
              }
              info.pdfOpen = KGNebula.pdfOpen();
              return info;
            }
          }
          return {err: 'no evlink'};
        })()""")
        print(f'[2] 点击"在原文中查看": {json.dumps(res, ensure_ascii=False)[:400]}')
        if res.get('err'):
            fails.append(f'未找到带证据的节点: {res["err"]}')
        else:
            st = res.get('state') or {}
            if st.get('error'):
                fails.append(f'pdf.js 加载失败: {st["error"]}')
            if res.get('pdfOpen') is not True:
                fails.append('点击后 PDF 抽屉未打开')
            if not st.get('pages'):
                fails.append('未取到 PDF 页数')
            else:
                print(f'     PDF 共 {st["pages"]} 页，当前第 {st.get("page")} 页')
            spans = st.get('spans') or 0
            hl = st.get('highlights') or 0
            print(f'     文本层 span {spans} 个，其中高亮 {hl} 个（片段 {st.get("snippet","")[:48]!r}）')
            if spans < 20:
                fails.append(f'文本层 span 过少（{spans}），页面可能没渲染出来')
            if hl <= 0:
                fails.append('**没有高亮任何 span —— PDF 高亮未生效**')
            # 3. 翻页后高亮应重算
            if st.get('pages', 0) > 1:
                ev(ws, 'KGNebula.pdfStep(1)')
                time.sleep(1.6)
                st2 = ev(ws, 'JSON.stringify({p: KGNebula.pdfState().page, '
                             'spans: document.querySelectorAll("#pdfTextLayer span").length, '
                             'hl: document.querySelectorAll("#pdfTextLayer span.hl").length})')
                d2 = json.loads(st2)
                print(f'[3] 翻到下一页: 第 {d2["p"]} 页，span {d2["spans"]}，高亮 {d2["hl"]}')
                if d2['p'] != (st.get('page') or 0) + 1:
                    fails.append('翻页未生效')
                if d2['spans'] <= 0:
                    fails.append('翻页后文本层为空')
                ev(ws, 'KGNebula.pdfStep(-1)')
                time.sleep(1.2)

        # 4. 关闭释放
        closed = ev(ws, 'KGNebula.closePdf()')
        left = ev(ws, 'document.querySelectorAll("#pdfTextLayer span").length')
        print(f'[4] 关闭后 open={closed}，残留 span={left}')
        if closed is not False:
            fails.append('关闭后抽屉仍打开')
        if left:
            fails.append('关闭后文本层未清空')

        # 5. 截图留档（高亮可见）
        ev(ws, """(async () => {
          const ud = KGNebula._internals().ud;
          for (let i = 0; i < ud.data.nodes.length; i++) {
            KGNebula.select(i);
            const a = document.querySelector('#detail .evlink');
            if (a) { a.click(); break; }
          }
        })()""")
        time.sleep(4.0)
        os.makedirs('output/verify', exist_ok=True)
        shot = cdp(ws, 'Page.captureScreenshot', {'format': 'png'})
        with open('output/verify/pdf-highlight.png', 'wb') as f:
            f.write(base64.b64decode(shot['result']['data']))
        print('\n截图已保存 output/verify/pdf-highlight.png')

        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    print('\n' + '=' * 60)
    if fails:
        print(f'未通过 {len(fails)} 项：')
        for f in fails:
            print('  · ' + f)
        return 1
    print('全部通过：PDF 高亮已实现且可断言')
    return 0


if __name__ == '__main__':
    sys.exit(main())
