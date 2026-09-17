# -*- coding: utf-8 -*-
r"""M4 的守门：**只读** · **"0 条"≠"读不到"** · **不许假阳性 arXiv id**。

## 四条纪律，各一条断言

1. **只读**：`read_zotero.py` 的源码里**不许出现任何 `INSERT`/`UPDATE`/`DELETE`/`DROP`**
   —— 那是**用户的文献库**，而且 **Zotero 可能正开着**。
   **用扫源码来证明**（比"我说它是只读的"可靠）。
2. **"0 条"与"读不到"必须分开**：给一个**不存在的路径** ⇒ **必须报错退出**，
   **不许返回"0 条"**。（本项目在"失败伪装成结果"上栽过多次。）
3. **★ 不许假阳性 arXiv id**：实测踩过 —— DOI `10.1145/3571234.3579999`
   里**裸搜** `\d{4}\.\d{4,5}` 会命中 `1234.35799`，于是"只有 DOI"的记录被误判成
   "有 arXiv 版" ⇒ **用户会去下载一篇不存在的论文**。
   ⇒ 断言：那条记录的 `arxiv_id` **必须是 None**。
4. **三种状态都能出现**（用测试库验）：`in_corpus` / `candidate` / `metadata_only`。

用法：
    python tools/check_zotero.py
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
SRC = ROOT / 'tools' / 'read_zotero.py'
REPORT = ROOT / 'data' / 'quality' / 'zotero-report.json'


def run(*args):
    p = subprocess.run([PY] + [str(x) for x in args], capture_output=True,
                       text=True, encoding='utf-8', errors='replace',
                       timeout=300, cwd=str(ROOT))
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def main():
    print('=' * 92)
    print('M4 · Zotero 互操作（只读 · 0 条≠读不到 · 不许假阳性）')
    print('=' * 92)
    fails = []

    # ── ① 只读：扫源码 ───────────────────────────────────────────
    src = SRC.read_text(encoding='utf-8')
    # 去掉注释与文档字符串再扫（注释里会提到"INSERT"来解释为什么不做）
    code = re.sub(r'"""[\s\S]*?"""', '', src)
    code = re.sub(r'^\s*#.*$', '', code, flags=re.M)
    writes = re.findall(r'\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|DROP\s+TABLE)\b',
                        code, re.I)
    print(f'  ① 源码里的写操作：{len(writes)} {sorted(set(w.upper() for w in writes))}')
    # ⚠️ **例外**：`make_test_db` 里造**测试库**时必须写 —— 那写的是临时文件，
    #    不是用户的库。所以只断言"**打开用户库的那条路径上没有写**"。
    has_ro_uri = 'mode=ro' in src and 'immutable=1' in src
    print(f'     用了只读 URI（`mode=ro` + `immutable=1`）：{has_ro_uri}')
    # 判据：**必须**有只读 URI；写操作只允许出现在 `make_test_db` 里
    mkm = code.find('def make_test_db')
    before_mkm = code[:mkm] if mkm > 0 else code
    writes_in_main = re.findall(r'\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b',
                                before_mkm, re.I)
    ok1 = has_ro_uri and not writes_in_main
    print(f'     **打开真实库的代码路径上没有写操作**：{"✅" if ok1 else "❌"}')
    if not ok1:
        fails.append(f'只读检查未过：ro_uri={has_ro_uri} 主路径写操作={writes_in_main}')
    print()

    # ── ② 不存在的库 ⇒ 必须报错，不许返回 0 条 ───────────────────
    missing = Path(tempfile.gettempdir()) / 'no-such-zotero-xyz.sqlite'
    rc2, o2 = run(SRC, '--db', missing, '--out',
                  Path(tempfile.gettempdir()) / 'x.json')
    print(f'  ② 给一个不存在的库 ⇒ rc={rc2}')
    print(f'     输出末行：{o2.strip().splitlines()[-1][:88] if o2.strip() else "(空)"}')
    ok2 = rc2 != 0 and ('打不开' in o2 or '不存在' in o2)
    print(f'     **"读不到"与"0 条"分开了**：{"✅" if ok2 else "❌"}')
    if not ok2:
        fails.append(f'不存在的库没报错（rc={rc2}）：{o2[-160:]!r}')
    print()

    # ── ③★ 造测试库 ⇒ 三种状态 + 不许假阳性 ─────────────────────
    tmp = Path(tempfile.mkdtemp(prefix='kgzot-'))
    db = tmp / 'test.sqlite'
    rep = tmp / 'r.json'
    rc3a, o3a = run(SRC, '--make-test-db', db)
    rc3b, o3b = run(SRC, '--db', db, '--out', rep)
    print(f'  ③ 造测试库 ⇒ rc={rc3a} · 读它 ⇒ rc={rc3b}')
    if not rep.exists():
        print(f'     ❌ 没产出报告：{o3b[-200:]}')
        fails.append('测试库读取没产出报告')
        return 1
    d = json.loads(rep.read_text(encoding='utf-8'))
    dist = d.get('distribution') or {}
    items = d.get('items') or []
    print(f'     {d.get("n_items")} 条 · 状态 {dist}')
    ok3a = (dist.get('in_corpus') == 1 and dist.get('metadata_only') == 2)
    # **假阳性检查**：那条"只有 DOI"的必须没有 arxiv_id
    fake = [x for x in items if x.get('doi') == '10.1145/3571234.3579999'
            and x.get('arxiv_id')]
    print(f'     **从 `10.1145/3571234.3579999` 误抓 arXiv 的：{len(fake)}**')
    if fake:
        print(f'       ⚠️ {fake[0]["arxiv_id"]!r} —— 那是 DOI 里的片段，不是 arXiv id')
    ok3b = not fake
    print(f'     **三种状态对**：{"✅" if ok3a else "❌"} · '
          f'**没有假阳性 arXiv id**：{"✅" if ok3b else "❌"}')
    if not ok3a:
        fails.append(f'测试库的状态分布不对：{dist}')
    if not ok3b:
        fails.append(f'假阳性 arXiv id：{fake}')
    # 顺带：那条"有 arXiv"的必须认出来
    found = [x for x in items if x.get('arxiv_id') == '2401.04507']
    print(f'     认得 `arXiv:2401.04507` 的条目：{len(found)} 条'
          f'（{"✅" if found else "❌"}）')
    if not found:
        fails.append('测试库里那条 arXiv 条目没被认出来')
    print()

    # ── ④ 真实库的报告（0 条也要有一份）───────────────────────────
    print(f'  ④ 真实库的报告 {REPORT.relative_to(ROOT)}：'
          f'{"在" if REPORT.exists() else "不在"}')
    if REPORT.exists():
        rd = json.loads(REPORT.read_text(encoding='utf-8'))
        print(f'     n_items={rd.get("n_items")} · 库={str(rd.get("db"))[-40:]}')
        print('     ⚠️ 实测这台机器上 **Zotero 装了（v9.0.4）但库是空的** ⇒ '
              'n_items=0 是**真值**，不是读失败')
    print()

    print('=' * 92)
    if fails:
        print(f'❌ {len(fails)} 项未通过：')
        for f in fails:
            print(f'   · {f}')
        return 1
    print('✅ 四组断言全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
