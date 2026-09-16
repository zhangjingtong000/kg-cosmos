# -*- coding: utf-8 -*-
r"""H2 · Schema 约束消融（开/关对比）—— TODO 里标着「**实验章节核心**」且「**零开发成本**」。

## 消融的是什么（**命名要准确**）

项目已经有一个开关，`src/kg_extractor.py:598`：

    KG_BARE_RELATION_TYPES=0（默认）  prompt 里给「类型名: 语义说明」（换行分隔）
    KG_BARE_RELATION_TYPES=1          prompt 里只给「类型名」（逗号分隔）

⇒ 所以这个消融对比的是「**给不给关系类型的语义说明**」，
**两种都给了类型表**。称它"Schema 开/关"是不准确的 ——
真正的"完全不给类型表"需要改代码，本脚本**不做那个**（也不该顺手做：
那会把「有没有约束」与「约束强不强」两件事混在一起）。

## 为什么它值得做

TODO 说它是「**实验章节核心**」而现状是「**无产物**」。
它直接对应 L4 的判据 —— 「谓词是否落在本体 15 类内」——
而**这个开关恰好就是控制"模型有多知道该选哪一类"**。

**关键指标**：
  · **谓词落在本体内**的比例（非法谓词多 ⇒ 约束没起作用）
  · **`RELATED_TO`（兜底类）占比** —— 兜底越多说明模型越"不知道选哪类"
  · 实体数 / 关系数（约束强会不会压制产出量）

## 为什么必须**两个进程**

`COMBINED_PREFIX` 是**模块级常量、import 时求值** ⇒ 同进程内切不了环境变量。
⇒ 外层脚本调两次 `subprocess`，各带一个环境变量。

用法：
    python tools/schema_ablation.py                          # 用默认源文本前 3000 字符
    python tools/schema_ablation.py --source data/gold/SOURCE-2012.00456v1-p1-12.txt
    python tools/schema_ablation.py --chars 1500 --out data/quality/schema-ablation.json
"""
import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))

DEFAULT_SRC = ROOT / 'data' / 'gold' / 'SOURCE-2012.00456v1-p1-12.txt'
ONCE_DIR = ROOT / 'data' / 'quality' / '_schema-ablation-runs'


def parse_args():
    ap = argparse.ArgumentParser(description='H2 Schema 约束消融')
    ap.add_argument('--source', default=str(DEFAULT_SRC))
    ap.add_argument('--chars', type=int, default=3000)
    # ⚠️ **偏移量不是可有可无的**：第一次跑（offset=0）取到的是**论文的标题页**
    #    （标题 + 作者 + 机构），那里的关系只有 `PRODUCES`/`LOCATED_AT`/`PART_OF` 三种，
    #    两种 prompt 下模型都只能选这几个 ⇒ **开/关没有区分度，两次输出一模一样**。
    #    要测出差异，得从**关系密集的段落**（方法/实验/相关工作）取。
    ap.add_argument('--offset', type=int, default=0,
                    help='从源文本的第几个字符开始取（默认 0）')
    ap.add_argument('--offset-pct', type=float, default=None,
                    help='按比例取（如 0.4 = 从 40% 处开始）；给了它就覆盖 --offset')
    ap.add_argument('--mode', default='local', help='抽取通道（默认 local）')
    ap.add_argument('--out', default=str(ROOT / 'data' / 'quality' / 'schema-ablation.json'))
    # 内层模式：只跑一次抽取，把结果写到 _schema-ablation-runs/<tag>.json
    ap.add_argument('--once', default=None, metavar='TAG',
                    help='内层：跑一次抽取，结果写 _schema-ablation-runs/<TAG>.json')
    return ap.parse_args()


ARGS = parse_args()


# ══════════════════════════════════════════════════════════════════════════
# 内层：跑一次抽取
# ══════════════════════════════════════════════════════════════════════════
def run_once(tag):
    import kg_extractor as K

    src = Path(ARGS.source)
    full = src.read_text(encoding='utf-8', errors='replace')
    off = ARGS.offset
    if ARGS.offset_pct is not None:
        off = int(len(full) * max(0.0, min(1.0, ARGS.offset_pct)))
    text = full[off:off + ARGS.chars]
    bare = os.environ.get('KG_BARE_RELATION_TYPES', '0').strip() in ('1', 'true', 'yes')
    print(f'  [{tag}] bare={bare} · 文本 [{off}:{off + len(text)}] / {len(full)} 字符 '
          f'· 模型 {K.MODELS[ARGS.mode]["text"]}')
    print(f'  [{tag}] prompt 里的关系块（前 120 字符）：'
          f'{K.COMBINED_PREFIX[K.COMBINED_PREFIX.find("Relations"):][:120]!r}')

    t0 = time.time()
    res = K.extract(text, mode=ARGS.mode)
    el = time.time() - t0

    ONCE_DIR.mkdir(parents=True, exist_ok=True)
    out = ONCE_DIR / f'{tag}.json'
    out.write_text(json.dumps({
        'tag': tag, 'bare': bare, 'chars': len(text), 'mode': ARGS.mode,
        'elapsed_s': round(el, 1), 'timing': res.get('timing'),
        'chunks': res.get('chunks'),
        'nodes': res.get('nodes') or [], 'edges': res.get('edges') or [],
    }, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'  [{tag}] ✅ {el:.0f}s · 实体 {len(res.get("nodes") or [])} · '
          f'关系 {len(res.get("edges") or [])} → {out.name}')
    return 0


# ══════════════════════════════════════════════════════════════════════════
# 外层：跑两次 + 对比
# ══════════════════════════════════════════════════════════════════════════
def score(rec):
    """算一次抽取的关键指标。"""
    import ontology as O
    edges = rec.get('edges') or []
    nodes = rec.get('nodes') or []
    types = Counter(str(e.get('type') or '?') for e in edges)
    legal = sum(n for t, n in types.items() if t in O.RELATION_TYPES)
    fallback = types.get('RELATED_TO', 0)
    return {
        'n_nodes': len(nodes),
        'n_edges': len(edges),
        'n_types_used': len(types),
        'type_dist': dict(types.most_common()),
        # ★ 核心指标：谓词落在本体内的比例（=L4 的判据）
        'predicate_in_ontology': round(legal / len(edges), 4) if edges else None,
        # ★ 兜底类占比：越高说明模型越"不知道选哪类"
        'related_to_share': round(fallback / len(edges), 4) if edges else None,
        'illegal_types': sorted(t for t in types if t not in O.RELATION_TYPES),
    }


def main():
    if ARGS.once:
        return run_once(ARGS.once)

    t0 = time.time()
    print('=' * 88)
    print('H2 · Schema 约束消融（关系类型"语义说明"开/关）')
    print('=' * 88)
    print(f'  源文本 {ARGS.source} · 取前 {ARGS.chars} 字符 · 通道 {ARGS.mode}')
    print()

    runs = [('with-sem', '0'), ('bare', '1')]
    for tag, val in runs:
        print(f'── 跑 {tag}（KG_BARE_RELATION_TYPES={val}）' + '─' * 40)
        env = dict(os.environ)
        env['KG_BARE_RELATION_TYPES'] = val
        extra = (['--offset-pct', str(ARGS.offset_pct)] if ARGS.offset_pct is not None
                 else ['--offset', str(ARGS.offset)])
        r = subprocess.run([sys.executable, str(Path(__file__)), '--once', tag,
                            '--source', ARGS.source, '--chars', str(ARGS.chars),
                            '--mode', ARGS.mode] + extra,
                           cwd=str(ROOT), env=env, capture_output=True, text=True)
        for line in (r.stdout or '').splitlines():
            print('  ' + line)
        if r.returncode != 0:
            print(f'  ❌ {tag} 失败（exit {r.returncode}）：{(r.stderr or "")[-300:]}')
            return 1
        print()

    recs = {}
    for tag, _v in runs:
        p = ONCE_DIR / f'{tag}.json'
        if not p.exists():
            print(f'  ❌ 缺 {p}')
            return 1
        recs[tag] = json.loads(p.read_text(encoding='utf-8'))

    sc = {tag: score(r) for tag, r in recs.items()}
    a, b = sc['with-sem'], sc['bare']

    print('=' * 88)
    print('对比')
    print('=' * 88)
    print(f'  {"指标":<28}{"给语义（开）":>16}{"裸列表（关）":>16}')
    print('  ' + '-' * 60)
    rows = [
        ('实体数', 'n_nodes', '{:d}'),
        ('关系数', 'n_edges', '{:d}'),
        ('用到的类型数', 'n_types_used', '{:d}'),
        ('**谓词落在本体内**', 'predicate_in_ontology', '{:.1%}'),
        ('**RELATED_TO 兜底占比**', 'related_to_share', '{:.1%}'),
    ]
    for label, key, fmt in rows:
        va, vb = a.get(key), b.get(key)
        fa = '—' if va is None else fmt.format(va)
        fb = '—' if vb is None else fmt.format(vb)
        print(f'  {label:<28}{fa:>16}{fb:>16}')

    for tag in ('with-sem', 'bare'):
        s = sc[tag]
        print()
        print(f'  ── {tag} 的类型分布（前 8）──')
        for t, n in list(s['type_dist'].items())[:8]:
            flag = '' if t == 'RELATED_TO' or t in sc[tag].get('_legal', []) else ''
            print(f'      {t:<20} {n}')
        if s['illegal_types']:
            print(f'      ⚠️ **本体外的类型**：{s["illegal_types"]}')

    # 结论要基于数，不要基于印象
    print()
    print('  ★ 读数')
    if a['predicate_in_ontology'] is not None and b['predicate_in_ontology'] is not None:
        d = a['predicate_in_ontology'] - b['predicate_in_ontology']
        print(f'      谓词在本体内的比例：给语义 {a["predicate_in_ontology"]:.1%} '
              f'vs 裸列表 {b["predicate_in_ontology"]:.1%}（差 {d:+.1%}）')
    if a['related_to_share'] is not None and b['related_to_share'] is not None:
        d2 = a['related_to_share'] - b['related_to_share']
        print(f'      兜底类占比：给语义 {a["related_to_share"]:.1%} '
              f'vs 裸列表 {b["related_to_share"]:.1%}（差 {d2:+.1%}）')

    payload = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'purpose': 'H2：实验章节核心 —— 关系类型"语义说明"开/关对抽取的影响',
        'caliber': '**消融的是"给不给语义说明"，两种都给了类型表** —— '
                   '称它"Schema 开/关"不准确；真正的"完全不给类型表"需要改代码，本实验不做。',
        'setup': {'source': ARGS.source, 'chars': ARGS.chars, 'mode': ARGS.mode,
                  'env': {'KG_BARE_RELATION_TYPES': '0 = 给语义 / 1 = 裸列表'}},
        'scores': sc,
        'elapsed_s': round(time.time() - t0, 1),
    }
    Path(ARGS.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                              encoding='utf-8')
    print()
    print(f'  已写 {Path(ARGS.out).name}（总耗时 {payload["elapsed_s"]:.0f}s）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
