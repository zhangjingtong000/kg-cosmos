"""下载 pdf.js 到 web/lib/pdfjs/，用于在页面内自绘 PDF 并高亮证据句。

【为什么改用 pdf.js】
  T6.2 原先用「Chrome 内置 PDF 查看器的 URL 片段」：
  `#page=N` 跳页**实测生效**，但 `#search=文本` 在无头 Chrome 下**未生效**
  （带与不带该参数的截图哈希完全相同），有头模式无法在本环境验证。
  于是"PDF 高亮"实际只做了一半。

  pdf.js 自己渲染 + 自建文本层，可以**精确定位到具体文本片段并画高亮**，
  而且完全可测（能截图验证高亮矩形存在）。顺带也符合刚确立的离线原则：
  文件放本地，不走 CDN。

【选型】
  `pdfjs-dist` 的 ESM 构建：`build/pdf.min.mjs` + `build/pdf.worker.min.mjs`。
  worker 路径必须显式指定（`GlobalWorkerOptions.workerSrc`），否则会去 CDN 找。

用法：
  python tools/fetch_pdfjs_local.py
"""
import os
import sys
from pathlib import Path

PROXY = "http://127.0.0.1:7897"
VER = "4.6.82"          # 4.x 稳定线；mjs 构建从 4.0 起提供
BASE = f"https://unpkg.com/pdfjs-dist@{VER}"
OUT = Path(r"D:\kg-demo\web\lib\pdfjs")

FILES = [
    ("build/pdf.min.mjs", "pdf.min.mjs"),
    ("build/pdf.worker.min.mjs", "pdf.worker.min.mjs"),
]


def main():
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.setdefault(k, PROXY)
    import requests

    OUT.mkdir(parents=True, exist_ok=True)
    ok = True
    for remote, local in FILES:
        url = f"{BASE}/{remote}"
        dst = OUT / local
        try:
            r = requests.get(url, timeout=120)
            r.raise_for_status()
            dst.write_bytes(r.content)
            print(f"  {local:26s} {len(r.content)/1024:8.1f} KB")
        except Exception as e:
            ok = False
            print(f"  {local:26s} 失败: {type(e).__name__}: {e}")

    if not ok:
        return 1

    # 校验：worker 路径是否被正确引用；核心文件是否含 ESM 导出
    core = (OUT / "pdf.min.mjs").read_text(encoding="utf-8", errors="replace")
    print(f"\n  校验 pdf.min.mjs: 含 'export' = {'export' in core[:4000] or 'export{' in core}")
    print(f"  校验 worker: {((OUT / 'pdf.worker.min.mjs').stat().st_size)/1024:.0f} KB")

    total = sum(f.stat().st_size for f in OUT.glob("*.mjs"))
    print(f"\n已写入 {OUT}（合计 {total/1024/1024:.2f} MB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
