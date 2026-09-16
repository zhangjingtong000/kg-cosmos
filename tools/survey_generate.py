# -*- coding: utf-8 -*-
r"""H1 · 综述段落生成（**图谱接地 + 句句溯源 + 主张-证据支持率**）。

## TODO 的原话与一处要改的来源

> **H1** 综述段落生成 | M5，论文落点「**产出工具 + 实验章新指标**」；
> 要求**图谱接地、句句溯源、主张-证据支持率**

⚠️ **来源要改**：H1 声称源自 `FOUR_LENS_SCAN.md`，但该文件的 8 项机会清单与复扫
**都没有"综述"二字**；真正的出处是 `EXECUTION_READINESS.md:49/:65`。
（TODO 自己已经把这条记为"引用时要改来源"。）

## 这份工具回答什么问题

> **"能不能生出一段**每句话都有出处**的综述？"**

⇒ 所以它**不是**"让模型写篇综述"（那随便一个模型都能写），
而是**"写完之后能机械核对每一句"**。

## 四个指标（**都不靠 LLM 判分**）

给模型的 prompt 里，证据是**带编号**的：

    [E1] TransE --USES--> FB15k（第 5 页）
    [E2] TransE --MEASURES--> Hits@10（第 6 页）
    ...

要求它**每句末尾写 `[E1][E3]`**。然后：

| 指标 | 算法 | 它回答什么 |
|---|---|---|
| **① 句句溯源率** | 有引用的句子 / 总句数 | "每句话都有出处吗" |
| **② 引用有效率** | 引用编号真的在证据表里的比例 | "引的是真编号吗"（防编造） |
| **③ 接地率** | 句中出现的证据表实体个数 / 句中出现过的全部"大写开头词" | "句子说的是证据里的事吗" |
| **★ ④ 主张-证据支持率** | 同时满足①②③的句子 / 总句数 | **这就是那个"实验章新指标"** |

**为什么不用 LLM 判分**：本项目已量过四个模型标注者对关系层的一致性 α 是**负的**
（`RESEARCH_POINT_EVIDENCE.md` 证据一）——
**用一个 α 为负的裁判给另一个模型打分，得到的分数没有意义。**
⇒ 四个指标全部是**可机械核对的事实**。

用法：
    python tools/survey_generate.py --topic "knowledge graph embedding"
    python tools/survey_generate.py --graph 2012.00456v1 --topic "TransE"
    python tools/survey_generate.py --verify-only     # 只重算已有产物的指标
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

GRAPHS = ROOT / 'data' / 'corpus' / 'graphs'
DEFAULT_OUT = ROOT / 'data' / 'quality' / 'survey-segments.json'
OLLAMA = os.environ.get('OLLAMA_HOST', 'http://127.0.0.1:11434')
MODEL = os.environ.get('KG_SURVEY_MODEL', os.environ.get('KG_QA_MODEL', 'qwen3.5:9b'))


def parse_args():
    ap = argparse.ArgumentParser(description='H1 综述段落生成')
    ap.add_argument('--topic', default='knowledge graph embedding')
    ap.add_argument('--graph', default='', help='只用这一篇（图快照名，不含 .json）')
    ap.add_argument('--max-evidence', type=int, default=18)
    ap.add_argument('--out', default=str(DEFAULT_OUT))
    ap.add_argument('--verify-only', action='store_true', help='只重算指标，不调模型')
    ap.add_argument('--timeout', type=int, default=300)
    return ap.parse_args()


ARGS = parse_args()


# ══════════════════════════════════════════════════════════════════════════
# 取证据（**这一步决定了接地有没有可能**）
# ══════════════════════════════════════════════════════════════════════════
def gather_evidence(topic, graph=None, limit=18):
    """按话题从图里挑证据，**每条带页码**（没有出处的证据对这个项目没有价值）。"""
    terms = [t for t in re.split(r'[^0-9A-Za-z\u4e00-\u9fff]+', topic.lower()) if len(t) >= 3]
    scored = []
    for f in sorted(GRAPHS.glob('*.json')):
        if graph and f.stem != graph:
            continue
        try:
            d = json.loads(f.read_text(encoding='utf-8-sig'))
        except Exception:
            continue
        for e in (d.get('edges') or []):
            s, t, ty = str(e.get('source') or ''), str(e.get('target') or ''), str(e.get('type') or '')
            hay = f'{s} {t} {ty}'.lower()
            n = sum(1 for x in terms if x in hay)
            if not n:
                continue
            ev = (e.get('evidence') or [{}])
            page = next((x.get('page') for x in ev if x.get('page')), None)
            quote = next((x.get('text') for x in ev if x.get('text')), '') or ''
            scored.append({
                'score': n, 'source': s, 'target': t, 'type': ty,
                'paper': f.stem, 'page': page,
                'quote': re.sub(r'\s+', ' ', quote)[:150],
            })
    # 有页码的优先（**接地要靠它**），其次按话题命中数
    scored.sort(key=lambda x: (x['page'] is None, -x['score'], x['paper']))
    return scored[:limit]


def render_evidence(evs):
    """把证据渲染成**带编号**的清单 —— 编号是"句句溯源"的锚点。"""
    lines = []
    for i, e in enumerate(evs, 1):
        cite = f'（{e["paper"]} 第 {e["page"]} 页）' if e.get('page') else f'（{e["paper"]}）'
        lines.append(f'[E{i}] {e["source"]} --{e["type"]}--> {e["target"]}{cite}')
    return '\n'.join(lines)


PROMPT = '''---Role---
You are writing ONE paragraph of a literature survey, grounded **only** in the
numbered evidence given below.

---Hard rules---
1. **Use ONLY the evidence below. Do NOT add any outside knowledge.**
2. **Every sentence MUST end with the evidence ids it is based on**, like `[E1][E3]`.
   A sentence without ids is a violation.
3. If the evidence does not support a claim, **do not make the claim**.
4. **When you name an entity that appears in the evidence, keep it in its ORIGINAL
   spelling** (usually English), e.g. write `Knowledge Graph Completion`, not its
   Chinese translation. The surrounding prose should be Chinese.
   ⇒ This is required so that every sentence can be checked automatically against
     the evidence it cites. A sentence whose entities cannot be matched back to its
     cited evidence counts as **unsupported**.
5. Write in Chinese (except entity names per rule 4). One paragraph, 4–6 sentences.
   No headings, no bullet points.
6. Do not invent evidence ids that are not in the list.

---Topic---
{topic}

---Evidence---
{evidence}

---Output---
'''


def call_ollama(prompt, timeout=300):
    """调本地模型。

    ⚠️ `"think": False` 是必需的（B6 实测的教训）：`qwen3.5:9b` 是 reasoning 模型，
    **复杂 prompt 下额度会全花在 `thinking` 上，`response` 直接为空**。
    """
    body = json.dumps({'model': MODEL, 'prompt': prompt, 'stream': False,
                       'think': False,
                       'options': {'temperature': 0.3, 'num_predict': 1200}}).encode('utf-8')
    req = urllib.request.Request(OLLAMA.rstrip('/') + '/api/generate', data=body,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    txt = (d.get('response') or '').strip()
    if not txt:
        raise RuntimeError(f'模型返回空 response（done_reason={d.get("done_reason")}）')
    return txt


# ══════════════════════════════════════════════════════════════════════════
# 四个指标（**可机械核对，零 LLM 判分**）
# ══════════════════════════════════════════════════════════════════════════
_SENT_SPLIT = re.compile(r'(?<=[。！？!?;；])\s*')
_IDS = re.compile(r'\[E(\d+)\]')


def split_sentences(text):
    """切句。**保留句末的引用标记**（它们在句号后面，切句时不能丢）。"""
    raw = [s.strip() for s in _SENT_SPLIT.split(text or '') if s.strip()]
    out = []
    for s in raw:
        # `……。[E1][E2]` 会被切成两段 ⇒ 把"只有引用标记"的那段并回上一句
        if out and not re.sub(r'(\[E\d+\])|\s', '', s):
            out[-1] = out[-1] + s
        else:
            out.append(s)
    return out


# ── 跨语言接地判定（2026-09-16 加）──────────────────────────────────────
#
# ## 为什么需要它
#
# 第一版只用**字符串包含**判"句子提到了证据里的哪个实体"：
#     `'Knowledge Graphs' in body`
# 而模型写的是**中文散文**（"知识图谱"），证据里是**英文实体名** ⇒
# **一个都匹配不上** ⇒ 句子全被归为"无法判定"。
# 实测：`link prediction` 那一跑 **5 句里 3 句 unjudgeable（60%）**，
# 于是"④ 主张-证据支持率 100%"的**分母只剩 2 句** —— 那个 100% 没有意义。
#
# prompt 里已经加了"实体保留原文写法"，但**判据不能依赖模型的配合**
# （9B 本地模型的指令遵循有限，实测它仍然写中文），
# 而且**把"判不出"的责任推给被评测的对象，本身就是个坏判据**。
#
# ## 解法：借 `embeddings.py` 的 bge-m3
#
# `bge-m3` 是**多语言**嵌入模型（项目本来就为 H4 装好了，本轮还顺手修好了它）。
# ⇒ 判"这句话是不是在说这个实体"，改为**比余弦相似度**，而不是比字符串。
#   这天然跨语言："知识图谱" 与 `Knowledge Graphs` 的余弦会高，
#   而与 `Hits@10` 的余弦会低。
#
# **嵌入服务不可用时自动退回字符串匹配**（返回 `None` 表示"判不了，回退"）——
# 这样这份工具在没有 Ollama 的机器上仍然能跑，只是退化成第一版的行为。
GROUND_TH = 0.55
#   比 H4 的 `HIGH=0.93` 松得多。为什么：
#     · H4 比的是**两个实体名**（几乎同形才该合）；
#     · 这里比的是**一整句话 ↔ 一个实体名**，语义密度差一个量级，
#       用 0.93 会把所有句子都判成"没提到"。
#   0.55 是在本项目的样本上试出来的，**且判不出的那一档会被单独计数**
#   （`unjudgeable`），所以中间地带不会污染"不接地"这个真问题的计数。


def _semantic_mentioned(sentence, evs, cache):
    """用嵌入判"这句话在说证据里的哪些实体"（**跨语言**）。

    返回实体名列表；**嵌入不可用时返回 `None`**（表示"回退到字符串匹配"）。
    """
    ents = sorted({e['source'] for e in evs if e.get('source')} |
                  {e['target'] for e in evs if e.get('target')})
    if not ents or not sentence.strip():
        return []
    try:
        import embeddings as E
        if not E.available():
            return None
        ekey = ('__ents__', tuple(ents))
        if ekey not in cache:
            cache[ekey] = E.embed(ents)
        skey = ('__sent__', sentence)
        if skey not in cache:
            cache[skey] = E.embed([sentence])[0]
        sv = cache[skey]
        return [ent for ent, ev in zip(ents, cache[ekey])
                if E.cosine(sv, ev) >= GROUND_TH]
    except Exception:
        return None


def evaluate(text, evs, use_semantic=True):
    """算四个指标。返回 dict（含逐句明细，**没有明细就没法改**）。

    ## ⚠️ 接地判据必须是**三态**，不是布尔

    第一版是布尔：`mentioned = [证据里的实体出现在句中的]`；`if mentioned: … else False`。
    实测立刻暴露问题 —— 模型写**中文散文**、证据是**英文实体名**，
    于是 `mentioned` 经常是**空列表**，而代码把它算成"**不接地**"。
    那两句实际写得没错（`[E2][E4]` 引对了），**是判据把"无法判定"当成了"失败"**。

    ⇒ 三态：
      · `grounded`     —— 句里提到的实体**都在**所引证据里
      · `ungrounded`   —— 提到了一些、但**有不在**所引证据里的（**真问题**）
      · `unjudgeable`  —— **一个证据实体都没提到** ⇒ 无从判断

    **主张-证据支持率的分母排除 `unjudgeable`** ——
    否则"没提到实体"会被当成"错"，指标就变成了惩罚文体而不是度量接地。

    （在 prompt 里也加了一条要求"实体保留原文写法"，从源头减少 `unjudgeable`；
      但**判据本身不能依赖模型的配合** —— 那正是这里要三态的原因。）
    """
    per_sentence = []
    n_cited = n_valid_all = n_grounded = n_ungrounded = n_unjudgeable = n_supported = 0
    sem_cache = {}
    sem_used = False
    for sent in split_sentences(text):
        ids = [int(m.group(1)) for m in _IDS.finditer(sent)]
        body = _IDS.sub('', sent).strip()
        valid = [i for i in ids if 1 <= i <= len(evs)]
        # ── 句子提到了哪些证据实体 ──
        # 先试**字符串**（准且快）；一个都没匹配上再试**嵌入**（跨语言）。
        # 顺序是有意的：模型听话时字符串就够，且它**不会**把 `Hits@10`
        # 误判成"在说 Knowledge Graphs"。
        mentioned_names = sorted({e['source'] for e in evs if e.get('source') and e['source'] in body} |
                                 {e['target'] for e in evs if e.get('target') and e['target'] in body})
        how = 'string'
        if not mentioned_names and use_semantic:
            got = _semantic_mentioned(body, evs, sem_cache)
            if got is None:
                pass                    # 嵌入不可用 ⇒ 保持字符串结果
            else:
                sem_used = True
                mentioned_names = got
                how = 'semantic' if got else 'string'
        mentioned_names = sorted(set(mentioned_names))
        cited_evs = [evs[i - 1] for i in valid]

        if not mentioned_names:
            state = 'unjudgeable'
        elif not cited_evs:
            state = 'ungrounded'          # 提到了实体却一条有效引用都没有
        else:
            cited_ents = {x['source'] for x in cited_evs} | {x['target'] for x in cited_evs}
            ok = all(nm in cited_ents for nm in mentioned_names)
            state = 'grounded' if ok else 'ungrounded'

        rec = {'sentence': body[:160], 'ids': ids, 'valid_ids': valid,
               'mentioned_entities': mentioned_names[:6],
               'matched_by': how,
               'has_citation': bool(ids), 'ids_valid': bool(ids) and len(valid) == len(ids),
               'grounding_state': state,
               'grounded': state == 'grounded'}
        # **支持 = 有引用 + 编号有效 + 接地**（unjudgeable 不算支持，但单独计数）
        rec['supported'] = rec['has_citation'] and rec['ids_valid'] and state == 'grounded'
        n_cited += rec['has_citation']
        n_valid_all += rec['ids_valid']
        n_grounded += state == 'grounded'
        n_ungrounded += state == 'ungrounded'
        n_unjudgeable += state == 'unjudgeable'
        n_supported += rec['supported']
        per_sentence.append(rec)

    n = len(per_sentence) or 1
    # **主张-证据支持率**：分母排除"无法判定"的句子
    judgeable = n_grounded + n_ungrounded
    return {
        'n_sentences': len(per_sentence),
        'trace_rate': round(n_cited / n, 4),            # ① 句句溯源率
        'valid_id_rate': round(n_valid_all / n, 4),     # ② 引用有效率
        'grounding_rate': round(n_grounded / n, 4),     # ③ 接地率（三态里的 grounded）
        'ungrounded': n_ungrounded,
        'unjudgeable': n_unjudgeable,
        # ★ ④ 主张-证据支持率 —— **必须与 judgeable_rate 一起看**
        'claim_support_rate': round(n_grounded / judgeable, 4) if judgeable else None,
        # **⑤ 可判定率** —— ④ 的配套。分母小的时候 ④ 再高也没意义。
        'judgeable_rate': round(judgeable / n, 4),
        'semantic_used': sem_used,
        'per_sentence': per_sentence,
    }


def main():
    t0 = time.time()
    print('=' * 92)
    print('H1 · 综述段落生成（图谱接地 · 句句溯源 · 主张-证据支持率）')
    print('=' * 92)

    if ARGS.verify_only:
        if not Path(ARGS.out).exists():
            print(f'  ❌ 没有 {ARGS.out} 可校验')
            return 1
        prev = json.loads(Path(ARGS.out).read_text(encoding='utf-8'))
        for seg in prev.get('segments') or []:
            seg['metrics'] = evaluate(seg.get('text') or '', seg.get('evidence') or [])
        m = [s['metrics'] for s in (prev.get('segments') or [])]
        if m:
            prev['summary'] = {
                k: round(sum(x[k] for x in m) / len(m), 4)
                for k in ('trace_rate', 'valid_id_rate', 'grounding_rate',
                          'claim_support_rate')}
        Path(ARGS.out).write_text(json.dumps(prev, ensure_ascii=False, indent=1),
                                  encoding='utf-8')
        print(f'  已重算 {len(m)} 段 · summary={prev.get("summary")}')
        return 0

    evs = gather_evidence(ARGS.topic, ARGS.graph or None, ARGS.max_evidence)
    print(f'  话题：{ARGS.topic!r}'
          + (f' · 限 {ARGS.graph}' if ARGS.graph else ''))
    print(f'  取到证据 {len(evs)} 条（**带页码的优先**）')
    if not evs:
        print('  ❌ 没有证据可写（换个话题或去掉 --graph）')
        return 1
    for line in render_evidence(evs).split('\n')[:6]:
        print(f'      {line}')
    if len(evs) > 6:
        print(f'      …（另有 {len(evs) - 6} 条）')

    prompt = PROMPT.format(topic=ARGS.topic, evidence=render_evidence(evs))
    t = time.time()
    try:
        text = call_ollama(prompt, ARGS.timeout)
    except Exception as e:
        print(f'  ❌ 生成失败：{type(e).__name__}: {e}')
        return 1
    el = time.time() - t
    print()
    print(f'  生成 {el:.0f}s · {len(text)} 字符')
    print('  ' + '-' * 86)
    for line in text.split('\n'):
        if line.strip():
            print(f'      {line.strip()}')
    print('  ' + '-' * 86)

    m = evaluate(text, evs)
    print()
    print('  ★ 四个指标（**零 LLM 判分**，全部可机械核对）')
    print(f'      ① 句句溯源率        {m["trace_rate"]:.1%}'
          f'  （有引用的句子 {int(round(m["trace_rate"] * m["n_sentences"]))}/{m["n_sentences"]}）')
    print(f'      ② 引用有效率        {m["valid_id_rate"]:.1%}'
          f'  （引的是真编号吗）')
    print(f'      ③ 接地率            {m["grounding_rate"]:.1%}'
          f'  （句子说的实体都在所引证据里）')
    csr = m['claim_support_rate']
    print(f'      ⑤ 可判定率          {m["judgeable_rate"]:.1%}'
          f'  （**④ 的配套**：分母小的时候 ④ 再高也没意义）')
    print(f'      ★ ④ **主张-证据支持率** '
          + (f'{csr:.1%}' if csr is not None else '—')
          + f'  ← **这就是那个"实验章新指标"**'
          + f'（分母 = 可判定句 {m["n_sentences"] - m["unjudgeable"]} 句）')
    # **三态必须分开报** —— 把"无法判定"混进"不接地"会让指标变成惩罚文体
    if m.get('unjudgeable'):
        print()
        print(f'  ⚠️ **无法判定**的句子 {m["unjudgeable"]} 句 —— '
              f'句里一个证据实体都没出现（多半写成了中文译名）。')
        print('     它们**不算失败**，但④的分母不含它们 ⇒ 分母小的时候要谨慎解读。')
    print()
    print('  ★ 三个状态的分布')
    for st, label in (('grounded', '接地'), ('ungrounded', '**不接地（真问题）**'),
                      ('unjudgeable', '无法判定')):
        c = sum(1 for s in m['per_sentence'] if s['grounding_state'] == st)
        print(f'      {label:<22} {c} 句')
    bad = [s for s in m['per_sentence'] if not s['supported']]
    if bad:
        print()
        print(f'  ⚠️ 未被支持的句子 {len(bad)} 句（**没有明细就没法改**）：')
        for s in bad[:5]:
            why = []
            if not s['has_citation']:
                why.append('没有引用')
            if not s['ids_valid']:
                why.append('引用了不存在的编号')
            if s['grounding_state'] == 'ungrounded':
                why.append('**句里提到的实体有不在所引证据里的**')
            elif s['grounding_state'] == 'unjudgeable':
                why.append('句里没有任何证据实体（无法判定，非失败）')
            print(f'      · [{s["grounding_state"]}] {s["sentence"][:62]!r}')
            print(f'        原因：{", ".join(why) or "?"} · ids={s["ids"]} · '
                  f'实体={s["mentioned_entities"]}')

    payload = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'purpose': 'H1：综述段落生成 —— 图谱接地 + 句句溯源 + 主张-证据支持率',
        'source_of_requirement':
            '`EXECUTION_READINESS.md:49/:65`（**不是** `FOUR_LENS_SCAN.md` —— '
            '该文件的机会清单与复扫都没有"综述"二字；TODO 自己已记下要改来源）',
        'design': {
            'no_llm_judge':
                '**不用 LLM 判分** —— 本项目已量过四个模型标注者对关系层的一致性 α 是**负的**；'
                '用一个 α 为负的裁判给另一个模型打分没有意义。四个指标全是可机械核对的事实。',
            'evidence_is_numbered':
                '证据带编号 [E1][E2]…，要求每句末尾标引用 ⇒ 引用可被机械解析与核对。',
            'why_grounding_is_strict':
                '接地率的判据是"**句子提到的实体出现在它所引用的证据里**" —— '
                '比"有没有引用"严得多，能挡住"引了一堆无关证据"这种假溯源。',
        },
        'model': MODEL,
        'topic': ARGS.topic, 'graph': ARGS.graph or None,
        'segments': [{'topic': ARGS.topic, 'graph': ARGS.graph or None,
                      'text': text, 'evidence': evs,
                      'elapsed_s': round(el, 1), 'metrics': m}],
        'summary': {k: m[k] for k in ('trace_rate', 'valid_id_rate',
                                      'grounding_rate', 'claim_support_rate')},
        'elapsed_s': round(time.time() - t0, 1),
    }
    Path(ARGS.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                              encoding='utf-8')
    print()
    print(f'  已写 {Path(ARGS.out).name}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
