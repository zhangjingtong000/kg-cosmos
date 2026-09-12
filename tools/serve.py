"""一条命令把系统跑起来（含前置检查）。

【为什么需要】
  此前"怎么跑起来"散在 README、内部记录、各页面报错文案里，而且说法不一：
  有的说 `python -m http.server 8098`（纯静态），有的说 `python src/app.py`（后端）。
  两者能提供的东西不同，但没人说清什么时候用哪个。
  → 本脚本把它们收敛成一个入口，并在启动前做前置检查。

【两种模式】
  --backend（默认）  起 FastAPI：界面 + 数据面 API 同源（推荐，演示主用）
                     前端会走 /api/kg/*，能证明前后端真的连通
  --static           只起静态站：完全不需要 Python 依赖与 Neo4j
                     前端自动回退到 kg-data/*.json（断网演示用）

用法：
  python tools/serve.py                 # 后端模式，端口 8000
  python tools/serve.py --static        # 纯静态，端口 8098
  python tools/serve.py --check         # 只做前置检查，不启动
  python tools/serve.py --port 8123
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import argparse
import json
import socket
import sys
from pathlib import Path

ROOT = Path(str(ROOT))
WEB = ROOT / 'web'
KG_DATA = WEB / 'kg-data'
QUALITY = ROOT / 'data' / 'quality'

# 页面跑起来真正需要的数据文件（缺了会白屏或报错，所以列为硬性检查）
REQUIRED = [
    (KG_DATA, 'filaments.json', '社区星云概览'),
    (KG_DATA, 'community-index.json', '社区索引'),
    (KG_DATA, 'comm-0.json', '社区 0 详情'),
    (KG_DATA, 'nebula.json', '全部实体（PathQA 用）'),
    (KG_DATA, 'analytics.json', '分析面板'),
    (WEB, 'index.html', '导航页'),
    (WEB, 'kg-nebula.html', '主视图'),
]


def port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def check(port):
    ok = True
    print('=== 前置检查 ===\n')

    print('[文件]')
    for d, name, why in REQUIRED:
        p = d / name
        mark = '✅' if p.exists() else '❌'
        size = f'{p.stat().st_size/1024:.0f} KB' if p.exists() else '缺失'
        print(f'  {mark} {name:24s} {size:>10s}  （{why}）')
        if not p.exists():
            ok = False

    print('\n[可选]')
    n_q = len(list(QUALITY.glob('*.json'))) if QUALITY.exists() else 0
    print(f'  · 质量产物 {n_q} 个（分析面板的可追溯来源）')
    pdfs = list((ROOT / 'data' / 'corpus').glob('*.pdf')) if (ROOT / 'data' / 'corpus').exists() else []
    print(f'  · 语料 PDF {len(pdfs)} 篇（证据深链需要它）')
    if not pdfs:
        print('    ⚠️ 没有 PDF：证据面板的「在原文中查看」会失败')

    print('\n[端口]')
    free = port_free(port)
    print(f'  {"✅" if free else "❌"} {port} {"可用" if free else "已被占用——换一个 --port，或先关掉占用进程"}')
    if not free:
        ok = False

    print('\n[依赖]')
    for mod, why in (('fastapi', '后端'), ('uvicorn', '后端'),
                     ('networkx', '图存储'), ('fitz', 'PDF 解析 (pymupdf)')):
        try:
            __import__(mod)
            print(f'  ✅ {mod:12s} （{why}）')
        except ImportError:
            print(f'  ·  {mod:12s} 未安装（{why} 需要）')

    print('\n[图数据库（可选）]')
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.5)
        neo = s.connect_ex(('127.0.0.1', 7687)) == 0
    print(f'  {"✅ Neo4j 在跑" if neo else "·  Neo4j 未运行（不影响界面演示）"}')

    print()
    print('结论：' + ('可以启动' if ok else '有阻塞项，见上面 ❌'))
    return ok


def main():
    ap = argparse.ArgumentParser(description='KG Cosmos 启动器')
    ap.add_argument('--static', action='store_true', help='只起静态站（离线演示模式）')
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--check', action='store_true', help='只做检查，不启动')
    args = ap.parse_args()

    port = args.port or (8098 if args.static else 8000)
    if not check(port):
        return 1
    if args.check:
        return 0

    print('\n' + '=' * 60)
    if args.static:
        import http.server
        import functools
        import os
        os.chdir(ROOT)
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
        print(f'静态模式（无 API，前端自动回退 kg-data/*.json）')
        print(f'  → http://127.0.0.1:{port}/web/')
        print('  Ctrl+C 停止\n')
        http.server.ThreadingHTTPServer(('127.0.0.1', port), handler).serve_forever()
    else:
        sys.path.insert(0, str(ROOT / 'src'))
        import uvicorn
        from app import app
        print('后端模式（界面 + 数据面 API 同源；前端将走 /api/kg/*）')
        print(f'  → http://127.0.0.1:{port}/          （导航页）')
        print(f'  → http://127.0.0.1:{port}/health     （存活与数据面）')
        print('  Ctrl+C 停止\n')
        uvicorn.run(app, host='127.0.0.1', port=port, log_level='warning')
    return 0


if __name__ == '__main__':
    sys.exit(main())
