# -*- coding: utf-8 -*-
"""验证证据深链（T6.2）：证据面板里的"在原文中查看"是否正确构造 PDF 链接。

能力边界要说清楚：**无头 Chrome 不渲染 PDF**，所以本脚本无法验证高亮效果，
只能验证链接本身——跳页参数、search 片段、以及 PDF 文件真的能被 HTTP 取到。
高亮的观感必须在真浏览器里看。

用法：
  python tools/cdp_verify_pdflink.py
"""
import base64
import hashlib
import json
import subprocess
import sys
import time
import urllib.parse
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
        return {'__error__': res['exceptionDetails'].get('text', 'exception')}
    return res.get('result', {}).get('value')


def main():
    port = 9950 + (int(time.time() * 1000) % 40)
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
        ws = websocket.create_connection(page['webSocketDebuggerUrl'], timeout=60)
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
        cases = [
            ('short text', ''),
            ('Knowledge graphs (KGs) of real-world facts about entities and…',
             'Knowledge graphs (KGs) of real-world facts'),
            ('  multi   space\ttext with enough length to qualify  ', None),
        ]
        snips = ev(ws, "(() => [" +
                   ",".join(f"KGNebula.pdfSnippetOf({json.dumps(c[0])})" for c in cases) +
                   "])()")
        print(f'[1] pdfSnippetOf 结果: {snips}')
        if snips[0] != '':
            fails.append('过短的文本不应产生 search 片段')
        if not snips[1] or '…' in snips[1] or '  ' in snips[1]:
            fails.append(f'snippet 里残留省略号或多空格: {snips[1]!r}')
        if snips[2] and '  ' in snips[2]:
            fails.append('snippet 未压平连续空白')

        # 2. 链接构造
        href = ev(ws, "KGNebula.pdfHrefOf('1703.08098v9.pdf', 4, 'A survey of embedding')")
        print(f'[2] pdfHrefOf → {href}')
        if '#page=4' not in href or 'search=' not in href:
            fails.append(f'链接缺少 page 或 search 参数: {href}')
        if '/data/corpus/' not in href:
            fails.append(f'链接路径不含 /data/corpus/: {href}')
        noq = ev(ws, "KGNebula.pdfHrefOf('x.pdf', 2, '')")
        if 'search=' in noq:
            fails.append('空片段时不应带 search 参数')

        # 3. 链接背后的 PDF 真的能取到（把 #fragment 去掉后请求）
        real = href.split('#')[0]
        try:
            with urllib.request.urlopen(real, timeout=10) as r:
                ctype = r.headers.get('Content-Type')
                clen = r.headers.get('Content-Length')
            print(f'[3] PDF 可访问: {r.status} {ctype} {clen} 字节')
            if 'pdf' not in (ctype or '').lower():
                fails.append(f'Content-Type 不是 PDF: {ctype}')
        except Exception as e:
            fails.append(f'PDF 取不到: {e}')

        # 4. 打开抽屉：选中一个有证据的节点，点它的"在原文中查看"
        res = ev(ws, """(() => {
          const ud = KGNebula._internals().ud;
          if (!ud || !ud.data) return {err: 'no data'};
          // 找一个在证据表里有出处的节点
          for (let i = 0; i < ud.data.nodes.length; i++) {
            KGNebula.select(i);
            const a = document.querySelector('#detail .evlink');
            if (a) {
              const before = KGNebula.panelOpen();
              a.click();
              return {
                node: ud.data.nodes[i].id,
                src: a.dataset.src, page: a.dataset.page,
                snipLen: (a.dataset.snip || '').length,
                detailBefore: before,
                pdfOpen: KGNebula.pdfOpen(),
                pdfSrc: KGNebula.pdfSrc(),
                detailAfter: KGNebula.panelOpen(),
              };
            }
          }
          return {err: 'no evlink'};
        })()""")
        print(f'[4] 点击"在原文中查看": {json.dumps(res, ensure_ascii=False)}')
        if res.get('err'):
            fails.append(f'未能找到带证据的节点: {res["err"]}')
        else:
            if res['pdfOpen'] is not True:
                fails.append('点击后 PDF 抽屉未打开')
            if f'#page={res["page"]}' not in (res['pdfSrc'] or ''):
                fails.append(f'PDF src 的页码与证据不一致: {res["pdfSrc"]}')
            if res['detailAfter']:
                fails.append('打开 PDF 后应让详情面板让位，实际仍打开')
            # 5. 关闭
            closed = ev(ws, "KGNebula.closePdf()")
            src_after = ev(ws, "KGNebula.pdfSrc()")
            print(f'[5] 关闭后 open={closed}  src={src_after}')
            if closed is not False:
                fails.append('关闭后抽屉仍处于打开态')
            if src_after != 'about:blank':
                fails.append(f'关闭后未释放 iframe（src={src_after}）')

        # ── 6. 页码片段的生效性（实测，不是推断）──
        # 无头 Chrome **能渲染 PDF**（这点与常见认知相反），所以跳页可以直接验：
        # 落到不同页时截图不同。而 `#search=` 无法这样验证成"生效"，
        # 只如实记录"它与不带该参数时截图相同"这一事实。
        def pdf_shot_hash(page, search=None):
            # 同一个 PDF 只改 fragment **不会触发导航**，页面会停在上一次的状态
            # （第一次跑这个测试时 #page=1 与 #page=8 的截图完全相同，
            #  差点被误判成"页码片段失效"）。加一个每次不同的 query 参数
            # 强制真正重新加载。
            tag = hashlib.sha1(f'{page}|{search}'.encode()).hexdigest()[:8]
            frag = f'#page={page}' + (f'&search={search}' if search else '')
            cdp(ws, 'Page.navigate',
                {'url': f'{BASE}/data/corpus/1703.08098v9.pdf?t={tag}{frag}'})
            time.sleep(3.5)
            s = cdp(ws, 'Page.captureScreenshot', {'format': 'png'})
            return hashlib.sha256(base64.b64decode(s['result']['data'])).hexdigest()[:12]

        h1 = pdf_shot_hash(1)
        h8 = pdf_shot_hash(8)
        print(f'[6] #page=1 → {h1}')
        print(f'    #page=8 → {h8}')
        if h1 == h8:
            fails.append('#page 片段未生效（第 1 页与第 8 页截图完全相同）')
        else:
            print('    OK 页码片段生效（两页截图不同）')

        h8s = pdf_shot_hash(8, 'score%20functions')
        same = (h8s == h8)
        print(f'[7] #page=8&search=score functions → {h8s}  '
              f'{"与 #page=8 完全相同" if same else "与 #page=8 不同"}')
        if same:
            print('    ⚠️ 实测记录：无头 Chrome 下 `#search=` **未生效**。')
            print('       代码保留该参数作为增强，但按钮文案与 tooltip 都不承诺高亮；')
            print('       有头模式是否生效无法在本环境验证。要可控高亮须改用 pdf.js 自绘。')
        else:
            print('    `#search=` 在本环境生效')

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
    print('全部通过：`#page=N` 已实测生效（第 1 页与第 8 页截图哈希不同）；'
          '`#search=` 在本环境的无头 Chrome 下未生效，代码已按"不承诺高亮"处理')
    return 0


if __name__ == '__main__':
    sys.exit(main())
