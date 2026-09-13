"""把字体基座接到各页面，并立起标题字阶。

【为什么必须做这一步】
  tools/build_fonts.py 已经把字体下载到 web/lib/fonts/（含 fonts.css），
  但**没有任何页面引用它**——实测 5 个页面的 document.fonts 都是空数组。
  用户说"字体不好看"，第一因就是"根本没加载"，不是选错了字体。

【设计决策：艺术字体只用于标题】
  · 中文子集只含 256 个字符（标题用字），**不含正文用字**；
    正文若用它会逐字回退，同一行出现两种字形，比现在更难看。
  · 正文继续用系统无衬线栈（PingFang / 雅黑 / system-ui），清晰可读。
  · 标题用 Noto Serif SC（中文）+ Playfair Display（拉丁与数字）——
    衬线在大字号下自带学术感，不需要靠粗体撑层级。
    这一条也避开了"各字重共用同一份字形"的限制。
"""
import re
from pathlib import Path

WEB = Path(r'D:\kg-demo\web')

PAGES = ['index.html', 'analytics.html', 'method-limits.html',
         'pathqa.html', 'refs.html']

LINK = '<link rel="stylesheet" href="./lib/fonts/fonts.css">'

# 标题族：中文衬线在前，拉丁衬线兜数字与英文，最后回退到系统衬线
TITLE_FONT = ('"Noto Serif SC","Playfair Display",'
              'Georgia,"Songti SC","SimSun",serif')

STYLE = f'''
/* ── 标题字体（由 tools/apply_fonts.py 注入）────────────────────────────
   fonts.css 是 tools/build_fonts.py 生成的子集字体，全部本地、零外部请求。
   只给标题用：正文子集不含正文用字，换上去会逐字回退反而更难看。 */
h1, h2, h3,
.kick, .eyebrow, .note b, .fig .n, .kpi b, .card h2, .entry h3 {{
  font-family: {TITLE_FONT};
}}
/* 标题在衬线下不需要那么粗——衬线本身已经提供笔画对比 */
h1, h2 {{ font-weight: 700; letter-spacing: -.02em; }}
h3 {{ font-weight: 600; }}
/* 数字用 Playfair 的等高数字，避免跳动 */
.fig .n, .kpi b, [data-count] {{ font-variant-numeric: lining-nums tabular-nums; }}
'''

for name in PAGES:
    p = WEB / name
    if not p.exists():
        print(f'  · {name} 不存在，跳过')
        continue
    t = p.read_text(encoding='utf-8', errors='replace')

    if 'lib/fonts/fonts.css' in t:
        print(f'  · {name} 已接线，跳过')
        continue

    # ① 注入 <link>：放在 a11y.css 之后（若有），否则放在 </head> 前
    if '<link rel="stylesheet" href="./lib/a11y.css">' in t:
        t = t.replace('<link rel="stylesheet" href="./lib/a11y.css">',
                      '<link rel="stylesheet" href="./lib/a11y.css">\n' + LINK, 1)
    elif '<link rel="stylesheet" href="./lib/voyage.css">' in t:
        t = t.replace('<link rel="stylesheet" href="./lib/voyage.css">',
                      LINK + '\n<link rel="stylesheet" href="./lib/voyage.css">', 1)
    else:
        # 没有外部样式表：插在最后一个 </style> 之后
        i = t.rfind('</style>')
        if i != -1:
            t = t[:i + len('</style>')] + '\n' + LINK + t[i + len('</style>'):]
        else:
            t = t.replace('</head>', LINK + '\n</head>', 1)

    # ② 注入标题字体规则：放在最后一个 </style> 之前（这样能覆盖前面的规则）
    i = t.rfind('</style>')
    if i != -1:
        t = t[:i] + STYLE + t[i:]
    else:
        # 页面没有 <style>：新建一个
        t = t.replace('</head>', '<style>' + STYLE + '</style>\n</head>', 1)

    p.write_text(t, encoding='utf-8')
    print(f'  ✓ {name} 已接线（link + 标题字体规则）')
