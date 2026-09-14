# -*- coding: utf-8 -*-
"""用项目自带的多人人工金标，评测两个抽取通道的 P/R/F1。

**为什么这个脚本才对**：用户早就要求「去找那些有人工判读的结果，用那些东西去走」，
而不是让他自己判。`data/gold/` 里一直躺着这套东西，此前从未被用于评测：

  · `annotation-A/B/C/GLM.json` —— **四个独立标注者**（strategy: sequential-full-pass）
  · `agreement-*.json`          —— 一致性（实体层 α 0.2312 / 关系层 α −0.0119）
  · `gold-*.json`               —— **仲裁后的金标**
        method: "multi-annotator independent labeling + majority vote + quote verification"
        每条带 votes / voters / quote_verified，另有 disputed_* 与 arbitration 记录
  · `channel-{local,cloud}-<paper>.partial.jsonl` —— 待评的抽取结果
  · `WORKSHEET-*.md` / `SOURCE-*.txt` —— 标注工作表与原文

`gold` 只有两篇（2012.00456v1 12 页 / 2401.04507v1 10 页），而 `channel-local-*`
两篇都有、`channel-cloud-*` 覆盖另外几篇 —— 所以**能对上的组合就在这里评测**，
对不上的如实标注为"无金标，不评测"，绝不拿别的篇章凑数。

口径（务必写清，否则数字会被误读）：
  · 实体：金标接受集 vs 抽取去重后的实体名集合，按 NFKD 折叠 + 生产判据归一后比。
  · 关系：金标接受集 vs 抽取去重后的 (source,type,target)，三个字段都归一后比。
  · **金标的关系层一致性 α = −0.0119**（四人几乎不重合，135/200 是单票被丢弃）——
    所以关系层的 P/R/F1 分母本身不稳，报告里必须同时给出这个前提，
    不能只甩一个 F1 出来。

用法：
  python tools/gold_eval.py                    # 所有能对上的组合
  python tools/gold_eval.py --json             # 机器可读
"""
import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / 'data' / 'gold'
sys.path.insert(0, str(ROOT / 'src'))


def fold(name: str) -> str:
    """NFKD 折叠 + 小写 + 去非字母数字。与生产判据同源。"""
    if not name:
        return ''
    n = unicodedata.normalize('NFKD', str(name))
    n = ''.join(ch for ch in n if not unicodedata.combining(ch))
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]+', '', n.lower())


def load_gold(paper):
    p = GOLD / f'gold-{paper}.json'
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding='utf-8'))


def load_channel(paper, mode):
    """读某通道在某一篇上的抽取结果。支持 .partial.jsonl 与 .json 两种形态。"""
    cands = [GOLD / f'channel-{mode}-{paper}.partial.jsonl',
             GOLD / f'channel-{mode}-{paper}.json']
    for p in cands:
        if not p.exists():
            continue
        ents, rels = {}, set()
        if p.suffix == '.jsonl':
            for line in p.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                for n in rec.get('nodes') or []:
                    if n.get('name'):
                        ents.setdefault(fold(n['name']), n.get('type'))
                for e in rec.get('edges') or []:
                    s, t = e.get('source'), e.get('target')
                    ty = e.get('type')
                    if s and t and ty:
                        rels.add((fold(s), str(ty).upper(), fold(t)))
        else:
            d = json.loads(p.read_text(encoding='utf-8'))
            for n in (d.get('nodes') or d.get('entities') or []):
                if n.get('name'):
                    ents.setdefault(fold(n['name']), n.get('type'))
            for e in (d.get('edges') or d.get('relations') or []):
                s, t, ty = e.get('source'), e.get('target'), e.get('type')
                if s and t and ty:
                    rels.add((fold(s), str(ty).upper(), fold(t)))
        return {'path': p.name, 'entities': ents, 'relations': rels}
    return None


def gold_sets(g):
    ents = {}
    for e in g.get('entities') or []:
        if e.get('name'):
            ents[fold(e['name'])] = e.get('type')
    rels = set()
    for r in g.get('relations') or []:
        s = r.get('source') or r.get('head')
        t = r.get('target') or r.get('tail')
        ty = r.get('type') or r.get('relation')
        if s and t and ty:
            rels.add((fold(s), str(ty).upper(), fold(t)))
    return ents, rels


def rel_key(r):
    s = r.get('source') or r.get('head')
    t = r.get('target') or r.get('tail')
    ty = r.get('type') or r.get('relation')
    if s and t and ty:
        return (fold(s), str(ty).upper(), fold(t))
    return None


def relation_golds(paper):
    """关系层的**三档口径**。

    为什么需要三档：`gold-*.json` 是**多数票**结果，而实测四人一致性
    α = −0.0119、200 个关系单元里 135 个只有一票被丢弃。于是「抽取的边
    不在金标里」既可能是真错、也可能是被多数票机制丢掉了。三档一起报，
    才能看出结论对口径有多敏感。

      · strict —— `gold.relations`（金标自身的多数票结果）
      · mid    —— 四份标注里**至少 2 票**的边
      · union  —— 四份标注里**至少 1 票**的边

    **按 paper_id 选标注文件，不硬编码文件名**：实测 `annotation-{A,B,C,GLM}.json`
    属于 `2012.00456v1`，而 `annotation2-{A,B,C,GLM}.json` 属于 `2401.04507v1`。
    第一版硬编码了 `annotation-`，结果对第二篇拼出了空集、宽松档 F1 变成 0
    （比严格档还低 —— 宽松档是严格档的超集，这在逻辑上不可能，正是靠这个
    矛盾才发现的）。
    """
    g = load_gold(paper)
    if g is None:
        return None
    out = {'strict': set(), 'mid': set(), 'union': set()}
    for r in g.get('relations') or []:
        k = rel_key(r)
        if k:
            out['strict'].add(k)

    votes = {}
    for p in sorted(GOLD.glob('annotation*.json')):
        try:
            d = json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            continue
        if d.get('paper_id') != paper:      # 只吃属于本篇的标注
            continue
        for r in d.get('relations') or []:
            k = rel_key(r)
            if k:
                votes[k] = votes.get(k, 0) + 1
    out['union'] = set(votes)
    out['mid'] = {k for k, v in votes.items() if v >= 2}
    out['_votes'] = votes
    out['_annotators'] = [p.name for p in sorted(GOLD.glob('annotation*.json'))
                          if (json.loads(p.read_text(encoding='utf-8')).get('paper_id') == paper)]
    return out


def prf(tp, fp, fn):
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return round(p, 4), round(r, 4), round(f, 4)


def eval_pair(paper, mode):
    g = load_gold(paper)
    if g is None:
        return None
    ch = load_channel(paper, mode)
    if ch is None:
        return None
    ge, gr = gold_sets(g)
    ce, cr = ch['entities'], ch['relations']

    tp_e = len(set(ge) & set(ce))
    fp_e = len(set(ce) - set(ge))
    fn_e = len(set(ge) - set(ce))
    pe, re_, fe = prf(tp_e, fp_e, fn_e)

    # 关系层三档口径：金标 α=−0.0119、135/200 单票被丢，
    # 「不在金标里」既可能是真错、也可能是多数票机制的产物。
    rgs = relation_golds(paper) or {'strict': set(), 'mid': set(), 'union': set()}
    rel_by_caliber = {}
    for cal in ('strict', 'mid', 'union'):
        gset = rgs.get(cal, set())
        tp = len(gset & cr)
        fp = len(cr - gset)
        fn = len(gset - cr)
        p_, r_, f_ = prf(tp, fp, fn)
        rel_by_caliber[cal] = {'gold_n': len(gset), 'tp': tp, 'fp': fp, 'fn': fn,
                              'P': p_, 'R': r_, 'F1': f_}

    rel = rel_by_caliber['strict']

    return {
        'paper': paper, 'mode': mode, 'file': ch['path'],
        'pages': len(g.get('source_pages') or []) and
                 (g['source_pages'][1] - g['source_pages'][0] + 1) or None,
        'gold': {'entities': len(ge), 'relations': len(rgs.get('strict', set()))},
        'pred': {'entities': len(ce), 'relations': len(cr)},
        'entity': {'tp': tp_e, 'fp': fp_e, 'fn': fn_e, 'P': pe, 'R': re_, 'F1': fe},
        'relation': {'tp': rel['tp'], 'fp': rel['fp'], 'fn': rel['fn'],
                     'P': rel['P'], 'R': rel['R'], 'F1': rel['F1']},
        'relation_calibers': rel_by_caliber,
        'gold_meta': {
            'annotators': g.get('annotators'),
            'method': g.get('method'),
            'stats': g.get('stats'),
        },
    }


def main():
    ap = argparse.ArgumentParser(description='用多人人工金标评测通道 P/R/F1')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    papers = sorted({p.name[len('gold-'):-len('.json')]
                     for p in GOLD.glob('gold-*.json')})
    out = []
    for paper in papers:
        for mode in ('cloud', 'local'):
            r = eval_pair(paper, mode)
            if r:
                out.append(r)

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print('═' * 78)
    print('用项目自带多人人工金标评测通道（数据：data/gold/）')
    print('═' * 78)
    if not out:
        print('  没有可对上的「金标 × 通道」组合')
        return 1

    for r in out:
        print(f"\n【{r['paper']}】通道 {r['mode']}   （{r['file']}）")
        st = r['gold_meta'].get('stats') or {}
        print(f"  金标 {r['gold']['entities']} 实体 / {r['gold']['relations']} 关系"
              f"　标注者 {r['gold_meta'].get('annotators')}")
        if st:
            print(f"  金标质量：多数票一致率 {st.get('majority_agreement_rate')} · "
                  f"两两 Jaccard {st.get('mean_pairwise_jaccard')}")
        print(f"  抽取 {r['pred']['entities']} 实体 / {r['pred']['relations']} 关系")
        e, rr = r['entity'], r['relation']
        print(f"  实体  P={e['P']:<7} R={e['R']:<7} F1={e['F1']:<7}"
              f"(TP {e['tp']} / FP {e['fp']} / FN {e['fn']})")
        print('  关系  三档口径并列（金标 α=−0.0119，结论对口径很敏感）：')
        names = {'strict': '严格（多数票）', 'mid': '中间（≥2 票）',
                 'union': '宽松（≥1 票）'}
        for cal in ('strict', 'mid', 'union'):
            c = (r.get('relation_calibers') or {}).get(cal)
            if not c:
                continue
            print(f"    {names[cal]:<16} 金标 {c['gold_n']:>4} 条  "
                  f"P={c['P']:<7} R={c['R']:<7} F1={c['F1']:<7}"
                  f"(TP {c['tp']} / FP {c['fp']} / FN {c['fn']})")

    print('\n' + '─' * 78)
    print('⚠️ 必须一并说明的前提（否则数字会被误读）')
    print('─' * 78)
    print('  · 金标关系层的一致性极低：四人对关系标注的 Krippendorff α = −0.0119，')
    print('    135/200 个关系单元只有一票、按多数票规则被丢弃。')
    print('    ⇒ 「抽取的边不在金标里」既可能是真错，也可能是被多数票机制丢掉的。')
    print('      所以上表并列三档：严格 / 中间（≥2 票）/ 宽松（≥1 票）。')
    print('      **严格档的绝对数字不该单独引用**，要看三档的走势。')
    print('  · 实体层一致性尚可：α = 0.2312（别名感知 0.2849），多票单元占多数。')
    print('  · 金标只有 2 篇（2012.00456v1 / 2401.04507v1），样本量小，')
    print('    只能作方向性判断，不能当作全库结论。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
