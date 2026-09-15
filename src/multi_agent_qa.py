# -*- coding: utf-8 -*-
r"""多 Agent 问答：检索 → 支持取证 / 反对取证 → 综合。

## 为什么有这个模块

用户昨天就要求做「多 Agent 问答」，我一直没做；评审随后指出
`web/ask.html` 是硬编码预设问答、`/chat` 是子串匹配原型 ——
**"多 Agent 问答"在交付物里不存在**。

正确的做法是**把它做出来**，不是删掉它。本模块就是那个实现。

## 四个 Agent 的分工

    ① 检索 Agent    —— 纯规则（种子匹配 + 2 跳 BFS），**零模型、可复现**
    ② 支持 Agent    —— 读子图证据，找**支持**某个答案的边
    ③ 反对 Agent    —— 独立地找**反对/矛盾**的边（不共享支持方的中间结论）
    ④ 综合 Agent    —— 只看到两方交上来的证据，给结论与置信度

②③ **并行且互不可见**，这是"多 Agent"的实质：它们不共用同一份中间判断，
否则就只是把一次调用拆成两次。

## 三条设计纪律

· **证据一律指向论文原文**：每条证据都带 `source_name`（论文文件名）、
  `page`（页码）、`text`（原文片段）。**不允许把项目自己的 docs/ 当证据** ——
  评审点名的正是「18 条证据全部指向项目自己的文档，0 条指向论文原文」。
· **LLM 只做判断，不做检索**：检索是纯规则的，所以"为什么找到这些"永远可复现、
  可解释；模型只在给定的证据集合上作判断。
· **反对 Agent 的产出必须保留**：即使它没找到反例，也要如实报「未发现反证」，
  不能因为"结论一致"就把它省略 —— 那会让"有反方"变成摆设。

模型用**本地 Ollama**（零 API 成本）；模型不可用时**自动降级为纯规则摘要**，
并在结果里标明 `degraded: true`，绝不假装有模型参与。
"""

import json
import os
import re
import urllib.error
import urllib.request
from collections import deque

OLLAMA = os.environ.get('OLLAMA_HOST', 'http://127.0.0.1:11434')
MODEL = os.environ.get('KG_QA_MODEL', 'qwen3.5:9b')


# ────────────────────────────────────────────────────────────────────────
# ① 检索 Agent —— 纯规则，零模型
# ────────────────────────────────────────────────────────────────────────
def _norm(s):
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]+', '', str(s or '').lower())


# ── K7 多轮追问 · 问题重写（2026-09-15）──────────────────────────────────
#
# ## 为什么需要"重写"而不是"把历史塞进检索"
#
# `retrieve()` 是**字面种子匹配**（问题里的词与实体名做词级匹配）。
# 而追问常常没有主语：
#
#     「那它的缺点呢？」  ← 「它」指谁？字面匹配找不到任何实体，检索直接空转
#
# 所以有两种做法，**只有一种是对的**：
#
#   ❌ **把历史原文拼进 question 再丢给 `retrieve`**
#      —— 上一轮的问题会被一起当成"当前问题"，种子匹配会**大幅偏向前几轮**，
#      而且历史越长、污染越重（这正是评审提醒的"别污染检索"）。
#
#   ✅ **先把追问补全，再走同一条检索链** —— 检索逻辑一行不改，只是喂给它的输入变干净
#
# ## ⚠️ 但"补全"和"塞历史"的界限在哪（这一点必须说清）
#
# 严格说，本函数**确实会**把上一轮的问句带进检索输入。区别在**带多少、带哪一轮**：
#
#   · **只带最近一轮的问题**（本实现）——
#     `retrieve()` 是"词级匹配 + BFS"，上一轮的主语（如 `TransE`）**正好是我们要的种子**。
#     带它就等于**把缺失的主语补回来**，这是**修复**，不是污染。
#   · **带整个多轮历史**（不做）——
#     历史越长，种子越多且越旧的问题占的权重越大；
#     到第 5 轮时，检索结果会被**第 1 轮的主题**主导。**那才是污染。**
#
# 所以本实现的边界是：**最多带一轮，且只在"短 + 含指代词"时带**。
# 另外**综合 Agent 才看完整历史**（它本来就要理解"用户在问第几个问题"），
# **检索 Agent 只看补全后的单句** —— 两个 Agent 的输入面是分开的。

#
# ## 为什么第一版用纯规则
#
# 用 LLM 重写更准，但它会**给检索加一次模型调用**（多几秒、且离线环境就没了）。
# 纯规则版覆盖的是最常见的一类：**问题短 + 含指代词**。
# 判错时的代价是"检索回退到原始问题"——**与不做重写等价，不会更差**。
_PRONOUNS = ('它', '他', '她', '这个', '那个', '这些', '那些', '该', '此',
             '其', '这', '那', '上述', '前面', '刚才')


def rewrite_question(question, history=None):
    """把追问补全成**可独立检索**的问题。`history` 形如 `[{'question','answer'}]`。

    返回 `(重写后的问题, 用了哪条历史)`；没重写时第二个元素是 `None`。

    **保守原则**：只在"短 + 含指代词"时才拼上一轮；否则原样返回 ——
    因为**误拼比不拼更糟**（会把完全无关的上一轮实体拉进检索）。
    """
    q = str(question or '').strip()
    if not q or not history:
        return q, None
    if len(q) > 30:                      # 长问题通常自带主语，不拼
        return q, None
    if not any(p in q for p in _PRONOUNS):
        return q, None
    last = None
    for h in reversed(history):
        cand = str((h or {}).get('question') or '').strip()
        if cand and cand != q:
            last = cand
            break
    if not last:
        return q, None
    # 拼成一句**读得通**的检索输入：`<上一轮问题>（追问：<当前>）`
    return f'{last}（追问：{q}）', last


def retrieve(question, nodes, edges, max_hops=2, max_nodes=60, history=None):
    """种子匹配 + BFS 子图。

    种子：问题里的词与实体名/描述做**词级**匹配（不是整句子串，避免
    「知识图谱」这种长词把什么都匹配上）。

    **K7（2026-09-15）**：加了 `history`。它**只用于把没主语的追问补全**
    （见 `rewrite_question` 的说明）—— **检索算法本身没有任何改动**。
    返回结果里会带 `rewritten_from`，让"这次检索用的是补全后的问题"可追溯。
    """
    q_used, rewritten_from = rewrite_question(question, history)
    question = q_used
    q = _norm(question)
    q_tokens = [t for t in re.split(r'[^0-9A-Za-z\u4e00-\u9fff]+', question.lower())
                if len(t) >= 2]
    if not q_tokens and not q:
        return {'seeds': [], 'nodes': [], 'edges': [], 'reason': '问题为空'}

    seeds = []
    for n in nodes:
        name = n.get('name') or n.get('id') or ''
        nn = _norm(name)
        desc = _norm(n.get('description') or '')
        hit = False
        if q and len(q) >= 3 and q in nn:
            hit = True
        elif nn and len(nn) >= 3 and nn in q:
            hit = True
        else:
            for t in q_tokens:
                if len(t) >= 3 and (t in nn or t in desc):
                    hit = True
                    break
        if hit:
            seeds.append(name)
    seeds = seeds[:12]

    # BFS
    adj = {}
    for e in edges:
        adj.setdefault(e.get('source'), []).append(e)
        adj.setdefault(e.get('target'), []).append(e)

    seen = set(seeds)
    frontier = deque((s, 0) for s in seeds)
    picked_edges = []
    while frontier and len(seen) < max_nodes:
        cur, hop = frontier.popleft()
        if hop >= max_hops:
            continue
        for e in adj.get(cur, []):
            other = e.get('target') if e.get('source') == cur else e.get('source')
            picked_edges.append(e)
            if other and other not in seen and len(seen) < max_nodes:
                seen.add(other)
                frontier.append((other, hop + 1))

    # 去重边
    uniq = {}
    for e in picked_edges:
        k = (e.get('source'), e.get('target'), e.get('type'))
        uniq.setdefault(k, e)

    by_name = {(n.get('name') or n.get('id')): n for n in nodes}
    return {
        'seeds': seeds,
        'nodes': [by_name[s] for s in seen if s in by_name],
        'edges': list(uniq.values()),
        # K7：把"这次检索实际用的问句"与"补全来源"带出来，便于追溯。
        # 前端会在检索步骤里显示"已把追问补全为：…" —— 否则用户
        # 会奇怪"我只问了'它的缺点'，怎么检索到了 TransE"。
        'question_used': question,
        'rewritten_from': rewritten_from,
        'reason': f'种子 {len(seeds)} 个，{max_hops} 跳扩展至 {len(seen)} 个节点、'
                  f'{len(uniq)} 条边（纯规则，可复现）',
    }


# ────────────────────────────────────────────────────────────────────────
# 证据抽取：把边转成"带论文原文的引文"，这是整套问答的地基
# ────────────────────────────────────────────────────────────────────────
def citations_of(edge, limit=1):
    """从一条边取出指向**论文原文**的引文。"""
    out = []
    for ev in (edge.get('evidence') or [])[:limit]:
        if not isinstance(ev, dict):
            continue
        txt = re.sub(r'\s+', ' ', str(ev.get('text') or '')).strip()
        out.append({
            'paper': ev.get('source_name') or ev.get('source_id') or '',
            'page': ev.get('page'),
            'quote': txt[:400],
            'source_id': ev.get('source_id'),
        })
    return out


def evidence_pack(subgraph, max_items=18):
    """把子图整理成给模型看/给前端显示的证据清单（每条都带原文）。"""
    items = []
    for e in subgraph.get('edges') or []:
        cits = citations_of(e)
        items.append({
            'claim': f"{e.get('source')} —[{e.get('type')}]→ {e.get('target')}",
            'type': e.get('type'),
            'source': e.get('source'),
            'target': e.get('target'),
            'citations': cits,
            'has_quote': bool(cits),
        })
    # 有原文引文的排前面 —— 模型要先看到可核验的东西
    items.sort(key=lambda x: (not x['has_quote'],))
    return items[:max_items]


# ────────────────────────────────────────────────────────────────────────
# ②③④ LLM 调用（本地 Ollama）
# ────────────────────────────────────────────────────────────────────────
def _ollama(prompt, timeout=180):
    """调本地 Ollama。返回文本；失败返回 None（由调用方降级，不抛）。"""
    body = json.dumps({
        'model': MODEL,
        'prompt': prompt,
        'stream': False,
        # 关闭思维链：抽取/判断任务上开思维链会拖慢并偏离格式
        'think': False,
        'options': {'temperature': 0.2, 'num_predict': 600},
    }).encode('utf-8')
    req = urllib.request.Request(
        f'{OLLAMA}/api/generate', data=body,
        headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode('utf-8'))
        return (data.get('response') or '').strip() or None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def _fmt_evidence(items, limit=14):
    lines = []
    for i, it in enumerate(items[:limit], 1):
        c = (it.get('citations') or [{}])[0]
        q = (c.get('quote') or '')[:180]
        loc = f"{c.get('paper','?')} p.{c.get('page','?')}"
        lines.append(f'[{i}] {it["claim"]}\n    引文（{loc}）：{q}')
    return '\n'.join(lines) if lines else '（无证据）'


SUPPORT_PROMPT = """你是**支持方**取证 Agent。只依据下面给出的图谱证据，找出**支持**这个问题的答案的证据。

问题：{q}

证据（每条都来自论文原文，已标注出处）：
{ev}

要求：
1. **只引用上面列出的编号**，不要引入外部知识；
2. 列出最强的 2-4 条支持证据，每条写：编号 + 一句话说明它如何支持；
3. 如果**没有任何一条**能支持，就明确写「无支持证据」——不要编。
4. 用中文，不超过 200 字。
"""

OPPOSE_PROMPT = """你是**反对方**取证 Agent，你的职责是**挑战**支持方的结论。只依据下面给出的图谱证据。

问题：{q}

证据（每条都来自论文原文）：
{ev}

要求：
1. 找出**与"直接肯定答案"相冲突**或**使结论变得不确定**的证据，例如：
   · 类型为 CONTRADICTS / 与支持方结论方向相反的边；
   · 端点在原文引文里**找不到**的边（证据薄弱的信号）；
   · 只在个别论文里出现、缺少旁证的边。
2. 每条写：编号 + 为什么它削弱结论；
3. **如果确实找不到反证，就明确写「未发现反证」** —— 这是有价值的结论，
   不要为了显得有产出而编造。
4. 用中文，不超过 200 字。
"""

SYNTH_PROMPT = """你是**综合** Agent。你只看到两方独立交上来的意见，请给最终结论。

问题：{q}

── 支持方说 ──
{sup}

── 反对方说 ──
{opp}

要求：
1. 一句话给出结论，然后说明依据（支持方哪几条、反对方哪几条）；
2. 明确标注**置信度**：高 / 中 / 低，并说明为什么；
3. 如果两方冲突且无法判定，就如实说「证据不足以判定」；
4. **不要引入两方都没提到的证据**；
5. 用中文，不超过 250 字。
"""


def answer(question, nodes, edges, use_llm=True, history=None):
    """跑完整的四 Agent 问答，返回结构化结果。

    返回值里每个字段都可直接给前端；`degraded=True` 表示模型不可用、
    只有检索结果（绝不假装有模型参与）。

    **K7（2026-09-15）**：`history` 形如 `[{"question","answer"}]`，
    只用于**把没主语的追问补全**（见 `rewrite_question`）——
    检索、取证、综合的算法本身一行未改。
    """
    sub = retrieve(question, nodes, edges, history=history)
    ev = evidence_pack(sub)

    result = {
        'question': question,
        'retrieval': {
            'seeds': sub['seeds'],
            'n_nodes': len(sub['nodes']),
            'n_edges': len(sub['edges']),
            'reason': sub['reason'],
            # K7：把"补全后的问句"带出去，界面据此说明"我把它理解成了什么"
            'question_used': sub.get('question_used'),
            'rewritten_from': sub.get('rewritten_from'),
        },
        'evidence': ev,
        'agents': [],
        'conclusion': '',
        'confidence': '',
        'degraded': False,
        'citations': [c for it in ev for c in (it.get('citations') or [])][:12],
    }

    if not ev:
        result['conclusion'] = '图谱里没有与这个问题相关的证据。'
        result['confidence'] = '—'
        return result

    if not use_llm:
        result['degraded'] = True
        result['conclusion'] = ('（未启用模型）以下是检索到的证据，'
                                '共 %d 条，其中 %d 条带论文原文引文。'
                                % (len(ev), sum(1 for x in ev if x['has_quote'])))
        return result

    block = _fmt_evidence(ev)
    sup = _ollama(SUPPORT_PROMPT.format(q=question, ev=block))
    opp = _ollama(OPPOSE_PROMPT.format(q=question, ev=block))

    if sup is None and opp is None:
        result['degraded'] = True
        result['conclusion'] = ('本地模型不可用（Ollama 未运行或模型未加载），'
                                '已降级为纯规则检索：共 %d 条证据、'
                                '%d 条带原文引文。'
                                % (len(ev), sum(1 for x in ev if x['has_quote'])))
        return result

    result['agents'] = [
        {'role': '支持方', 'text': sup or '（支持方 Agent 无输出）'},
        {'role': '反对方', 'text': opp or '（反对方 Agent 无输出）'},
    ]
    synth = _ollama(SYNTH_PROMPT.format(
        q=question, sup=sup or '（无）', opp=opp or '（无）'))
    result['conclusion'] = synth or ('支持方：%s\n反对方：%s' % (sup, opp))

    # 置信度：从综合文本里抽，抽不到就按反对方是否有收获粗判
    result['confidence'] = _confidence_of(result['conclusion'], opp)
    return result


def _confidence_of(text, opp_text):
    """从综合文本里抽置信度。

    正则要容忍模型的各种写法：`置信度：高` / `置信度 高` / `置信度为高` /
    `置信度是高`（第一次实测模型写的就是「置信度为高」，旧正则漏了，
    结果 confidence 落到兜底分支显示成「中」，而模型明明说的是「高」）。
    """
    m = re.search(r'置信度[：:\s是为]{0,2}\s*(高|中|低)', text or '')
    if m:
        return m.group(1)
    return '低' if (opp_text and '未发现反证' not in opp_text) else '中'


# ════════════════════════════════════════════════════════════════════════
# 分步接口：让「多个 Agent 在分工」这件事在界面上看得见
# ════════════════════════════════════════════════════════════════════════
#
# **为什么要拆**：多 Agent 问答是本项目最主要的看点，而 `answer()` 把
# ②③④ 的结果**一次性**返回 —— 观众看到的只是"一次问答给了两段文字"，
# 看不出有分工，等于把这个看点表达没了。
#
# 拆成四步之后，界面可以一步一行状态：
#
#     ① 检索 Agent   命中 12 个种子 → 2 跳 → 60 节点 / 86 边（纯规则，不调模型）
#     ② 支持 Agent   正在读 18 条证据…
#     ③ 反对 Agent   独立读同一批证据…（不共享支持方的中间结论）
#     ④ 综合 Agent   只看两方交上来的东西
#
# **步骤之间无状态**：证据由前端回传，后端不存 session —— 证据只有几十 KB，
# 回传的成本远低于维护会话状态的风险（这个项目在"状态没落盘"上栽过：
# 上传任务曾因后端重启而丢，见 `app.py` 的 `_save_task`）。

def step_retrieve(question, nodes, edges, history=None):
    """① 检索：纯规则，**不调模型**，所以永远快、永远可复现。

    **K7**：`history` 只用于补全没主语的追问；检索算法本身未改。
    """
    sub = retrieve(question, nodes, edges, history=history)
    ev = evidence_pack(sub)
    return {
        'stage': 'retrieve',
        'agent': '检索 Agent',
        'note': '纯规则：种子匹配 + 2 跳 BFS，未调用模型，结果可复现',
        'retrieval': {
            'seeds': sub['seeds'],
            'n_nodes': len(sub['nodes']),
            'n_edges': len(sub['edges']),
            'reason': sub['reason'],
            # K7：让界面能说出"我把你的追问补全成了什么"
            'question_used': sub.get('question_used'),
            'rewritten_from': sub.get('rewritten_from'),
        },
        'evidence': ev,
        'n_evidence': len(ev),
        'n_with_quote': sum(1 for x in ev if x.get('has_quote')),
    }


def step_agents(question, evidence, role):
    """②③ 支持方 / 反对方 —— **各自独立**读同一批证据。

    分两次调用而不是一次跑两个，是为了让界面能分别显示
    「支持方正在读…」与「反对方正独立地读…」。**"独立"是这个看点的核心**：
    一次返回两段，观众分不清它们是两次独立判断，还是同一段话拆成两半。
    """
    ev = evidence or []
    block = _fmt_evidence(ev)
    if role == 'support':
        txt = _ollama(SUPPORT_PROMPT.format(q=question, ev=block))
        return {'stage': 'agents', 'agent': '支持 Agent', 'role': '支持方',
                'note': '读全部证据，找**支持**答案的边',
                'text': txt, 'ok': txt is not None}
    if role == 'oppose':
        txt = _ollama(OPPOSE_PROMPT.format(q=question, ev=block))
        return {'stage': 'agents', 'agent': '反对 Agent', 'role': '反对方',
                'note': '**独立**读同一批证据（不共享支持方的中间结论），找反证；'
                        '找不到就如实说「未发现反证」，不编',
                'text': txt, 'ok': txt is not None}
    raise ValueError("role 必须是 'support' 或 'oppose'")


def step_synth(question, support, oppose):
    """④ 综合：**只看两方交上来的东西**，不接触原始证据。"""
    txt = _ollama(SYNTH_PROMPT.format(
        q=question, sup=support or '（无）', opp=oppose or '（无）'))
    return {
        'stage': 'synth', 'agent': '综合 Agent',
        'note': '只看到两方交上来的意见，不接触原始证据',
        'text': txt or ('支持方：%s\n反对方：%s' % (support, oppose)),
        'confidence': _confidence_of(txt, oppose),
        'ok': txt is not None,
    }
