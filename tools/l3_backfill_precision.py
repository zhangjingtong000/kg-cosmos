"""反建节点的精度抽检（对抗评审的 major 项）。

【为什么必须做】
  从边反建了 4304 个节点，判据是「名字在引用它的边的 evidence 里逐字出现
  + 形态过滤」。但形态过滤很粗，实测已经漏进来句子形态的名字：

      "A class in a triple to predict."

  没有抽检，`4304` 这个数字不能引用，`L3 96.2%` 也失去意义
  （如果反建名里有相当比例根本不是实体，那"补齐端点"就是往图里灌噪声）。

【做法】
  分三层抽样（被 1 条边引用 / 2–4 条 / ≥5 条），每层按名字长度与形态再分层，
  导出**可核验的清单**并用一组启发式给出初判，最后给出分层精度估计。
  这份清单是给人复核的，不是为了自动下结论。
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from idempotent_write import dumps, write_if_changed

GRAPH = ROOT / 'data/corpus/graphs'
OUT = ROOT / 'data/quality/l3-backfill-precision-sample.json'
random.seed(20260912)

# 启发式：判"这个名字像不像一个实体"
SENTENCE_TAIL = re.compile(r'[.!?]$')                 # 以句号/问号收尾
LOOKS_SENTENCE = re.compile(r'\b(is|are|was|were|has|have|can|will|to|of|the|a|an|'
                            r'that|which|where|when|how)\b', re.I)
HAS_LATEX = re.compile(r'[\\$^_{}]|\\[a-zA-Z]+')
HAS_TABLE_JUNK = re.compile(r'[|@#%*=<>~]|\d+\s*pt\b', re.I)
ACRONYM = re.compile(r'^[A-Z][A-Za-z0-9\-]*( [A-Z][A-Za-z0-9\-]*)*$')
NAME_LIKE = re.compile(r"^[A-Z][\w\-'’\.]*( [A-Za-z0-9][\w\-'’\.]*){0,5}$")

# 论文标题与会议全名**是合法的实体**（Document / Event），不能当噪声。
# 初版把 `Retrieval-augmented generation for large language models: A survey`
# 和 `IEEE Conference on Computer Vision and Pattern Recognition`
# 都打成了 noise，于是把 5.6% 的"噪声率"报高了——实际那是抽取出来的正常实体。
PAPER_TITLE = re.compile(
    r'(:|—|–|\ba survey\b|\ban overview\b|\btutorial\b|\breview\b|'
    r'\bfor\b.*\b(with|using|via)\b)', re.I)
VENUE_LIKE = re.compile(
    r'\b(conference|workshop|symposium|journal|proceedings|congress|'
    r'meeting|transactions|letters)\b', re.I)


def classify(name: str) -> str:
    s = name.strip()
    words = s.split()
    if HAS_LATEX.search(s) or HAS_TABLE_JUNK.search(s):
        return 'noise-junk'
    if SENTENCE_TAIL.search(s) and len(words) >= 4:
        return 'noise-sentence'
    # 论文标题 / 会议全名先于"长短语"判断，它们是合法实体
    if PAPER_TITLE.search(s) and len(words) <= 20:
        return 'entity-title'
    if VENUE_LIKE.search(s) and len(words) <= 14:
        return 'entity-venue'
    if len(words) >= 4 and LOOKS_SENTENCE.search(s) and not s.isupper():
        return 'noise-sentence'
    if len(words) >= 9:
        return 'noise-long'
    if NAME_LIKE.match(s) or ACRONYM.match(s):
        return 'entity-like'
    if s.islower() and len(words) == 1:
        return 'noise-lower'
    return 'uncertain'


def main():
    files = sorted(GRAPH.glob('*.json'))
    known = set()
    for f in files:
        d = json.loads(f.read_text(encoding='utf-8'))
        for n in d.get('nodes', []):
            nm = (n.get('name') or '').strip()
            if nm and not n.get('_backfilled'):
                known.add(nm)

    occ = Counter()
    ev_hit = Counter()
    for f in files:
        d = json.loads(f.read_text(encoding='utf-8'))
        for e in d.get('edges', []):
            ev = e.get('evidence')
            if isinstance(ev, list):
                ev = ' '.join(str(x.get('text') if isinstance(x, dict) else x) for x in ev)
            evn = re.sub(r'[^a-z0-9]+', '', str(ev or '').lower())
            for side in ('source', 'target'):
                nm = (e.get(side) or '').strip()
                if not nm or nm in known:
                    continue
                occ[nm] += 1
                if re.sub(r'[^a-z0-9]+', '', nm.lower()) in evn:
                    ev_hit[nm] += 1

    # 只统计**真被反建**的那些（形态过滤后 + 证据命中）
    JUNK = re.compile(
        r'^(summary|task|tasks|table|figure|fig\.?|section|appendix|abstract|'
        r'introduction|conclusion|results?|method|methods|approach|model|models|'
        r'example|examples|input|output|data|dataset|datasets|entity|entities|'
        r'relation|relations|knowledge|experts?|others?|misc|etc|na|none|unknown|'
        r'[a-z]|m|h|dr\.?|mr\.?|ms\.?)$', re.I)

    def looks_like_entity(nm):
        s = nm.strip()
        if len(s) < 3 or not any(ch.isalpha() for ch in s):
            return False
        if JUNK.match(s):
            return False
        if len(s) <= 4 and s.islower():
            return False
        return True

    backfilled = [n for n in occ if ev_hit[n] > 0 and looks_like_entity(n)]
    print(f'反建名总数 {len(backfilled)}')

    # 分层
    tiers = defaultdict(list)
    for n in backfilled:
        c = occ[n]
        tier = 'occ1' if c == 1 else ('occ2-4' if c <= 4 else 'occ5+')
        tiers[tier].append(n)

    print(f'\n{"层":8s} {"数量":>6s} {"占比":>7s}   形态初判分布')
    overall = Counter()
    for tier in ('occ1', 'occ2-4', 'occ5+'):
        names = tiers[tier]
        cls = Counter(classify(n) for n in names)
        overall.update(cls)
        dist = ', '.join(f'{k} {v}' for k, v in cls.most_common())
        print(f'{tier:8s} {len(names):6d} {len(names)/len(backfilled)*100:6.1f}%   {dist}')

    total = sum(overall.values())
    noise = sum(v for k, v in overall.items() if k.startswith('noise'))
    ent = sum(v for k, v in overall.items() if k.startswith('entity'))
    unc = overall.get('uncertain', 0)
    print(f'\n整体形态初判（n={total}）：')
    print(f'  entity-like  : {overall.get("entity-like",0):5d}')
    print(f'  entity-title : {overall.get("entity-title",0):5d}  （论文标题，合法实体）')
    print(f'  entity-venue : {overall.get("entity-venue",0):5d}  （会议全名，合法实体）')
    print(f'  entity 小计  : {ent:5d} ({ent/total*100:5.1f}%)')
    print(f'  uncertain    : {unc:5d} ({unc/total*100:5.1f}%)')
    print(f'  noise        : {noise:5d} ({noise/total*100:5.1f}%)')
    print(f'\n  ⇒ 这是**噪声率的上界**：noise 档里仍可能混着缩写与指标名；')
    print(f'     uncertain 档大概率也以真实体为主（人名、方法缩写）。')
    print(f'     **真精度必须人工读 sample 清单**，本脚本只负责把清单摆出来。')

    # 分层抽样：每层抽 60 个（噪声档与 entity-like 档按比例）
    SAMPLE = 60
    sample = []
    for tier in ('occ1', 'occ2-4', 'occ5+'):
        names = sorted(tiers[tier])
        by_cls = defaultdict(list)
        for n in names:
            by_cls[classify(n)].append(n)
        picked = []
        for cls, lst in by_cls.items():
            k = max(1, round(SAMPLE * len(lst) / max(1, len(names))))
            picked.extend(random.sample(lst, min(k, len(lst))))
        picked = picked[:SAMPLE]
        for n in picked:
            sample.append({'name': n, 'tier': tier, 'occurrences': occ[n],
                           'heuristic': classify(n)})

    print(f'\n=== 抽样清单（{len(sample)} 条，供人工复核）===')
    for cls in ('noise-junk', 'noise-sentence', 'noise-long', 'noise-lower',
                'uncertain', 'entity-like'):
        items = [s for s in sample if s['heuristic'] == cls]
        if not items:
            continue
        print(f'\n--- {cls}（{len(items)} 条）---')
        for s in items[:14]:
            print(f"   [{s['tier']:6s} ×{s['occurrences']:<3d}] {s['name'][:78]!r}")

    out = {
        'backfilled_total': len(backfilled),
        'heuristic_distribution': dict(overall),
        'heuristic_noise_share': round(noise / total, 4),
        'tiers': {k: len(v) for k, v in tiers.items()},
        'sample_size': len(sample),
        'sample': sample,
        'note': ('启发式只给出上界：noise 档几乎确定不是实体，entity-like 档仍可能'
                 '混入泛指词。真精度需人工读 sample 清单逐条判定。'
                 '本脚本不自动改数据。'),
    }
    write_if_changed(OUT, dumps(out))
    print(f'\n报告已写出: {OUT}')


if __name__ == '__main__':
    main()
