# -*- coding: utf-8 -*-
r"""全库问答（GraphRAG 式 Global Search）的浏览器实测。

## 为什么必须用浏览器测

这一项**全部是接线**：模式按钮 → `askReal` 分流 → `POST /api/qa/global/step`
→ `adaptQAGlobal` → 社区卡片 / 证据卡 / 导出。在 Python 里读源码证明不了它跑得通。
（脚本用 Playwright —— 项目纪律：**新脚本一律 Playwright**，
见 `tools/check_e2e_stack.py`。）

## 断言七组

1. **模式控件存在**，默认是「单篇图」（`aria-checked` 即状态）；
2. **切到全库**：按钮态翻转、全局示例问题出现、语境提示改口；
3. **全库提问真的走社区摘要**：四步跑完，`#trace` 里出现「社区级摘要」；
4. **★ 页面上能看出这是社区级证据**（这一段是纪律的可见形式）：
   · 社区卡片上有「社区级摘要」标签；
   · 证据卡的来源写的是**社区 + 生成它的模型**；
   · 没有原文可点的那句写的是「社区级摘要 · 不是论文原文」；
   · 结论区的说明写着「不是论文原文、也没有页码」；
   · 统计里「带原文引文」是 0（社区级证据没有引文）。
5. **零 JS 报错**（`pageerror`）；
6. **切回单篇**：`?src=<真图>` 上仍走「种子 → 子图 → 原文引文」那条老路，
   `#trace`/`#caveat` 里**不许出现**社区口径，`#to3d` 仍写"这片子图"；
7. **单篇的证据仍是论文原文**（有 `.tag.s` 引文侧）。

## 用法

    python tools\cdp_verify_global_qa.py

默认打 `http://127.0.0.1:8000`（跑新代码的后端）。若 8000 被别的进程占着
（实测遇到过：另一个 agent 的后端先起了），用环境变量换端口：

    $env:KG_QA_BASE='http://127.0.0.1:8010'; python tools\cdp_verify_global_qa.py

⚠️ 它会**真调本地 Ollama**（支持/反对/综合三步），两轮问答实测约 1~3 分钟。
"""
import os
import sys

# ⚠️ 默认 8000；被占用时用 `KG_QA_BASE` 换（**不硬编码端口**，
#    否则"后端在别的端口"就会让这一项看起来是红的）
BASE = os.environ.get('KG_QA_BASE', 'http://127.0.0.1:8000').rstrip('/')
ASK = BASE + '/ask.html'
# 单篇那一组要一张**真实存在**的图（磁盘快照），否则会走 404 分支
GRAPH_ID = os.environ.get('KG_QA_GID', 'ad690dfb')
GLOBAL_Q = os.environ.get('KG_QA_GLOBAL_Q', '这批文献整体在研究什么？')
PAPER_Q = os.environ.get('KG_QA_PAPER_Q', 'What method does this paper propose?')
TIMEOUT_MS = int(os.environ.get('KG_QA_TIMEOUT_MS', '240000'))


def main():
    from playwright.sync_api import sync_playwright

    print('=' * 88)
    print('全库问答（GraphRAG Global Search）的浏览器实测')
    print('=' * 88)
    print(f'  后端 {BASE} · 单篇图 {GRAPH_ID}')
    print(f'  全局问题：{GLOBAL_Q}')
    print()

    fails = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        errs = []
        page.on('pageerror', lambda e: errs.append(str(e)))

        # ── ⓪ 打开页面 ────────────────────────────────────────────────
        page.goto(ASK, wait_until='networkidle', timeout=60000)
        page.wait_for_function('() => window.__ready === true', timeout=30000)

        # ── ① 模式控件存在，默认"单篇图" ───────────────────────────────
        st = page.evaluate("""() => {
          const g = document.getElementById('modeGraph');
          const l = document.getElementById('modeGlobal');
          const cat = (typeof GLOBAL_CAT !== 'undefined') ? GLOBAL_CAT : null;
          return {
            has: !!g && !!l,
            g: g ? g.getAttribute('aria-checked') : null,
            l: l ? l.getAttribute('aria-checked') : null,
            ctx: (document.getElementById('ctx') || {}).textContent || '',
            n_comm: cat ? cat.n : null,
          };
        }""")
        print(f'  ① 模式控件：存在={st["has"]} · 单篇={st["g"]} · 全库={st["l"]}'
              f' · 后端报告社区摘要 {st["n_comm"]} 条')
        print(f'     初始语境：{st["ctx"][:88]!r}')
        if not st['has']:
            fails.append('页面上没有模式控件（#modeGraph / #modeGlobal）')
        elif st['g'] != 'true' or st['l'] != 'false':
            fails.append(f'默认模式不是「单篇图」（单篇={st["g"]} 全库={st["l"]}）')
        if st['n_comm'] != 18:
            fails.append(f'前端没拿到社区摘要清单（GLOBAL_CAT.n={st["n_comm"]}，应 18）')
        print()

        # ── ② 切到全库：按钮态 / 示例问题 / 语境提示 ────────────────────
        page.click('#modeGlobal')
        page.wait_for_timeout(200)
        sw = page.evaluate("""() => {
          const vis = sel => {
            const el = document.querySelector(sel);
            if (!el) return null;
            return !el.hidden && el.offsetParent !== null;
          };
          return {
            g: document.getElementById('modeGraph').getAttribute('aria-checked'),
            l: document.getElementById('modeGlobal').getAttribute('aria-checked'),
            gchip: vis('#chips .chip[data-mode="graph"]'),
            lchip: vis('#chips .chip[data-mode="global"]'),
            ctx: (document.getElementById('ctx') || {}).textContent || '',
            how: !document.getElementById('howGlobal').hidden,
            howOld: !document.getElementById('how').hidden,
          };
        }""")
        print(f'  ② 切换后：单篇={sw["g"]} · 全库={sw["l"]} · 全库示例问题可见={sw["lchip"]}'
              f' · 单篇示例问题可见={sw["gchip"]}')
        print(f'     语境：{sw["ctx"][:88]!r}')
        if sw['g'] != 'false' or sw['l'] != 'true':
            fails.append('点了「全库」按钮但 aria-checked 没翻转')
        if not sw['lchip'] or sw['gchip']:
            fails.append(f'示例问题没跟着模式换（全库={sw["lchip"]} 单篇={sw["gchip"]}）')
        if '全库' not in (sw['ctx'] or ''):
            fails.append(f'语境提示没说清在问全库：{sw["ctx"][:60]!r}')
        if not sw['how'] or sw['howOld']:
            fails.append('全库模式的说明面板没切换')
        print()

        # ── ③④ 提一个全局问题，等四步跑完 ──────────────────────────────
        #
        # ⚠️ **等"跑完"不能等 `#trace` 里出现社区字样** —— 那是 **Agent 工作台
        #    的中间文案**（"② 支持 Agent 正在读社区级摘要…"），第一步就会命中，
        #    于是断言在只有 1/4 步的时候就开始跑了（第一版正是这么写的）。
        #    完成的正判据是 `CUR.global === true` —— 它是 `play()`（四步之后）
        #    设的，`paint()`/渲染也都靠它。
        print(f'  ③ 提问：{GLOBAL_Q}（会真调本地模型，请稍等…）')
        page.fill('#q', GLOBAL_Q)
        page.press('#q', 'Enter')
        try:
            page.wait_for_function(
                """() => { try { return !!(CUR && CUR.global); }
                           catch (e) { return false; } }""",
                timeout=TIMEOUT_MS)
        except Exception as e:
            fails.append(f'全库问答没跑完（等 CUR.global 超时）：{e}')
        res = page.evaluate("""() => {
          const txt = id => ((document.getElementById(id) || {}).textContent || '');
          const cards = [].map.call(document.querySelectorAll('#gpanel .ccard'),
            c => ({
              tag: (c.querySelector('.ctag') || {}).textContent || '',
              name: (c.querySelector('.cname') || {}).textContent || '',
              summary: ((c.querySelector('.csum') || {}).textContent || '').slice(0, 60),
              note: (c.querySelector('.cnote') || {}).textContent || '',
            }));
          const tags = [].map.call(document.querySelectorAll('#camps .tag'),
            t => t.textContent || '');
          const offs = [].map.call(document.querySelectorAll('#camps .lnk.off'),
            t => t.textContent || '');
          const metas = [].map.call(document.querySelectorAll('#camps .meta'),
            t => t.textContent || '');
          const stakes = [].map.call(document.querySelectorAll('#camps .stake'),
            t => t.textContent || '');
          return {
            trace: txt('trace'), caveat: txt('caveat'), verdict: txt('verdict'),
            stats: txt('gstats'), campsHtml: document.getElementById('camps').innerHTML,
            cards: cards, tags: tags, offs: offs, metas: metas, stakes: stakes,
            to3d: (document.getElementById('to3d') || {}).textContent || '',
            to3dHidden: !!(document.getElementById('to3d') || {}).hidden,
            nSteps: [].filter.call(document.querySelectorAll('#alog li'),
              li => li.className === 'done').length,
          };
        }""")
        print(f'     社区卡片 {len(res["cards"])} 张 · Agent 完成 {res["nSteps"]}/4 步')
        print(f'     trace: {res["trace"][:110]}')
        print(f'     统计: {res["stats"][:120]}')
        if res['nSteps'] < 4:
            fails.append(f'四步没走完（完成 {res["nSteps"]}/4）')
        if not res['cards']:
            fails.append('社区卡片一张都没有（#gpanel .ccard）')
        if '社区级摘要' not in res['trace']:
            fails.append(f'trace 里看不出用的是社区摘要：{res["trace"][:80]!r}')
        print()

        # ── ④ 页面上必须能看出"这是社区级证据" ─────────────────────────
        checks = [
            ('社区卡片带「社区级摘要」标签',
             bool(res['cards']) and all('社区级摘要' in c['tag'] for c in res['cards'])),
            ('社区卡片写了"不是论文原文"',
             bool(res['cards']) and any('不是论文原文' in c['note'] for c in res['cards'])),
            ('证据来源写的是"社区「…」+ 生成模型"',
             any('社区「' in m and '生成' in m for m in res['metas'])),
            ('没有原文可点的那句写的是"社区级摘要 · 不是论文原文"',
             any('社区级摘要' in o for o in res['offs'])),
            ('证据类型标签是"社区摘要"',
             any('社区摘要' in t for t in res['tags'])),
            ('两方的立场写的是"社区级摘要"',
             any('社区级摘要' in s for s in res['stakes'])),
            ('统计里"带原文引文"是 0（社区级）', '0（社区级）' in res['stats']),
            ('统计里写着检索范围是"全库 · 社区摘要"', '全库 · 社区摘要' in res['stats']),
            ('结论区的说明写着"不是论文原文"', '不是论文原文' in res['caveat']),
        ]
        print('  ④ 界面上的"社区级"标识：')
        for label, ok in checks:
            print(f'      {"OK  " if ok else "FAIL"} {label}')
            if not ok:
                fails.append(f'界面看不出「{label}」')
        # 全库模式不许说成原文引文
        if '全部可回溯到原文' in page.evaluate(
                "() => document.getElementById('camps').innerHTML"):
            fails.append('全库模式仍写着"全部可回溯到原文" —— 那是单篇模式的口径')
        print(f'     #to3d：{res["to3d"][:60]!r}（hidden={res["to3dHidden"]}）')
        if res['to3d'] and '成员实体' not in res['to3d']:
            fails.append(f'全库模式的 3D 按钮没改口径：{res["to3d"]!r}')
        print()

        # ── ⑤ 零 JS 报错 ──────────────────────────────────────────────
        if errs:
            print(f'  ⚠️ 页面 JS 报错 {len(errs)} 条：')
            for e in errs[:5]:
                print(f'     {e[:140]}')
            fails.append(f'页面有 JS 报错：{errs[0][:120]}')
        else:
            print('  ⑤ 零 JS 报错 ✅')
        print()

        # ── ⑥⑦ 切回单篇：旧行为仍然正常 ────────────────────────────────
        errs.clear()
        page.goto(ASK + '?src=' + GRAPH_ID, wait_until='networkidle', timeout=60000)
        page.wait_for_function('() => window.__ready === true', timeout=30000)
        st2 = page.evaluate("""() => ({
          g: document.getElementById('modeGraph').getAttribute('aria-checked'),
          l: document.getElementById('modeGlobal').getAttribute('aria-checked'),
          ctx: (document.getElementById('ctx') || {}).textContent || '',
          lchip: (function(){ const el = document.querySelector('#chips .chip[data-mode=\\"global\\"]');
                             return el ? !el.hidden : null; })(),
        })""")
        print(f'  ⑥ 单篇页：单篇={st2["g"]} · 全库={st2["l"]} · 语境 {st2["ctx"][:64]!r}')
        if st2['g'] != 'true' or st2['l'] != 'false':
            fails.append('新开单篇页面时默认模式不对')
        if st2['lchip']:
            fails.append('单篇模式下全局示例问题不该显示')
        print(f'     提问：{PAPER_Q}')
        page.fill('#q', PAPER_Q)
        page.press('#q', 'Enter')
        try:
            # 单篇的完成判据同样是 `CUR`（`play()` 四步之后才设），
            # 而且这时 `#gpanel` 里应当画出子图（svg）
            page.wait_for_function(
                """() => { try {
                     return !!(CUR && !CUR.global
                               && document.querySelectorAll('#gpanel svg').length);
                   } catch (e) { return false; } }""",
                timeout=TIMEOUT_MS)
        except Exception as e:
            fails.append(f'单篇问答没跑完（等 CUR + 子图 svg 超时）：{e}')
        old = page.evaluate("""() => {
          const txt = id => ((document.getElementById(id) || {}).textContent || '');
          return {
            trace: txt('trace'), caveat: txt('caveat'), stats: txt('gstats'),
            cards: document.querySelectorAll('#gpanel .ccard').length,
            svg: document.querySelectorAll('#gpanel svg').length,
            tags: [].map.call(document.querySelectorAll('#camps .tag'),
                             t => t.textContent || ''),
            nQuoteTier: document.querySelectorAll('#camps .tag.s').length,
            to3d: (document.getElementById('to3d') || {}).textContent || '',
          };
        }""")
        print(f'     trace: {old["trace"][:110]}')
        print(f'     子图 svg={old["svg"]} · 社区卡片={old["cards"]}'
              f' · 带原文引文的证据标签={old["nQuoteTier"]} · 标签={old["tags"][:6]}')
        if '社区级摘要' in old['trace'] or '社区摘要' in old['trace']:
            fails.append(f'单篇模式的 trace 混进了社区口径：{old["trace"][:80]!r}')
        if '社区级摘要' in old['caveat']:
            fails.append('单篇模式的结论说明混进了社区口径')
        if '种子实体' not in old['stats']:
            fails.append(f'单篇模式的统计不对（应有"种子实体"）：{old["stats"][:80]!r}')
        if old['cards']:
            fails.append('单篇模式画出了社区卡片 —— 两个模式的渲染串了')
        if not old['svg']:
            fails.append('单篇模式没有画出子图（#gpanel svg 为 0）')
        if not old['nQuoteTier']:
            fails.append('单篇模式的证据里没有一条"带原文引文"（.tag.s 为 0）'
                         ' —— 那就不是原来的单篇问答了')
        if old['to3d'] and '这片子图' not in old['to3d']:
            fails.append(f'单篇模式的 3D 按钮口径变了：{old["to3d"]!r}')
        if errs:
            fails.append(f'单篇模式有 JS 报错：{errs[0][:120]}')
        else:
            print('  ⑦ 单篇模式零 JS 报错 ✅')
        print()

        browser.close()

    print('=' * 88)
    if fails:
        print(f'❌ {len(fails)} 项未通过：')
        for f in fails:
            print(f'   · {f}')
        return 1
    print('✅ 七组断言全部通过：全库模式走社区摘要且处处标出"社区级"；单篇模式未变')
    return 0


if __name__ == '__main__':
    sys.exit(main())
