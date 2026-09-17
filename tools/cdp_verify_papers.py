# -*- coding: utf-8 -*-
r"""K2 的浏览器实测：论文管理页。

## 断言六组

1. **载入**且**三个区块都渲染**（我传过哪些 / 传过但没成的 / 已入库的）
   —— 少任何一块都等于把 N7 定的"四层身份"又压回了一层；
2. **数字与注册表逐字相等**（上传图 3 · 传过没成 6 · 已入库 17）；
3. **★ 静态站下按钮禁用 + 说清为什么**
   —— 这一条针对的失败模式是"点了报错"。静态站没有后端，
   **"不能删"必须在点之前就看得见**（这与 K12 的"没跑≠全过了"是同一条纪律）；
4. **★ 删除对话框：不填原因点确认 ⇒ 不提交**（**原因必填**是后端的硬要求，
   前端也要拦一道，否则用户填了半天才被 400 打回）；
5. **★ 二次确认：把 `needconfirm` 的按钮点开 ⇒ 出现"输入 id 确认"的输入框**
   （删除语料图会让那篇从图谱里消失，必须多一道）；
6. **页面自己说清"移入回收站不是删除"**（否则用户不敢点、或以为真删了）。

用法：
    python tools/cdp_verify_papers.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get('KG_STATIC', 'http://127.0.0.1:8000')
# 静态站（无后端）—— 用来验第 3 组。8098 服务 web/
STATIC = os.environ.get('KG_STATIC_PLAIN', 'http://127.0.0.1:8098/web')
REG = os.path.join(ROOT, 'web', 'kg-data', 'paper-registry.json')


def main():
    from playwright.sync_api import sync_playwright

    if not os.path.exists(REG):
        print(f'  ❌ 缺 {REG}（先跑 tools/build_paper_registry.py）')
        return 1
    with open(REG, encoding='utf-8') as f:
        d = json.load(f)
    c = d.get('counts') or {}

    print('=' * 88)
    print('K2 · 论文管理页的浏览器实测')
    print('=' * 88)
    print(f'  注册表：上传图 {c.get("uploaded_graphs")} · '
          f'传过没成 {c.get("upload_failed_no_graph")} · 已入库 {c.get("in_graph")}')
    print()

    fails = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1500, 'height': 1000})
        errs = []
        page.on('pageerror', lambda e: errs.append(str(e)))

        page.goto(BASE + '/papers.html', wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(1800)

        # ── ① 载入 + 三个区块都渲染 ──────────────────────────────────
        st = page.evaluate("""() => ({
          ready: !!window.__papersReady, st: window.__papersState || null,
          up: document.querySelectorAll('#uploads tbody tr').length,
          fl: document.querySelectorAll('#failed tbody tr').length,
          co: document.querySelectorAll('#corpus tbody tr').length,
          kpis: document.querySelectorAll('#kpis .kpi').length,
          sub: (document.getElementById('sub') || {}).textContent || '',
        })""")
        print(f'  ① ready={st["ready"]} · KPI {st["kpis"]} 个')
        print(f'     上传 {st["up"]} 行 · 失败 {st["fl"]} 行 · 已入库 {st["co"]} 行')
        print(f'     副标题：{st["sub"][:110]}')
        ok1 = (st['ready'] and st['up'] == c.get('uploaded_graphs')
               and st['fl'] == c.get('upload_failed_no_graph')
               and st['co'] == c.get('in_graph') and st['kpis'] == 6)
        print(f'     三个区块都渲染且行数与注册表相等：{"✅" if ok1 else "❌"}')
        if not ok1:
            fails.append(f'区块不对：上{st["up"]}/失{st["fl"]}/库{st["co"]} '
                         f'vs {c.get("uploaded_graphs")}/'
                         f'{c.get("upload_failed_no_graph")}/{c.get("in_graph")}')
        print()

        # ── ② 数字与注册表一致 ──────────────────────────────────────
        kpi = page.evaluate("""() => [...document.querySelectorAll('#kpis .kpi')]
          .map(e => [e.querySelector('.k').textContent.trim(),
                     e.querySelector('.v').textContent.trim()])""")
        print(f'  ② KPI：{kpi}')
        want = {'清单条目': c.get('manifest_total'), '已下载': c.get('downloaded_pdf'),
                '已入库': c.get('in_graph'), '上传图': c.get('uploaded_graphs'),
                '上传任务': c.get('upload_tasks'),
                '传过但没成': c.get('upload_failed_no_graph')}
        bad2 = [(k, v) for k, v in kpi if str(want.get(k)) != str(v)]
        print(f'     与注册表不符的：{len(bad2)} {bad2[:3]}')
        ok2 = not bad2
        print(f'     {"✅" if ok2 else "❌"}')
        if not ok2:
            fails.append(f'KPI 与注册表不符：{bad2}')
        print()

        # ── ③ 页面说清"移入回收站不是删除" ───────────────────────────
        txt = page.evaluate("() => document.body.innerText || ''")
        said = {
            'not_delete': '不是删除' in txt or '没有真删' in txt or '不会真删' in txt,
            'why_required': '必填' in txt,
            'trash_path': '_trash' in txt,
        }
        print(f'  ③ 页面说明：{said}')
        ok3 = said['not_delete'] and said['trash_path']
        print(f'     **说清"不是删除"**：{"✅" if ok3 else "❌"}')
        if not ok3:
            fails.append(f'页面没说清"不是删除"：{said}')
        print()

        # ── ④ ★ 删除对话框：不填原因不让提交 ─────────────────────────
        dlg = page.evaluate("""() => {
          const b = document.querySelector('button[data-kind="upload"]');
          if (!b) return null;
          b.click();
          const d = document.getElementById('dlg');
          return {open: d.open, why: !!document.getElementById('dlgWhy'),
                  confirm_hidden: document.getElementById('dlgConfirmWrap').hidden};
        }""")
        page.wait_for_timeout(300)
        print(f'  ④ 点"移入回收站"⇒ 对话框 open={dlg and dlg["open"]} · '
              f'原因框在={dlg and dlg["why"]}')
        # 不填原因就点确认
        blocked = page.evaluate("""() => {
          let alerted = false;
          window.alert = () => { alerted = true; };
          const before = document.getElementById('dlgOut').hidden;
          document.getElementById('dlgOk').click();
          const after = document.getElementById('dlgOut').hidden;
          return {alerted: alerted, before: before, after: after,
                  still_open: document.getElementById('dlg').open};
        }""")
        page.wait_for_timeout(400)
        print(f'     不填原因点确认 ⇒ alert={blocked["alerted"]} · '
              f'对话框仍开着={blocked["still_open"]}')
        ok4 = bool(dlg and dlg['open'] and blocked['alerted'] and blocked['still_open'])
        print(f'     **空原因被拦住**：{"✅" if ok4 else "❌"}')
        if not ok4:
            fails.append(f'空原因没被拦：{blocked}')
        page.evaluate("() => { document.getElementById('dlg').close(); }")
        page.wait_for_timeout(200)
        print()

        # ── ⑤ ★ 二次确认：语料图的按钮要求输入 id ─────────────────────
        cf = page.evaluate("""() => {
          const b = document.querySelector('button[data-kind="corpus_graph"]');
          if (!b) return null;
          b.click();
          const d = document.getElementById('dlg');
          const wrap = document.getElementById('dlgConfirmWrap');
          const code = document.getElementById('dlgCode').textContent;
          return {open: d.open, confirm_shown: !wrap.hidden, code: code,
                  id: b.dataset.id};
        }""")
        page.wait_for_timeout(300)
        print(f'  ⑤ 语料图按钮 ⇒ 确认框显示={cf and cf["confirm_shown"]} · '
              f'要求输入 {cf and cf["code"]!r}')
        # 填错的 id 也不该提交
        wrong = page.evaluate("""() => {
          let alerted = false;
          window.alert = () => { alerted = true; };
          document.getElementById('dlgWhy').value = '测试原因';
          document.getElementById('dlgConfirm').value = 'WRONG-ID';
          document.getElementById('dlgOk').click();
          return {alerted: alerted, still_open: document.getElementById('dlg').open};
        }""")
        page.wait_for_timeout(400)
        print(f'     填错 id 点确认 ⇒ alert={wrong["alerted"]} · '
              f'仍开着={wrong["still_open"]}')
        ok5 = bool(cf and cf['confirm_shown'] and cf['code'] == cf['id']
                   and wrong['alerted'] and wrong['still_open'])
        print(f'     **二次确认真的在拦**：{"✅" if ok5 else "❌"}')
        if not ok5:
            fails.append(f'二次确认没拦住：{cf} {wrong}')
        page.evaluate("() => { document.getElementById('dlg').close(); }")
        print()

        # ── ⑥ ★ 静态站：按钮禁用 + 说清为什么 ────────────────────────
        page.goto(STATIC + '/papers.html', wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(2000)
        s6 = page.evaluate("""() => {
          const bs = [...document.querySelectorAll('button[data-kind]')];
          const t = document.body.innerText || '';
          return {can_write: (window.__papersState || {}).can_write,
                  n_btn: bs.length,
                  n_disabled: bs.filter(b => b.disabled).length,
                  said_static: t.indexOf('静态站') >= 0,
                  said_cannot_del: t.indexOf('不能删') >= 0,
                  sub: (document.getElementById('sub') || {}).textContent || ''};
        }""")
        print(f'  ⑥ 静态站：can_write={s6["can_write"]} · 按钮 {s6["n_btn"]} 个 · '
              f'禁用的 {s6["n_disabled"]} 个')
        print(f'     副标题：{s6["sub"][:96]}')
        print(f'     页面说了"静态站"={s6["said_static"]} · '
              f'"不能删"={s6["said_cannot_del"]}')
        ok6 = (s6['can_write'] is False and s6['n_btn'] > 0
               and s6['n_disabled'] == s6['n_btn'] and s6['said_static'])
        print(f'     **不能删在点之前就看得见**：{"✅" if ok6 else "❌"}')
        if not ok6:
            fails.append(f'静态站没禁用按钮或没说清：{s6}')
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
