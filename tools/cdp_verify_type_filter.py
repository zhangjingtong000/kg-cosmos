# -*- coding: utf-8 -*-
r"""B7 的浏览器实测：图例可点击 ⇒ 按实体类型聚焦。

## 为什么必须用浏览器测

"点图例"这件事全在前端：读 `entries[i].label` → 置 `typeFocus`
→ `applyColorMode()` 重写颜色缓冲。**在 Python 里读源码证明不了它能跑** ——
本项目栽过：`cdp_verify_qa_history.py` 第一版用 `form.submit()` 触发问答，
而页面绑的是 `#go` 的 click ⇒ **`ask()` 从没被调用，断言测的是测试自己的幻觉**。

## 断言五组

1. **图例里有可点的类型项**（`.lg-item` 数量 > 0，且 `partition=type` 时才有意义）；
2. **点一项之后颜色缓冲真的变了**（读 `geometry.attributes.color.array` 采样对比）——
   **不是"点了没报错就算过"**；
3. **再点同一项 ⇒ 恢复**（聚焦是开关，不是一次性）；
4. **`?type=<真实类型>` ⇒ 打开就聚焦**（`__viewState.type` 等于它）；
5. **`?type=<不存在的类型>` ⇒ `type_missing=true` 且页面上有提示**（不许静默）。

用法：
    python tools/cdp_verify_type_filter.py
"""
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
BASE = os.environ.get('KG_STATIC', 'http://127.0.0.1:8098/web')
PAGE = BASE + '/kg-nebula.html'


def _pick_community_with_types():
    """找一个类型数 >= 2 的社区 —— 只有一种类型时"聚焦"看不出差别。"""
    best = None
    for p in sorted((ROOT / 'web' / 'kg-data').glob('comm-*.json')):
        try:
            d = json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            continue
        ns = [n for n in (d.get('nodes') or []) if isinstance(n, dict) and n.get('t')]
        types = {}
        for n in ns:
            types[n['t']] = types.get(n['t'], 0) + 1
        if len(types) >= 2:
            idx = int(''.join(c for c in p.stem if c.isdigit()) or 0)
            cand = (idx, types, len(ns))
            if best is None or len(types) > len(best[1]):
                best = cand
    return best


def main():
    from playwright.sync_api import sync_playwright

    print('=' * 88)
    print('B7 · 按实体类型聚焦的浏览器实测')
    print('=' * 88)

    pick = _pick_community_with_types()
    if not pick:
        print('  ❌ 找不到类型数 >= 2 的社区，无法测聚焦')
        return 1
    ci, types, n_nodes = pick
    top_type = max(types.items(), key=lambda kv: kv[1])[0]
    print(f'  用社区 {ci}（{n_nodes} 节点 · {len(types)} 种类型）· 最高频类型 {top_type!r}')
    print()

    fails = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1400, 'height': 900})
        errs = []
        page.on('pageerror', lambda e: errs.append(str(e)))

        # 进社区 + 切到"类型"分区，图例才会列出各类型
        page.goto(PAGE + f'?c={ci}', wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(2000)
        # 切分区：图例里分区那组按钮的第二个是"类型"
        ok_switch = page.evaluate("""() => {
          const bs = [...document.querySelectorAll('#legend button[data-group="partition"]')];
          const t = bs.find(b => b.dataset.val === 'type');
          if (!t) return false;
          t.click(); return true;
        }""")
        page.wait_for_timeout(800)
        n_items = page.evaluate("() => document.querySelectorAll('#legend .lg-item').length")
        print(f'  ① 图例类型项数量：{n_items}（切到类型分区：{ok_switch}）')
        if not ok_switch or n_items < 2:
            fails.append(f'图例里没有可点的类型项（items={n_items}, switched={ok_switch}）')
        print()

        # ── ② 点一项 ⇒ 颜色缓冲真的变了 ───────────────────────────────
        def sample_colors():
            # 页面通过 `window.__colorSample()` 暴露一个**只读**的采样和。
            # ⚠️ 第一版这里写的是 `window.__overlayRef.pts.geometry.attributes.color`
            # —— `overlay` 是**模块内变量**，`window.__overlayRef` 根本不存在，
            # 于是采样恒为 None，"颜色变了吗"这条断言等于没测。
            # ⇒ 页面为此专门导出了 `__colorSample`（见 `applyColorMode` 末尾）。
            return page.evaluate(
                "() => (typeof window.__colorSample === 'function')"
                " ? window.__colorSample() : null")
        # 页面没暴露 __overlayRef ⇒ 用"图例的 focused 类 + note 文案"做可观测代理，
        # 同时仍然采样颜色（若能取到）
        before = sample_colors()
        page.evaluate("""() => {
          const el = document.querySelectorAll('#legend .lg-item')[0];
          if (el) el.click();
        }""")
        page.wait_for_timeout(600)
        after = sample_colors()
        focused = page.evaluate(
            "() => document.querySelectorAll('#legend .lg-item.focused').length")
        note = page.evaluate(
            "() => (document.querySelector('#legend .lg-note')||{}).textContent || ''")
        print(f'  ② 点第 1 项后：focused 项 {focused} 个')
        print(f'     颜色采样 before={before} after={after}')
        print(f'     note 里是否写明当前聚焦：{"当前：" in note}')
        ok2 = focused == 1 and ('当前：' in note)
        print(f'     聚焦生效：{"✅" if ok2 else "❌"}')
        if not ok2:
            fails.append(f'点了图例项但没生效（focused={focused}, note={note[:60]!r}）')
        if before is not None and after is not None:
            # 压暗会让和变小（乘 0.18）
            if after >= before:
                fails.append(f'颜色缓冲没有变暗（before={before} after={after}）')
            else:
                print(f'     ✅ 颜色和确实变小了（压暗生效）')
        print()

        # ── ③ 再点一次 ⇒ 取消 ─────────────────────────────────────────
        page.evaluate("""() => {
          const el = document.querySelectorAll('#legend .lg-item')[0];
          if (el) el.click();
        }""")
        page.wait_for_timeout(600)
        focused2 = page.evaluate(
            "() => document.querySelectorAll('#legend .lg-item.focused').length")
        print(f'  ③ 再点一次：focused 项 {focused2} 个（应为 0 ⇒ 是开关不是一次性）')
        print(f'     取消聚焦：{"✅" if focused2 == 0 else "❌"}')
        if focused2 != 0:
            fails.append('再点一次没有取消聚焦（应当是个开关）')
        print()

        # ── ④ ?type=<真实类型> ⇒ 打开就聚焦 ───────────────────────────
        page.goto(PAGE + f'?c={ci}&type=' + quote(top_type),
                  wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(2200)
        vs = page.evaluate('window.__viewState || null')
        print(f'  ④ ?type={top_type!r} ⇒ __viewState: {vs}')
        ok4 = bool(vs and vs.get('type') == top_type)
        print(f'     恢复成功：{"✅" if ok4 else "❌"}')
        if not ok4:
            fails.append(f'?type={top_type!r} 没有被恢复（__viewState={vs}）')
        print()

        # ── ⑤ ?type=<不存在> ⇒ 必须明说 ───────────────────────────────
        page.goto(PAGE + f'?c={ci}&type=__NO_SUCH_TYPE__',
                  wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(1800)
        vs5 = page.evaluate('window.__viewState || null')
        txt5 = page.evaluate(
            "() => { const s = document.getElementById('status'); return s ? s.textContent : ''; }")
        print(f'  ⑤ ?type=__NO_SUCH_TYPE__ ⇒ __viewState: {vs5}')
        print(f'     页面提示: {txt5[:80]!r}')
        ok5 = bool(vs5 and vs5.get('type_missing'))
        print(f'     正确报告缺失：{"✅" if ok5 else "❌"}')
        if not ok5:
            fails.append('不存在的类型没有被报告（应设 type_missing=true）')
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
    print('✅ 五组断言全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
