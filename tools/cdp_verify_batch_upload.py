# -*- coding: utf-8 -*-
r"""K6 的浏览器实测：批量上传。

## 为什么必须用浏览器测

批量逻辑全在前端：收多文件 → **串行**排队 → 逐篇写结果 → 全批跑完才给汇总。
**在 Python 里读源码证明不了它能跑** —— 本项目栽过：
`cdp_verify_qa_history.py` 第一版用 `form.submit()` 触发，而页面绑的是 `#go` 的 click。

## 怎么测才不花几十分钟

真抽一篇 PDF 要**几十分钟**（本地模型逐块跑）⇒ 不能拿真论文测。
**改用空 PDF**：扩展名合法 ⇒ 前端收下、后端接受、**后台任务失败** ⇒
几秒内就能拿到 `{ok:false}`。

**这样恰好能一次测到三件事**：
1. **批量**：两个文件真的都被处理了（`__batchState.total == 2`）；
2. **★ 一篇失败不停整批**：两个都失败，但 `done == 2`（不是卡在第一个）；
3. **★ 完成时不自作主张跳转**：页面**仍在 upload.html**
   （单篇上传时 `poll` 会 `location.href`，批量时必须关掉）。

## 断言五组

1. `<input type="file">` 有 `multiple`；
2. 选两个文件后 `__batchState.total == 2`；
3. **两个都被处理**（`done == 2`）—— 失败也不停；
4. **两个都被记为失败**（空 PDF 必然失败，不是"静默成功"）；
5. **页面没有跳走**，且批量面板显示了汇总。

用法：
    python tools/cdp_verify_batch_upload.py
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = os.environ.get('KG_STATIC', 'http://127.0.0.1:8098/web')
PAGE = BASE + '/upload.html'
OUT = ROOT / 'web' / '_batch_probe'


def main():
    from playwright.sync_api import sync_playwright

    # 造两个空 PDF（扩展名合法、内容为空 ⇒ 后台任务会失败，但**前端流程走完整**）
    OUT.mkdir(parents=True, exist_ok=True)
    probes = []
    for i in (1, 2):
        p = OUT / f'__probe_{i}.pdf'
        p.write_bytes(b'')            # 空文件
        probes.append(str(p))

    print('=' * 88)
    print('K6 · 批量上传的浏览器实测')
    print('=' * 88)
    print(f'  用 {len(probes)} 个**空 PDF**（几秒内失败）—— 不拿真论文测（那要几十分钟）')
    print()

    fails = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1400, 'height': 900})
        errs = []
        page.on('pageerror', lambda e: errs.append(str(e)))

        page.goto(PAGE, wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(1200)

        # ── ① input 有 multiple ───────────────────────────────────────
        has_multi = page.evaluate(
            "() => { const f = document.getElementById('file'); return !!(f && f.multiple); }")
        print(f'  ① <input multiple>：{"✅" if has_multi else "❌"}')
        if not has_multi:
            fails.append('<input type="file"> 缺少 multiple 属性')
        print()

        # ── ②③④ 选两个文件 ⇒ 走批量 ──────────────────────────────────
        page.set_input_files('#file', probes)
        # 空 PDF 的失败可能几秒（后端起任务 → 报错）
        deadline = time.time() + 120
        st = None
        while time.time() < deadline:
            page.wait_for_timeout(1500)
            st = page.evaluate('window.__batchState || null')
            if st and st.get('done') == 2:
                break
        print(f'  ② __batchState: {st}')
        if not st:
            fails.append('点了上传但 __batchState 一直没出现 —— uploadMany 没跑')
        else:
            print(f'     total={st["total"]} done={st["done"]} '
                  f'ok={sum(1 for r in st["results"] if r.get("ok"))}')
            if st['total'] != 2:
                fails.append(f'只收了 {st["total"]} 个文件（应 2）—— 多选没生效')
            print()
            print('  ③ 两个都被处理（**一篇失败不停整批**）：'
                  f'{"✅" if st["done"] == 2 else "❌"}')
            if st['done'] != 2:
                fails.append(f'只处理了 {st["done"]}/2 —— 第一篇失败后整批停了')
            print()
            bad = [r for r in st['results'] if not r.get('ok')]
            print(f'  ④ 被记为失败的 {len(bad)} 条（空 PDF 必然失败，'
                  f'**不是静默成功**）：{"✅" if bad else "❌"}')
            for r in st['results']:
                print(f'       {r["name"]}: ok={r.get("ok")} err={str(r.get("error"))[:50]}')
            if not bad:
                fails.append('空 PDF 竟然被记为成功 —— 失败没有被如实报告')
        print()

        # ── ⑤ 没有跳走 + 汇总可见 ─────────────────────────────────────
        url = page.url
        batch_txt = page.evaluate(
            "() => { const b = document.getElementById('batch'); return b ? b.textContent : ''; }")
        print(f'  ⑤ 当前 URL: {url}')
        print(f'     批量面板文字: {batch_txt[:110]!r}')
        ok5 = url.endswith('upload.html') or 'upload.html' in url
        print(f'     没有跳转（仍在 upload.html）：{"✅" if ok5 else "❌"}')
        if not ok5:
            fails.append(f'批量上传把页面带走了（{url}）—— 应关掉 poll 的自动跳转')
        if '批量' not in batch_txt:
            fails.append(f'批量面板没有显示汇总：{batch_txt[:80]!r}')
        print()

        if errs:
            print(f'  ⚠️ 页面 JS 报错 {len(errs)} 条：')
            for e in errs[:5]:
                print(f'     {e[:140]}')
            fails.append(f'页面有 JS 报错：{errs[0][:120]}')

        browser.close()

    # 清理探针文件
    for p in probes:
        try:
            Path(p).unlink()
        except Exception:
            pass
    try:
        OUT.rmdir()
    except Exception:
        pass

    print('=' * 88)
    if fails:
        print(f'❌ {len(fails)} 项未通过：')
        for f in fails:
            print(f'   · {f}')
        return 1
    print('✅ 五组断言全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
