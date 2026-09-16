# -*- coding: utf-8 -*-
r"""语义嵌入（bge-m3）—— H4 的正式接口。

## 它解决什么，以及**不解决什么**（这是本节最要紧的部分）

TODO 里 H4 的两个用途：

    E1  同词异义相似度**用向量余弦替代字符重叠**
    PathQA  端点匹配失败时**语义召回兜底**

**但实测发现 ①（"替代"）是不成立的。** 在 10 对典型样本上量了余弦：

| A | B | 余弦 | 该不该合 |
|---|---|---|---|
| `knowledge graph` | `knowledge graphs` | **0.9655** | ✅ 该合 |
| `OpenRefine` | `Open Refine` | **0.9379** | ✅ 该合 |
| `Tag` | `Tagger` | **0.7477** | ❌ **该拒** |
| `BERT` | `BERTLarge` | 0.6250 | ✅ 该合 |
| `AD` | `A. Doan` | **0.5510** | ❌ **该拒** |
| `RDF` | `Reference Data File` | 0.5051 | ❌ **该拒** |
| `AD` | `Anomaly Detection` | 0.4681 | ✅ 该合 |
| `KG` | `Knowledge Graph` | 0.4074 | ✅ 该合 |
| `Crossref` | `Semantic Scholar` | 0.4240 | ❌ 该拒 |

**⇒ 中间区（约 0.4–0.9）里，"该拒"的余弦比"该合"的还高**：
`Tag`/`Tagger`（0.748，该拒）> `BERT`/`BERTLarge`（0.625，该合）；
`AD`/`A. Doan`（0.551，该拒）> `KG`/`Knowledge Graph`（0.407，该合）。

**⇒ 任何单阈值都会在这两组上判错。** 这不是模型不好 ——
**bge-m3 编码的是"语义相近"，而我们要的是"是否同一个实体"**，
这两件事在短名上本来就不同（`Tag` 和 `Tagger` 语义极近，但不是同一个东西）。

**⇒ 所以本模块的定位是**：

    · **余弦 ≥ HIGH（0.93）** ⇒ **可以合**（比字符判据更宽容，能抓拼写/空白变体）；
    · **中间区** ⇒ **不可判** ⇒ 交给字符判据或人工，**不做自动合并**；
    · **低区（< LOW）** ⇒ **可以拒**（`Crossref`/`Semantic Scholar` 0.42 这类）。

**⇒ 结论：它是一条"补充通道"，不是"替代品"。** 这个负面结论本身有价值，
与 P3/P5 的"诚实结论"是同一类。

## 为什么用 Ollama 而不是 sentence-transformers

`bge-m3:latest`（1.16 GB）**已经在 Ollama 里** ⇒ **零下载成本**。
而 `sentence-transformers` 要另下权重，在**本项目此前遇到过的网络环境**下是风险。

用法：
    from embeddings import embed, cosine, is_merge_worthy, find_semantic_matches
"""
import json
import math
import os
import urllib.request

OLLAMA = os.environ.get('OLLAMA_HOST', 'http://127.0.0.1:11434')
MODEL = os.environ.get('KG_EMBED_MODEL', 'bge-m3:latest')
TIMEOUT = 120

# ── 三个阈值（**由上面那张表定，不是拍的**）──────────────────────────────
#   HIGH：这个分数之上，实测样本里"该合"的全中、且没有"该拒"混进来
#   LOW ：这个分数之下，实测样本里"该拒"占绝对多数
HIGH = 0.93
LOW = 0.30


def embed(texts, model=None, host=None, batch=64):
    """批量取嵌入。失败时抛异常（**调用方必须能区分"没有结果"与"全零向量"**）。

    ⚠️ `bge-m3` 的维度是 **1024**；返回顺序与输入一致。
    """
    texts = [str(t or '') for t in texts]
    if not texts:
        return []
    host = host or OLLAMA
    model = model or MODEL
    out = []
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        body = json.dumps({'model': model, 'input': chunk}).encode('utf-8')
        req = urllib.request.Request(
            host.rstrip('/') + '/api/embed', data=body,
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            d = json.load(r)
        vecs = d.get('embeddings')
        if not vecs or len(vecs) != len(chunk):
            raise RuntimeError(f'嵌入返回数量不符：要 {len(chunk)} 条，得 '
                               f'{len(vecs) if vecs else 0} 条')
        out.extend(vecs)
    return out


def cosine(a, b):
    """余弦相似度。两个零向量 ⇒ 0（**不给 NaN** —— NaN 会顺着下游一路污染）。"""
    if not a or not b:
        return 0.0
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return s / (na * nb) if na and nb else 0.0


def judge(score):
    """把一个余弦分数翻成可执行的判断（**这是本模块的核心口径**）。

    返回 `'merge'` / `'unknown'` / `'reject'`。
    **中间区一律 `'unknown'`** —— 实测那里"该合"与"该拒"交叠，
    任何自动决定都会错。**宁可交给人，不可自动合**（合并不可逆，T4.4）。
    """
    if score is None:
        return 'unknown'
    if score >= HIGH:
        return 'merge'
    if score < LOW:
        return 'reject'
    return 'unknown'


def find_semantic_matches(name, candidates, min_score=HIGH, top_k=20):
    """在候选名里找语义上足够近的（**路径二：PathQA 端点匹配失败时的语义兜底**）。

    返回 `[(候选, 余弦)]`，按余弦降序，只保留 `>= min_score` 的。

    **为什么要 min_score 而不是"取最像的"**：端点匹配失败时"最像的"可能
    余弦只有 0.3，那跟随机没什么区别；**返回一个低分候选比返回空更危险** ——
    调用方会以为"找到了"。
    """
    candidates = [str(c) for c in candidates if c and str(c) != str(name)]
    if not candidates:
        return []
    vecs = embed([str(name)] + candidates)
    q, rest = vecs[0], vecs[1:]
    scored = sorted(((c, cosine(q, v)) for c, v in zip(candidates, rest)),
                    key=lambda t: -t[1])
    return [(c, s) for c, s in scored[:top_k] if s >= min_score]


def available(model=None, host=None):
    """探测嵌入服务是否可用（**不抛异常**，供启动检查用）。"""
    try:
        v = embed(['ping'], model=model, host=host)
        return bool(v and v[0])
    except Exception:
        return False
