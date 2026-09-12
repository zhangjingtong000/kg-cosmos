"""L7 约束精度抽查：把判为 reversed / role_violation 的边按固定种子抽样，供人工核对。

【为什么要这个脚本】
  tools/l7_relation_constraints_audit.py 给出"检出多少条"，但**不给"检出得准不准"**。
  端点类型是抽取出来的标签（docs/L5_SIX_LAYER_REPORT.md §5.1 记录过它不可靠），
  所以"类型对反了"不等于"方向真错了"。判断精度只能靠人逐条读 description。
  本脚本不自动下结论——它把样例固定下来（固定 seed），让这个判断**可复现、可复核**，
  而不是停留在"作者看过觉得还行"。

  实测结论（2026-09-12，seed=7，每类抽 20 条）：
    CREATED_BY   reversed 20 抽 → 17 条描述明确是「被创作物 → 创作者」，3 条无描述
                 （描述字段只有 "CREATED_BY"），无一条是误报。
    PRODUCES     reversed 20 抽 → 12 条真反转，8 条是谓词误用或类型噪声，
                 假阳性集中在 Concept→Process 一格。
    EVALUATES    reversed 8 条全抽 → **0 条真反转**，据此已撤销该类的方向指纹。
  所以只有 CREATED_BY 的反转可以直接进自动修复流程；PRODUCES 只进人工队列。

用法：
  python tools/l7_constraint_precision_sample.py
  python tools/l7_constraint_precision_sample.py --per-type 30 --seed 7
"""
import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from ontology import RELATION_ENDPOINT_CONSTRAINTS as CONSTRAINTS  # noqa: E402

GRAPH = ROOT / 'data' / 'corpus' / 'graphs'
VERIFY = ROOT / 'data' / 'quality' / 'relation-verify-all.json'
OUT = ROOT / 'data' / 'quality' / 'l7-constraint-samples.json'


def parse_args():
    ap = argparse.ArgumentParser(description='L7 约束精度抽查（固定种子）')
    ap.add_argument('--per-type', type=int, default=20)
    ap.add_argument('--seed', type=int, default=7)
    ap.add_argument('--desc-len', type=int, default=170)
    return ap.parse_args()


ARGS = parse_args()


def load_node_types():
    t = {}
    for f in sorted(GRAPH.glob('*.json')):
        snap = json.loads(f.read_text(encoding='utf-8'))
        for n in snap.get('nodes', []):
            nm = (n.get('name') or '').strip()
            if nm and nm not in t:
                t[nm] = n.get('type') or 'Concept'
    return t


def verdict(c, ts, tt):
    """与 tools/l7_relation_constraints_audit.py 逐字一致的裁决函数。"""
    if c['kind'] == 'unconstrained':
        return 'unconstrained'
    if c['kind'] == 'symmetric':
        ok = ts in c['source_allowed'] and tt in c['target_allowed']
        return 'compliant_role' if ok else 'role_violation'
    cs, ct = c['source_core'], c['target_core']
    if cs and ct:
        if ts in cs and tt in ct:
            return 'compliant_directed'
        if tt in cs and ts in ct:
            return 'reversed'
    ok = ts in c['source_allowed'] and tt in c['target_allowed']
    return 'compliant_role' if ok else 'role_violation'


def main():
    ntype = load_node_types()
    edges = json.loads(VERIFY.read_text(encoding='utf-8'))['edges']
    buckets = defaultdict(lambda: defaultdict(list))
    for e in edges:
        r = e['type']
        if r not in CONSTRAINTS:
            continue
        ts, tt = ntype.get(e['source']), ntype.get(e['target'])
        if ts is None or tt is None or ts == 'Unknown' or tt == 'Unknown':
            continue
        buckets[r][verdict(CONSTRAINTS[r], ts, tt)].append((e, ts, tt))

    rng = random.Random(ARGS.seed)
    report = {'seed': ARGS.seed, 'per_type': ARGS.per_type, 'by_type': {}}
    for r in sorted(buckets, key=lambda x: -len(buckets[x]['reversed'])):
        rows = buckets[r]['reversed']
        role = buckets[r]['role_violation']
        if not rows and not role:
            continue
        entry = {'n_reversed': len(rows), 'n_role_violation': len(role), 'samples': []}
        print('=' * 108)
        print(f'### {r} · reversed {len(rows)} 条 / role_violation {len(role)} 条'
              f'（seed={ARGS.seed}，各抽 {ARGS.per_type}）')
        for label, pool in (('reversed', rows), ('role_violation', role)):
            if not pool:
                continue
            for e, ts, tt in rng.sample(pool, min(ARGS.per_type, len(pool))):
                desc = (e.get('description') or '')[:ARGS.desc_len]
                print(f'  [{label}] {e["source"][:32]!r}({ts}) → '
                      f'{e["target"][:32]!r}({tt})  L5={e.get("entail_prob")}')
                print(f'      {desc}')
                entry['samples'].append({
                    'class': label, 'source': e['source'], 'source_type': ts,
                    'target': e['target'], 'target_type': tt,
                    'l5': e.get('entail_prob'), 'description': desc,
                })
        report['by_type'][r] = entry

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n样例已写出: {OUT}')
    print('判读口径：读 description 里 source 与 target 谁是主动方，'
          '与表的 semantics 比对；description 为空或只有关系名的记为「无信息」。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
