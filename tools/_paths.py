# -*- coding: utf-8 -*-
r"""共用的路径工具 —— **只为了一个在 Windows 上必然会遇到的坑**。

## 坑是什么

`os.path.relpath(p, ROOT)` 与 `Path.relative_to(ROOT)` 在**跨盘**时都会报错：

    ValueError: path is on mount 'C:', start on mount 'D:'

而本项目的脚本**大量用相对路径打印**（"→ data/quality/xxx.json"），
又**允许把输出指到别处**（测试用 `tmp_path`、归档用 `KG_ARCHIVE_DIR`），
于是**跨盘是常态而不是例外**。

**实测踩到两次**：
  · `tools/metric_provenance.py`（N1，归档目录被测试指到 C 盘）
  · `tools/apply_edit_plan.py`（K1，计划文件在 pytest 的 C 盘临时目录）

**两次的症状相同**：一个**打印语句**把整个命令搞挂，而且报的是
`ValueError` 而不是"某个功能不可用" —— 很难一眼看出是路径问题。

## 用法

    from _paths import ROOT, rel
    print(f'→ {rel(out_path)}')      # 跨盘时自动退回绝对路径
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def rel(p, base=None):
    """尽量给相对路径；**跨盘时退回绝对路径**（而不是抛异常）。"""
    p = Path(p)
    base = Path(base) if base else ROOT
    try:
        return p.relative_to(base)
    except ValueError:
        try:
            import os
            return Path(os.path.relpath(p, base))
        except ValueError:
            return p
