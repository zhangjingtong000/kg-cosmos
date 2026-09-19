# -*- coding: utf-8 -*-
r"""清理**抽取失败后留下的空任务目录**。

## 为什么需要它（K6 让它从"小事"变成"会失控"）

`data/<gid>/` 是上传任务的落盘位置。抽取失败时**后端刻意不删目录** ——
那是为了保留现场（PDF、中间产物）供排查，是对的。

**但没有任何清理机制**，于是每次失败都留下一个目录。实测（2026-09-16）：

    没有 graph.json 的目录：8 个
      4e570fea · 565c2dc1 · 5a472e23 · 61186860
      6fffe00e · 7dc6079d · beb6e555 · c4266bac

**而 K6 的批量上传把这个放大了**：一次拖 34 篇，失败的就会留下 34 个目录。
（2026-09-19 更正：原写 28 —— 那是 `data/corpus/_manifest.json` 还只有 28 条
时的读数；现清单 **34 条**（28 篇英文 arXiv + 6 篇中文期刊），故同步改成 34。）
⇒ 批量上传配一个清理入口，是这一项的一部分（**不然"方便"会变成"堆积"**）。

## 判据：**只删"既没有 graph.json、又没有任何有用内容"的目录**

这道口径是刻意保守的 —— 删数据不可逆：

| 情况 | 处理 |
|---|---|
| 有 `graph.json` | **绝不删**（这是抽成功的图） |
| 只有 PDF（没有 graph.json） | **默认不删** —— 那可能是**正在跑的任务**，也可能是"抽了一半"的现场 |
| 只有 0 字节文件 / 没有 PDF | **可删**（抽取从来没可能成功过） |
| `--failed-only` | 连"有 PDF 但没 graph.json"也删（**明确要求才做**） |

⚠️ **`data/corpus` / `distill` / `external` / `gold` 这些是数据目录，不是任务目录**，
按白名单排除（它们的特征是没有 `<8位hex>` 这样的名字）。

用法：
    python tools/clean_failed_uploads.py                 # 只看会删什么（dry-run）
    python tools/clean_failed_uploads.py --apply          # 真删
    python tools/clean_failed_uploads.py --apply --failed-only   # 连"有 PDF 但没图"也删
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'

# **任务目录的特征**：8 位十六进制（`uuid.uuid4().hex[:8]`）。
# 凭这个把它们与 `corpus` / `distill` / `external` / `gold` 这些**数据目录**区分开 ——
# 而不是靠黑名单（黑名单会随新目录失效）。
TASK_RE = re.compile(r'^[0-9a-f]{8}$')


def parse_args():
    ap = argparse.ArgumentParser(description='清理抽取失败留下的空任务目录')
    ap.add_argument('--apply', action='store_true', help='真删（默认只报告）')
    ap.add_argument('--failed-only', action='store_true',
                    help='连"有 PDF 但没有 graph.json"的也删（默认保留，因为可能是正在跑的）')
    return ap.parse_args()


def classify(d):
    """返回 (类别, 说明)。类别 ∈ keep / empty / pdf_only。"""
    if (d / 'graph.json').exists():
        return 'keep', '有 graph.json'
    files = [p for p in d.rglob('*') if p.is_file()]
    if not files:
        return 'empty', '空目录'
    total = sum(p.stat().st_size for p in files)
    if total == 0:
        return 'empty', f'{len(files)} 个文件但都是 0 字节'
    has_pdf = any(p.suffix.lower() == '.pdf' for p in files)
    if has_pdf:
        return 'pdf_only', f'有 PDF（{len(files)} 个文件 · {total} 字节）但没有 graph.json'
    return 'empty', f'{len(files)} 个文件 · {total} 字节（无 PDF、无 graph.json）'


def main():
    a = parse_args()
    print('=' * 88)
    print('清理抽取失败留下的任务目录')
    print('=' * 88)
    if not DATA.exists():
        print(f'  ❌ {DATA} 不存在')
        return 1

    # **只扫"名字像任务目录"的那些** —— 别把 corpus/gold 之类误当任务
    dirs = [d for d in sorted(DATA.iterdir())
            if d.is_dir() and TASK_RE.match(d.name)]
    print(f'  名字像任务目录的 {len(dirs)} 个（8 位十六进制）')
    print()

    todo, keep = [], []
    for d in dirs:
        kind, why = classify(d)
        if kind == 'keep':
            keep.append((d, why))
        elif kind == 'pdf_only' and not a.failed_only:
            keep.append((d, why + ' ⇒ **默认保留**（可能是正在跑的）'))
        else:
            todo.append((d, kind, why))

    if keep:
        print(f'  保留 {len(keep)} 个：')
        for d, why in keep[:14]:
            print(f'      · {d.name}  {why}')
        if len(keep) > 14:
            print(f'      …（另有 {len(keep) - 14} 个）')
        print()

    if not todo:
        print('  ✅ 没有可清理的目录')
        return 0

    freed = 0
    for d, _kind, _why in todo:
        freed += sum(p.stat().st_size for p in d.rglob('*') if p.is_file())
    print(f'  ★ 可清理 {len(todo)} 个目录 · 合计 {freed} 字节：')
    for d, kind, why in todo:
        print(f'      · {d.name}  [{kind}] {why}')

    if not a.apply:
        print()
        print('  （这是 dry-run。真删请加 --apply）')
        print('  ⚠️ 删除不可逆 —— 所以默认只报告。')
        return 0

    n = 0
    for d, _kind, _why in todo:
        try:
            shutil.rmtree(d)
            n += 1
        except Exception as e:
            print(f'      ⚠️ {d.name} 删不掉：{type(e).__name__}: {e}')
    print()
    print(f'  ✅ 已删 {n}/{len(todo)} 个 · 释放 {freed} 字节')
    return 0


if __name__ == '__main__':
    sys.exit(main())
