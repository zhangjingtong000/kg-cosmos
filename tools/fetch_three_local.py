"""把 three.js 及其用到的 addons 下载到 web/lib/three/，供离线演示使用。

背景：早先的评审记录 §1 第 6 条是「**离线演示铁律**」——
现场演示要有"预构建缓存图谱 + 本地 Ollama"双保险。
但 `web/kg-nebula.html` 的 three.js 走 unpkg CDN，
`tools/cdp_verify_offline.py` 已实测断网后该页完全无法启动。

只下真正被用到的三个文件，不整包拉取：
  build/three.module.js                 核心（约 1.2 MB）
  examples/jsm/controls/OrbitControls.js
  examples/jsm/renderers/CSS2DRenderer.js
后两者在 r160 里只依赖 `three` 裸模块名，靠 importmap 解析，无需再拉别的。

用法：
  python tools/fetch_three_local.py
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import os
import sys
from pathlib import Path

PROXY = "http://127.0.0.1:7897"
VER = "0.160.0"
BASE = f"https://unpkg.com/three@{VER}"
OUT = ROOT / "web/lib/three"

FILES = [
    ("build/three.module.js", "three.module.js"),
    ("examples/jsm/controls/OrbitControls.js", "OrbitControls.js"),
    ("examples/jsm/renderers/CSS2DRenderer.js", "CSS2DRenderer.js"),
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
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            dst.write_bytes(r.content)
            print(f"  {local:24s} {len(r.content)/1024:8.1f} KB  ← {remote}")
        except Exception as e:
            ok = False
            print(f"  {local:24s} 失败: {type(e).__name__}: {e}")

    if not ok:
        print("\n有文件下载失败，importmap 不要改成本地路径")
        return 1

    # 校验：addons 里对 three 的引用必须是裸模块名（靠 importmap 解析），
    # 若出现相对路径说明 r160 的依赖结构变了，本地化会缺文件。
    import re
    for local in ("OrbitControls.js", "CSS2DRenderer.js"):
        txt = (OUT / local).read_text(encoding="utf-8")
        bare = re.search(r"from\s+'three'", txt)
        rel = re.findall(r"from\s+'(\.\.?/[^']+)'", txt)
        print(f"  校验 {local}: 依赖裸名 three = {bool(bare)}；相对导入 = {rel or '无'}")
        if not bare:
            print("    ⚠️ 未发现 `from 'three'`，需人工确认依赖形式")
        if rel:
            print(f"    ⚠️ 存在相对导入 {rel}，需要一并下载")
            ok = False

    print(f"\n已写入 {OUT}")
    total = sum(f.stat().st_size for f in OUT.glob('*.js'))
    print(f"合计 {total/1024/1024:.2f} MB")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
