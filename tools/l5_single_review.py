# -*- coding: utf-8 -*-
"""L5 人工盲判 · 单篇/全库一键起服务（复用 l5_validity_review.py 的 HTTP 层，避免两份白名单分叉）。

用法：
  E:\\Python313\\python.exe tools\\l5_single_review.py --hash ad690dfb
  E:\\Python313\\python.exe tools\\l5_single_review.py --validity
  → 浏览器打开 http://127.0.0.1:8097/l5-review.html

若不想起任何服务：直接打开已在跑的 8000：
  http://127.0.0.1:8000/l5-review.html?sheet=./l5-data/ad690dfb-sheet.json
（8000 已把 web/ 挂在根，清单与原文都从 web/l5-data/ 取，标注存浏览器本地并「导出 JSON」。）
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESIGN_DIR = ROOT / "tmp" / "l5-single"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hash", default="")
    ap.add_argument("--validity", action="store_true")
    ap.add_argument("--port", type=int, default=8097)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    name = a.hash or ("validity" if a.validity else "")
    if not name:
        raise SystemExit("要用 --hash <单篇> 或 --validity")
    sheet = DESIGN_DIR / f"{name}-sheet.jsonl"
    if not sheet.exists():
        raise SystemExit(f"缺少清单 {sheet}\n先运行：\n"
                         f"  python tools/l5_single_sample.py "
                         f"{'--hash ' + a.hash if a.hash else '--validity'}")
    labels = DESIGN_DIR / f"{name}-labels.jsonl"
    cmd = [sys.executable, str(ROOT / "tools" / "l5_validity_review.py"),
           "--port", str(a.port), "--sheet", str(sheet), "--labels", str(labels)]
    if a.no_browser:
        cmd.append("--no-browser")
    print(f"清单 {sheet}")
    print(f"标注落盘（append-only）：{labels}")
    return subprocess.call(cmd, cwd=str(ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
