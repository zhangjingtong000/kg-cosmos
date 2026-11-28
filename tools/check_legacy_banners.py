# -*- coding: utf-8 -*-
r"""A7 的守门：**历史原型页必须都有那条横幅**。

## 为什么需要一个单独的守门

`tools/add_legacy_banners.py` 是**一次性**的（加完就不再需要跑）。
而**这条纪律要长期成立**：
> **新增一个原型页时，它必须带上"这是历史版本"的横幅。**

没有守门的话，下一个加到 `web/` 下的实验页**默认不会有横幅** ——
而它的全部危险恰恰在于"**看起来像正式页**"
（TODO 里点名的例子是 `index-classic.html`：**20 个出链，看起来比导航页还全**）。

## 判据

与 `add_legacy_banners.py` **共用同一份白名单与同一套判定**（直接 import），
**不复制一份** —— 两份名单一定会漂，而漂的那一份就是漏洞。

用法：
    python tools/check_legacy_banners.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from add_legacy_banners import MARK, WEB, is_legacy  # noqa: E402


def main():
    print('=' * 92)
    print('A7 · 历史原型页的横幅守门')
    print('=' * 92)
    if not WEB.exists():
        print(f'  ⚠️ {WEB} 不存在，跳过')
        return 0

    # 2026-11-28：并行版本统一移进 `web/legacy/`，横幅纪律跟着扩到那个目录 ——
    # 否则"搬走"就等于"免检"，而搬走恰恰是**历史页最集中的地方**。
    pages = sorted(list(WEB.glob('*.html')) + list((WEB / 'legacy').glob('*.html')))
    legacy = [p for p in pages if is_legacy(p)]
    official = [p for p in pages if not is_legacy(p)]
    print(f'  web/ 与 web/legacy/ 下 {len(pages)} 个页面 · 正式页 {len(official)} · '
          f'历史页 {len(legacy)}')

    missing = []
    for p in legacy:
        try:
            if MARK not in p.read_text(encoding='utf-8', errors='replace'):
                missing.append(p.name)
        except Exception as e:
            missing.append(f'{p.name}（读不了：{type(e).__name__}）')

    if missing:
        print()
        print(f'  ❌ **{len(missing)} 个历史页没有横幅**：')
        for n in missing[:20]:
            print(f'      · {n}')
        if len(missing) > 20:
            print(f'      …另有 {len(missing) - 20} 个')
        print()
        print('  ⇒ 跑 `python tools/add_legacy_banners.py --apply` 补上。')
        print('  ⇒ 或者：如果你**确实想让它成为正式页**，')
        print('     就把文件名加进 `add_legacy_banners.py` 的 `OFFICIAL` 白名单')
        print('     —— **那是一个有意的决定，应该留下痕迹**。')
        return 1

    print()
    print(f'  ✅ 所有 {len(legacy)} 个历史页都有"这是历史版本"的横幅')
    print('     （正式页不受影响 —— 白名单见 `add_legacy_banners.py`）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
