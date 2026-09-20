"""检查所有页面内联 JS 的语法（不需要浏览器）。

【为什么必须单独有这一项】
  2026-09-12 在 analytics.html 里加一段"四档位 + 产出构成"的渲染逻辑时，
  写成嵌套三元 + 模板字符串，**括号连改四版都没数对**：
  每次都是"页面全白"，而回归里的 CDP 检查只报"某个元素不可见"，
  看不出是 JS 语法错误 —— 直到手工把 `<script>` 抽出来跑 node --check 才定位。

  这类错误的表现是**整页白屏**（`boot()` 根本没执行），后果比一个数据错严重得多，
  而检测成本极低。所以补进回归：**每个页面的内联脚本都要过 node --check**。

  （这也是为什么那处代码最后改成了顺序拼接——嵌套三元 + 模板字符串
    的括号靠肉眼数不可靠，而顺序拼接一眼可验证。）

用法：
  python tools/check_js_syntax.py
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'

# 有些页面通过 <script src> 引外部文件，那些单独查
SKIP_DIRS = {'lib'}


def main():
    node = shutil.which('node')
    if not node:
        print('⚠️ 找不到 node，跳过 JS 语法检查（本项不判失败）。')
        print('   安装 Node.js 后本项才会真正生效。')
        return 0

    # ⚠️ **2026-09-20 修（守门假绿审计 §三·B3）**：
    #
    # ① 覆盖面：`web/benchmark/index.html` 是**内部基准工具页**，不在顶层，
    #    此前**从未被查过**（它有 1 段 `type="module"` 内联脚本）。现在纳入。
    #
    # ② 顺带修一个**潜在误报**（这条比 ① 更要紧）：原来不管什么 type，
    #    都写成 **`.js`** 再 `node --check`，而 `.js` 在 Node 里按 **CommonJS** 解析
    #    ⇒ 只要某个 `type="module"` 的脚本里出现 `import ... from`，就会被判成
    #    `SyntaxError: Cannot use import statement outside a module`。
    #    现有 42 段恰好都没有裸 `import`，所以这个坑一直没被踩到；
    #    而 `benchmark/index.html` 那段**正好有 import** ⇒ 若不先修后缀，
    #    一扩大范围就会**把对的判成错的**（本文件注释自己写着"误报比漏报更糟"）。
    #
    # `web/_archive/` 仍**故意排除**：那两页是 `ARCHIVE_POLICY` 的**方式 B**
    # （移入归档目录）留档 —— 归档目录本身就是标记，不再要求页面级横幅/检查。
    pages = sorted(list(WEB.glob('*.html')) + list((WEB / 'benchmark').glob('*.html')))
    bad = []
    checked = 0
    total_blocks = 0

    for p in pages:
        html = p.read_text(encoding='utf-8', errors='replace')
        # 只取**内联** script：没有 src 属性的 <script>。
        blocks = []
        for m in re.finditer(r'<script([^>]*)>(.*?)</script>', html, re.S | re.I):
            attrs, body = m.group(1), m.group(2)
            if re.search(r'\bsrc\s*=', attrs, re.I):
                continue                    # 外链脚本，另有处理
            # ⚠️ 必须跳过非 JS 的 type：
            #   <script type="importmap"> 里装的是 **JSON**（`{"imports": {...}}`），
            #   按 JS 解析会把 `{` 当块语句、在 `:` 上报语法错。
            #   初版没排除它，于是 9 个页面（含主视图 kg-nebula.html）全部误报——
            #   而那几页实测都能正常跑。**误报比漏报更糟**，会训练人忽略红灯。
            mt = re.search(r'\btype\s*=\s*["\']?([^"\'\s>]+)', attrs, re.I)
            is_module = False
            if mt:
                t = mt.group(1).lower()
                if t not in ('module', 'text/javascript', 'application/javascript'):
                    continue
                # `module` 与普通 script 的**解析模式不同**，下面按它选临时文件后缀。
                is_module = (t == 'module')
            if body.strip():
                blocks.append((body, is_module))
        total_blocks += len(blocks)
        for i, (body, is_module) in enumerate(blocks):
            checked += 1
            # ⚠️ **module 必须用 `.mjs`**（2026-09-20 修）：`node --check` 对 `.js`
            #    按 CommonJS 解析 ⇒ `import ... from` 会被误判成语法错。
            with tempfile.NamedTemporaryFile('w',
                                             suffix='.mjs' if is_module else '.js',
                                             delete=False, encoding='utf-8') as f:
                f.write(body)
                tmp = f.name
            r = subprocess.run([node, '--check', tmp],
                               capture_output=True, text=True)
            Path(tmp).unlink(missing_ok=True)
            if r.returncode != 0:
                first = [ln for ln in (r.stderr or '').splitlines() if ln.strip()]
                msg = next((ln for ln in first if 'SyntaxError' in ln), first[0] if first else '?')
                bad.append((p.name, i, msg.strip()[:110]))

    print(f'检查 {len(pages)} 个页面 / {checked} 段内联脚本'
          f'（共 {total_blocks} 段非空）')
    if bad:
        print()
        for name, i, msg in bad:
            print(f'  ❌ {name} 第 {i + 1} 段: {msg}')
        print(f'\n以上页面会有 JS 语法错误 ⇒ **整页白屏**（boot() 不会执行）。')
        return 1
    print('✅ 全部内联脚本语法正确')

    # 顺带查 lib/ 下的本地 JS（页面引用的）
    lib_bad = []
    for js in sorted((WEB / 'lib').glob('*.js')):
        r = subprocess.run([node, '--check', str(js)], capture_output=True, text=True)
        if r.returncode != 0:
            first = [ln for ln in (r.stderr or '').splitlines() if ln.strip()]
            lib_bad.append((js.name, (first[0] if first else '?')[:110]))
    if lib_bad:
        print()
        for name, msg in lib_bad:
            print(f'  ❌ lib/{name}: {msg}')
        return 1
    print(f'✅ lib/ 下 {len(list((WEB / "lib").glob("*.js")))} 个本地脚本语法正确')
    return 0


if __name__ == '__main__':
    sys.exit(main())
