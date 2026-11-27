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
import os
import socket
import sys
from pathlib import Path

ROOT = Path(str(ROOT))
WEB = ROOT / 'web'
KG_DATA = WEB / 'kg-data'
QUALITY = ROOT / 'data' / 'quality'


def load_dotenv():
    """把仓库根下的 `.env` 读进 `os.environ`（**已存在的不覆盖**）。

    **为什么需要它**：项目此前**没有任何加载 `.env` 的代码** ——
    所有 `os.environ.get(...)` 读的都是**真实环境变量**。
    所以 `.env.example`（本会话新加的模板）拷成 `.env` 也**不会生效**，
    它只是一份文档。这个函数把那条路接通。

    **为什么放在 `serve.py` 而不是 `app.py`**：`serve.py` 是 README 与
    RUNBOOK 里写的**唯一入口**（两种模式都经它），放这里两种模式都能受益。

    **为什么"已存在的不覆盖"**：`.env` 是**默认值来源**，不是**命令。
    命令行/系统里显式设的环境变量优先级应当更高** —— 否则
    `KG_NEO4J_PASSWORD=xxx python tools/serve.py` 会被 `.env` 里的旧值盖掉。

    依赖 `python-dotenv`（已安装）。**没装时不报错**，只提示一句 ——
    因为 `.env` 是可选的，不该阻塞启动。
    """
    env_path = ROOT / '.env'
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv as _ld
    except ImportError:
        print(f'⚠️ 发现 {env_path.name}，但未安装 python-dotenv，已忽略它')
        print('   （装：E:\\Python313\\python.exe -m pip install python-dotenv）')
        return
    n_before = len(os.environ)
    _ld(env_path, override=False)          # override=False ⇒ 已存在的不覆盖
    added = len(os.environ) - n_before
    print(f'·  已加载 {env_path.name}（新增 {added} 个变量；已存在的不覆盖）')


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
    demo_used = []
    for d, name, why in REQUIRED:
        p = d / name
        demo = d / 'demo' / name
        if p.exists():
            mark, size = '✅', f'{p.stat().st_size/1024:.0f} KB'
        elif demo.exists():
            # 精简版不带全量图谱：示例数据能顶上来，页面就不会白屏。
            # 但必须显式标出来 —— 否则"18 个社区"这类数字会被当成真实结果。
            mark, size = '⚠️', f'示例 {demo.stat().st_size/1024:.0f} KB'
            demo_used.append(name)
        else:
            mark, size = '❌', '缺失'
            ok = False
        print(f'  {mark} {name:24s} {size:>10s}  （{why}）')

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

    # ── 本地模型（Ollama）────────────────────────────────────────────────
    # **2026-09-15 补**：此前这个检查**完全没有** —— 而上传与问答都要用它。
    # 演示现场"传了半天没反应"最常见的原因就是 Ollama 没起来，
    # 而 `serve.py` 是唯一入口，它不说就没人说。
    #
    # **故意不做"自动拉起"**：起服务是有副作用的动作（占显存、改系统状态），
    # 不该由一个"检查"函数顺手做掉。**明确告诉人怎么起**就够了。
    print('\n[本地模型（上传与问答要用）]')
    ollama_host = os.environ.get('OLLAMA_HOST', 'http://127.0.0.1:11434')
    ollama_up = False
    try:
        import urllib.request
        with urllib.request.urlopen(f'{ollama_host}/api/tags', timeout=3) as r:
            tags = json.loads(r.read().decode('utf-8'))
        models = [m.get('name') for m in (tags.get('models') or [])]
        ollama_up = True
        want = os.environ.get('KG_QA_MODEL', 'qwen3.5:9b')
        has_qwen = any(str(m).startswith(want.split(':')[0]) for m in models)
        print(f'  ✅ Ollama 在跑（{len(models)} 个模型）')
        print(f'  {"✅" if has_qwen else "· "} 抽取/问答模型 {want}'
              f'{" 已就绪" if has_qwen else " 未找到（抽取会失败，问答会降级为纯规则）"}')
    except Exception:
        print(f'  ❌ Ollama 未运行（{ollama_host}）')
        print('     ⚠️ **上传抽取与多 Agent 问答都要用它**；界面能开，但这两件会失败或降级')
        print('     起法：另开一个终端执行 `ollama serve`（模型会自动加载）')

    print()
    print('结论：' + ('可以启动' if ok else '有阻塞项，见上面 ❌'))
    if demo_used:
        print(f'     （{len(demo_used)} 个文件走 web/kg-data/demo/ 示例数据：'
              f'页面能渲染，但规模不是全量 —— 完整数据见 RUNBOOK.md）')
    if not ollama_up:
        print('      （Ollama 未起不算阻塞 —— 但演示上传/问答前请先起它）')
    return ok


def main():
    ap = argparse.ArgumentParser(description='KG Cosmos 启动器')
    ap.add_argument('--static', action='store_true', help='只起静态站（离线演示模式）')
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--check', action='store_true', help='只做检查，不启动')
    args = ap.parse_args()

    # **在任何检查之前**加载 .env —— 否则 `.env` 里设的 KG_NEO4J_* /
    # OLLAMA_HOST / KG_QA_MODEL 都不会被下面的检查看到（检查会误报"未配置"）。
    load_dotenv()

    port = args.port or (8098 if args.static else 8000)
    if not check(port):
        return 1
    if args.check:
        return 0

    print('\n' + '=' * 60)
    if args.static:
        import http.server
        import os
        import posixpath
        from urllib.parse import unquote

        # ⚠️ **2026-09-15 修一个破盲漏洞**：此前根目录就是仓库根，
        # 于是 `/data/quality/l5-validity-key.json` **一条 URL 就能下载**
        # （实测 200 / 24498 字符）。该文件含 200 条 × (entail_prob/pass/hit/stratum)，
        # 拿到就**当场作废 L5 人工盲判** —— 而那是本项目唯一可信的效度来源。
        #
        # 同一个洞项目**已经堵过两次**（`src/app.py:194-205`、`tools/l5_validity_review.py:87-97`），
        # **唯独这个静态入口没堵**，而 README 明说它是"断网演示用"的官方入口。
        #
        # **第一版修法是错的**：把根改成 `ROOT/'web'`。那确实堵住了洞，
        # **但打断了 14 个 CDP 脚本**（它们全都访问 `http://127.0.0.1:8098/web/...`），
        # 回归当场红了 2 项。这正是"改了但没验证" —— 所以改成**精确拦截**：
        # 根保持不变（`/web/` 路径照旧可用），只把仓库里的敏感目录挡掉。
        BLOCKED_PREFIX = (
            '/data/', '/src/', '/tools/', '/tests/', '/docs/',
            '/.git/', '/.worktrees/', '/.superpowers/', '/tmp/', '/output/',
        )
        BLOCKED_EXACT = (
            '/README.md', '/RUNBOOK.md', '/CONTRIBUTING.md', '/LICENSE',
            '/requirements.txt', '/pyproject.toml', '/.gitignore',
            '/.env', '/.env.example',
        )
        # Windows 上路径不区分大小写（`/DATA/…` 能取到 `/data/…` 的文件），
        # 所以归一化之后要跟一份小写副本比。启动时算一次，不必每次请求都算。
        BLOCKED_PREFIX_LOWER = tuple(p.lower() for p in BLOCKED_PREFIX)
        BLOCKED_EXACT_LOWER = tuple(p.lower() for p in BLOCKED_EXACT)
        _real_translate = http.server.SimpleHTTPRequestHandler.translate_path
        # Windows 的路径比较不区分大小写，而 `SimpleHTTPRequestHandler` 在
        # Windows 上照样按不区分大小写打开文件 ⇒ 守卫也必须按不区分大小写比。
        _CI = (os.name == 'nt')

        class GuardedHandler(http.server.SimpleHTTPRequestHandler):
            """静态站处理器：挡掉仓库里的敏感路径，其余照旧。

            挡法是把路径指到一个**不存在的文件**上 —— `SimpleHTTPRequestHandler`
            随后自然返回 404，不必自己拼响应。
            **注意**：判定要用**原始请求路径**，不能用 `translate_path` 的结果
            （后者已经过规范化，且会受 `directory` 影响）。

            ★★ 2026-09-21 修：上面那句"原始请求路径"的取舍**本身是个洞**。
            此前只做**字面前缀**比较，于是下面四种写法全部绕过（实测）：
                /./data/quality/l5-validity-key.json          → 200 / 27300 B
                /DATA/quality/metric-provenance.json          → 200 / 8405 B
                /web/../data/quality/metric-provenance.json   → 200 / 8405 B
                /web/%2e%2e/data/quality/…                    → 200 / 8405 B
            其中第一个正是本文件上面那段注释**声称已经堵住**的文件 ——
            破盲的后果是 L5 人工盲判当场作废，而那是本项目唯一可信的效度来源。
            修法：比较**规范化之后**的路径（`/./`、`/../`、多余的 `/`
            全部折叠掉），并在 Windows 上做大小写归一。
            教训：黑名单判定必须建立在"规范形式"上，否则等于没判。
            """
            def translate_path(self, path):
                raw = unquote(path.split('?', 1)[0].split('#', 1)[0])
                norm = posixpath.normpath(raw)
                while norm.startswith('//'):        # normpath 保留开头的双斜杠
                    norm = norm[1:]
                if not norm.startswith('/'):
                    norm = '/' + norm
                key = norm.lower() if _CI else norm
                blocked = BLOCKED_PREFIX_LOWER if _CI else BLOCKED_PREFIX
                exact = BLOCKED_EXACT_LOWER if _CI else BLOCKED_EXACT
                if key.startswith(blocked) or key in exact:
                    return os.path.join(self.directory, '__blocked_by_serve__')
                real = _real_translate(self, path)
                # ── 精简版的数据缺口：kg-data/<name> 缺失时回退示例数据 ──
                # 缺了全量图谱，十几个页面本来只剩一句「数据加载失败」。
                # 示例数据在 web/kg-data/demo/（规模被裁过，不代表真实结果），
                # 只对 /web/kg-data/<裸文件名>.json 生效，不影响其它路径。
                if not os.path.exists(real):
                    prefix = '/web/kg-data/'
                    if norm.startswith(prefix):
                        rest = norm[len(prefix):]
                        if rest and '/' not in rest and rest.endswith('.json'):
                            cand = os.path.join(self.directory, 'web', 'kg-data',
                                                'demo', rest)
                            if os.path.exists(cand):
                                return cand
                return real

            def list_directory(self, path):
                """**不列目录**。

                此前 `http://127.0.0.1:8098/` 返回的是仓库根的完整索引
                （明文列出 `.git/`、`.env.example`、`.worktrees/` …），
                等于把"该往哪打"直接告诉访问者。静态站只需要能取到文件，
                不需要能枚举文件。"""
                self.send_error(404, 'No permission to list directory')
                return None

        handler = GuardedHandler
        os.chdir(ROOT)
        # `directory` 通过类属性传给 handler（partial 与自定义类不能混用）
        GuardedHandler.directory = str(ROOT)
        print(f'静态模式（无 API，前端自动回退 kg-data/*.json）')
        print(f'  → http://127.0.0.1:{port}/web/')
        print('  🔒 已拦截仓库敏感路径（/data/ /src/ /tools/ /docs/ …）')
        print('     —— 此前 /data/quality/l5-validity-key.json 可被直接下载，'
              '会让 L5 人工盲判作废')
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
