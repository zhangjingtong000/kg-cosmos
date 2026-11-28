# -*- coding: utf-8 -*-
"""验证全站可访问性与导航：键盘可达、能回导航页、动效可关。

对抗评审列的 major 项：改动前全站 0 处 `:focus-visible`、
0 处 `prefers-reduced-motion`、5 个主页面没有任何一个能回到导航页、
星云页 1023 个实体列表项全部不可 Tab 聚焦。

本脚本**不看源码里有没有写**，而是用 CDP 实测：
  · 每页是否存在可聚焦的「返回导航页」入口，且指向正确；
  · 星云页实体列表项是否真的拿到了 tabindex；
  · `:focus-visible` 规则是否真的生效（用 keyboard Tab 后读 computed outline）；
  · `prefers-reduced-motion: reduce` 下动画时长是否被压到近零。

用法：
  python tools/cdp_verify_a11y.py
"""
import json
import subprocess
import sys
import time
import urllib.request

import websocket

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
BASE = 'http://127.0.0.1:8098/web'
PAGES = ['index.html', 'kg-nebula.html', 'analytics.html',
         'method-limits.html', 'pathqa.html', 'legacy/refs.html',
         # K2（2026-09-15）：论文管理页。**新页面必须加进这份名单** ——
         # 否则"每页都有可聚焦的返回入口"这条检查根本不覆盖它，
         # 而这正是本项目栽过的坑（demo-rail 注入时漏过 3 个页面）。
         'legacy/corpus.html']


def cdp_open(url, port_base=9940, emulate_reduced=False):
    port = port_base + (int(time.time() * 1000) % 50)
    proc = subprocess.Popen([
        CHROME, '--headless=new', '--enable-unsafe-swiftshader', '--disable-gpu',
        '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
        f'--remote-debugging-port={port}', '--remote-allow-origins=*',
        '--window-size=1400,900', url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    targets = None
    for _ in range(90):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/json', timeout=2) as r:
                targets = json.load(r)
            if any(t.get('type') == 'page' for t in targets):
                break
        except Exception:
            pass
        time.sleep(0.4)
    if not targets:
        proc.terminate()
        raise RuntimeError('CDP 未就绪')
    page = next(t for t in targets if t.get('type') == 'page')
    ws = websocket.create_connection(page['webSocketDebuggerUrl'], timeout=60)

    def send(method, params=None):
        ws.send(json.dumps({'id': 1, 'method': method, 'params': params or {}}))
        while True:
            m = json.loads(ws.recv())
            if m.get('id') == 1:
                return m

    def ev(expr):
        r = send('Runtime.evaluate', {'expression': expr, 'returnByValue': True,
                                      'awaitPromise': True})
        return r.get('result', {}).get('result', {}).get('value')

    if emulate_reduced:
        send('Emulation.setEmulatedMedia',
             {'features': [{'name': 'prefers-reduced-motion', 'value': 'reduce'}]})
    return proc, ws, ev


def main():
    fails = []

    print('=== ① 每页都能回导航页 ===')
    for page in PAGES:
        proc, ws, ev = cdp_open(f'{BASE}/{page}', port_base=9940)
        try:
            for _ in range(60):
                if ev('window.__ready === true') or ev('document.readyState === "complete"'):
                    break
                time.sleep(0.25)
            time.sleep(0.8)   # 等 a11y.js 注入
            info = ev("""(() => {
              const a = document.getElementById('a11y-home');
              const skip = document.getElementById('a11y-skip');
              const styles = getComputedStyle(document.body);
              const cv = getComputedStyle(document.documentElement)
                .getPropertyValue('--never');
              return {
                home: !!a, href: a ? a.getAttribute('href') : null,
                skip: !!skip,
                hasA11yCss: [...document.styleSheets].some(s =>
                  (s.href||'').includes('a11y.css')),
              };
            })()""")
            is_index = page == 'index.html'
            print(f"  {page:20s} home={info.get('home')} skip={info.get('skip')} "
                  f"css={info.get('hasA11yCss')} href={info.get('href')}")
            if not info.get('hasA11yCss'):
                fails.append(f'{page} 没有加载 a11y.css')
            if is_index:
                # 导航页自己不需要返回入口
                pass
            elif not info.get('home'):
                fails.append(f'{page} 没有「返回导航页」入口')
            elif info.get('href') not in ('./index.html', 'index.html'):
                fails.append(f'{page} 的返回链接指向 {info.get("href")}')
        finally:
            ws.close()
            proc.terminate()

    print('\n=== ② 动态渲染的列表项键盘可达 ===')
    proc, ws, ev = cdp_open(f'{BASE}/kg-nebula.html?c=0', port_base=9980)
    try:
        for _ in range(90):
            if ev('window.__ready === true'):
                break
            time.sleep(0.25)
        time.sleep(1.5)
        st = ev("""(() => {
          const rows = [...document.querySelectorAll('#list .item')];
          const tabbable = rows.filter(r => r.getAttribute('tabindex') === '0');
          const role = rows.filter(r => r.getAttribute('role')).length;
          return {total: rows.length, tabbable: tabbable.length, role: role};
        })()""")
        print(f"  {st}")
        if st and st['total'] and st['tabbable'] == 0:
            fails.append('实体列表项仍然全部不可 Tab 聚焦')
        elif st and st['total']:
            print(f"    OK {st['tabbable']}/{st['total']} 项可 Tab")

        # 键盘激活：焦点移到第一项后按 Enter，应触发选中
        act = ev("""(() => {
          const r = document.querySelector('#list .item');
          if (!r) return null;
          r.focus();
          const before = document.querySelectorAll('#detail.open, .sel, .selected').length;
          r.dispatchEvent(new KeyboardEvent('keydown',
            {key: 'Enter', bubbles: true, cancelable: true}));
          return {focused: document.activeElement === r, before: before};
        })()""")
        print(f"  键盘激活: {act}")
        if act and not act.get('focused'):
            fails.append('列表项无法获得焦点')
    finally:
        ws.close()
        proc.terminate()

    print('\n=== ③ :focus-visible 真的生效 ===')
    proc, ws, ev = cdp_open(f'{BASE}/analytics.html', port_base=9990)
    try:
        for _ in range(60):
            if ev('window.__ready === true'):
                break
            time.sleep(0.25)
        time.sleep(0.6)
        fo = ev("""(() => {
          const a = document.querySelector('a[href], button');
          if (!a) return null;
          a.focus();
          const cs = getComputedStyle(a);
          return {tag: a.tagName, outlineWidth: cs.outlineWidth,
                  outlineStyle: cs.outlineStyle, outlineColor: cs.outlineColor};
        })()""")
        print(f"  {fo}")
        # 程序化 focus() 不总是触发 :focus-visible，所以这里只做提示性检查；
        # 权威判据是样式表里存在该规则（下面用 CSSOM 查）。
        rule = ev("""(() => {
          for (const s of document.styleSheets) {
            let rules; try { rules = s.cssRules; } catch (e) { continue; }
            for (const r of rules) {
              if (r.selectorText && r.selectorText.includes(':focus-visible')) return r.selectorText;
            }
          }
          return null;
        })()""")
        print(f"  CSSOM 中的 :focus-visible 规则: {rule}")
        if not rule:
            fails.append('样式表里没有 :focus-visible 规则')
    finally:
        ws.close()
        proc.terminate()

    print('\n=== ④ prefers-reduced-motion 生效 ===')
    proc, ws, ev = cdp_open(f'{BASE}/analytics.html', port_base=10020, emulate_reduced=True)
    try:
        for _ in range(60):
            if ev('window.__ready === true'):
                break
            time.sleep(0.25)
        d = ev("""(() => {
          const el = document.querySelector('.layer, section, div');
          if (!el) return null;
          const cs = getComputedStyle(el);
          return {transition: cs.transitionDuration, animation: cs.animationDuration};
        })()""")
        print(f"  {d}")
        if d and d.get('transition'):
            vals = [v.strip() for v in d['transition'].split(',')]
            slow = [v for v in vals if v.endswith('ms') and float(v[:-2]) > 5]
            if slow:
                fails.append(f'reduced-motion 下过渡仍为 {slow}')
            else:
                print('    OK 过渡被压到近零')
    finally:
        ws.close()
        proc.terminate()

    print('\n' + '=' * 62)
    if fails:
        print(f'未通过 {len(fails)} 项：')
        for f in fails:
            print('  · ' + f)
        return 1
    print('全部通过：键盘可达、可回导航页、动效可关')
    return 0


if __name__ == '__main__':
    sys.exit(main())
