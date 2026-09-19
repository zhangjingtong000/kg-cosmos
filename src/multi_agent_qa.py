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
from pathlib import Path

OLLAMA = os.environ.get('OLLAMA_HOST', 'http://127.0.0.1:11434')
MODEL = os.environ.get('KG_QA_MODEL', 'qwen3.5:9b')

# B6 的社区摘要产物（`tools/build_community_summaries.py` 生成）。
# **全库问答的料就是它** —— 18 个社区各一段"这个社区在研究什么"。
# 路径以本文件为锚点解析（不写死盘符，也不依赖 cwd）。
COMMUNITY_SUMMARIES = (Path(__file__).resolve().parent.parent
                       / 'web' / 'kg-data' / 'community-summaries.json')


# ────────────────────────────────────────────────────────────────────────
# ① 检索 Agent —— 纯规则，零模型
# ────────────────────────────────────────────────────────────────────────
def _norm(s):
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]+', '', str(s or '').lower())


# ── 跨语言种子桥接（2026-09-18）─────────────────────────────────────────
#
# ## 为什么必须补这一层
#
# `retrieve()` 的种子是**字面词级匹配**：问题里的词去和**实体名/描述**比。
# 而本项目的图谱是从**英文论文**抽的 —— 实测 `data/3e38f803/graph.json`
# 的 1045 个实体名里**含中文的有 0 个**（作者名、术语、机构名全是英文）。
#
# ⇒ **中文提问必然 0 种子 ⇒ 0 证据 ⇒ ②③④ 三步全部"未执行"。**
# 用户看到的是一句「图谱里没有与这个问题相关的证据」，
# 但真实原因不是"图谱里没有"，而是**问题与图谱不在同一种语言里**。
# 这两句话对用户的意思完全不同，**不能混为一谈**。
#
# 复现（2026-09-18）：
#   · `"What method does this paper propose?"` → 种子 12 个 → 18 条证据 → 四步走完（16 s）
#   · `"这篇论文提出了什么方法？"`             → 种子  0 个 →  0 条证据 → 卡在第一步
#
# ## 边界：这**不是**把检索改成"模型检索"
#
# 「LLM 只做判断，不做检索」是硬纪律，这里**没有破**：
#   · 桥接**只在规则匹配 0 种子、且问题含中文**时才触发 ⇒
#     英文问题、或中文问题里带了能命中的英文术语时，**一次模型都不调**；
#   · 模型产出的是**检索词**，不是答案；选哪些节点、BFS 怎么走，
#     仍然完全由 `retrieve()` 的原规则决定；
#   · 桥接结果写进返回值 `bridge`，界面**必须如实显示**
#     （"我把你的中文问题转成了这些英文词"）——否则用户无法判断
#     检索到底用了什么，K7 的 `rewritten_from` 就是为同一件事做的。
#
# ⚠️ 代价要说清：触发桥接时，**这一步不再"零模型、瞬时"**（多一次本地调用，
# 实测约 3~8 s），且**不再严格可复现**（模型采样）。所以 `reason` 里必须写明，
# 不能让界面继续宣称"纯规则、未调用模型"。

CJK_RE = re.compile(r'[\u4e00-\u9fff]')

BRIDGE_PROMPT = """把下面这个**中文**问题转成用来检索**英文学术知识图谱**的英文关键词。

要求：
· 只输出关键词，用**空格**分隔，3 到 6 个；
· 关键词要**能在英文论文的实体名里出现**（术语、方法名、任务名、指标名），
  例如 knowledge graph / large language model / entity alignment / reasoning；
· **不要**输出句子、解释、编号、markdown、引号、中文；
· 拿不准就用问题里最核心的名词。

中文问题：{q}

英文关键词："""


def _match_seeds(q, q_tokens, nodes):
    """种子匹配 —— 抽成函数是为了让"桥接后的英文词"**走完全同一条规则**。

    ⚠️ 不能给桥接词另写一套更宽松的阈值：那会让中文提问和英文提问
    的检索口径不一致，A/B 之类的对比就失去意义。
    """
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
    return seeds[:12]


def bridge_keywords_zh(question, timeout=60):
    """纯中文问题 → 英文检索关键词。失败返回 `None`（由调用方降级，不抛）。

    `temperature=0` + `num_predict=48`：桥接要的是几个词，不是一段话；
    给多了模型反而会开始解释，把关键词淹掉。
    """
    try:
        raw = _ollama(BRIDGE_PROMPT.format(q=question),
                      timeout=timeout, temperature=0.0, num_predict=48)
    except TypeError:
        # 老签名（没有 temperature/num_predict 参数）时的兜底，不让桥接把检索拖崩
        raw = _ollama(BRIDGE_PROMPT.format(q=question), timeout=timeout)
    if not raw:
        return None
    # 模型偶尔还是会带解释/换行/项目符号 —— 只留**纯 ASCII 词**，其余丢掉。
    line = raw.strip().splitlines()[0] if raw.strip() else ''
    words = re.findall(r'[A-Za-z][A-Za-z0-9\-]{1,}', line)
    seen, out = set(), []
    for w in words:
        lw = w.lower()
        if lw in seen or len(lw) < 3:
            continue
        seen.add(lw)
        out.append(w)
        if len(out) >= 6:
            break
    return ' '.join(out) if out else None


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

    **跨语言桥接（2026-09-18）**：本项目的图谱抽自英文论文（实体名里
    含中文的为 0 个），纯中文问题在词面匹配下**必然是 0 种子**。
    所以规则匹配落空、且问题含中文时，会调一次本地模型把问题转成
    **英文检索关键词**，再走**完全同一套** `_match_seeds` + BFS。
    触发与否、用的什么词、有没有命中，全部写进返回值的 `bridge`，
    并且 `reason` 会如实改口（不再宣称"纯规则、未调用模型"）。
    """
    q_used, rewritten_from = rewrite_question(question, history)
    question = q_used
    q = _norm(question)
    q_tokens = [t for t in re.split(r'[^0-9A-Za-z\u4e00-\u9fff]+', question.lower())
                if len(t) >= 2]
    if not q_tokens and not q:
        return {'seeds': [], 'nodes': [], 'edges': [], 'reason': '问题为空'}

    seeds = _match_seeds(q, q_tokens, nodes)

    # ── 跨语言桥接：只在"规则落空 + 含中文"时走（见 BRIDGE_PROMPT 上方的说明）
    bridge = None
    if not seeds and CJK_RE.search(question or ''):
        kw = bridge_keywords_zh(question)
        if kw:
            q_b = _norm(kw)
            toks_b = [t for t in re.split(r'[^0-9A-Za-z\u4e00-\u9fff]+', kw.lower())
                      if len(t) >= 2]
            seeds = _match_seeds(q_b, toks_b, nodes)
            # 命中与否都要如实记：桥接失败也是结果，不能让界面以为它成功了
            bridge = {'query_en': kw, 'seeds': list(seeds), 'hit': bool(seeds)}
        else:
            bridge = {'query_en': None, 'seeds': [], 'hit': False,
                      'note': '模型未返回可用英文关键词（模型不可用或超时）'}

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
    # `reason` 是给用户看的一句话解释 —— **做了什么就必须说什么**：
    # 走了桥接就不能再写"纯规则、可复现"，落了空也要说清是"没有交集"
    # 而不是让用户以为"图谱里本来就没有这个知识"。
    if bridge and bridge.get('query_en'):
        tail = ('（中文问题先转成英文检索词「%s」%s；这一步调用了本地模型，'
                '不再严格可复现）' % (bridge['query_en'],
                                      '命中' if bridge.get('hit') else '仍未命中'))
    elif bridge:
        tail = '（中文问题需要先转英文检索词，但本地模型没有返回可用关键词）'
    else:
        tail = '（纯规则，可复现）'
    if not seeds:
        # ⚠️ **不要在这里写 markdown 粗体** —— 这个字符串会经
        # `setAgent()` 的 `textContent` 直接进界面，`**` 会原样显示成星号。
        # 本项目已经栽过一次"markdown 泄漏进 DOM"（18 处）。
        tail += '；问题与图谱实体没有词面交集 ⇒ 0 条证据'
    return {
        'seeds': seeds,
        'nodes': [by_name[s] for s in seen if s in by_name],
        'edges': list(uniq.values()),
        # K7：把"这次检索实际用的问句"与"补全来源"带出来，便于追溯。
        # 前端会在检索步骤里显示"已把追问补全为：…" —— 否则用户
        # 会奇怪"我只问了'它的缺点'，怎么检索到了 TransE"。
        'question_used': question,
        'rewritten_from': rewritten_from,
        # 跨语言桥接的完整记录（转成什么词、命中哪些种子）—— 界面必须能显示
        'bridge': bridge,
        'reason': f'种子 {len(seeds)} 个，{max_hops} 跳扩展至 {len(seen)} 个节点、'
                  f'{len(uniq)} 条边{tail}',
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
def _ollama(prompt, timeout=180, temperature=0.2, num_predict=600):
    """调本地 Ollama。返回文本；失败返回 None（由调用方降级，不抛）。

    `temperature` / `num_predict` 可调，是为了**跨语言桥接**那一步：
    它要的是"几个英文关键词"，不是一段判断，给 600 token 反而会让模型
    开始写解释、把关键词淹掉（见 `bridge_keywords_zh`）。
    两个参数的默认值**保持原样**，所以支持/反对/综合三个 Agent 的行为不变。
    """
    body = json.dumps({
        'model': MODEL,
        'prompt': prompt,
        'stream': False,
        # 关闭思维链：抽取/判断任务上开思维链会拖慢并偏离格式
        'think': False,
        'options': {'temperature': temperature, 'num_predict': num_predict},
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
    """把证据格式化成给模型看的文本。

    **两种证据形状，两种格式化**（2026-09-26 加全库模式）：
      · 图证据（单篇模式）：每条带 `citations`，格式化时写出论文名与页码；
      · 社区级证据（全库模式）：**没有引文**（它不是论文原文），
        格式化时写出社区名、摘要正文、成员实体。

    ⇒ 判据放在这里而不是让调用方传参：**"这批证据是社区级的"这件事
      写在数据里**（`level == 'community'`），而不是靠调用方记得传对标志 ——
      调用方一旦忘了传，社区摘要就会被当成论文原文喂给模型（那是本项目的红线）。
    """
    if items and all(isinstance(it, dict) and it.get('level') == 'community'
                     for it in items):
        return _fmt_community_evidence(items, limit)
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

# ── 全库模式（社区级摘要）的三套 prompt（2026-09-26）─────────────────────
#
# **为什么要另写三套，而不是改上面那三套**：
# 上面那三套里写着"证据（每条都来自论文原文，已标注出处）"——
# 这句话对社区摘要是**假的**：摘要是本地模型根据社区的实体与关系**生成**的概括，
# 它没有页码、不是任何一篇论文的原话。
# 拿旧 prompt 去读社区摘要，等于**把生成的摘要说成原文证据** ——
# 这正是本项目"不要把结构推断说成事实"那条纪律要防的事。
#
# 三套新 prompt 的第一句就把边界写死，并要求模型**不许**用
# "某篇论文证明了……"这种口径，只能说"某个社区关注……"。

COMMUNITY_SUPPORT_PROMPT = """你是**支持方**取证 Agent。下面给你的**不是论文原文**，而是\
**社区级摘要** —— 每条由一个本地模型根据该社区的实体与关系写出，用来概括"这个社区在研究什么"。

问题：{q}

{ev}

要求：
1. **只引用上面列出的编号**，不要引入外部知识；
2. 列出最相关的 2-4 条，每条写：编号 + 一句话说明它如何支持这个问题的答案；
3. 如果**没有任何一条**能支持，就明确写「无支持证据」——不要编；
4. **不许把社区摘要说成论文原文**：可以说"某社区围绕……展开"，
   不能说"某篇论文证明了……"。
5. 用中文，不超过 200 字。
"""

COMMUNITY_OPPOSE_PROMPT = """你是**反对方**取证 Agent，职责是**挑战**支持方的结论。\
下面给你的**不是论文原文**，而是**社区级摘要**（本地模型根据社区的实体与关系生成的概括）。

问题：{q}

{ev}

要求：
1. 找出**与"直接肯定答案"相冲突**或**使结论变得不确定**的东西，例如：
   · 摘要自己说"这个社区没有明显的共同主题"（那它就不该被当成一个论据）；
   · 摘要的成员实体与问题只是表面词面相同、主题其实不同；
   · 摘要里提到的实体**不在它自己的成员清单里**（接地性可疑）。
2. 每条写：编号 + 为什么它削弱结论；
3. **如果确实找不到反证，就明确写「未发现反证」** —— 这是有价值的结论，
   不要为了显得有产出而编造。
4. **不把社区摘要当作论文原文**（它有出处，但那个出处是"某个社区"，不是某一页）。
5. 用中文，不超过 200 字。
"""

COMMUNITY_SYNTH_PROMPT = """你是**综合** Agent。你只看到两方独立交上来的意见，请给最终结论。

⚠️ 这一轮的证据全部是**社区级摘要**（本地模型根据社区实体与关系生成的概括），\
**不是论文原文**。结论必须按这个口径写。

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
5. **不许说成"论文原文表明"**：应当说"社区摘要显示……"，
   并说明这是**社区级概括**而不是某一篇论文的原话。
6. 用中文，不超过 250 字。
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
            # 跨语言桥接记录（与 `step_retrieve` 保持同一字段名）
            'bridge': sub.get('bridge'),
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
    """① 检索：**默认**纯规则、不调模型，所以永远快、永远可复现。

    **例外是跨语言桥接**（见 `BRIDGE_PROMPT` 上方）：纯中文问题在
    英文图谱上词面匹配必然落空，此时会多一次本地模型调用把问题转成
    英文检索词。`note` 会如实改口 —— **不能让界面在调了模型之后
    还宣称"未调用模型"**。

    **K7**：`history` 只用于补全没主语的追问；检索算法本身未改。
    """
    sub = retrieve(question, nodes, edges, history=history)
    ev = evidence_pack(sub)
    bridge = sub.get('bridge')
    note = '纯规则：种子匹配 + 2 跳 BFS，未调用模型，结果可复现'
    if bridge:
        if bridge.get('query_en'):
            note = ('**中文问题 → 先转成英文检索词**「%s」，再走同一条规则检索%s'
                    % (bridge['query_en'],
                       '（命中）' if bridge.get('hit') else '（仍未命中）'))
        else:
            note = ('中文问题需要先转成英文检索词，但本地模型未返回可用关键词'
                    '（检索退化为 0 种子，如实报告，不编）')
    return {
        'stage': 'retrieve',
        'agent': '检索 Agent',
        'note': note,
        'retrieval': {
            'seeds': sub['seeds'],
            'n_nodes': len(sub['nodes']),
            'n_edges': len(sub['edges']),
            'reason': sub['reason'],
            # K7：让界面能说出"我把你的追问补全成了什么"
            'question_used': sub.get('question_used'),
            'rewritten_from': sub.get('rewritten_from'),
            # 跨语言桥接：转成了什么词、有没有命中 —— 必须让界面能显示
            'bridge': bridge,
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

    **全库模式（2026-09-26）**：证据可能是**社区级摘要**而不是论文原文。
    此时换一套 prompt —— 原 prompt 里写着"每条都来自论文原文"，
    拿它去读社区摘要就等于**把生成的摘要说成原文证据**（本项目红线）。
    判据取自**证据自身的 `level` 字段**，不由调用方传参（见 `_fmt_evidence`）。
    """
    ev = evidence or []
    block = _fmt_evidence(ev)
    comm = _is_community_evidence(ev)
    if role == 'support':
        txt = _ollama((COMMUNITY_SUPPORT_PROMPT if comm else SUPPORT_PROMPT)
                      .format(q=question, ev=block))
        return {'stage': 'agents', 'agent': '支持 Agent', 'role': '支持方',
                'note': ('读全部社区级摘要，找支持答案的社区；'
                         '摘要由本地模型生成，不许说成论文原文'
                         if comm else '读全部证据，找**支持**答案的边'),
                'evidence_level': 'community' if comm else 'graph',
                'text': txt, 'ok': txt is not None}
    if role == 'oppose':
        txt = _ollama((COMMUNITY_OPPOSE_PROMPT if comm else OPPOSE_PROMPT)
                      .format(q=question, ev=block))
        return {'stage': 'agents', 'agent': '反对 Agent', 'role': '反对方',
                'note': ('**独立**读同一批社区级摘要（不共享支持方的中间结论），'
                         '找反证；找不到就如实说「未发现反证」，不编'
                         if comm else
                         '**独立**读同一批证据（不共享支持方的中间结论），找反证；'
                         '找不到就如实说「未发现反证」，不编'),
                'evidence_level': 'community' if comm else 'graph',
                'text': txt, 'ok': txt is not None}
    raise ValueError("role 必须是 'support' 或 'oppose'")


def step_synth(question, support, oppose, level='graph'):
    """④ 综合：**只看两方交上来的东西**，不接触原始证据。

    `level='community'` 时用社区版 prompt（2026-09-26）：综合方虽然看不到原始证据，
    但它会写"依据是哪些"，而那个依据的上游是**社区级摘要**而不是论文原文。
    原 prompt 里没有这一句 ⇒ 结论会顺着"论文原文"的口径写下去。
    **默认值 `'graph'` 保证单篇模式一字不变。**
    """
    comm = (level == 'community')
    txt = _ollama((COMMUNITY_SYNTH_PROMPT if comm else SYNTH_PROMPT).format(
        q=question, sup=support or '（无）', opp=oppose or '（无）'))
    return {
        'stage': 'synth', 'agent': '综合 Agent',
        'note': '只看到两方交上来的意见，不接触原始证据',
        'evidence_level': 'community' if comm else 'graph',
        'text': txt or ('支持方：%s\n反对方：%s' % (support, oppose)),
        'confidence': _confidence_of(txt, oppose),
        'ok': txt is not None,
    }


# ════════════════════════════════════════════════════════════════════════
# 全库问答（GraphRAG 式 Global Search）—— 2026-09-26
# ════════════════════════════════════════════════════════════════════════
#
# ## 为什么加这一节
#
# B6 已经把 **18 个社区摘要** 做好了（`web/kg-data/community-summaries.json`，
# 并已接到社区星云页展示），**但问答链路完全没用它** ——
# 单篇问答的检索是"字面命中实体 → 2 跳 BFS → 子图"，问题必须落在**某一篇论文**的图上。
# ⇒ 用户问「这批文献整体在研究什么」「有哪些互相矛盾的观点」时，系统答不了：
#   这两句话在任何单篇图里都没有对的实体可命中。
#
# 这一节把现成的社区摘要接进同一条四步链路（检索 → 支持 → 反对 → 综合），
# 对应 Microsoft GraphRAG 的 **Global Search**：
# 全局问题不落到某一篇论文，而是落到**社区级摘要**上。
#
# ## 三条纪律
#
# **① 零模型检索**：社区的选取是**词面匹配 + 可复现打分**，不调模型。
#   项目里有 `src/embeddings.py`（bge-m3），**这里刻意不用**：
#     · 它需要 Ollama 在跑且模型已加载 —— **检索不该因为模型没起就整个失效**；
#     · 实测它在"短名是否同一实体"上单阈值必错（见 `embeddings.py` 头部那张表），
#       而我们要的正是"社区名/实体名 与 问题 有没有交集"这种**字面**判断；
#     · 单篇模式已经有一套字面判据（`_match_seeds`），**全库模式沿用同一套口径**，
#       两种模式之间才可比。
#
# **② 社区摘要不许被说成论文原文**：证据里带 `level='community'` 与
#   `level_label`，`citations` 恒为空（**从数据结构上就不给"伪装成原文引文"留位置**），
#   支持/反对/综合三步都换用社区版 prompt（原 prompt 写着"每条都来自论文原文"）。
#
# **③ 找不到就如实说**：一个问题若在社区摘要里**没有任何词面命中**，
#   如实报 `scope='all'`（"没有命中任何社区的标识词 ⇒ 给的是全库综述"），
#   而不是假装精确检索到了。没有社区摘要文件时 `scope='none'`、证据 0 条。
#
# ## 全库检索的打分口径（可复现、可解释）
#
#   社区名命中        +3   （"Embeddings" 出现在问题里 —— 最强的信号）
#   每个成员实体命中  +2   （最多计 3 个 —— 社区的核心实体名被点到）
#   摘要正文用词命中  +1   （最多计 2 个 —— 弱信号，中文摘要对中文问题有用）
#   命中阈值 2.0 ⇒ 至少命中 1 个成员实体，或社区名，才算"指向了这个社区"。
#
#   **中文问题为什么常常落空**：社区名与成员实体是**英文**（图谱抽自英文论文），
#   而中文问题被切出来的是整串汉字，与英文标识词没有词面交集 ⇒ 0 命中 ⇒
#   走 `scope='all'`（全库综述）。**这恰好是全局问题的正确行为**，
#   而且不需要为此调一次模型做翻译。

COMMUNITY_NAME_WEIGHT = 3.0
COMMUNITY_MEMBER_WEIGHT = 2.0
COMMUNITY_SUMMARY_WEIGHT = 1.0
COMMUNITY_MIN_SCORE = 2.0
COMMUNITY_TOP_K = 4
# `scope='all'` 时取几个社区：**不取全部 18 个** ——
# 每条摘要约 300–900 字，18 条塞进 prompt 会超过本地模型的上下文，
# 结果是被静默截断（"给了但模型没看到"，比不给更危险）。
# 取规模最大的 6 个：它们覆盖的实体最多，最接近"这群文献整体在研究什么"。
COMMUNITY_ALL_LIMIT = 6

# 缓存：路径 → (mtime, size, 记录列表)。**按 mtime 失效**，
# 这样重新跑一次 B6 生成摘要后不必重启后端。
_COMM_CACHE = {}


def _community_record(rec, meta):
    """把 `community-summaries.json` 的一条压成检索与展示都要用的形状。

    **成员的来源是 `facts.core_ids`**（生成摘要时喂给模型的那批核心实体）——
    它就是摘要的"接地清单"：摘要里提到的实体名应当在这一批里
    （B6 的 `--verify` 正是按这个校验的）。
    """
    facts = rec.get('facts') or {}
    grounding = rec.get('grounding') or {}
    return {
        'id': str(rec.get('id') or ''),
        'name': str(rec.get('name') or ''),
        'summary': str(rec.get('summary') or '').strip(),
        'members': [str(x) for x in (facts.get('core_ids') or []) if x],
        'n_nodes': facts.get('n_nodes'),
        'n_edges': facts.get('n_edges'),
        'top_types': facts.get('top_types') or {},
        'grounding_ok': bool(grounding.get('has_citation_line')
                             and not grounding.get('cited_unknown')),
        'says_no_theme': bool(grounding.get('says_no_theme')),
        'ok': bool(rec.get('ok')),
        'model': meta.get('model') or '',
        'generated': meta.get('generated') or '',
        'source': 'web/kg-data/community-summaries.json',
    }


def load_community_summaries(path=None, use_cache=True):
    """读 B6 的社区摘要。**失败返回 `[]`，不抛** —— 由调用方如实报"没有社区摘要"。

    只保留 `ok=True` 且摘要正文非空的条目：B6 的记录里可能有
    "模型返回空 response"这类失败项（脚本自己会把它们标成 `ok: False`），
    把它们当成"这个社区没有内容"喂给模型，就是在**把失败当事实**。

    **不缓存跨进程**：每次按 mtime 判断。测试传 `path` 时可关缓存。
    """
    p = Path(path) if path else COMMUNITY_SUMMARIES
    try:
        st = p.stat()
    except OSError:
        return []
    key = str(p)
    hit = _COMM_CACHE.get(key)
    if use_cache and hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2]
    try:
        d = json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    meta = {'model': d.get('model'), 'generated': d.get('generated')}
    out = []
    for rec in (d.get('communities') or []):
        if not isinstance(rec, dict):
            continue
        r = _community_record(rec, meta)
        if r['ok'] and r['summary']:
            out.append(r)
    if use_cache:
        _COMM_CACHE[key] = (st.st_mtime, st.st_size, out)
    return out


def community_catalog(path=None):
    """社区摘要的**轻量清单**（不含正文）—— 给界面显示"全库 = N 个社区摘要"。"""
    comms = load_community_summaries(path)
    return {
        'n': len(comms),
        'source': 'web/kg-data/community-summaries.json',
        'model': (comms[0].get('model') if comms else '') or '',
        'generated': (comms[0].get('generated') if comms else '') or '',
        'purpose': 'B6：每个社区一段"这个社区在研究什么"（GraphRAG 的社区摘要）',
        'communities': [{'id': c['id'], 'name': c['name'],
                         'n_nodes': c['n_nodes'], 'n_edges': c['n_edges']}
                        for c in comms],
    }


def _term_hits(term, q, q_tokens):
    """词面命中 —— 与 `_match_seeds` **同一套判据**（互相包含 / 词级包含）。

    刻意不另写一套更宽松的阈值：那会让"全库模式"和"单篇模式"对
    "什么叫命中"给出不同答案，两者之间的任何对比都失去意义
    （这条理由与 `_match_seeds` 上方的注释是同一条）。
    """
    n = _norm(term)
    if not n or len(n) < 2:
        return False
    if q and len(n) >= 3 and n in q:
        return True
    if q and len(q) >= 3 and q in n:
        return True
    return any(len(t) >= 3 and t in n for t in q_tokens)


def score_community(question, community):
    """给一个社区打分。返回 `(分数, 命中说明列表)`。**纯函数、零模型。**"""
    q = _norm(question)
    q_tokens = [t for t in re.split(r'[^0-9A-Za-z\u4e00-\u9fff]+',
                                    str(question or '').lower()) if len(t) >= 2]
    score, matched = 0.0, []

    name = community.get('name') or ''
    if _term_hits(name, q, q_tokens):
        score += COMMUNITY_NAME_WEIGHT
        matched.append('社区名：%s' % name)

    mhits = [m for m in (community.get('members') or []) if _term_hits(m, q, q_tokens)][:3]
    if mhits:
        score += COMMUNITY_MEMBER_WEIGHT * len(mhits)
        matched.append('成员实体：%s' % '、'.join(mhits))

    s = _norm(community.get('summary'))
    shits = [t for t in q_tokens if len(t) >= 3 and t in s][:2]
    if shits:
        score += COMMUNITY_SUMMARY_WEIGHT * len(shits)
        matched.append('摘要用词：%s' % '、'.join(shits))
    return score, matched


def retrieve_communities(question, communities=None, top_k=COMMUNITY_TOP_K):
    """**全库检索**：在所有社区摘要上做词面匹配，取最相关的 top-K。

    返回 `{'hits': [...], 'scope': 'focused'|'all'|'none', 'n_total': N, 'reason': str}`。

    三种 `scope` 各自的含义（**界面必须照实显示，不能都写成"检索到了"**）：
      · `focused` —— 有社区被词面命中，取分数最高的 top-K；
      · `all`     —— **一个都没命中** ⇒ 覆盖规模最大的若干社区，
                     这是**全库综述**（GraphRAG global search 的做法），
                     不是"精确检索到了这些社区"；
      · `none`    —— 没有可用的社区摘要（文件缺失/为空）或问题为空。
    """
    q = str(question or '').strip()
    comms = communities if communities is not None else load_community_summaries()
    n_total = len(comms)
    if not q:
        return {'hits': [], 'scope': 'none', 'n_total': n_total, 'reason': '问题为空'}
    if not comms:
        return {'hits': [], 'scope': 'none', 'n_total': 0,
                'reason': ('没有可用的社区摘要'
                           '（web/kg-data/community-summaries.json 缺失或为空）'
                           '—— 全库模式需要它，这里如实报"找不到"，不编')}

    scored = []
    for c in comms:
        sc, matched = score_community(q, c)
        scored.append((sc, c, matched))
    # 同分时：节点多的社区优先，再按 id 稳定排序（**结果必须可复现**）
    scored.sort(key=lambda t: (-t[0], -(t[1].get('n_nodes') or 0),
                               str(t[1].get('id') or '')))

    k = max(1, int(top_k or COMMUNITY_TOP_K))
    focused = [t for t in scored if t[0] >= COMMUNITY_MIN_SCORE][:k]
    if focused:
        hits = [{'community': c, 'score': sc, 'matched': mt} for sc, c, mt in focused]
        return {
            'hits': hits, 'scope': 'focused', 'n_total': n_total,
            'reason': ('社区摘要词面匹配：命中 %d 个社区（共 %d 个，取分数最高的 %d 个）'
                       '—— 纯规则、零模型、可复现'
                       % (len(focused), n_total, len(hits))),
        }

    bysize = sorted(scored, key=lambda t: (-(t[1].get('n_nodes') or 0),
                                           str(t[1].get('id') or '')))[:COMMUNITY_ALL_LIMIT]
    hits = [{'community': c, 'score': 0.0, 'matched': ['（词面未命中）']}
            for _sc, c, _mt in bysize]
    return {
        'hits': hits, 'scope': 'all', 'n_total': n_total,
        'reason': ('问题没有命中任何社区的标识词（社区名 / 成员实体）'
                   '⇒ 覆盖规模最大的 %d 个社区（共 %d 个）：这是「全库综述」，'
                   '不是精确检索 —— 纯规则、零模型、可复现'
                   % (len(hits), n_total)),
    }


def _is_community_evidence(ev):
    """这批证据是不是社区级的。**判据在数据里，不靠调用方传参。**"""
    return bool(ev) and all(isinstance(it, dict) and it.get('level') == 'community'
                            for it in ev)


def community_evidence_pack(retrieval, max_items=None):
    """把命中的社区整理成**社区级证据**（形状与图证据兼容，但语义不同）。

    两条刻意的设计：

      · `citations` **恒为空**：社区摘要是**生成的**，不是论文原文，
        它没有页码可指。留一个空的 citations 而不是填社区名进去 ——
        空列表会让界面显示"这条没有原文引文"，
        **而"看起来像原文引文"的那种填充会让人以为它是原文**（本项目的红线）。
      · `has_quote=False`：沿用 `evidence_pack` 的字段名，
        于是所有"按 has_quote 判断"的既有代码都会把社区证据归到"无原文引文"一侧 ——
        **这正是事实**。
    """
    items = []
    for h in (retrieval or {}).get('hits') or []:
        c = h.get('community') or {}
        members = c.get('members') or []
        items.append({
            'claim': '社区「%s」（%s）· %s 实体 / %s 关系' % (
                c.get('name') or '?', c.get('id') or '?',
                c.get('n_nodes') if c.get('n_nodes') is not None else '?',
                c.get('n_edges') if c.get('n_edges') is not None else '?'),
            'type': 'COMMUNITY_SUMMARY',
            'source': '社区：%s' % (c.get('name') or c.get('id') or '?'),
            'target': '成员实体：%s' % ('、'.join(members[:6]) if members else '（无记录）'),
            'citations': [],
            'has_quote': False,
            # ★ 这一对字段是"别把结构推断说成事实"那条纪律在数据层的落点
            'level': 'community',
            'level_label': '社区级摘要（本地模型生成 · 不是论文原文）',
            'matched': h.get('matched') or [],
            'score': h.get('score'),
            'community': {
                'id': c.get('id'), 'name': c.get('name'),
                'summary': c.get('summary') or '',
                'members': members,
                'n_nodes': c.get('n_nodes'), 'n_edges': c.get('n_edges'),
                'model': c.get('model'), 'generated': c.get('generated'),
                'source': c.get('source'),
                'grounding_ok': c.get('grounding_ok'),
                'says_no_theme': c.get('says_no_theme'),
            },
        })
    return items[:max_items] if max_items else items


def _fmt_community_evidence(items, limit=14):
    """社区级证据 → 给模型看的文本。**开头第一句就说明它不是论文原文。**"""
    lines = ['（注意：下面每一条都是**社区级摘要** —— 由本地模型根据该社区的实体与关系'
             '写出的概括，不是论文原文，也不是任何一篇论文的原话。）']
    for i, it in enumerate(items[:limit], 1):
        c = it.get('community') or {}
        lines.append('[%d] %s\n    社区摘要：%s\n    成员实体：%s' % (
            i, it.get('claim'),
            (c.get('summary') or '（摘要缺失）')[:400],
            '、'.join((c.get('members') or [])[:8]) or '（无）'))
    return '\n'.join(lines)


def step_retrieve_global(question, communities=None, top_k=COMMUNITY_TOP_K, history=None):
    """① 全库检索：**在所有社区摘要上做词面匹配**（不碰单篇图的实体与边）。

    返回形状与 `step_retrieve` 保持一致（`stage` / `note` / `retrieval` /
    `evidence` / `n_evidence` / `n_with_quote`），所以前端可以用同一套渲染代码，
    只在 `retrieval.mode == 'global'` 时换文案。

    `n_nodes` / `n_edges` 恒为 0：全库模式**没有子图**（它检索的是社区摘要，
    不是节点与边）。填 0 而不是拿社区数去凑，是为了让界面上的数字**不撒谎**。

    `history` 与单篇模式同样只用于**补全没主语的追问**（`rewrite_question`）。
    """
    q_used, rewritten_from = rewrite_question(question, history)
    r = retrieve_communities(q_used, communities, top_k=top_k)
    ev = community_evidence_pack(r)
    scope = r.get('scope')
    n_total = r.get('n_total') or 0

    note = ('全库模式：在 %d 条社区摘要上做词面匹配（不查某一篇论文的实体与边），'
            '未调用模型，结果可复现' % n_total)
    if scope == 'all':
        note += ('；问题没有命中任何社区的标识词 ⇒ 覆盖规模最大的 %d 个社区，'
                 '这是全库综述而不是精确检索' % len(ev))
    elif scope == 'none':
        note = r.get('reason') or '没有可用的社区摘要'

    return {
        'stage': 'retrieve',
        'agent': '检索 Agent（全库 · 社区摘要）',
        'mode': 'global',
        'note': note,
        'retrieval': {
            'mode': 'global',
            'scope': scope,
            # 命中社区的**名字**放进 seeds：界面上"命中了什么"要看得见
            'seeds': [h['community'].get('name') for h in (r.get('hits') or [])],
            'n_nodes': 0,
            'n_edges': 0,
            'n_communities': len(ev),
            'n_communities_total': n_total,
            'reason': r.get('reason'),
            'question_used': q_used,
            'rewritten_from': rewritten_from,
            'bridge': None,
            'source': 'web/kg-data/community-summaries.json',
        },
        'evidence': ev,
        'n_evidence': len(ev),
        # 社区摘要**没有**论文原文引文 —— 0 就是 0，不许含糊
        'n_with_quote': 0,
    }
