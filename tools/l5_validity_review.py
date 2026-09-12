# -*- coding: utf-8 -*-
"""L5 域内效度抽检 · 第二步：本地盲判服务（零依赖，标准库 http.server）。

为什么要有这一步：本项目的核心约束是**零云端支出**，而 L5 的效度又只能由
**判别器之外**的判定者给出（否则循环论证）。可用的独立判定者只有人。
所以这个脚本的唯一目的是把「人工盲判 200 条」的边际成本压到最低：
  · 一屏一条，同屏给出 hypothesis / premise / 完整证据段落；
  · 一键展开 pdf.js 自绘的原文页并高亮前提句（离线，走 web/lib/pdfjs）；
  · 键盘 1/2/3 判定并自动进入下一条，随时可停、可续；
  · **不下发也不显示 entail_prob / pass / hit** —— 清单来自
    l5_validity_sample.py 的盲判清单，分数在另一份 key 文件里，本服务不读它。

服务只监听 127.0.0.1，只做三件事：
  GET  /api/items          返回盲判清单 + 已有标注
  POST /api/label          追加一条标注（append-only JSONL，崩溃不丢）
  GET  /                   静态文件（web/ 目录，pdf.js 也在里面）

用法：
  python tools/l5_validity_review.py            # 默认 8097 端口
  python tools/l5_validity_review.py --port 8097
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import argparse
import json
import mimetypes
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(str(ROOT))
WEB = ROOT / "web"
SHEET = ROOT / "data" / "quality" / "l5-validity-sample.jsonl"
LABELS = ROOT / "data" / "quality" / "l5-validity-labels.jsonl"

ARGS = None


def load_items():
    if not ARGS.sheet.exists():
        raise SystemExit(f"缺少盲判清单 {ARGS.sheet}\n"
                         f"先运行 python tools/l5_validity_sample.py")
    return [json.loads(l) for l in ARGS.sheet.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def load_labels():
    out = {}
    if ARGS.labels.exists():
        for l in ARGS.labels.read_text(encoding="utf-8").splitlines():
            if l.strip():
                try:
                    r = json.loads(l)
                    out[r["id"]] = r
                except Exception:
                    pass
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):    # 静音逐请求日志，只保留自己的提示
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/items":
            return self._send(200, {"items": load_items(), "labels": load_labels()})
        if path in ("/", "/l5-review.html"):
            path = "/l5-review.html"
        # 盲判页要用 pdf.js 打开 data/corpus/<paper>.pdf（见 web/l5-review.html 的
        # `../data/corpus/...`）。静态根是 web/，所以 /data/ 必须单独映射到项目根，
        # 否则 PDF 抽屉会 404 —— 而「能翻到原文」正是这个界面存在的理由。
        if path.startswith("/data/"):
            f = (ROOT / path.lstrip("/")).resolve()
            allowed = str((ROOT / "data").resolve())
        else:
            f = (WEB / path.lstrip("/")).resolve()
            allowed = str(WEB.resolve())
        if not str(f).startswith(allowed) or not f.is_file():
            return self._send(404, {"error": "not found"})
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        if f.suffix == ".mjs":
            ctype = "text/javascript"
        return self._send(200, f.read_bytes(), ctype)

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/api/label":
            return self._send(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            rec = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._send(400, {"error": str(e)})
        if not rec.get("id") or rec.get("verdict") not in ("support", "reject", "unclear"):
            return self._send(400, {"error": "bad record"})
        ARGS.labels.parent.mkdir(parents=True, exist_ok=True)
        with ARGS.labels.open("a", encoding="utf-8") as fh:   # append-only：崩溃也只丢最后一条
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return self._send(200, {"ok": True})


def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8097)
    ap.add_argument("--sheet", type=Path, default=SHEET)
    ap.add_argument("--labels", type=Path, default=LABELS,
                    help="标注落盘文件。第二个标注者请换一个（用于算标注者间一致性）")
    ap.add_argument("--no-browser", action="store_true")
    ARGS = ap.parse_args()
    items = load_items()
    done = load_labels()
    url = f"http://127.0.0.1:{ARGS.port}/l5-review.html"
    print("=== L5 域内效度盲判 ===")
    print(f"  清单 {ARGS.sheet}")
    print(f"  清单 {len(items)} 条，已标 {len(done)} 条，待标 {len(items)-len(done)} 条")
    print(f"  标注落盘（append-only）：{ARGS.labels}")
    print(f"  地址：{url}")
    print("  停止：Ctrl+C。随时可停可续（重开本脚本即从第一条未标的继续）。")
    print("  ⚠ 界面**不显示** entail_prob / pass / hit —— 这是盲判，别去翻 key 文件。")
    if not ARGS.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    ThreadingHTTPServer(("127.0.0.1", ARGS.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
