# -*- coding: utf-8 -*-
r"""守门：Markdown 表格的**列数一致性**（防止插入更正时把行写坏）。

## 为什么需要它（2026-09-26 加）
上一轮往 GFM 表格的**行内**插入了大量 `⚠️ 更正：… <br>…`，子代理自己就踩过一次
"写在行尾 `|` **之后**会被解析器整段丢掉"的坑。当时我用一个一次性脚本粗查，
**判据写错了**（把"以 `|` 开头但没结尾"的行当成块结束，于是块被切断、行号错乱），
报出 6 处"不齐"，逐行核对后发现**多数是假警报**。

⇒ 这次换成**可靠判据**，并且当正式守门用：
  1. 表格行 = **同一行里含 ≥2 个 `|`**（GFM 允许行首行尾不带 `|`）；
  2. 以连续表格行组成一个块；块内**列数**（`|` 数）必须与**表头行**一致；
  3. 表头行 = 块的第 1 行；分隔行（`|---|`）允许 `:` 对齐标记，但列数必须相同。

**口径**：只报"与表头列数不同"的行；`|---|` 行缺列也算错。
用法：
    python tools/check_md_tables.py            # 全仓 docs + 根目录 md
    python tools/check_md_tables.py a.md b.md  # 只查指定文件
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 与项目其它守门一致：文档（含归档）都查，但**跳过第三方/生成物**
SKIP_DIRS = {'.git', 'node_modules', '_archive', 'output'}
SEP = re.compile(r'^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$')


def cells(line: str) -> int:
    """一行的**列数**：先掐掉首尾的 `|`，再数剩下的 `|` + 1。

    ⚠️ 两个坑（都踩过）：
      1. **不能直接数 `|` 的个数** —— 行首行尾的 `|` 在 GFM 里是**可选**的，
         把它们算进列数会让"带边框"和"不带边框"的表互相误报。
      2. **`\\|` 是转义，不是分隔符** —— 单元格里写数学记号
         （`|S|/|G|`）或 shell 管道（`<|>`）必须写成 `\\|`，
         那种行是**正确的**。第一版没排除转义，把
         `GOLD_ARBITRATION_RULES.md` 与 `H8_E2E_STACK.md` 里**已经写对**的
         `X \\| USES \\| Y` 报成了错 —— **判据误报比漏报更糟**（会训练人忽略报红）。
    """
    s = line.strip()
    if s.startswith('|'):
        s = s[1:]
    if s.endswith('|'):
        s = s[:-1]
    s = s.replace('\\|', '')      # 转义的竖线不算列分隔符
    return s.count('|') + 1


def check(path: Path):
    bad = []
    lines = path.read_text(encoding='utf-8', errors='replace').split('\n')
    i = 0
    while i < len(lines):
        ln = lines[i]
        # 表格块的第一行：含 ≥2 个 `|`，且下一行是分隔行（GFM 表头的判据）
        if ln.count('|') >= 2 and i + 1 < len(lines) and SEP.match(lines[i + 1]) \
                and '|' in lines[i + 1]:
            head_cols = cells(ln)
            j = i
            while j < len(lines) and lines[j].count('|') >= 2:
                c = cells(lines[j])
                if c != head_cols:
                    bad.append((j + 1, head_cols, c, lines[j][:120]))
                j += 1
            i = j
        else:
            i += 1
    return bad


def main() -> int:
    args = sys.argv[1:]
    if args:
        files = [Path(a) for a in args]
    else:
        files = sorted(p for p in list(ROOT.glob('*.md')) + list(ROOT.glob('docs/*.md'))
                       if not any(part in SKIP_DIRS for part in p.parts))
    total = 0
    for p in files:
        if not p.exists():
            continue
        bad = check(p)
        if bad:
            rel = p.relative_to(ROOT) if p.is_absolute() and ROOT in p.parents else p
            print(f'❌ {rel}')
            for ln_no, want, got, text in bad:
                print(f'    :{ln_no}  列数 {got}（表头 {want}）  {text}')
                total += 1
    print(f'\n{"❌" if total else "✅"} 表格列数：'
          f'{total} 处不一致' if total else
          f'✅ 表格列数一致（查了 {len(files)} 份 Markdown）')
    return 1 if total else 0


if __name__ == '__main__':
    sys.exit(main())
