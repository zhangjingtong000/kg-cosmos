# -*- coding: utf-8 -*-
r"""N6 的守门：**前端不许运行时读 `data/`**（静态站取不到 ⇒ 面板会白）。

## TODO 的原话

> | **N6** | ⬜ **前端数据必须预生成到 `web/kg-data/`** |
> CDP 回归跑 **8098 静态站**，而 `kg-api.js:81-86` 在静态站**直接抛错**
> ⇒ K3 若运行时读 `data/quality/entity-alias-map.json`，**面板会白** |
> `kg-api.js:81-86`、先例 `evidence-N.json`

## 为什么这是个真问题（不是洁癖）

回归里有一大批检查跑的是 **8098 静态站**（只服务 `web/`，**不服务 `data/`**）。
⇒ 页面只要在运行时去取 `data/...`，**在静态站上必然拿不到**。
而 `kg-api.js` 的处理是**明确失败**（不静默返回空对象）——
那是对的，但**结果是页面白/面板空**。

**⇒ 所以纪律是**：**前端要用的数据，必须预生成到 `web/kg-data/`。**
先例：`evidence-N.json`（从 `data/quality/` 预生成的）。

## 判据（**只查真会发请求的地方**，不查文字）

第一版如果拿 `grep data/` 去扫，会扫出一堆**假阳性**：
```
analytics.html:647  出处 <code>data/quality/entity-alias-map.json</code>   ← 说明文字
ask.html:911        src:'data/quality/l5-six-layer.json'                  ← 数据字段（展示用）
```
**这些都不会发请求。** ⇒ 判据必须**看上下文**：

| 算不算 | 长什么样 |
|---|---|
| **算** | `fetch('...data/...')` · `getDocument('...data/...')` · `new URL('...data/...')` · `<script src="...data/...">` · `<img src="...data/...">` |
| **不算** | `<code>data/...</code>`（说明）· `src:'data/...'`（引号在外的 JS 对象字段） |

**⚠️ 有一个允许的例外**：**带回退链的**。
`l5-review.html` 写的是
```js
return [`./l5-data/${paper}.pdf`, `../data/corpus/${paper}.pdf`];
```
—— **先试静态站自带的那份，取不到再试 `data/`**。
那是**正确做法**（静态站能跑、后端在时也能跑）。
⇒ 所以判据是：**「指向 `data/` 且同一处没有 `web/` 内的备选」才算违规。**

用法：
    python tools/check_frontend_data.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'

# 会发请求的写法：取"引号紧跟在函数/属性后"的那些
PATTERNS = [
    # fetch('x') / fetch("x") / fetch(`x`)
    re.compile(r'''fetch\(\s*[`'"]([^`'"]+)[`'"]'''),
    # pdfjsLib.getDocument('x')
    re.compile(r'''getDocument\(\s*[`'"]([^`'"]+)[`'"]'''),
    # new URL(`x`, ...)
    re.compile(r'''new URL\(\s*[`'"]([^`'"]+)[`'"]'''),
    # <script src="x"> / <link href="x"> / <img src="x">
    re.compile(r'''<(?:script|link|img|iframe|source)[^>]*\b(?:src|href)=["']([^"']+)["']'''),
]

# 允许的例外：**同一段代码里有 web/ 内的备选**（回退链）。
FALLBACK_HINT = re.compile(r'''['"`]\.?/?[\w\-./]*l5-data/|['"`]\./[\w\-]+\.(?:json|pdf)''')

# ★ 判"指向仓库的 `data/`"：**必须是路径开头的那一段**。
#
# ⚠️ **不能写 `'data/' in url`** —— 那是**子串匹配**，
#    而 `./kg-data/nebula.json` 里**恰好含 `data/`**（`kg-data/` 的后半段）！
#    第一版就是这么写的，于是把**全部合法引用**都判成了违规
#    （`pathqa.html:950 ./kg-data/nebula.json` 这种）。
#    ⇒ 判据要锚在**路径边界**上：`data/` 前面必须是 `./`、`../`、`/` 或**行首**。
REPO_DATA = re.compile(r'(?:^|\.\./|\./|/)data/')


def points_at_repo_data(url):
    """这个引用是不是在取**仓库根下的 `data/`**（静态站没有）？"""
    if url.startswith('data/'):          # 行首就是 data/
        return True
    if '../data/' in url or './../data/' in url:
        return True
    if '/data/' in url and not url.startswith('./kg-data/'):
        # `/data/` 也是仓库根的（如 `http://host/data/...`）；
        # 但**排除** `./kg-data/`（那是 web/ 内的，静态站有）
        return True
    return False


def scan(path):
    """返回该文件里**真正指向 data/ 且没有备选**的引用。"""
    try:
        src = path.read_text(encoding='utf-8', errors='replace')
    except Exception:
        return []
    bad = []
    for pat in PATTERNS:
        for m in pat.finditer(src):
            url = m.group(1)
            if not points_at_repo_data(url):
                continue
            # 取这一行 + 上下两行，看有没有备选路径
            i = m.start()
            ctx = src[max(0, i - 400):i + 400]
            if FALLBACK_HINT.search(ctx):
                continue        # **有回退链 ⇒ 是正确做法**
            line = src[:i].count('\n') + 1
            bad.append((line, url))
    return bad


def main():
    print('=' * 92)
    print('N6 · 前端不许运行时读 `data/`（静态站取不到 ⇒ 面板会白）')
    print('=' * 92)
    if not WEB.exists():
        print(f'  ⚠️ {WEB} 不存在')
        return 0

    files = sorted(list(WEB.glob('*.html')) + list((WEB / 'lib').glob('*.js')))
    print(f'  扫 {len(files)} 个前端文件（{len(list(WEB.glob("*.html")))} 个页面 '
          f'+ {len(list((WEB / "lib").glob("*.js")))} 个 lib 脚本）')
    print()

    all_bad = []
    for f in files:
        bad = scan(f)
        if bad:
            all_bad.append((f, bad))

    if not all_bad:
        print('  ✅ 没有"运行时读 data/ 且没有回退"的引用')
        print('     （`l5-review.html` 的 `./l5-data/` → `../data/corpus/` 回退链'
              '是被允许的正确做法）')
        return 0

    print(f'  ❌ **{len(all_bad)} 个文件有违规引用**：')
    for f, bad in all_bad:
        print(f'      {f.relative_to(ROOT)}')
        for line, url in bad[:6]:
            print(f'          L{line}: {url}')
    print()
    print('  ⇒ 两条出路（**都不许"反正后端在时能用"**）：')
    print('     ① **预生成到 `web/kg-data/`**（先例：`evidence-N.json`）；')
    print('     ② 或写成**回退链**：先试 `./kg-data/…`（静态站有），')
    print('        取不到再试 `../data/…`（见 `l5-review.html` 的写法）。')
    return 1


if __name__ == '__main__':
    sys.exit(main())
