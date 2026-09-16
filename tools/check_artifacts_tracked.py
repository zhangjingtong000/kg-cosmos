# -*- coding: utf-8 -*-
r"""检查「**文档里引用的产物**」是否真的在版本控制里（P13b）。

## 为什么需要它 —— 一次真实的数据丢失

跑 P3 第二篇时，**第一篇的产物被覆盖了**。我一度以为"它在 git 里"，
核实发现 `git show 32b93f0:data/quality/protocol-ladder.json` **返回 0 字节** ——
**它从未入库**。

**根因**：`data/` 被 `.gitignore` 排除，而我当时只 `git add -f` 了
**53 个"被文档引用"** 的产物 —— `protocol-ladder.json` **不在那 53 个里**
（因为它当时只在运行输出里，文档里还没写）。

**⇒ 教训：「覆盖写固定路径」+「产物在 gitignore 目录里」= 数据必然丢。**

## 判据：**文档引用 = 应当入库**

不搞白名单（"哪些该入库"是主观的），改用**可验证的判据**：

    凡是 `docs/*.md` 或 `README.md` 里**写出来的** `data/...` 路径，
    就是一个"我承诺过它存在"的产物 ⇒ 它必须
      ① 文件存在（否则文档在指一个不存在的东西）
      ② 被 git 跟踪（否则别人 clone 下来就看不到它）

两条都能机械检查。**只读**，不改任何东西。

用法：
    python tools/check_artifacts_tracked.py          # 检查
    python tools/check_artifacts_tracked.py --list   # 顺带列出已跟踪的产物数
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def parse_args():
    ap = argparse.ArgumentParser(description='文档引用的产物是否已入库')
    ap.add_argument('--list', action='store_true', help='顺带列出统计')
    ap.add_argument('--quiet', action='store_true')
    return ap.parse_args()


ARGS = parse_args()

# 只认"看起来像产物文件"的路径：data/ 下、以 .json/.jsonl/.md 结尾。
# **不认目录**（`data/corpus/graphs/` 这种是数据源，不是产物）。
#
# ⚠️ **扩展名要从长到短排**：写成 `(?:json|jsonl)` 时，
# `entities.jsonl` 会先被 `json` 匹配上、留下一个 `l` 被 `.rstrip()` 吃掉，
# 于是报出一个**根本不存在的 `entities.json`** —— 本检查器第一版就栽在这上面
# （三个 `.jsonl` 被误报成缺失的 `.json`）。
# ⚠️ **`data/` 前面必须不是路径分隔符或连字符** —— 否则
# `web/kg-data/atlas.json` 里的子串 `data/atlas.json` 会被当成一个独立路径，
# 于是报出五条"路径不精确"的假警告。**本检查器第三版才修对。**
#
# 这个正则前后改过三次，每次都是它自己错：
#   ① `(?:json|jsonl)` —— 短的在前，`entities.jsonl` 被截成 `entities.json`；
#   ② `(?<![\w/-])`   —— Python 的 `\w` 在 Unicode 下**包含中文**，负向断言失效；
#   ③ 缺了 `data/` 前的边界 —— `web/kg-data/x` 里的 `data/x` 被误认。
# ⇒ **教训：检查器报出的问题，要先验证检查器自己。**
PATH_RE = re.compile(r'(?<![A-Za-z0-9/_.-])data/[A-Za-z0-9_\-./]+\.(?:jsonl|json|csv|md)')

# 明确不算产物的（第三方数据、外部下载、被 .gitignore 有意排除的大文件）
EXCLUDE_PREFIX = (
    'data/external/',        # SciREX 等外部数据集（有下载说明）
    'data/gold/',            # 源文本
)
# 这些是"大文件、有意不入库"，`REPRODUCE.md` 里有重建说明
LARGE_KNOWN = (
    'data/quality/relation-verify-all',          # 三个 ~53 MB 的大文件
    'data/quality/subgraph-supported.json',      # 5.1 MB
    'data/corpus/graphs/2012.00456v1.json',      # 2.2 MB
    'data/3e38f803/graph.json',                  # 3.2 MB
)

# 文档里写"待产出 / 尚未生成 / 运行时生成"的引用不算错 —— 那是**如实标注**。
# 判据：同一行（或紧邻上下文）出现这些词。
PENDING_WORDS = ('待产出', '尚未生成', '运行时生成', '计划中', '未产出', 'TODO')


def _is_pending(fname, path):
    """这一处引用是否被标注为"还没产出"。只看路径所在的那一行。"""
    try:
        lines = (ROOT / 'docs' / fname).read_text(encoding='utf-8').splitlines()
    except Exception:
        try:
            lines = (ROOT / fname).read_text(encoding='utf-8').splitlines()
        except Exception:
            return False
    for ln in lines:
        if path in ln and any(w in ln for w in PENDING_WORDS):
            return True
    return False


def find_same_name(path):
    """原路径不存在时，按**同名文件**找一下（文档常省略 `web/kg-data/` 前缀）。

    返回相对路径或 None。**这是"路径不精确"而不是"没有"** ——
    所以调用方把它记成警告、不是失败。
    """
    name = Path(path).name
    for base in ('web/kg-data', 'web/l5-data', 'web', 'data'):
        for p in (ROOT / base).rglob(name):
            if p.is_file():
                return str(p.relative_to(ROOT)).replace('\\', '/')
    return None


def doc_files():
    out = [ROOT / 'README.md']
    out += sorted((ROOT / 'docs').glob('*.md'))
    return [p for p in out if p.exists()]


def referenced_paths():
    """从文档里收集被引用的 data/ 路径。返回 {相对路径: [引用它的文件]}。"""
    refs = {}
    for f in doc_files():
        try:
            txt = f.read_text(encoding='utf-8')
        except Exception:
            continue
        for m in PATH_RE.finditer(txt):
            p = m.group(0).rstrip('.,;:)`')
            if p.startswith(EXCLUDE_PREFIX) or p.startswith(LARGE_KNOWN):
                continue
            refs.setdefault(p, []).append(f.name)
    return refs


def tracked_set():
    """`git ls-files data/` 的结果（相对仓库根的 posix 路径）。"""
    r = subprocess.run(['git', 'ls-files', 'data/'], cwd=str(ROOT),
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return {line.strip().replace('\\', '/') for line in r.stdout.splitlines() if line.strip()}


def main():
    tracked = tracked_set()
    if tracked is None:
        print('  ⚠️ 不是 git 仓库（或 git 不可用）—— 跳过')
        return 0

    refs = referenced_paths()
    missing, untracked, imprecise, pending = [], [], [], []
    for p, who in sorted(refs.items()):
        if (ROOT / p).exists():
            if p not in tracked:
                untracked.append((p, who))
            continue
        # 原路径不在 —— 先看是不是"文档省了 web/kg-data/ 前缀"，再看有没有标"待产出"
        same = find_same_name(p)
        if same:
            imprecise.append((p, same, who))
        elif all(_is_pending(w, p) for w in who):
            pending.append((p, who))
        else:
            missing.append((p, who))

    if ARGS.list:
        print(f'  文档 {len(doc_files())} 份 · 引用产物 {len(refs)} 个 · '
              f'已跟踪 {len(tracked)} 个')
    print(f'  被文档引用的产物：{len(refs)} 个')

    fails = 0
    if missing:
        fails += len(missing)
        print()
        print(f'  ❌ **文档引用了 {len(missing)} 个不存在的产物**：')
        for p, who in missing:
            print(f'       {p}   ← 被 {", ".join(sorted(set(who))[:3])} 提到')
        print('     ⇒ 要么补出这个产物，要么把文档里的引用删掉／标成"待产出"。')
        print('       （文档指着一个不存在的东西，是最容易骗过自己的那类错误。）')

    if untracked:
        fails += len(untracked)
        print()
        print(f'  ❌ **文档引用了 {len(untracked)} 个未入库的产物**'
              f'（`data/` 被 gitignore，必须 `git add -f`）：')
        for p, who in untracked:
            print(f'       {p}   ← 被 {", ".join(sorted(set(who))[:3])} 提到')
        print()
        print('     修法：')
        for p, _w in untracked[:6]:
            print(f'       git add -f "{p}"')
        if len(untracked) > 6:
            print(f'       …还有 {len(untracked) - 6} 个')
        print('     ⇒ 不补的话：**别人 clone 下来看不到它，而文档说它在那儿**；')
        print('       更糟的是"以后想拿旧版本对比"时会发现 git 里没有（P13 就是这么丢的）。')

    if imprecise:
        print()
        print(f'  ⚠️ {len(imprecise)} 处**路径写得不精确**（同名文件在别处，不算失败）：')
        for p, same, who in imprecise:
            print(f'       {p}  →  实际在 {same}   ← {", ".join(sorted(set(who))[:2])}')
        print('     建议：文档里写全路径（前端产物在 `web/kg-data/`、盲态产物在 `web/l5-data/`），')
        print('     否则读者照着 `data/...` 去找会扑空。')

    if pending:
        print()
        print(f'  · {len(pending)} 处引用已标注"未产出"，跳过：'
              + ', '.join(p for p, _w in pending[:4])
              + ('…' if len(pending) > 4 else ''))

    print()
    if fails:
        print(f'❌ {fails} 项问题（缺文件 {len(missing)} · 未入库 {len(untracked)}）'
              f'　另有 {len(imprecise)} 处路径不精确')
        return 1
    print(f'✅ 文档引用的 {len(refs)} 个产物都存在、且都已入库'
          f'（{len(imprecise)} 处路径不精确 → 建议写全；{len(pending)} 处已标"未产出"）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
