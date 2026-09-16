# -*- coding: utf-8 -*-
r"""H4 语义兜底：端点 + 前端接入。

## 为什么这两层都要测

**端点层**（`POST /api/semantic-match`）：
  · **"未就绪"必须能区分于"没找到"** —— 嵌入文件没生成时返回 `ready:false`，
    而不是空数组。**否则前端会把"功能没开"显示成"图里没有这个概念"**，
    那是本项目最想避免的一类"沉默的错误结论"。
  · `min_score` 必须真的过滤 —— H4 的标定结论是"余弦中间区不可判"，
    返回一个 0.3 分的"最像的"比返回空更危险（调用方会以为找到了）。

**前端层**（`web/pathqa.html`）：
  · **字面命中时不该走兜底**（白花一次嵌入）；
  · **字面不命中时走兜底，且必须把"这是语义兜底"写在界面上** ——
    否则用户会以为自己的词真的字面出现在图里。

## 用 8000 而不是 8098

`8098` 是纯静态站（没有 API）。这一项需要真端点 ⇒ 用 `python src/app.py` 起的 8000。

用法：
    python tools/cdp_verify_semantic_fallback.py
"""
import json
import subprocess
import sys
import time
import urllib.request

import websocket

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
BASE = 'http://127.0.0.1:8000'


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
        return {'__error__': str(res['exceptionDetails'])[:300]}
    return res.get('result', {}).get('value')


def post(path, payload, timeout=180):
    body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(BASE + path, data=body,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _submit(ws, question):
    """触发检索。

    ⚠️ **不能用 `form.dispatchEvent(new Event('submit'))`** ——
    这个页面**没有绑 form 的 submit**，而是
    `#go` 的 click 与 `#q` 的 Enter。
    第一版测试就是栽在这里：派发了 submit，`ask()` 根本没跑，
    `window.__lastAsk` 一直是上一次的值，于是断言"字面不命中时没走兜底"
    —— **测的不是代码，是测试自己的幻觉。**
    """
    ev(ws, "(function(){var q=document.getElementById('q');"
           f"q.value={json.dumps(question)};"
           "document.getElementById('go').click();})()")


def main():
    fails = []

    # ── 端点层（先做，不需要浏览器）──────────────────────────────────────
    print('[端点] POST /api/semantic-match')
    try:
        j = post('/api/semantic-match', {'query': 'TransE', 'top_k': 5, 'min_score': 0.5})
    except Exception as e:
        print(f'  ❌ 端点不可用：{type(e).__name__}: {e}')
        print('     （先起后端：python src/app.py）')
        return 1

    ready = j.get('ready')
    print(f'  ready = {ready}')
    if not ready:
        print(f'  · 嵌入未就绪（这是如实报告，不是失败）：{j.get("note")}')
        print('  ⇒ 端点层"未就绪"路径通过；就绪后的断言等嵌入生成后再跑')
    else:
        ms = j.get('matches') or []
        print(f'  候选 {len(ms)} 条：' + ', '.join(f'{m["name"]}({m["score"]})' for m in ms[:5]))
        if not ms:
            fails.append('嵌入已就绪，但 "TransE" 一个候选都没返回 —— 阈值或数据有问题')
        for m in ms:
            if m['score'] < 0.5:
                fails.append(f'min_score=0.5 却返回了 {m["score"]} 的候选：{m["name"]}')
        if j.get('n_candidates', 0) < 1000:
            fails.append(f'候选池太小（{j.get("n_candidates")}），嵌入可能只算了一部分')

    # min_score 必须真的过滤
    try:
        j2 = post('/api/semantic-match', {'query': 'TransE', 'top_k': 50, 'min_score': 0.99})
        hi = j2.get('matches') or []
        print(f'  min_score=0.99 ⇒ {len(hi)} 条（应远少于 min_score=0.5 的结果）')
        if ready and len(hi) >= len(j.get('matches') or []) and hi:
            fails.append('提高 min_score 没有减少候选 —— 过滤没生效')
    except Exception as e:
        fails.append(f'min_score 过滤测试失败：{type(e).__name__}')

    # ── 前端层 ──────────────────────────────────────────────────────────
    port = 9900 + (int(time.time() * 1000) % 40)
    proc = subprocess.Popen([
        CHROME, '--headless=new', '--enable-unsafe-swiftshader', '--disable-gpu',
        '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
        f'--remote-debugging-port={port}', '--remote-allow-origins=*',
        '--window-size=1400,900', f'{BASE}/pathqa.html',
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
        cdp(ws, 'Runtime.enable')
        for _ in range(300):
            if ev(ws, 'window.__ready === true') is True:
                break
            time.sleep(0.1)

        print()
        print('[前端] web/pathqa.html')
        # ① 字面能命中的问句 ⇒ **不该走兜底**
        _submit(ws, 'TransE 的局限')
        time.sleep(1.2)
        la = ev(ws, 'JSON.stringify(window.__lastAsk)') or '{}'
        d = json.loads(la)
        print(f'  字面命中："TransE 的局限" ⇒ seeds {len(d.get("seeds") or [])} · '
              f'fallback={d.get("fallback")}')
        if not (d.get('seeds') or []):
            fails.append('"TransE 的局限" 字面应当命中，却没有种子')
        if d.get('fallback'):
            fails.append('字面已命中却仍走了语义兜底 —— 白花一次嵌入')

        # ② 字面命中不了的问句 ⇒ **必须走兜底路径**
        #
        # ⚠️ **不断言"兜底一定找到了"** —— 那取决于数据（余弦够不够 0.5）。
        # 可断言的是**路径走通了**：`fallback` 从 `None`（没走）变成
        # `'used'`（走了）或 `'off'`（走了但后端说没就绪）。
        _submit(ws, 'zzz 完全不存在的概念 qqq')
        time.sleep(8.0)
        d2 = json.loads(ev(ws, 'JSON.stringify(window.__lastAsk)') or '{}')
        print(f'  字面不命中：fallback={d2.get("fallback")} · '
              f'兜底候选 {(d2.get("fallbackMatches") or [])[:4]}')
        if d2.get('fallback') == 'pending':
            fails.append('语义兜底一直停在 pending（请求没回来）')
        if d2.get('fallback') is None:
            fails.append('字面不命中时**没有走**语义兜底路径 '
                         '（fallback 仍是 None —— 说明 ask() 根本没跑到那个分支）')
        if ready and d2.get('fallback') != 'used':
            fails.append(f'嵌入已就绪，兜底路径应当被走到，实际 {d2.get("fallback")!r}')

        # ②-b 再试一个**语义上确实接近图里某实体**的中文问句
        _submit(ws, '平移嵌入模型')
        time.sleep(8.0)
        d3 = json.loads(ev(ws, 'JSON.stringify(window.__lastAsk)') or '{}')
        print(f'  "平移嵌入模型"：fallback={d3.get("fallback")} · '
              f'兜底候选 {(d3.get("fallbackMatches") or [])[:4]}')

        # ③ 若兜底**找到了种子**，界面上必须有那条警告
        note = ev(ws, "(()=>{const e=document.querySelector('.fbnote');"
                      "return e ? e.innerText : ''})()")
        got_seeds = bool(d3.get('seeds') or d2.get('seeds'))
        print(f'  兜底提示 = {(note or "")[:70]!r}（兜底找到种子 = {got_seeds}）')
        if d3.get('fallback') == 'used' and got_seeds and '字面没有命中' not in (note or ''):
            fails.append('走了语义兜底且找到了种子，但界面上没有"字面没有命中"的提示 —— '
                         '用户会以为字面命中了')
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass

    print()
    print('=' * 62)
    if fails:
        print(f'❌ {len(fails)} 项失败：')
        for f in fails:
            print(f'   · {f}')
        return 1
    print('全部通过：端点区分"未就绪/没找到"、min_score 生效、'
          '前端只在字面失败时兜底且如实标注')
    return 0


if __name__ == '__main__':
    sys.exit(main())
