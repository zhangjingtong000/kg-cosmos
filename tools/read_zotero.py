# -*- coding: utf-8 -*-
r"""M4 · **Zotero / 文献管理器互操作**（读 `zotero.sqlite`，**只读**）。

## TODO 的原话

> | **M4** | ⬜ **Zotero / 文献管理器互操作** | A（遗漏） | 同上 |

## ★ M4 的"一半"其实已经做完了（在 M5 里）

`tools/export_citations.py` 导出 **`.ris`** 与 **`.csl.json`** ——
**Zotero 两种都能直接吃**（File → Import，或者拖进窗口）。
⇒ **"把我们的结果给 Zotero"这件事已经通了。**

**⇒ 本脚本补的是另一半：把 Zotero 里已有的读出来，与本地语料对比。**
那回答的是一个很实际的问题：**「我 Zotero 里收着的那些，图谱里有哪些没抽？」**

## ⚠️ 三条纪律（读别人的数据库）

1. **只读**：`file:...?mode=ro&immutable=1` ——
   那是**用户的文献库**，而且 **Zotero 可能正开着**（它自己会锁库）。
   **本脚本一条 `INSERT`/`UPDATE`/`DELETE` 都没有。**
2. **不猜 schema 的语义**：Zotero 的字段存在 `itemData` 里（EAV 结构），
   字段名要经 `fields.fieldName` 查。**直接读表名猜字段必错**（第一版探针已验证）。
3. **读不出就说读不出**：库不存在、被锁、schema 变了 ⇒ **如实报错，不返回空结果**
   （**"0 条"与"读不到"是两件完全不同的事** —— 本项目在这上面栽过很多次）。

## ★ 实测：这台机器上 **Zotero 装了（v9.0.4），但库是空的**

```
items  **0 行** · itemData **0 行** · creators **0 行**
```

⇒ 所以**真实库验证不了**。本脚本自带一个**最小 Zotero schema 的测试库生成器**
（`--make-test-db`），用**真实的表结构**造 3 条记录 ⇒ **可复跑的验证**。

用法：
    python tools/read_zotero.py                      # 读真实库
    python tools/read_zotero.py --db <路径>
    python tools/read_zotero.py --make-test-db <路径>  # 造测试库
    python tools/read_zotero.py --db <测试库> --out report.json
"""
import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REG = ROOT / 'web' / 'kg-data' / 'paper-registry.json'
OUT = ROOT / 'data' / 'quality' / 'zotero-report.json'
DEFAULT_DB = Path.home() / 'Zotero' / 'zotero.sqlite'

RE_ARXIV = re.compile(r'(\d{4}\.\d{4,5})(v\d+)?')
RE_DOI = re.compile(r'10\.\d{4,9}/[^\s"{}]+')
# ⚠️ **arXiv id 只能从"明确指向 arXiv"的地方取** —— 实测踩过：
#    DOI `10.1145/3571234.3579999` 里**裸搜** `\d{4}\.\d{4,5}` 会命中 `1234.35799`
#    ⇒ 于是那条"只有 DOI"的记录被误判成 `candidate`（有 arXiv 版）⇒
#    **用户会去下载一篇不存在的 arXiv 论文**。
#    ⇒ 只认三种明确形态：arXiv 的 DOI 前缀 / `extra` 里的 `arXiv:` / `url` 里的 `arxiv.org/abs/`。
RE_ARXIV_DOI = re.compile(r'10\.48550/arxiv\.(\d{4}\.\d{4,5})(v\d+)?', re.I)
RE_ARXIV_TAG = re.compile(r'arxiv[:\s]*(\d{4}\.\d{4,5})(v\d+)?', re.I)
RE_ARXIV_URL = re.compile(r'arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(v\d+)?', re.I)


def extract_arxiv(doi, url, extra):
    """从**明确的地方**取 arXiv id（**不从任意 DOI 里裸搜**）。"""
    for pat, src in ((RE_ARXIV_DOI, doi or ''),
                     (RE_ARXIV_TAG, extra or ''),
                     (RE_ARXIV_URL, url or ''),
                     (RE_ARXIV_TAG, url or '')):
        m = pat.search(src)
        if m:
            return m.group(1) + (m.group(2) or '')
    return None


def connect_ro(db):
    """**只读**打开。读不到就抛 —— **不返回空结果**。"""
    if not Path(db).exists():
        raise FileNotFoundError(f'Zotero 库不存在：{db}')
    # `immutable=1` 让它**不建 -wal/-shm、不尝试加锁**（Zotero 开着时也能读）
    uri = f'file:{str(db).replace(chr(92), "/")}?mode=ro&immutable=1'
    return sqlite3.connect(uri, uri=True, timeout=5)


def read_items(con):
    """按 Zotero 的 EAV 结构读条目 ⇒ 标题/DOI/url/date/extra/作者。"""
    cur = con.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cur.fetchall()}
    need = {'items', 'itemTypes', 'itemData', 'itemDataValues', 'fields'}
    missing = need - tables
    if missing:
        raise RuntimeError(f'Zotero 库的表结构不对，缺：{sorted(missing)}')

    # 条目（排除已删）
    q = """
    SELECT i.itemID, it.typeName, i.key
    FROM items i JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
    WHERE i.itemID NOT IN (SELECT itemID FROM deletedItems)
    ORDER BY i.itemID
    """
    if 'deletedItems' not in tables:
        q = q.replace('WHERE i.itemID NOT IN (SELECT itemID FROM deletedItems)', '')
    cur.execute(q)
    rows = cur.fetchall()

    # 字段值（一次全取，避免逐条查询）
    fields = {}
    cur.execute("""
      SELECT id.itemID, f.fieldName, idv.value
      FROM itemData id
      JOIN fields f ON f.fieldID = id.fieldID
      JOIN itemDataValues idv ON idv.valueID = id.valueID
      WHERE f.fieldName IN ('title','DOI','url','date','publicationTitle','extra')
    """)
    for iid, fn, val in cur.fetchall():
        fields.setdefault(iid, {})[fn] = val

    # 作者
    creators = {}
    if 'itemCreators' in tables and 'creators' in tables:
        cur.execute("""
          SELECT ic.itemID, c.firstName, c.lastName, ct.creatorType
          FROM itemCreators ic
          JOIN creators c ON c.creatorID = ic.creatorID
          LEFT JOIN creatorTypes ct ON ct.creatorTypeID = ic.creatorTypeID
          ORDER BY ic.itemID, ic.orderIndex
        """)
        for iid, fn, ln, ct in cur.fetchall():
            if (ct or 'author') != 'author':
                continue
            nm = ' '.join(x for x in (fn, ln) if x).strip()
            if nm:
                creators.setdefault(iid, []).append(nm)

    out = []
    for iid, tn, key in rows:
        if tn in ('attachment', 'note', 'annotation'):
            continue                      # **不是文献条目**
        d = fields.get(iid) or {}
        doi = d.get('DOI')
        if not doi:
            mm = RE_DOI.search(' '.join(str(d.get(k) or '')
                                        for k in ('url', 'extra')))
            doi = mm.group(0) if mm else None
        out.append({
            'zotero_item_id': iid, 'zotero_key': key, 'item_type': tn,
            'title': d.get('title'), 'date': d.get('date'),
            'doi': doi, 'url': d.get('url'),
            'publication': d.get('publicationTitle'),
            # **只从明确的地方取**（见 `extract_arxiv` 的注释）
            'arxiv_id': extract_arxiv(d.get('DOI'), d.get('url'), d.get('extra')),
            'authors': creators.get(iid) or [],
        })
    return out


def make_test_db(path):
    """造一个**真实 schema 子集**的测试库（3 条：有 arXiv / 只有 DOI / 都没有）。"""
    p = Path(path)
    if p.exists():
        p.unlink()
    con = sqlite3.connect(str(p))
    cur = con.cursor()
    # **按真实 Zotero 的表结构**（只看 M4 用到的那些）
    cur.executescript("""
    CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
    CREATE TABLE items (itemID INTEGER PRIMARY KEY, itemTypeID INT, key TEXT);
    CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
    CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value TEXT);
    CREATE TABLE itemData (itemID INT, fieldID INT, valueID INT);
    CREATE TABLE creators (creatorID INTEGER PRIMARY KEY, firstName TEXT, lastName TEXT);
    CREATE TABLE creatorTypes (creatorTypeID INTEGER PRIMARY KEY, creatorType TEXT);
    CREATE TABLE itemCreators (itemID INT, creatorID INT, creatorTypeID INT, orderIndex INT);
    CREATE TABLE deletedItems (itemID INTEGER PRIMARY KEY);
    """)
    cur.executemany('INSERT INTO itemTypes VALUES (?,?)',
                    [(1, 'journalArticle'), (2, 'preprint'), (3, 'conferencePaper'),
                     (4, 'attachment')])
    cur.executemany('INSERT INTO fields VALUES (?,?)',
                    [(1, 'title'), (2, 'DOI'), (3, 'url'), (4, 'date'),
                     (5, 'publicationTitle'), (6, 'extra')])
    cur.executemany('INSERT INTO creatorTypes VALUES (?,?)',
                    [(1, 'author'), (2, 'editor')])
    cur.executemany('INSERT INTO items VALUES (?,?,?)',
                    [(10, 1, 'AAA111'), (11, 2, 'BBB222'), (12, 1, 'CCC333')])
    # 标题 + 字段
    vals = [
        (10, 1, 'A Paper Zotero Has, In Our Corpus'),
        (10, 2, '10.48550/arXiv.2401.04507'),
        (10, 4, '2024-01-09'),
        (11, 1, 'A Paper Zotero Has, Only A DOI'),
        (11, 2, '10.1145/3571234.3579999'),
        (11, 5, 'Some Journal'),
        (12, 1, 'A Paper With Neither DOI Nor arXiv'),
        (12, 3, 'https://example.org/x'),
    ]
    for i, (iid, fid, v) in enumerate(vals, start=1):
        cur.execute('INSERT INTO itemDataValues VALUES (?,?)', (i, v))
        cur.execute('INSERT INTO itemData VALUES (?,?,?)', (iid, fid, i))
    cur.executemany('INSERT INTO creators VALUES (?,?,?)',
                    [(1, 'Xiaxia', 'Wang'), (2, 'Gong', 'Cheng')])
    cur.executemany('INSERT INTO itemCreators VALUES (?,?,?,?)',
                    [(10, 1, 1, 0), (10, 2, 1, 1), (11, 1, 1, 0)])
    con.commit()
    con.close()
    return p


def main():
    ap = argparse.ArgumentParser(description='M4 读 Zotero（**只读**）')
    ap.add_argument('--db', default=str(DEFAULT_DB))
    ap.add_argument('--out', default=str(OUT))
    ap.add_argument('--make-test-db', default='', help='造一个测试库并退出')
    a = ap.parse_args()
    t0 = time.time()

    if a.make_test_db:
        p = make_test_db(a.make_test_db)
        print(f'  ✅ 已造测试库 {p}（3 条：有 arXiv / 只有 DOI / 都没有）')
        return 0

    print('=' * 92)
    print('M4 · Zotero 互操作（读 `zotero.sqlite`，**只读**）')
    print('=' * 92)
    print(f'  库：{a.db}')
    try:
        con = connect_ro(a.db)
    except Exception as e:
        # ⚠️ **"读不到"与"0 条"是两件事** —— 这里必须报错，不能返回空结果
        print(f'  ❌ 打不开：{type(e).__name__}: {e}')
        return 1
    try:
        items = read_items(con)
    except Exception as e:
        print(f'  ❌ 读取失败：{type(e).__name__}: {e}')
        return 1
    finally:
        con.close()
    print(f'  ★ 读到 **{len(items)} 条**文献条目')
    print()

    if not items:
        print('  ⚠️ **库是空的**（Zotero 装了但没条目）——')
        print('     这**不是读失败**，而是"用户还没往 Zotero 里放东西"。')
        print('     ⇒ 想验证这段代码，用 `--make-test-db` 造一个测试库。')

    # 与本地语料对比
    reg = json.loads(REG.read_text(encoding='utf-8')) if REG.exists() else {'papers': []}
    local_doi = {str(p.get('doi') or '').replace('https://doi.org/', '').lower()
                 for p in (reg.get('papers') or []) if p.get('doi')}
    local_arx = {re.sub(r'v\d+$', '', str(p['arxiv_id']))
                 for p in (reg.get('papers') or []) if p.get('arxiv_id')}

    def status(it):
        d = (it.get('doi') or '').lower()
        aid = re.sub(r'v\d+$', '', it['arxiv_id']) if it.get('arxiv_id') else None
        if d and d in local_doi:
            return 'in_corpus'
        if aid and aid in local_arx:
            return 'in_corpus'
        if aid:
            return 'candidate'          # **有 arXiv id ⇒ 可以下载并抽取**
        return 'metadata_only'

    for it in items:
        it['status'] = status(it)
    dist = {}
    for it in items:
        dist[it['status']] = dist.get(it['status'], 0) + 1
    if items:
        print(f'  ★ 与本地语料对比：{dist}')
        print()
        for it in items[:12]:
            mark = {'in_corpus': '✓', 'candidate': '★', 'metadata_only': '·'}[it['status']]
            print(f"    {mark} [{it['status']:<13}] {str(it.get('title'))[:50]}")
            print(f"        {str(it.get('date'))[:10]:<12}"
                  f"{'arXiv:' + it['arxiv_id'] if it.get('arxiv_id') else ''}"
                  f"{'  doi:' + it['doi'][:34] if it.get('doi') else ''}")
        if len(items) > 12:
            print(f'    …另有 {len(items) - 12} 条')
        print()
        print('  ★ `candidate` 的那些是**最有用的一类**：')
        print('     Zotero 里有、有 arXiv id、而**图谱里没有** ⇒ **可以直接下载并抽取**。')

    payload = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'purpose': 'M4：Zotero 互操作（读 zotero.sqlite，只读）',
        'db': str(a.db), 'n_items': len(items), 'distribution': dist,
        'design': {
            'read_only':
                '**只读打开**：`file:...?mode=ro&immutable=1`。'
                '那是**用户的文献库**，而且 **Zotero 可能正开着**。'
                '**本脚本一条 INSERT/UPDATE/DELETE 都没有。**',
            'not_guessing_schema':
                'Zotero 的字段是 **EAV 结构**（`itemData` + `fields` + `itemDataValues`），'
                '**直接读表名猜字段必错**（第一版探针已验证）。',
            'read_failure_is_not_empty':
                '**"0 条"与"读不到"是两件完全不同的事** —— '
                '库不在/被锁/schema 变了 ⇒ **如实报错，不返回空结果**。',
            'half_already_done':
                '**M4 的"导出侧"已在 M5 里做完**：`export_citations.py` 导出的 '
                '`.ris` 与 `.csl.json` **Zotero 两种都能直接吃**。'
                '本脚本补的是另一半（把 Zotero 已有的读出来对比）。',
        },
        'items': items,
        'elapsed_s': round(time.time() - t0, 1),
    }
    outp = Path(a.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n  已写出 {outp}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
