# -*- coding: utf-8 -*-
r"""H6 · KGE 链接预测（PyKeen TransE / RotatE）—— **并顺带完成 H7 的"数据支撑"**。

## 这个脚本要回答什么

TODO 里两条是连着的：

    H6  KGE 链接预测（PyKeen TransE/RotatE）
        「做图谱补全候选，**喂人工修正队列**」
    H7  GNN 的"数据支撑"论证
        纪律原文：「把'没做'**升格为'评估过并有数据支撑的拒绝'**」
        —— 但**"数据支撑"全项目搜不到**（决策写了，论证缺）

⇒ 所以本脚本不只是"跑个 KGE"，它要给出一组数：
**（a）这个图谱在链接预测上到底能到什么水平；（b）离"能用"差多远。**

## 两种划分，这是本脚本最要紧的设计

| 划分 | 训练/测试怎么分 | 回答什么问题 |
|---|---|---|
| **A · 按边随机** | 随机切分边 | 文献里报的那个数（**乐观**） |
| **B · 按论文** | **测试集只含训练时完全没出现过的论文** | **换一篇新论文还能不能预测** |

**B 才是这个项目真正关心的**：图谱是**逐篇论文抽出来的**，
而它的用途是"读一篇新论文时，帮我补出可能有但没抽到的关系"。
**A 的分数会被"同篇论文里的近邻边"抬高** —— 那些边在测试时仍然见过同篇的其它边。

**⇒ 两个数都报，并明确说清差别。** 这本身就是一条"数据支撑"。

## 为什么这能回答 H7

GNN 相对 KGE 的卖点是"利用邻域结构"更好。但**它的前提是数据里有足够多、
足够密、足够一致的邻域**。本脚本给出的两组数正好能检验这个前提：

  · **划分 B 的 MRR** —— 若跨论文泛化本来就差，
    那上 GNN 也不会凭空变好（**面对的是同一份稀疏性**）；
  · **测试集里"训练时没见过的实体"占比** ——
    图谱是**逐篇论文长出来的**，新论文必然带来大量新实体；
    **对没见过的实体做链接预测，本质上是"用结构猜"，而新实体恰恰没有结构**。

**⇒ 有了这些数，"不上 GNN"就从"没做"变成"评估过并有数据支撑的拒绝"。**

> ⚠️ **一个被数据否掉的论据**（2026-09-16，`--dry-run` 打的脸）：
> 我原本还想说"关系类型极度长尾 ⇒ GNN 的额外参数更容易过拟合"。
> **实测不成立**：15 类关系里**没有任何一类的边数 < 50**，
> 最小的类也有 50+ 条。**长尾论据作废，不写进结论。**
> （这条留着是为了记住：**先量再讲**。）

用法：
    python tools/kge_link_prediction.py                  # 两种划分各跑 TransE + RotatE
    python tools/kge_link_prediction.py --models TransE  # 只跑一个
    python tools/kge_link_prediction.py --epochs 20 --dry-run   # 只看数据切分，不训练
"""
import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))

GRAPHS = ROOT / 'data' / 'corpus' / 'graphs'
OUT = ROOT / 'data' / 'quality' / 'kge-link-prediction.json'

import ontology as O  # noqa: E402

SEED = 20260916


def parse_args():
    ap = argparse.ArgumentParser(description='KGE 链接预测（H6 + H7 的数据支撑）')
    ap.add_argument('--models', nargs='+', default=['TransE', 'RotatE'])
    ap.add_argument('--epochs', type=int, default=100)
    ap.add_argument('--embedding-dim', type=int, default=128)
    ap.add_argument('--batch-size', type=int, default=1024)
    ap.add_argument('--test-ratio', type=float, default=0.1)
    ap.add_argument('--held-out-papers', type=int, default=3,
                    help='划分 B 里整篇留作测试的论文数')
    ap.add_argument('--device', default=None, help='cpu / cuda（默认自动）')
    ap.add_argument('--out', default=str(OUT))
    ap.add_argument('--dry-run', action='store_true', help='只做数据切分与统计，不训练')
    return ap.parse_args()


ARGS = parse_args()


def load_triples():
    """读 17 篇图快照 → [(head, rel, tail, paper_id)]。

    **按篇读而不是用 `graph_source.load_graph()`**：后者的返回是汇总图，
    丢了"哪条边来自哪篇"——而划分 B 正需要那个信息。
    同时**手动做与 `graph_source` 同口径的字段错位修正**，免得两个脚本口径不一致。
    """
    triples = []
    per_paper = {}
    for f in sorted(GRAPHS.glob('*.json')):
        pid = f.stem
        try:
            d = json.loads(f.read_text(encoding='utf-8-sig'))
        except Exception as e:
            print(f'  ⚠️ {pid} 读失败：{type(e).__name__}')
            continue
        names = set()
        for n in (d.get('nodes') or []):
            nm = n.get('name') or n.get('id')
            if nm:
                names.add(str(nm))
        cnt = 0
        for e in (d.get('edges') or []):
            s, t = str(e.get('source') or ''), str(e.get('target') or '')
            r = str(e.get('type') or '')
            # 字段错位的边：description 里才是真类型（与 graph_source 同规则）
            desc = str(e.get('description') or '').strip()
            if r not in O.RELATION_TYPES and desc in O.RELATION_TYPES:
                r = desc
            elif r not in O.RELATION_TYPES:
                r = 'RELATED_TO'          # 越界谓词统一改写（同 graph_source）
            if s and t and r:
                triples.append((s, r, t, pid))
                cnt += 1
        per_paper[pid] = {'nodes': len(names), 'edges': cnt}
    return triples, per_paper


def split_by_edge(triples, ratio, rng):
    """划分 A：随机按边切（文献常规做法）。"""
    idx = list(range(len(triples)))
    rng.shuffle(idx)
    k = int(len(idx) * ratio)
    test_i = set(idx[:k])
    train = [t for i, t in enumerate(triples) if i not in test_i]
    test = [t for i, t in enumerate(triples) if i in test_i]
    return train, test, {'kind': 'by-edge', 'note': f'随机切 {ratio:.0%} 的边作测试'}


def split_by_paper(triples, n_papers, rng):
    """划分 B：整篇留作测试 —— **测试集的论文在训练时完全没出现过**。"""
    papers = sorted({t[3] for t in triples})
    rng.shuffle(papers)
    test_papers = set(papers[:n_papers])
    train = [t for t in triples if t[3] not in test_papers]
    test = [t for t in triples if t[3] in test_papers]
    return train, test, {
        'kind': 'by-paper',
        'held_out': sorted(test_papers),
        'note': f'整篇留 {n_papers} 篇作测试（训练时完全没出现）',
    }


def to_factory(triples, entity_to_id=None, rel_to_id=None):
    """转 PyKeen 的 TriplesFactory。

    ⚠️ **`dtype` 必须是字符串**：`from_labeled_triples` 只接受 `<U` 类型的数组，
    给 `dtype=object` 会在内部转 tensor 时报
    `TypeError: can't convert np.ndarray of type numpy.object_`。
    """
    import numpy as np
    arr = np.array([[h, r, t] for h, r, t, _p in triples], dtype=str)
    from pykeen.triples import TriplesFactory
    if entity_to_id is not None:
        # ⚠️ **必须显式给 mapping，且新实体要能加进来** —— 见 `restrict_to_known()`
        return TriplesFactory.from_labeled_triples(
            arr, entity_to_id=dict(entity_to_id), relation_to_id=dict(rel_to_id))
    return TriplesFactory.from_labeled_triples(arr)


def restrict_to_known(test, train):
    """把测试集里"**两端至少有一端是训练时没见过的新实体**"的边剔出去。

    ## 为什么要剔除，以及它意味着什么

    KGE（TransE/RotatE）给每个实体学一个**嵌入向量**。
    **训练时没出现过的实体根本没有嵌入**，模型对它的打分是随机的 ——
    把它们留在测试集里，测的就不是"模型好不好"，而是"多少边碰巧撞上了随机数"。

    这是 link prediction 的标准区分：**transductive**（测试实体都在训练集里）
    与 **inductive**（允许新实体）。后者需要专门的方法（如用文本描述生成嵌入），
    **TransE/RotatE 这类纯结构模型做不了**。

    ## 但被剔掉的边**本身就是一个结论**

    ⇒ 所以本函数把剔除的条数**报出来**：
    **划分 B 里有多大比例的边涉及新实体**，直接说明
    "图谱逐篇长出来之后，新论文的实体有多少是旧图完全没见过的"。
    **这个比例越高，"纯结构方法"就越不够用** —— 那是 H7 要的数据支撑。
    """
    known_e = {t[0] for t in train} | {t[2] for t in train}
    keep, dropped = [], 0
    for h, r, t, p in test:
        if h in known_e and t in known_e:
            keep.append((h, r, t, p))
        else:
            dropped += 1
    return keep, dropped



def run_one(model_name, train, test, meta, tag):
    """训练一个模型并评估。返回指标字典。"""
    import torch
    from pykeen.pipeline import pipeline
    from pykeen.evaluation import RankBasedEvaluator

    # ① 先剔掉"涉及训练未见实体"的测试边，并**记下剔了多少**
    #    （对 by-edge 划分通常是 0；对 by-paper 划分会很多 —— 那正是结论之一）
    test_kept, test_dropped = restrict_to_known(test, train)

    tr = to_factory(train)
    te = to_factory(test_kept, entity_to_id=tr.entity_to_id, rel_to_id=tr.relation_to_id)
    unseen_e = len([e for e in te.entity_to_id if e not in tr.entity_to_id])
    unseen_r = len([r for r in te.relation_to_id if r not in tr.relation_to_id])

    pct = (test_dropped / len(test) * 100) if test else 0
    print(f'    [{tag}/{model_name}] 训练 {tr.num_triples} 条 · 测试 {te.num_triples} 条 · '
          f'实体 {tr.num_entities} · 关系 {tr.num_relations}')
    if test_dropped:
        print(f'        ⚠️ 剔除 {test_dropped}/{len(test)} 条测试边（{pct:.1f}%）'
              f'—— **它们涉及训练时没见过的实体，纯结构模型给不出嵌入**')

    t0 = time.time()
    result = pipeline(
        training=tr, testing=te, model=model_name,
        model_kwargs=dict(embedding_dim=ARGS.embedding_dim),
        optimizer='Adam', optimizer_kwargs=dict(lr=0.001),
        training_kwargs=dict(num_epochs=ARGS.epochs, batch_size=ARGS.batch_size,
                             use_tqdm=False),
        random_seed=SEED, device=ARGS.device or ('cuda' if torch.cuda.is_available() else 'cpu'),
        evaluator=RankBasedEvaluator,
        evaluation_kwargs=dict(use_tqdm=False),
    )
    elapsed = time.time() - t0
    m = result.metric_results.to_dict()
    both = (m.get('both') or {}).get('realistic') or (m.get('both') or {}).get('optimistic') or {}
    def g(k):
        v = both.get(k)
        if isinstance(v, dict):
            v = v.get('mean')
        return None if v is None else float(v)
    return {
        'model': model_name, 'split': meta['kind'], 'elapsed_s': round(elapsed, 1),
        'n_train': tr.num_triples, 'n_test': te.num_triples,
        'n_test_dropped_new_entity': test_dropped,
        'pct_test_dropped': round(pct, 2),
        'n_entities': tr.num_entities, 'n_relations': tr.num_relations,
        'unseen_entities_in_test': unseen_e, 'unseen_relations_in_test': unseen_r,
        'mrr': g('inverse_harmonic_mean_rank'),
        'hits@1': g('hits_at_1'), 'hits@3': g('hits_at_3'), 'hits@10': g('hits_at_10'),
    }


def main():
    t_all = time.time()
    print('=' * 84)
    print('H6 · KGE 链接预测（TransE / RotatE）—— 兼作 H7 的"数据支撑"')
    print('=' * 84)

    triples, per_paper = load_triples()
    ents = {t[0] for t in triples} | {t[2] for t in triples}
    rels = Counter(t[1] for t in triples)
    print(f'  三元组 {len(triples)} 条 · 实体 {len(ents)} 个 · 关系 {len(rels)} 类 · '
          f'来自 {len(per_paper)} 篇')
    top = rels.most_common(3)
    print(f'  关系分布：前 3 类占 {sum(c for _r, c in top) / len(triples):.1%}'
          f'（{", ".join(f"{r} {c}" for r, c in top)}）')
    sparse = [r for r, c in rels.items() if c < 50]
    print(f'  **长尾**：{len(sparse)} 类关系的边数 < 50，合计 '
          f'{sum(rels[r] for r in sparse)} 条（{sum(rels[r] for r in sparse) / len(triples):.1%}）')
    print()

    rng = random.Random(SEED)
    splits = [split_by_edge(triples, ARGS.test_ratio, rng)]
    if len(per_paper) > ARGS.held_out_papers:
        splits.append(split_by_paper(triples, ARGS.held_out_papers, random.Random(SEED)))

    for train, test, meta in splits:
        print(f'  [{meta["kind"]}] {meta["note"]}')
        print(f'      训练 {len(train)} 条 · 测试 {len(test)} 条')
        if meta.get('held_out'):
            print(f'      留出论文：{", ".join(meta["held_out"])}')
    print()

    if ARGS.dry_run:
        print('  （--dry-run：只做数据切分与统计，不训练）')
        return 0

    rows = []
    for train, test, meta in splits:
        for model in ARGS.models:
            try:
                rows.append(run_one(model, train, test, meta, meta['kind']))
            except Exception as e:
                print(f'    ❌ [{meta["kind"]}/{model}] 失败：{type(e).__name__}: {e}')
                rows.append({'model': model, 'split': meta['kind'],
                             'error': f'{type(e).__name__}: {e}'})

    print()
    print('=' * 84)
    print('结果（realistic 设定）')
    print('=' * 84)
    print(f'  {"划分":<10}{"模型":<9}{"MRR":>8}{"Hits@1":>9}{"Hits@3":>9}{"Hits@10":>9}'
          f'{"耗时":>8}{"新实体边%":>11}')
    for r in rows:
        if r.get('error'):
            print(f'  {r["split"]:<10}{r["model"]:<9}  失败：{r["error"][:52]}')
            continue
        f = lambda x: '—' if x is None else f'{x:.4f}'   # noqa: E731
        print(f'  {r["split"]:<10}{r["model"]:<9}{f(r["mrr"]):>8}{f(r["hits@1"]):>9}'
              f'{f(r["hits@3"]):>9}{f(r["hits@10"]):>9}{r["elapsed_s"]:>6.0f}s'
              f'{r["pct_test_dropped"]:>10.1f}%')
    print()
    print('  「新实体边%」= 测试集里**涉及训练未见实体**的边所占比例 ——')
    print('  这些边**被剔除了**（纯结构模型给不出嵌入），而它本身就是结论：')
    print('  **比例越高，说明这个图谱越依赖"新实体"，纯结构方法越不够用。**')

    payload = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'purpose': 'H6 链接预测 + H7「GNN 是否值得做」的数据支撑',
        'design': {
            'split_A_by_edge': '随机切边（文献常规，乐观）',
            'split_B_by_paper': '**整篇留作测试** —— 训练时完全没出现过的论文，'
                                '这才是"读一篇新论文时能不能补关系"的真实设定',
            'eval_setting': 'realistic（PyKeen 默认的过滤式排名）',
            'seed': SEED,
        },
        'data': {
            'triples': len(triples), 'entities': len(ents),
            'relations': len(rels), 'papers': len(per_paper),
            'relation_counts': dict(rels.most_common()),
            'long_tail_relations_lt50': len(sparse),
            'long_tail_edges': sum(rels[r] for r in sparse),
            'per_paper': per_paper,
        },
        'config': {'models': ARGS.models, 'epochs': ARGS.epochs,
                   'embedding_dim': ARGS.embedding_dim, 'batch_size': ARGS.batch_size,
                   'test_ratio': ARGS.test_ratio, 'held_out_papers': ARGS.held_out_papers},
        'results': rows,
        'splits': [m for _tr, _te, m in splits],
        'elapsed_s': round(time.time() - t_all, 1),
    }
    Path(ARGS.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                              encoding='utf-8')
    print()
    print(f'  已写 {Path(ARGS.out).name}（耗时 {payload["elapsed_s"]}s）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
