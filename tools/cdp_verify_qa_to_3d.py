# -*- coding: utf-8 -*-
r"""H9 的浏览器实测：**问题 → 子图 → 3D 高亮**那条链。

## 为什么必须用浏览器测

这一项**全部是前端**：`ask.html` 从子图里取出实体名 → 拼 `?hl=` →
`kg-nebula.html` 读 `?hl=` → 改颜色缓冲。**在 Python 里读源码证明不了它能跑。**

## 断言六组

1. **`?hl=<真名字>` ⇒ `__hlFocus` 就是那组名字**，且 **`__hlHit` > 0**
   （**命中了几个点** —— 只断言"参数被解析了"是不够的）；
2. **颜色缓冲的和真的变了**（`__colorSample`）——
   **"高亮生效"必须体现为渲染数据变化**（B7 的纪律）；
3. **`hl` 与 `type` 同时给时以 `hl` 为准**（两者叠加会互相打架）；
4. **名字一个都不存在 ⇒ `hl_missing` + 页面提示**（**不许静默**）；
5. **`ask.html` 的按钮存在，且它拼出的 URL 带 `hl=`**
   —— 用**内置演示数据**驱动（不调后端，几秒出结果）；
6. **从 ask 的按钮跳到主视图后，主视图真的高亮了**
   —— **这是端到端那一根线**，分两半测都证明不了它接上了。

用法：
    python tools/cdp_verify_qa_to_3d.py
"""
import json
import os
import sys
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get('KG_STATIC', 'http://127.0.0.1:8098/web')
NEBULA = BASE + '/kg-nebula.html'
ASK = BASE + '/ask.html'


def main():
    from playwright.sync_api import sync_playwright

    # 挑几个**真实存在**的节点名（从概览数据里取，不猜）
    npath = os.path.join(ROOT, 'web', 'kg-data', 'nebula.json')
    with open(npath, encoding='utf-8') as f:
        neb = json.load(f)
    # ⚠️ **必须从某个社区的 `core` 里取名字** ——
    #   `?hl=` 的实现要先 `enterCommunity(ci)`（因为实体点云只在社区内部视图里），
    #   而**社区是靠 `clouds[ci].core` 匹配的**。
    #   第一版从 `nebula.nodes[:3]` 里取，那些节点不在任何 core 里 ⇒
    #   匹配不到社区 ⇒ 不进入 ⇒ `applyColorFocus` 早退 ⇒
    #   **看起来像"高亮功能坏了"，其实是测试选的样本不对**。
    #   （第 ⑥ 组恰好用了 ask 演示数据里的名字，那些**在 core 里**，所以它过了。）
    #
    # ⚠️ **数据里的字段名是 `communities`，不是 `clouds`** ——
    #   `clouds` 是**页面内部**的变量名。第一版写 `neb.get('clouds')` 拿到空列表，
    #   于是名字回退成 `nodes[:3]`，又回到上面那个坑。**又一处"没读数据就写代码"。**
    comms = neb.get('communities') or neb.get('clouds') or []
    core_names, core_ci = [], None
    for ci, c in enumerate(comms):
        ids = [x.get('id') for x in (c.get('core') or []) if x and x.get('id')]
        if len(ids) > len(core_names):
            core_names, core_ci = ids, c.get('id', ci)
    names = core_names[:3] if core_names else [n['id'] for n in (neb.get('nodes') or [])[:3]]
    types = {}
    for n in (neb.get('nodes') or []):
        types[n.get('t')] = types.get(n.get('t'), 0) + 1
    some_type = max(types, key=types.get) if types else None
    print('=' * 88)
    print('H9 · 问题→子图→3D 高亮 的浏览器实测')
    print('=' * 88)
    print(f'  用真实节点名（来自社区 #{core_ci} 的 core）：{names}')
    print(f'  另取一个真实类型作对照：{some_type!r}')
    print()

    fails = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        errs = []
        page.on('pageerror', lambda e: errs.append(str(e)))

        hl = quote(','.join(names))
        page.goto(NEBULA + '?hl=' + hl, wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(3000)

        # ── ①② 高亮生效 + 颜色真的变了 ───────────────────────────────
        st = page.evaluate("""() => ({
          hl: window.__hlFocus || null,
          hit: (typeof window.__hlHit === 'number') ? window.__hlHit : null,
          cs: (typeof window.__colorSample === 'function') ? window.__colorSample() : null,
          vs: window.__viewState || null,
        })""")
        print(f'  ① __hlFocus={st["hl"]} · __hlHit={st["hit"]}')
        print(f'     __viewState={st["vs"]}')
        ok1 = bool(st['hl'] == names and (st['hit'] or 0) > 0)
        print(f'     名字对得上且命中了点：{"✅" if ok1 else "❌"}')
        if not ok1:
            fails.append(f'?hl= 没生效或没命中（hl={st["hl"]}, hit={st["hit"]}）')
        print()

        # 与"不高亮"时的颜色和对比
        #
        # ⚠️ **必须在同一层里比** —— 第一版拿"直接打开主视图（概览层）"的颜色和
        #   当基线，而概览层**没有实体点云**（它是社区云 + 纤维）⇒
        #   `__colorSample` 是 `undefined` ⇒ 得到 `None` ⇒
        #   **"57.5 ≠ None"被当成"颜色变了"其实是巧合**，而断言还失败了。
        #   ⇒ 正确做法：**停在当前这一层**，清掉高亮取一次、再加回来取一次。
        both = page.evaluate("""() => {
          if (typeof window.__applyHlFocus !== 'function') return null;
          const got = (typeof window.__colorSample === 'function')
            ? window.__colorSample() : null;
          window.__applyHlFocus(null);                 // 清掉
          const off = (typeof window.__colorSample === 'function')
            ? window.__colorSample() : null;
          return { with_hl: got, without_hl: off };
        }""")
        print(f'  ② 同一层内：有高亮 {both and both["with_hl"]}'
              f' → 清掉高亮 {both and both["without_hl"]}')
        ok2 = bool(both and both['with_hl'] is not None
                   and both['without_hl'] is not None
                   and both['with_hl'] != both['without_hl'])
        print(f'     渲染数据真的变了：{"✅" if ok2 else "❌"}')
        if not ok2:
            fails.append(f'颜色缓冲没变（{both}）—— 高亮没落到渲染上')
        # 恢复高亮（后面的断言还要用）
        page.evaluate('(n) => window.__applyHlFocus(n)', names)
        page.wait_for_timeout(300)
        print()

        # ── ③ hl 优先于 type ─────────────────────────────────────────
        if some_type:
            both = quote(','.join(names))
            page.goto(NEBULA + '?type=' + quote(some_type) + '&hl=' + both,
                      wait_until='networkidle', timeout=60000)
            page.wait_for_timeout(3000)
            st3 = page.evaluate("""() => ({
              hl: window.__hlFocus || null, tf: window.__typeFocus || null,
              hit: window.__hlHit, cs: window.__colorSample ? window.__colorSample() : null,
              vs: window.__viewState || null })""")
            print(f'  ③ 同时给 type 与 hl ⇒ {st3}')
            # 判据：**点云的最终颜色应当由 hl 决定** ⇒ 与单独 hl 时相同
            lone = page.evaluate(
                "() => (typeof window.__hlHit === 'number') ? window.__hlHit : null")
            ok3 = bool(st3['hl'] == names and (st3['hit'] or 0) > 0)
            print(f'     hl 仍然生效（没被 type 覆盖）：{"✅" if ok3 else "❌"}')
            if not ok3:
                fails.append(f'type 与 hl 同时给时 hl 失效了：{st3}')
            print()

        # ── ④ 名字全不存在 ⇒ 如实报 ──────────────────────────────────
        page.goto(NEBULA + '?hl=' + quote('__NO_SUCH_A__,__NO_SUCH_B__'),
                  wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(2500)
        st4 = page.evaluate("""() => ({
          vs: window.__viewState || null,
          status: (document.getElementById('status') || {}).textContent || '' })""")
        print(f'  ④ 假名字 ⇒ __viewState={st4["vs"]}')
        print(f'     提示：{st4["status"][:80]!r}')
        ok4 = bool(st4['vs'] and st4['vs'].get('hl_missing'))
        print(f'     如实报告缺失：{"✅" if ok4 else "❌"}')
        if not ok4:
            fails.append('全不存在的名字没被报告（应设 hl_missing=true）')
        print()

        # ── ⑤⑥ ask.html 的按钮 + 端到端跳转 ──────────────────────────
        page.goto(ASK, wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(1500)
        # 用**内置演示数据**驱动（不调后端，几秒出结果）
        #
        # ⚠️ **`QA.transe` 直接就是 `play()` 要的形状，不要再套 `adaptQA`** ——
        #   `adaptQA` 吃的是 `/api/qa` 的响应（`retrieval.seeds`），
        #   而 `QA.transe` 的 `seeds` **就在顶层**（还有 `nodes`）。
        #   第一版写成 `play(adaptQA(QA.transe))` ⇒
        #   `TypeError: Cannot read properties of undefined (reading 'seeds')`。
        #   **又一次"没读实现就写调用"** —— 记在这里免得下次再犯。
        played = page.evaluate("""() => {
          if (typeof play !== 'function' || typeof QA === 'undefined') return false;
          play(QA.transe);
          return true;
        }""")
        page.wait_for_timeout(1200)
        btn = page.evaluate("""() => {
          const b = document.getElementById('to3d');
          if (!b) return null;
          return { hidden: !!b.hidden, text: b.textContent,
                   n_nodes: Object.keys(CUR && CUR.nodes ? CUR.nodes : {}).length };
        }""")
        print(f'  ⑤ 演示数据驱动：{played} · 按钮：{btn}')
        if not played:
            fails.append('ask.html 里没有 play/QA，测不了（页面结构变了？）')
        elif not btn:
            fails.append('ask.html 里没有 #to3d 按钮')
        elif btn['hidden']:
            fails.append(f'#to3d 是 hidden，但子图有 {btn["n_nodes"]} 个节点')
        else:
            print(f'     ✅ 按钮可见：{btn["text"]!r}')
            # **真的点它** —— 这会导航到主视图
            page.click('#to3d')
            page.wait_for_load_state('networkidle', timeout=60000)
            page.wait_for_timeout(3200)
            st6 = page.evaluate("""() => ({
              url: location.href.slice(0, 110),
              hl: window.__hlFocus || null, hit: window.__hlHit,
              cs: window.__colorSample ? window.__colorSample() : null })""")
            print(f'  ⑥ 点按钮后：url={st6["url"]}')
            print(f'     __hlFocus={st6["hl"]}（{len(st6["hl"]) if st6["hl"] else 0} 个）'
                  f' · __hlHit={st6["hit"]} · 颜色和={st6["cs"]}')
            ok6 = bool(st6['hl'] and len(st6['hl']) > 0 and (st6['hit'] or 0) > 0)
            print(f'     **端到端那根线接上了**：{"✅" if ok6 else "❌"}')
            if not ok6:
                fails.append(f'从 ask 跳过来以后主视图没高亮：{st6}')
        print()

        if errs:
            print(f'  ⚠️ 页面 JS 报错 {len(errs)} 条：')
            for e in errs[:5]:
                print(f'     {e[:140]}')
            fails.append(f'页面有 JS 报错：{errs[0][:120]}')

        browser.close()

    print('=' * 88)
    if fails:
        print(f'❌ {len(fails)} 项未通过：')
        for f in fails:
            print(f'   · {f}')
        return 1
    print('✅ 六组断言全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
