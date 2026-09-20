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


def read_mean(ws):
    """读 3×3 网格的 RGB 均值。

    为什么不用单点：着色器的时间系数只有 0.05，短时间内画面变化本来就小；
    再叠加"某个像素恰好变化微弱"，单点断言会在 CPU 繁忙（例如同时在跑
    LLM 抽取）时误报"时间驱动失效"。均值才是"整屏光量"的稳定估计，
    这也正是第 2b 项（滚动变亮）一直采用 3×3 的原因。
    """
    return ev(ws, """(() => {
      const c = document.getElementById('voyageCanvas');
      if (!c) return null;
      const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
      if (!gl) return null;
      const px = new Uint8Array(4);
      let r = 0, g = 0, b = 0, n = 0;
      for (const fx of [0.25, 0.5, 0.75]) {
        for (const fy of [0.25, 0.5, 0.75]) {
          gl.readPixels(Math.round(c.width * fx), Math.round(c.height * fy),
                        1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
          r += px[0]; g += px[1]; b += px[2]; n++;
        }
      }
      return [Math.round(r / n), Math.round(g / n), Math.round(b / n)];
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
        # 必须等页面自报就绪再检查背景。
        # voyage.js 是 ES module：要先下载、解析、编译着色器、画上首帧，才会置
        # window.__ready。这里以前只 sleep(1.0)，在慢机器或首次冷启动时必然读到
        # 空画布，表现成「画布没有启用 / 时间驱动失效 / 着色器没被滚动驱动」——
        # 那是**检查得太早**，不是页面坏了。（2026-09-14 修）
        for _ in range(300):
            if ev(ws, 'window.__ready === true') is True:
                break
            time.sleep(0.1)
        time.sleep(1.2)

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

        # 2. 时间自走断言：**不滚动**，静置一段时间，画面必须自己继续更新。
        #    背景是实时着色器（时间驱动），这是"页面是活的"的最低证据；
        #    旧版帧序列只在滚动时才动，正是被用户抱怨"死板"的根源。
        #
        #    **判据在 2026-09-14 换成帧计数 + u_t**：原先靠"3×3 均值像素变化量
        #    ≥3"间接推断，但着色器时间系数小、CPU 繁忙时帧率又低，实测两次
        #    误报（变化量 2），都发生在机器忙的时候 —— 不是产品问题，是断言
        #    选错了观测量。`window.__frames` / `window.__u_t` 由 voyage.js 在
        #    每帧 drawArrays 后写入，是"确实在按时间更新"的直接证据，且不受
        #    CPU 负载影响。像素变化与 u_t 仍一并打印，便于对照。
        f_a = ev(ws, 'window.__frames || 0')
        t_a = ev(ws, 'window.__u_t || 0')
        p_a = read_mean(ws)
        time.sleep(2.0)
        f_b = ev(ws, 'window.__frames || 0')
        t_b = ev(ws, 'window.__u_t || 0')
        p_b = read_mean(ws)
        diff = sum(abs(a - b) for a, b in zip(p_a, p_b)) if (p_a and p_b) else -1
        df = (f_b or 0) - (f_a or 0)
        dt = (t_b or 0) - (t_a or 0)
        print(f'[2] 时间自走: 帧 {f_a}→{f_b} (Δ{df}) · u_t {t_a:.2f}→{t_b:.2f} '
              f'(Δ{dt:.2f}) · 像素变化量 {diff}')
        if not f_b:
            fails.append('window.__frames 不存在 —— voyage.js 未运行或未更新')
        elif df < 5:
            fails.append(f'2 秒内只渲染了 {df} 帧 —— 时间驱动失效')
        elif dt < 1.0:
            fails.append(f'u_t 只前进了 {dt:.2f} 秒 —— 时间 uniform 没有推进')
        else:
            print(f'    ✅ 帧在增长且 u_t 在推进 ⇒ 时间驱动有效')
        # 像素变化只作提示，不再作为判据（观测量太脆弱，见上面的说明）
        if diff >= 0 and diff < 3:
            print(f'    · 提示：像素变化量只有 {diff}（着色器时间系数小，属正常）')

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

        # 2b-2. 点击涟漪（2026-09-20 加）。
        #
        # **为什么必须补这一条**：这里原先**完全没有覆盖点击涟漪** ——
        # 全仓库 `grep -r "ripple|spawnRipple|u_r"` 只命中 `web/lib/voyage.js`，
        # 本脚本上面的量/帧/u_t 三个断言一个都碰不到它。
        # 后果是实测出来的：有人给涟漪的"年龄"套了一层 `expo.out`，
        # 以为那是"快速荡开、缓慢停下"，但 shader 的亮度项是 `exp(-1.8 * z)`
        # —— **年龄同时是半径与亮度的自变量**，于是"扩得快"等于"灭得快"：
        # 同一时刻亮度只剩 1/4.8、环在约 0.15 秒就撞到屏幕内切圆、全程约 12 帧。
        # 弱了 4.8 倍，而**没有任何测试会红**，只能靠用户肉眼发现。
        #
        # 判据（不看像素，只看 GSAP 写进 `r.age` 的数）：
        #   ① 点下去必须真的产生 1 个涟漪；
        #   ② 年龄必须**单调递增**；
        #   ③ 年龄推进速率必须落在合理区间 —— 这一条正是"前段压缩型缓动"的捕手：
        #      线性 2.2/3.6s = 0.611/s；expo.out 之类会在 0.2 秒内冲到 1.0+/s。
        #      这里取 [0.35, 0.95]：低于 0.35 说明慢到"看不见在动"，
        #      高于 0.95 说明又把年龄压缩到前段了。
        ev(ws, """window.dispatchEvent(new PointerEvent('pointerdown',
          {clientX: Math.round(innerWidth*0.5), clientY: Math.round(innerHeight*0.55),
           bubbles: true}))""")
        time.sleep(1.2)
        rip = ev(ws, """(() => {
          if (typeof window.__voyageRipple !== 'function') return { hook: false };
          const a = window.__voyageRipple();
          return { hook: true, n: a.filter(Boolean).length, sample: a.find(Boolean) || null };
        })()""")
        time.sleep(1.2)
        rip2 = ev(ws, """(() => {
          const a = window.__voyageRipple();
          const r = a.find(Boolean);
          return r ? { age: r.age, amp: r.amp } : null;
        })()""")
        if not rip or not rip.get('hook'):
            fails.append('window.__voyageRipple 不存在 —— voyage.js 未暴露只读涟漪快照'
                         '（没有它就无法测"点击真的产生了涟漪"）')
        elif rip['n'] < 1 or not rip.get('sample'):
            fails.append('点击后没有产生涟漪（spawnRipple 没跑，或 MOTION_OFF 被误判）')
        elif not rip2:
            fails.append('涟漪在 1.2 秒后就消失了 —— 生命周期不对')
        else:
            age1 = float(rip['sample']['age'])
            age2 = float(rip2['age'])
            rate = (age2 - age1) / 1.2 if age2 > age1 else 0.0
            print(f'[2b-2] 点击涟漪: 数量={rip["n"]} · 年龄 {age1:.3f} → {age2:.3f} '
                  f'(速率 {rate:.3f}/s · 期望 0.611) · 强度={rip["sample"]["amp"]:.2f}')
            if age2 <= age1:
                fails.append(f'涟漪年龄没有推进（{age1:.3f} → {age2:.3f}）'
                             '—— 年龄若停住，点击处会留一个永不消失的静止亮斑')
            elif not 0.35 <= rate <= 0.95:
                fails.append(f'涟漪年龄推进速率 {rate:.3f}/s 不在 [0.35, 0.95] 内'
                             '（期望 0.611）：太低=慢到看不出在动，'
                             '太高=又把年龄压缩到前段（expo.out 那类缓动会让环"扩得快、灭得快"）')
            else:
                print('    ✅ 涟漪真的产生了，且年龄按线性推进（没有前段压缩）')

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
        # 2026-09-14：入口从 4 个增到 6 个（加了 upload.html 起点、ask.html 提问），
        # 并把顺序改成"先演示（上传→提问）、后看成果（主视图/面板/PathQA/局限）"。
        # 这里改成校验**必须包含这几条关键路径**，而不是钉死一个数字 ——
        # 以后再加页面不用回来改断言，但少一条就会红。
        entries = ev(ws, """[...document.querySelectorAll('a.entry')].map(e => ({
          href: e.getAttribute('href'),
          title: (e.querySelector('h3')||{}).textContent || ''
        }))""")
        print(f'[3] 入口数: {len(entries)}')
        for e in entries:
            print(f"    {e['title'][:18]:20s} → {e['href']}")
        REQUIRED = ['upload.html', 'ask.html', 'kg-nebula.html',
                    'analytics.html', 'pathqa.html', 'method-limits.html']
        got = {e['href'] for e in entries}
        missing = [h for h in REQUIRED if h not in got]
        if missing:
            fails.append(f'导航页缺少入口: {missing}')
        if len(entries) < len(REQUIRED):
            fails.append(f'入口少于 {len(REQUIRED)} 个，实为 {len(entries)}')

        # 4. 数据区被 JS 填充（说明数据链路仍在工作）
        facts = ev(ws, "(document.getElementById('liveFacts')||{}).innerText || ''")
        print(f'[4] 数据区: {facts[:70].replace(chr(10), " / ")!r}')
        # **不硬编码规模**（2026-09-15 修）—— 原来写死 `'18,780' not in facts`，
        # 而本项目的数据规模会变（概览口径从 18780 变成 19427），
        # 于是"界面明明正常显示"也会被判失败。
        # **这与 `check_doc_numbers.py` 的测试数基准是同一类病**：
        # 一个写死的基准，本身就是下一次过时。
        # 改为"确实填了数字"：至少一个 ≥4 位的数字（含千分位逗号）。
        import re as _re
        if not _re.search(r'\d{1,3}(?:,\d{3})+|\d{4,}', facts):
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
