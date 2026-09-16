# -*- coding: utf-8 -*-
r"""B6 · 社区摘要 —— 对每个社区生成一段"这个社区在研究什么"。

## 为什么它值得做

TODO 的原话：
> 商业对标里唯一"**素材现成且能出彩**"的盲区。GraphRAG 的核心卖点，
> 而**我们的社区已经有名字**（`comm-2.json` 的 `name` 是 "AAAI"）
> 却没有一句话说明"这个社区在研究什么"。

**素材确实现成**：`web/kg-data/comm-*.json` 里已经有
  · `name`（社区名）· `core`（核心实体）· `nodes` · `edges`（带引文）
⇒ 差的只是"把这些喂给本地模型，要它写一段话"。

## 三个刻意的设计

**① 摘要必须"接地"：只用给它的东西**
prompt 里明确要求"**只根据给定的实体与关系，不要补充任何外部知识**"，
并要求**末尾列出它引用了哪几个实体** ——
这样"摘要里出现了没给它的实体"就能被机械检出（见 `--verify`）。

**② 与 H11 的纪律一致：不知道就说不知道**
如果某个社区的核心实体之间**看不出共同主题**（边很少、实体很杂），
prompt 允许它回答"这个社区没有明显共同主题"，**而不是硬编一个**。

**③ 摘要里带页码引用**
给模型的边里带 `page`，要求它在提到某条关系时写出处。
**没有引文的摘要对这个项目没有价值** —— 它违背"句句可溯源"的整体纪律。

## 用法

    python tools/build_community_summaries.py --limit 3     # 先跑 3 个看效果
    python tools/build_community_summaries.py                # 全部 18 个（约 30 分钟）
    python tools/build_community_summaries.py --verify       # 只校验已有摘要的接地性
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'web' / 'kg-data'
OUT = DATA / 'community-summaries.json'

OLLAMA = os.environ.get('OLLAMA_HOST', 'http://127.0.0.1:11434')
MODEL = os.environ.get('KG_SUMMARY_MODEL', os.environ.get('KG_QA_MODEL', 'qwen3.5:9b'))


def parse_args():
    ap = argparse.ArgumentParser(description='B6 社区摘要')
    ap.add_argument('--limit', type=int, default=0, help='只做前 N 个（试跑用）')
    ap.add_argument('--only', nargs='+', default=None, help='只做指定的社区文件（如 comm-2）')
    ap.add_argument('--out', default=str(OUT))
    ap.add_argument('--verify', action='store_true', help='只校验已有摘要的接地性')
    ap.add_argument('--timeout', type=int, default=300)
    return ap.parse_args()


ARGS = parse_args()


def load_communities():
    out = []
    for p in sorted(DATA.glob('comm-*.json'),
                    key=lambda q: int(re.search(r'\d+', q.stem).group())):
        try:
            d = json.loads(p.read_text(encoding='utf-8'))
        except Exception as e:
            print(f'  ⚠️ {p.name} 读失败：{type(e).__name__}')
            continue
        out.append((p.stem, d))
    return out


def build_facts(d, max_entities=14, max_edges=16):
    """把社区压成"给模型看的事实"。

    **必须带上引文**：没有出处的摘要对这个项目没有价值。
    """
    nodes = d.get('nodes') or []
    edges = d.get('edges') or []
    # 核心实体按度数取前 N
    core = sorted(nodes, key=lambda n: -(n.get('d') or 0))[:max_entities]
    core_ids = {n.get('id') for n in core}
    # 边优先取"两端都在核心实体里"的（那些最能说明主题）
    inner = [e for e in edges
             if isinstance(e, list) and len(e) >= 2
             and e[0] in core_ids and e[1] in core_ids]
    pick = (inner or edges)[:max_edges]

    ent_lines = []
    for n in core:
        t = n.get('t') or ''
        ent_lines.append(f'- {n.get("id")}（{t}）' if t else f'- {n.get("id")}')

    rel_lines = []
    for e in pick:
        if not isinstance(e, list) or len(e) < 3:
            continue
        src, tgt, typ = e[0], e[1], e[2]
        page = e[3] if len(e) > 3 else None
        cite = f'（第 {page} 页）' if page else ''
        rel_lines.append(f'- {src} --{typ}--> {tgt}{cite}')

    type_counts = Counter(e[2] for e in edges if isinstance(e, list) and len(e) >= 3)
    return {
        'name': d.get('name') or '',
        'n_nodes': len(nodes), 'n_edges': len(edges),
        'entities': ent_lines, 'relations': rel_lines,
        'core_ids': sorted(x for x in core_ids if x),
        'top_types': dict(type_counts.most_common(6)),
    }


PROMPT = '''---Role---
You are a Knowledge Graph analyst. Given the core entities and relations of ONE
community from a scholarly knowledge graph, write a short factual summary of
**what this community is about**.

---Rules---
1. **Only use the entities and relations given below. Do NOT add outside knowledge.**
2. If the given entities/relations do **not** share an obvious common theme,
   say so plainly: "这个社区没有明显的共同主题". Do NOT invent one.
3. Whenever you state a relation, mention its source page if one is given.
4. Write in Chinese. 3–5 sentences. No headings, no bullet lists, no markdown.
5. **End with a line** exactly like: `引用实体: A、B、C`
   listing only entity names that appear above.

---Community---
name: {name}
size: {n_nodes} entities / {n_edges} relations
most common relation types: {top_types}

---Core entities---
{entities}

---Relations---
{relations}

---Output---
'''


def call_ollama(prompt, timeout=300):
    """调本地模型。

    ## ⚠️ `"think": False` 是必需的（2026-09-16 实测）

    `qwen3.5:9b` 是 **reasoning 模型**：它的返回里有 `thinking` 字段
    （内容形如 `Thinking Process:\n\n1. **Analyze the Request:**...`）。
    **复杂 prompt 下，模型的额度会大量花在 `thinking` 上，`response` 直接为空。**

    实测（本脚本第一次跑 3 个社区）：
        comm-0 ✅ 463 字符 · comm-1 ❌ **0 字符** · comm-2 ❌ **0 字符**
    而单独用一句短 prompt 调它时 `eval_count=1446`、`response` 正常 ——
    **⇒ 不是模型坏了，是"思考把所有输出额度吃掉了"。**

    `"think": False` 是 Ollama 的正式参数，关掉它之后 `response` 正常返回。

    ## 为什么不用"`response` 为空时回退读 `thinking`"

    那会把模型的**草稿**当成结论 —— 而 `thinking` 里常有
    "我需要考虑……也许应该……" 这类自我对话。
    **本项目对"看起来对了、其实是别的东西"这类失败最敏感**（G3 就是栽在这上面）。
    """
    payload = {
        'model': MODEL, 'prompt': prompt, 'stream': False,
        'think': False,                      # ← 关键：关掉 reasoning 的思考输出
        'options': {'temperature': 0.3, 'num_predict': 900},
    }
    body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(OLLAMA.rstrip('/') + '/api/generate', data=body,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    text = (d.get('response') or '').strip()
    if not text:
        # **空输出要报出来，不要静默当成功** —— 否则统计里会混进"成功但没内容"
        think = (d.get('thinking') or '')
        raise RuntimeError(
            f'模型返回空 response（eval_count={d.get("eval_count")} · '
            f'done_reason={d.get("done_reason")} · thinking {len(think)} 字符）')
    return text



def verify_grounding(text, facts):
    """校验"接地性"：摘要里提到的实体名，是不是都在给它的清单里。

    **这是这份摘要唯一的机械质量检验** ——
    如果它提到了清单外的实体，那多半是模型在"补外部知识"。
    """
    given = {str(x).lower() for x in facts['core_ids']}
    # 粗粒度：从摘要里找"像实体名"的片段（连续英文/中文词组），看有几个不在清单里
    cand = re.findall(r'[A-Za-z][A-Za-z0-9\-\+\.]{2,}', text)
    unknown = sorted({c for c in cand if c.lower() not in given})
    # 摘要末尾的 `引用实体: ...`
    m = re.search(r'引用实体[:：]\s*(.+)', text)
    cited = [s.strip() for s in re.split(r'[、,，]', m.group(1))] if m else []
    cited_unknown = [c for c in cited if c and c.lower() not in given]
    return {
        'unknown_tokens': unknown[:12],
        'n_unknown_tokens': len(unknown),
        'cited': cited,
        'cited_unknown': cited_unknown,
        'has_citation_line': bool(m),
        'says_no_theme': '没有明显' in text or '未发现明显' in text,
    }


def main():
    t0 = time.time()
    comms = load_communities()
    print('=' * 88)
    print('B6 · 社区摘要')
    print('=' * 88)
    print(f'  发现 {len(comms)} 个社区 · 模型 {MODEL}')

    if ARGS.only:
        comms = [(s, d) for s, d in comms if s in set(ARGS.only)]
    if ARGS.limit:
        comms = comms[:ARGS.limit]

    if ARGS.verify:
        if not Path(ARGS.out).exists():
            print(f'  ❌ 没有 {ARGS.out} 可校验')
            return 1
        prev = json.loads(Path(ARGS.out).read_text(encoding='utf-8'))
        bad = 0
        for rec in prev.get('communities') or []:
            v = rec.get('grounding') or {}
            mark = '✅' if not v.get('cited_unknown') and v.get('has_citation_line') else '⚠️'
            if mark != '✅':
                bad += 1
            print(f'  {mark} {rec["id"]:10} {rec.get("name",""):26} '
                  f'未知词 {v.get("n_unknown_tokens")} · 引用行 {v.get("has_citation_line")}')
        print()
        print(f'  {len(prev.get("communities") or [])} 条 · 有问题的 {bad} 条')
        return 0

    print(f'  本次做 {len(comms)} 个')
    print()

    records = []
    for i, (stem, d) in enumerate(comms, 1):
        facts = build_facts(d)
        print(f'  [{i}/{len(comms)}] {stem} · {facts["name"]!r} · '
              f'{facts["n_nodes"]} 实体 / {facts["n_edges"]} 边 · '
              f'主要类型 {list(facts["top_types"])[:3]}')
        prompt = PROMPT.format(
            name=facts['name'], n_nodes=facts['n_nodes'], n_edges=facts['n_edges'],
            top_types=json.dumps(facts['top_types'], ensure_ascii=False),
            entities='\n'.join(facts['entities']) or '（无）',
            relations='\n'.join(facts['relations']) or '（无）')
        t = time.time()
        try:
            text = call_ollama(prompt, timeout=ARGS.timeout)
        except Exception as e:
            print(f'      ❌ 失败：{type(e).__name__}: {e}')
            records.append({'id': stem, 'name': facts['name'], 'ok': False,
                            'error': f'{type(e).__name__}: {e}'})
            continue
        el = time.time() - t
        g = verify_grounding(text, facts)
        # 输出前 2 行作为预览
        preview = ' / '.join(x for x in text.splitlines() if x.strip())[:150]
        print(f'      ✅ {el:.0f}s · {len(text)} 字符 · 未知词 {g["n_unknown_tokens"]} '
              f'· 引用行 {g["has_citation_line"]}')
        print(f'         {preview}')
        records.append({'id': stem, 'name': facts['name'], 'ok': True,
                        'elapsed_s': round(el, 1), 'summary': text,
                        'grounding': g, 'facts': {
                            'n_nodes': facts['n_nodes'], 'n_edges': facts['n_edges'],
                            'core_ids': facts['core_ids'], 'top_types': facts['top_types']}})

    ok = sum(1 for r in records if r.get('ok'))
    bad_ground = [r['id'] for r in records
                  if r.get('ok') and (r['grounding'].get('cited_unknown')
                                      or not r['grounding'].get('has_citation_line'))]
    payload = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'model': MODEL,
        'purpose': 'B6：每个社区一段"这个社区在研究什么"（GraphRAG 的核心卖点）',
        'design': {
            'only_given_facts': 'prompt 明确要求"只用给定的实体与关系，不补充外部知识"',
            'may_say_no_theme': '允许回答"这个社区没有明显的共同主题"，不硬编',
            'must_cite': '要求末尾列出"引用实体: …"，并可机械校验',
            'grounding_check': '从摘要里抽出英文词，看有多少不在给定的实体清单里',
        },
        'n_total': len(records), 'n_ok': ok, 'grounding_suspect': bad_ground,
        'communities': records,
        'elapsed_s': round(time.time() - t0, 1),
    }
    Path(ARGS.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                              encoding='utf-8')
    print()
    print('=' * 88)
    print(f'  完成 {ok}/{len(records)} · 接地性可疑 {len(bad_ground)} 条 {bad_ground[:5]}')
    print(f'  已写 {Path(ARGS.out).name}（{payload["elapsed_s"]:.0f}s）')
    return 0 if ok == len(records) else 1


if __name__ == '__main__':
    sys.exit(main())
