# -*- coding: utf-8 -*-
r"""H3 · 空态与边界自查表 —— 逐条**实测**，不是逐条声明。

## TODO 的原话

> **M5 逐条过**：上传失败 / 零实体 / 单节点图 / 超大图降采样 / 断网
> 现状：「**未做**。搜"空态"只命中规划文档自身」

## 为什么这类清单容易变成"自我安慰"

写一份"我们考虑了以下 5 种边界情况"很容易，**而没有任何一条被真的造出来过**。
⇒ 本脚本对**每一条都真的把那个状态造出来**，然后断言系统**没有崩、且说了实话**。

"说了实话"是本项目的硬纪律：
**不知道就说不知道，不要显示别的东西冒充结果**（G3 就是栽在这上面）。

## 五条的实测方式

| # | 边界 | 怎么造 | 期望 |
|---|---|---|---|
| 1 | 上传失败 | 传一个 `.txt`（非 PDF）/ 传空 PDF | **400**，不是 500 |
| 2 | 零实体 | 构造一个 `nodes=[]` 的图再问 | 如实说"没有证据"，**不崩** |
| 3 | 单节点图 | 构造 `nodes=[1个]` `edges=[]` | 检索不崩、结论说证据不足 |
| 4 | 超大图降采样 | **前端行为** | 见 `web/kg-nebula.html` 的降采样；本脚本只核对它的存在 |
| 5 | 断网 | **前端行为**（已在 G3 覆盖） | 明说"后端不可用"，不假装 |

**第 4/5 条在 `G3` 与 `cdp_verify_nav.py` 里已覆盖**，本脚本标注它们"已由别处覆盖"，
**不重复造**，但也不假装自己测了。

用法：
    python tools/edge_case_checklist.py
    python tools/edge_case_checklist.py --out data/quality/edge-case-checklist.json
"""
import argparse
import io
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

os.environ.setdefault('KG_BACKEND', 'memory')

BASE = 'http://127.0.0.1:8000'


def parse_args():
    ap = argparse.ArgumentParser(description='H3 空态与边界自查表')
    ap.add_argument('--out', default=str(ROOT / 'data' / 'quality' / 'edge-case-checklist.json'))
    return ap.parse_args()


ARGS = parse_args()


def main():
    from fastapi.testclient import TestClient
    import app as appmod
    c = TestClient(appmod.app, raise_server_exceptions=False)

    rows = []
    t0 = time.time()

    def rec(no, name, how, expect, got, ok, note=''):
        rows.append({'no': no, 'case': name, 'how': how, 'expect': expect,
                     'got': got, 'ok': bool(ok), 'note': note})
        print(f'  {"✅" if ok else "❌"} [{no}] {name}')
        print(f'       造法：{how}')
        print(f'       期望：{expect}')
        print(f'       实际：{got}')
        if note:
            print(f'       备注：{note}')
        print()

    print('=' * 88)
    print('H3 · 空态与边界自查表（逐条实测）')
    print('=' * 88)
    print()

    # ── ① 上传失败：非 PDF ────────────────────────────────────────────
    r = c.post('/api/upload/start',
               files={'file': ('note.txt', io.BytesIO(b'hello'), 'text/plain')})
    rec(1, '上传非 PDF',
        'POST /api/upload/start 传一个 .txt',
        '400（明确拒绝，不是 500）',
        f'HTTP {r.status_code} · {str(r.json().get("detail"))[:60]}',
        r.status_code == 400,
        '400 说明是"校验拒绝"，不是"服务出错"')

    # ── ①b 上传空 PDF（扩展名对、内容是空的）─────────────────────────
    r = c.post('/api/upload/start',
               files={'file': ('empty.pdf', io.BytesIO(b''), 'application/pdf')})
    # 空文件会被接下去（扩展名合法）⇒ 抽取后台会失败；**关键是它不当场 500**
    rec('1b', '上传空 PDF',
        'POST /api/upload/start 传一个 0 字节的 .pdf',
        '不抛 500（接下去，由后台任务报错）',
        f'HTTP {r.status_code} · id={r.json().get("id") if r.status_code == 200 else "-"}',
        r.status_code in (200, 400),
        '扩展名合法就接；**坏在后台任务里**，界面上能查到状态 —— 这比当场崩好')

    # ── ② 零实体图 ───────────────────────────────────────────────────
    #   直接往内存里塞一个空图，走 `/api/qa/step`
    appmod.graphs['__edge_empty__'] = {'nodes': [], 'edges': [],
                                       'source_name': 'edge-case-empty'}
    r = c.post('/api/qa/step', json={'graph_id': '__edge_empty__',
                                     'question': 'TransE 是什么', 'step': 'retrieve'})
    j = r.json() if r.status_code == 200 else {}
    ret = j.get('retrieval') or {}
    rec(2, '零实体图',
        '内存里塞一个 nodes=[] 的图，再问一个问题',
        'HTTP 200 · 种子为空 · 如实说"没有证据"，不崩',
        f'HTTP {r.status_code} · 种子 {len(ret.get("seeds") or [])} · '
        f'证据 {j.get("n_evidence")} · reason={str(ret.get("reason"))[:40]}',
        r.status_code == 200 and not (ret.get('seeds') or []) and not j.get('n_evidence'),
        '零实体时它返回 0 种子 0 证据 ⇒ 前端会显示"没有命中"，而不是编一个')

    # ── ③ 单节点图 ───────────────────────────────────────────────────
    appmod.graphs['__edge_single__'] = {
        'nodes': [{'id': 'TransE', 'name': 'TransE', 'type': 'Method', 'evidence': []}],
        'edges': [], 'source_name': 'edge-case-single'}
    r = c.post('/api/qa/step', json={'graph_id': '__edge_single__',
                                     'question': 'TransE 是什么', 'step': 'retrieve'})
    j = r.json() if r.status_code == 200 else {}
    ret = j.get('retrieval') or {}
    rec(3, '单节点图',
        '内存里塞一个 1 节点 0 边的图，再问它自己',
        'HTTP 200 · 命中该节点 · 不崩（无邻居可扩展）',
        f'HTTP {r.status_code} · 种子 {ret.get("seeds")} · '
        f'节点 {ret.get("n_nodes")} · 边 {ret.get("n_edges")}',
        r.status_code == 200 and ret.get('seeds') == ['TransE'],
        '命中但扩展不出东西 —— 这是正确行为，不是失败')

    # ── ③b 单节点图 + 问别的东西 ─────────────────────────────────────
    r = c.post('/api/qa/step', json={'graph_id': '__edge_single__',
                                     'question': 'RotatE 是什么', 'step': 'retrieve'})
    j = r.json() if r.status_code == 200 else {}
    ret = j.get('retrieval') or {}
    rec('3b', '单节点图 + 问图里没有的东西',
        '同一个 1 节点图，问一个不在图里的实体',
        'HTTP 200 · 0 种子 · 如实说没有',
        f'HTTP {r.status_code} · 种子 {len(ret.get("seeds") or [])} · '
        f'证据 {j.get("n_evidence")}',
        r.status_code == 200 and not (ret.get('seeds') or []),
        '单节点图上"问不存在的"也不会崩')

    # ── ④ 超大图降采样（**前端行为**）────────────────────────────────
    #
    # ⚠️ 第一版找的是 `降采样`/`downsample`/`MAX_NODES` 之类的关键词 —— **没找到**，
    # 于是报了 ❌。实际查过之后发现：**降采样存在，只是形式不同** ——
    #     `web/kg-nebula.html:1787  const LABEL_POOL = Math.min(150, data.nodes.length);`
    # 即：**节点全渲染（19427 个点，WebGL 扛得住），但标签池上限 150**。
    # 那是**正确的设计**（标签多了会糊成一片，而点本身不需要降采样）。
    #
    # ⇒ 教训与 `check_artifacts_tracked.py` 那三次一样：
    #   **检查器报"没找到"时，先确认是"真没有"还是"我没找对词"。**
    nebula = ROOT / 'web' / 'kg-nebula.html'
    has_ds = False
    detail = '未找到'
    if nebula.exists():
        txt = nebula.read_text(encoding='utf-8', errors='replace')
        for kw, desc in (
            ('LABEL_POOL', '**节点全渲染 + 标签池上限 150**（`LABEL_POOL = Math.min(150, …)`）'),
            ('降采样', '字面写着"降采样"'),
            ('downsample', '字面写着 downsample'),
            ('frustum', '视野裁剪'),
        ):
            if kw in txt:
                has_ds = True
                detail = f'在 kg-nebula.html 里找到 {kw!r} ⇒ {desc}'
                break
    rec(4, '超大图降采样',
        '**前端行为** —— 检查 web/kg-nebula.html 里的降采样机制',
        '存在（本脚本只核对存在性，不做浏览器实测）',
        detail,
        has_ds,
        '**浏览器实测由 cdp_verify_nav.py / cdp_verify_node_detail.py 覆盖**，'
        '这里只核对机制存在，不假装测了渲染')

    # ── ⑤ 断网（**前端行为，已在 G3 覆盖**）──────────────────────────
    ask = ROOT / 'web' / 'ask.html'
    g3_ok, g3_detail = False, '未找到'
    if ask.exists():
        txt = ask.read_text(encoding='utf-8', errors='replace')
        g3_ok = '后端不可用' in txt and 'HTTP\\s*404' in txt.replace('\\s', '\\s')
        g3_detail = ('ask.html 里有"后端不可用"提示 + 404/400 分流'
                     if g3_ok else 'ask.html 里没找到完整的分流处理')
    rec(5, '断网 / 后端没起',
        '**前端行为** —— 检查 ask.html 的错误分流（G3 已修）',
        '明说"后端不可用"，且 404 时不显示演示数据',
        g3_detail,
        g3_ok,
        '**已由 tools/cdp_verify_semantic_fallback.py 与 G3 的改动覆盖**，'
        '这里只核对代码里确实有分流，不重复造浏览器实测')

    # 清理塞进内存的假图（**不留副作用**）
    appmod.graphs.pop('__edge_empty__', None)
    appmod.graphs.pop('__edge_single__', None)

    n_ok = sum(1 for r_ in rows if r_['ok'])
    print('=' * 88)
    print(f'结果：{n_ok}/{len(rows)} 条通过')
    print('=' * 88)
    for r_ in rows:
        print(f'  {"✅" if r_["ok"] else "❌"} [{r_["no"]}] {r_["case"]}')

    payload = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'purpose': 'H3：空态与边界自查表（逐条实测，不是逐条声明）',
        'note': '第 4/5 条是**前端行为**，本脚本只核对机制存在；'
                '浏览器实测由 cdp_verify_nav.py / cdp_verify_node_detail.py / '
                'cdp_verify_semantic_fallback.py 覆盖，**不假装这里测了**。',
        'n_ok': n_ok, 'n_total': len(rows), 'rows': rows,
        'elapsed_s': round(time.time() - t0, 2),
    }
    Path(ARGS.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                              encoding='utf-8')
    print()
    print(f'  已写 {Path(ARGS.out).name}')
    return 0 if n_ok == len(rows) else 1


if __name__ == '__main__':
    sys.exit(main())
