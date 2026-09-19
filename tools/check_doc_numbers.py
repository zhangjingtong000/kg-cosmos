"""文档数字与产物一致性检查：防止 README / 报告再次写 stale 数字。

【为什么需要】
  README 曾长期停留在 8 月状态：把已完成的功能标成"规划中"、
  写"32 条测试"（实际 74）、写"28 篇语料入库运行中"（实际 17 篇），
  并且**完全没提** L5 / 六层评测 / claim 重建等真正的贡献。
  `已移除的旧版手册` 更严重：把已被证伪的"实验④ 人工标注金标 P/R/F1"
  当作演示核心，按它准备会拿作废证据回答最要紧的一问。

  这类问题的共性是：**文档里的数字没有任何东西去核对它**。
  代码有测试、指标有回归，文档却只靠人记得改。

【做法】
  把几处关键数字的**权威来源**（data/quality/*.json）读出来，
  再去 README / RUNBOOK / 六层报告里找**互相矛盾**的旧值。
  只报"明确对不上"的，不做模糊判断——宁可漏报也不误报。
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(str(ROOT))
Q = ROOT / 'data' / 'quality'

# (展示名, 权威取值, 该值在文档里"应该出现"的形式, 禁止出现的旧值)
# 旧值取自本轮修 README 时实际发现的过时数字。


def load():
    six = json.loads((Q / 'l5-six-layer.json').read_text(encoding='utf-8'))
    l8 = json.loads((Q / 'l8-structure-audit.json').read_text(encoding='utf-8'))
    l7 = json.loads((Q / 'l7-direction-verification.json').read_text(encoding='utf-8'))
    metrics = json.loads((Q / 'metric-provenance.json').read_text(encoding='utf-8'))['metrics']
    # ── 2026-09-19 新增三类权威来源 ──
    # 为什么加：审计发现守门只盯 data/quality/，于是**语料篇数、中文篇数、
    # 概览层规模**这三类数字在白名单外一路漏网（`README.md:6` 的
    # 「17 篇综述、1.94 万实体名」与 `web/index.html` 的 `data-count="19427"`
    # 都是这么错的）。它们各有明确产物，加进来就能守。
    nebula = json.loads((ROOT / 'web' / 'kg-data' / 'nebula.json').read_text(encoding='utf-8'))
    corpus = json.loads((ROOT / 'web' / 'kg-data' / 'corpus-index.json').read_text(encoding='utf-8'))
    registry = json.loads((ROOT / 'web' / 'kg-data' / 'paper-registry.json').read_text(encoding='utf-8'))

    def m(k):
        return metrics.get(k, {}).get('value')

    stats = corpus['stats']
    return {
        'nodes': l8['scale']['nodes'],
        'edges_overview': l8['scale']['edges'],
        'edges_nebula': nebula['meta']['edges'],
        'communities': nebula['meta']['communities'],
        'corpus_total': stats['total'],
        'corpus_in_graph': stats['in_graph'],
        'corpus_downloaded_only': stats['downloaded_only'],
        'corpus_zh_in_graph': sum(
            1 for p in registry['papers']
            if p.get('lang') == 'zh' and p.get('in_graph')),
        'edges_eval': six['edges_total'],
        'edges_supported': six['edges_supported'],
        'l1_pct': round(six['L1_entity_presence']['rate'] * 100, 1),
        'l1_extracted_pct': round(six['L1_entity_presence']['rate_extracted'] * 100, 1),
        'l3_closed_pct': round(six['L3_endpoints']['all'] * 100, 1),
        'l3_extracted_pct': round(
            six.get('L3_endpoints_extracted_caliber', {}).get('all', 0) * 100, 1),
        'l5_pct': round(six['L5_triplet']['all'] * 100, 1),
        'l2_cumulative': six['L2_entity_clustering'].get('cumulative_merged_groups'),
        'l7_pct': round(l7['compliance_rate'] * 100, 1),
        'l7_resolvable': l7['edges_resolvable'],
        'l9_pct': round((m('L9_endpoint_recall') or 0) * 100, 1),
        'isolated_nodes': m('L8_isolated_nodes'),
        'clustering_ratio': m('L8_clustering_vs_random'),
    }


DOCS = [
    'README.md', 'RUNBOOK.md', 'docs/L5_SIX_LAYER_REPORT.md',
    # ── 前端页面也必须扫 ──
    # 初版只扫 Markdown，于是 `web/index.html` 的首屏 facts 里同时躺着两个问题：
    # `63.7% 关系原文支持率`（说法错误）与 `L1 92.8%`（旧值，应为 94.4%）。
    # 而这两个恰恰是**评委第一眼看到的**，比 docs 里的数字更该被守住。
    'web/index.html', 'web/analytics.html', 'web/kg-nebula.html',
    'web/method-limits.html', 'web/pathqa.html',
    # ── 2026-09-19 加 `web/diff.html` ──
    # **起因**：F2 多图对比页的注释与正文都写死过「0/136」「28/136」，
    # 而它**不在这个列表里** ⇒ ① 的旧值扫描**根本扫不到它**。
    # 这与 `docs/verification.md` 那次「改对没人守、改错没人发现」是同一个缺口。
    'web/diff.html',
    # ── 2026-09-15 扩展：把"含数字且会被引用"的文档纳入守门 ──
    #
    # **起因**：我改白名单时想反向验证（往文档里注入一个真旧值，看守门还灵不灵），
    # 注入到 `docs/verification.md` 后**竟然不报** —— 一查才发现**它根本不在这个列表里**。
    # 而它有 349 行、含大量`被引用的数字`。
    #
    # 这与线三评审独立指出的「**A1 无守门**：`check_doc_numbers.py` 的 DOCS
    # 不含 `docs/DEMO_SCRIPT.md`，改对没人守、改错没人发现」是**同一个缺口**。
    #
    # 纳入标准：**当前有效 + 含被引用数字 + 不是历史记录**。
    # 特意**不纳入** `docs/TODO.md` 与 `docs/ERA_TRADEOFF.md` ——
    # 它们**故意引用旧值做记录**（如「此前 L1 92.8%」「93 项」），纳入会大量误报。
    'docs/verification.md',
    'docs/DEMO_SCRIPT.md',                    # ← 评审：A1 改四处错数字，此前无守门
    'docs/DEMO_FLOW_AND_FEATURES.md',
    'docs/METRIC_CALIBERS.md',
    'docs/已移除的旧版手册',
    'docs/COMPETITIVE_FEATURE_BENCHMARK.md',
    # ── 2026-09-19 扩展：把"给演示取数用"的数字卡纳入守门 ──
    #
    # **起因**：`docs/DOC_AUDIT_NUMBERS.md` 的审计发现 `NUMBERS_CARD.md`
    # **完全不在这个列表里**，于是它整页的规模数字全是旧图（28/17 篇、
    # 19427/43867、pytest 178、回归 63、提交 416），**改对没人守、改错没人发现**。
    # 而这一页的用途恰恰是「演示 / 论文 / 演示时需要说一个数的时候，从这一页取」
    # ⇒ **它错一次，演示就说错一次**。
    #
    # ⚠️ 它的写法与 README 不同（表格里是 `**16 类**` 这类），
    # 所以下面另配了 `CARD_NEED` / `CARD_STALE` 两组断言，不靠 README 那套。
    'docs/NUMBERS_CARD.md',
]

# 明确作废、不该再作为依据出现的表述（出现过一次就要报，除非同行有"已证伪"字样）
RETRACTED = [
    (r'实验④', '实验④（人工标注金标 P/R/F1）已被证伪'),
    (r'三通道抽取对比', '三通道抽取对比实验未成立（标注一致性 α≈0）'),
]


def _pytest_count():
    """跑一次 `pytest --collect-only` 拿**实时**测试数；拿不到返回 None。

    **为什么动态读而不是写死**（N10，2026-09-15 修）：
    这里原先写死「测试数应为 **74** 条」，而实测已经是 **85** ——
    **一个写死的基准，本身就是下一次过时**。
    这与本项目"文档数字会漂"的毛病同源，所以改成实时读。
    拿不到时返回 None，脚本会跳过这一项而不是误报。
    """
    try:
        r = subprocess.run(
            [sys.executable, '-m', 'pytest', 'tests', '-q', '--collect-only'],
            capture_output=True, text=True, timeout=90, cwd=str(ROOT))
        m = re.search(r'(\d+)\s+tests?\s+collected', (r.stdout or '') + (r.stderr or ''))
        return int(m.group(1)) if m else None
    except Exception:
        return None


def _verify_all_count():
    """从 `verify_all.py` 里**数出**检查项数；拿不到返回 None。

    **为什么动态数而不是写死**：与 `_pytest_count()` 同一个道理 ——
    **一个写死的基准，本身就是下一次过时**。
    （实测教训：`DEMO_SCRIPT.md` 里那句"30/30"就是这么过期的。）

    ⚠️ **要数两个列表**：`CHECKS`（需要浏览器）**与** `DATA_CHECKS`（纯数据侧）。
    第一版只数了前者 ⇒ 得到 25 而实际 44 ⇒ **检查本身报了三个假警报**。

    ⚠️ **还要减去标了 `"slow"` 的项** —— 文档里写的是
    「`verify_all.py --fast` 是 N/N」，而 `--fast` **跳过 slow**。
    不减去的话会得到 45 而实际 44，**检查又报一个假警报**。
    """
    p = Path(__file__).resolve().parent / 'verify_all.py'
    try:
        src = p.read_text(encoding='utf-8')
    except Exception:
        return None
    total = 0
    for name in ('CHECKS', 'DATA_CHECKS'):
        m = re.search(name + r'\s*=\s*\[(.*?)\n\]', src, re.S)
        if not m:
            continue
        blk = m.group(1)
        total += len(re.findall(r'^\s{4}\(', blk, re.M))
        # `, "slow")` / `, 'slow')` 结尾的项被 `--fast` 跳过。
        # ⚠️ **两种引号都要认** —— 第一版只写了双引号，于是新加的一项
        # （用了单引号）没被减掉，检查算出 46 而实跑 45 ⇒ **又一个假警报**。
        total -= len(re.findall(r',\s*["\']slow["\']\)', blk))
    return total or None


def main():
    cur = load()
    print('=== 文档数字一致性 ===\n')
    print('权威取值（来自 data/quality/）：')
    for k, v in cur.items():
        print(f'  {k:22s} {v}')
    print()

    fails = []

    # ① 已知的旧值不该再出现。
    #
    # 判据要**足够具体**，否则会误报：初版把裸的 '63.8%' 列为旧值，
    # 结果报了三处"误报"——那三处讲的是**本体内子集**的通过率 63.8%
    # （43630 条里的 27848 条），与全集 63.7%（27954/43867）是两个不同的数，
    # 两个都对。
    #
    # ⚠️ 但也不能**过度收紧**：上一版把裸的 '92.8%' 换成了
    # 'L1 实体存在 92.8%' 与 '**92.8%**' 两种带上下文的写法，
    # 而 `web/index.html` 里实际是 `<b>92.8%</b><span>实体证据存在率</span>`
    # ——两种模式都不匹配，于是**检查形同虚设**。
    # 这是"验证验证器"时发现的（往 HTML 里注入一个旧值，检查居然不报）。
    # 所以现在把三种载体都列上：Markdown 粗体、纯文本、HTML 标签。
    L1 = cur['l1_pct']
    # 实时测试数（拿不到就是 None，相关检查会跳过而不是误报）
    _N_TESTS = _pytest_count()
    print(f'  （实时测试数：{_N_TESTS if _N_TESTS else "未取到"}）')
    n_checks = _verify_all_count()
    STALE = {
        '15116': f"节点数应为 {cur['nodes']}",
        '20869': f"概览层边数应为 {cur['edges_overview']}",
        '15322': '实体名数已变（反建后为 20785）',
        '16287': 'L5 支持子图节点数已变',
        # L1 的三种写法（Markdown / 纯文本 / HTML）
        'L1 实体存在 92.8%': f"L1 应为 {L1}%",
        '**92.8%**': f"L1 应为 {L1}%",
        'L1 92.8%': f"L1 应为 {L1}%",
        '<b>92.8%</b>': f"L1 应为 {L1}%",
        '32 条 pytest': f'测试数应为 {_N_TESTS} 条' if _N_TESTS else '测试数已过时',
        '32 条，含': f'测试数应为 {_N_TESTS} 条' if _N_TESTS else '测试数已过时',
        '27981': f"L5 支持边数应为 {cur['edges_supported']}",
        '归并 384 组': f"L2 累计归并应为 {cur['l2_cumulative']} 组",
        '**归并 384**': f"L2 累计归并应为 {cur['l2_cumulative']} 组",
        # ── 2026-09-19 新增：审计查出的、此前**完全不在禁值表里**的过时数字 ──
        #
        # 为什么必须加：`19427`（节点）与 `43867`（评测全集边数）在
        # `docs/DOC_AUDIT_NUMBERS.md §二` 里是**出现频次最高**的两个旧值，
        # 而 STALE 里一个都没有 —— **白名单只禁自己见过的旧值，就永远慢一步**。
        # 现在按"上一版权威值"整批登记，且旧值一律带口径词，避免误报。
        '19427': f"节点数应为 {cur['nodes']}（旧值：反建前的实体名数）",
        'nodes=19427': f"概览层节点数应为 {cur['nodes']}",
        '43867': f"评测全集边数应为 {cur['edges_eval']}",
        '31308': '该 Neo4j 实例已不存在；库内规模为旧快照',
        '29120': '概览层边数旧值（现为 nebula.json 的抽样导出上限口径）',
        '18780': f"节点数应为 {cur['nodes']}（Neo4j 旧快照）",
        # L3 / L5：旧读数。⚠️ 用**带口径的整串**，不裸禁 '68.4%'/'63.7%'，
        # 因为 `L5_SIX_LAYER_REPORT.md` 与其"必读"小节是**合法历史引用**。
        'L3 端点只有 68.4%': f"L3 抽取口径应为 {cur['l3_extracted_pct']}%",
        '端点完整性只有 **68.4%**': f"L3 抽取口径应为 {cur['l3_extracted_pct']}%",
        'L5 三元组验证 | 63.7%（27954）': f"L5 应为 {cur['l5_pct']}%（支持子集 {cur['edges_supported']}）",
        'L5 = 63.7%': f"L5 应为 {cur['l5_pct']}%",
        # 结构缺陷：孤立节点与聚类系数倍数
        '3036 个孤立节点': f"孤立节点应为 {cur['isolated_nodes']} 个",
        '3036（16.17%）': f"孤立节点应为 {cur['isolated_nodes']} 个",
        '**3036（16.2%）**': f"孤立节点应为 {cur['isolated_nodes']} 个",
        '聚类系数 **914 倍': f"聚类系数应为 {cur['clustering_ratio']} 倍（当前节点口径）",
        '914 倍 vs 597 倍': f"聚类系数应为 {cur['clustering_ratio']} 倍（当前节点口径）",
        # L7：98.0% / 93.2% 两个旧读数已被 92.4% 取代
        '合规 5819': f"L7 合规数应为 5832（合规率 {cur['l7_pct']}%）",
        '5819 / 6241': f"L7 应报 {cur['l7_pct']}%（可判 {cur['l7_resolvable']} 条）",
        '合规率 98.0%': f"L7 合规率应为 {cur['l7_pct']}%",
        '合规 98.0%': f"L7 合规率应为 {cur['l7_pct']}%",
        # 语料三数（此前完全没有守门）
        '28 篇语料': f"语料应为 {cur['corpus_total']} 条",
        '**28 篇**（已入库 **17**）': f"语料应为 {cur['corpus_total']} 条 / 入库 {cur['corpus_in_graph']} 条",
        '17 篇入库': f"入库应为 {cur['corpus_in_graph']} 条",
        '**17 篇**': f"入库应为 {cur['corpus_in_graph']} 条",
        '中文语料只有 1 篇': f"中文出图应为 {cur['corpus_zh_in_graph']} 篇",
        # ── F2 多图对比：旧规模（17 篇 / 136 对）的读数（2026-09-19 加）──
        #
        # **为什么用"带分母的分式"整串、而不是裸禁 `28` / `136`**：
        # 那两个都是**裸数字**，在别处完全可能是别的量（`136` 尤其泛），
        # 裸禁会误报。而「28/136」这种**带口径的分式**只会出现在
        # "讲多图对比"的地方 ⇒ 禁它不误报。
        # 这正是本脚本反复栽过的两个坑（"过度收紧"与"形同虚设"）之间的取法。
        #
        # ⚠️ 分母 `136` 单列在这里**没有意义**：它已由 ⑧ 的"正文不许写死 N/M 对"
        # 结构断言覆盖 —— 也就是说，就算将来对数再变（210 → 更大），
        # ⑧ 仍然守着，而这个禁值表只负责"已知旧值不再出现"。
        '28/136': '零共享边对数旧值；当前应由 web/kg-data/diff.json 现算',
        '28 / 136': '零共享边对数旧值（17 篇规模时）',
        '0/136': '节点交集为空的对数旧值（17 篇规模时）',
        '0 / 136': '节点交集为空的对数旧值（17 篇规模时）',
    }
    #   · **`MARKERS` 必须逐次收紧**：写宽了会把真旧值一起放过（`已改` 一度让
    #     `README` 里的 `338` 行漏网）。所以下面每加一个词都要问一句
    #     「有没有可能这一行真在用旧值、而它恰好含这个词？」
    #     `此前` / `历史` 是安全的：它们描述的是**这句话本人在讲旧值**。
    #   · **2026-09-15 追加两个白名单词**：L1 现在**有两个合法口径** ——
    #     「含反建节点」19463/20785 = 93.6%（权威产物的 rate）
    #     与「纯抽取」15161/16481 = 92.0%。
    #     并列展示时 92.0% 是**真值而不是旧值**，所以不能报。
    #     用**精确词**而不是泛化的"口径"——后者会让真正的旧值漏网
    #     （这个脚本已经栽过一次"过度收紧"和一次"形同虚设"）。
    #   · **行内 pragma `数字守门:忽略`**（2026-09-19 加）：给"整段都是历史留档"
    #     的地方用 —— 逐字稿式的历史表（`L5_SIX_LAYER_REPORT` §3.1 的旧图表、
    #     `METRIC_CALIBERS` 的"已废弃"行、`verification` 的埋虫演习记录）里，
    #     `43867` / `18780` 是**这段文字的主角**，删了就读不懂。
    #     为什么不用更宽的 `MARKERS`：那些行**没有**"旧/历史"这类词
    #     （如 `| 层 | 全集（43867） | …`），词表加不进去；
    #     而把 `43867` 从 STALE 里去掉就等于放弃守它 —— 所以改用**显式 pragma**：
    #     **要免检就必须写明"这里在讲历史"**，而不是靠脚本猜。
    MARKERS = ('已改', '修正', '此前', '曾', '旧', '❌', '历史',
               '纯抽取', '不含反建', '抽取口径')
    PRAGMA = '数字守门:忽略'

    def _kept_lines(raw):
        """按行返回 (真实行号, 行内容)，**跳过带 pragma 的行**。"""
        return [(i, ln) for i, ln in enumerate(raw.split('\n'), 1)
                if PRAGMA not in ln]

    print('=== ① 旧值扫描 ===')
    for doc in DOCS:
        p = ROOT / doc
        if not p.exists():
            continue
        kept = _kept_lines(p.read_text(encoding='utf-8', errors='replace'))
        txt = '\n'.join(ln for _i, ln in kept)
        for bad, why in STALE.items():
            for m in re.finditer(re.escape(bad), txt):
                idx = txt[:m.start()].count('\n')
                line_no, line = kept[idx]
                if any(k in line for k in MARKERS):
                    continue
                fails.append(f'{doc}:{line_no} 出现旧值 {bad!r} —— {why}')
                print(f'  ❌ {doc}:{line_no}  {bad!r}  ({why})')
    if not any('旧值' in f for f in fails):
        print('  ✅ 未发现旧值')

    # ② 已作废的结论不该被当作依据
    print('\n=== ② 已作废结论扫描 ===')
    found_retracted = False
    for doc in DOCS:
        p = ROOT / doc
        if not p.exists():
            continue
        lines = p.read_text(encoding='utf-8', errors='replace').splitlines()
        for i, line in enumerate(lines, 1):
            for pat, why in RETRACTED:
                if re.search(pat, line):
                    # 同行或**邻近**有"证伪/失效/不要指/作废"就算已标注。
                    #
                    # **2026-09-15 修**：注释一直写着"或邻近"，但代码只看 `line` ——
                    # 于是 `已移除的旧版手册:38/40` 被误报：
                    # 那两行说的正是"这份 playbook 写于 8 月，其中把「实验④…」",
                    # 而"**已被证伪**"写在**下一行**（`:39`）。
                    # 现在按注释的本意取前后各 2 行做上下文。
                    ctx = '\n'.join(lines[max(0, i - 3):i + 2])
                    if any(k in ctx for k in ('证伪', '失效', '作废', '不要指', '~~', '❌')):
                        continue
                    fails.append(f'{doc}:{i} 仍以「{pat}」为依据（{why}）')
                    print(f'  ❌ {doc}:{i}  {line.strip()[:88]}')
                    found_retracted = True
    if not found_retracted:
        print('  ✅ 未发现未标注的作废结论')

    # ③ 关键数字必须在 README 里出现（缺失说明文档又落后了）
    print('\n=== ③ README 是否反映当前规模 ===')
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    need = {
        str(cur['nodes']): '节点数',
        str(cur['edges_eval']): '评测全集边数',
        f"{cur['l3_extracted_pct']}%": 'L3 抽取口径',
        f"{cur['l9_pct']}%": 'L9 端点召回',
        f"{cur['l2_cumulative']}": 'L2 累计归并',
        # N10（2026-09-15 加）：测试数也该被守 —— 此前它只在 STALE 里当旧值，
        # 而**没有任何检查强制文档写出这个数**，所以它可以一直是错的。
    }
    if _N_TESTS:
        need[str(_N_TESTS)] = f'测试数（{_N_TESTS} 条）'
    for tok, why in need.items():
        if tok in readme:
            print(f'  ✅ README 含 {why} ({tok})')
        else:
            fails.append(f'README 未反映{why}（{tok}）——文档可能又落后了')
            print(f'  ❌ README 缺 {why} ({tok})')

    # ④ **回归项数**：文档里写的 "N/N" 必须与 `verify_all.py` 的实际项数一致
    #
    # **为什么加这一条**（2026-09-16）：`DEMO_SCRIPT.md` 的"演示前必须确认"里
    # 写着「`verify_all.py --fast` 是 **30/30**」，而实际已经是 **44/44** ——
    # **一处典型的过期数字**。而它**没被本脚本抓到**，因为上面那张 `need`
    # 表只认"指标数字"，**不认"回归项数"**。
    #
    # ⇒ 这跟 N10（测试数）是同一个道理：**一个会被引用的数字，
    #   如果没有任何检查强制它同步，它就会一直是错的。**
    print('\n=== ④ 文档里的回归项数是否与实际一致 ===')
    n_checks = _verify_all_count()
    if n_checks is None:
        print('  ⚠️ 数不出 verify_all 的项数，跳过这一项')
    else:
        print(f'  实测 `verify_all.py` 有 {n_checks} 项')
        # 在"会被引用"的那几份文档里找 `NN/NN` 形态的写法。
        #
        # ⚠️ **必须带上下文**：光看 `\d+/\d+` 会误报 ——
        #    `DEMO_SCRIPT.md` 里有一句"命中 70 个实体，**类型全对（70/70）**"，
        #    那是**实体类型的通过率**，不是回归项数。
        #    ⇒ 只在**同行含 `verify_all` 或"回归"**时才认。
        stale = []
        for rel in ('README.md', 'docs/DEMO_SCRIPT.md', 'docs/RUNBOOK.md',
                    '早先的内部记录'):
            p = ROOT / rel
            if not p.exists():
                continue
            for i, line in enumerate(p.read_text(encoding='utf-8').split('\n'), 1):
                if 'verify_all' not in line and '回归' not in line:
                    continue
                for m in re.finditer(r'(\d{2})\s*/\s*\1', line):
                    if int(m.group(1)) != n_checks:
                        stale.append((rel, m.group(0), i))
        if stale:
            for rel, tok, ln in stale:
                fails.append(f'{rel}:{ln} 写着回归「{tok}」，实际是 {n_checks}/{n_checks}'
                             f' —— 过期数字')
                print(f'  ❌ {rel}:{ln} 写着 {tok}（实际 {n_checks}）')
        else:
            print(f'  ✅ 文档里的回归项数与实际一致（{n_checks}/{n_checks}）')

    # ⑤ **语料三数**：篇数 / 入库数 / 只下载数（2026-09-19 新增）
    #
    # **为什么加**：`docs/DOC_AUDIT_NUMBERS.md` 的审计发现，守门此前**只盯
    # `data/quality/` 的指标**，于是"语料篇数"这一类在白名单外一路漏网 ——
    # `README.md` 的首屏第一句写着「17 篇综述、1.94 万实体名、4.39 万条边」，
    # 而实际是 21 篇入库 / 20785 节点 / 45708 边；`web/index.html` 首屏
    # 也写着「28 篇论文」。
    #
    # ⚠️ **口径必须分开断言**：33 是"下载到的 PDF 数"、21 是"进了图的论文数"、
    # 12 是"只下载未入库"。三者混用是这一项目最典型的错法
    # （28 篇语料 / 17 篇入库 就是上一轮的写法）。
    print('\n=== ⑤ 语料篇数是否与产物一致 ===')
    corpus_docs = ('README.md', 'docs/NUMBERS_CARD.md', 'web/index.html',
                   'docs/verification.md')
    # 「入库 N 篇」必须等于 in_graph；「N 篇只下载」必须等于 downloaded_only。
    #
    # ⚠️ **不能用 `(\d+) 篇` 这种裸模式**：实测它把 `2026-09-19 加的 4 篇` 里的
    # `4 篇` 也匹配了（`4 篇中文期刊` 是"新增篇数"，不是语料总数）——
    # 于是检查报了假警报。这与 ④ 里"70/70 不是回归项数"是同一类教训：
    # **只认带口径词的写法**。
    CORPUS_PATS = (
        (r'入库\s*\*{0,2}(\d+)\*{0,2}\s*篇', 'corpus_in_graph', '入库篇数'),
        (r'(\d+)\s*篇（?（?只下载', 'corpus_downloaded_only', '只下载篇数'),
        (r'含\s*\*{0,2}(\d+)\*{0,2}\s*篇只下载', 'corpus_downloaded_only', '只下载篇数'),
        # ⚠️ 语料总条数的写法有两种：README 用「语料登记 **34 条**」，
        # `web/index.html` 用「33 篇论文清单：…含 12 篇只下载」。
        # 后者刻意**不在这条规则里**：那句话里的 `33 篇` 指的是"清单里能点开的
        # PDF 数"，而 `stats.total` 是"登记条数"（34 = 33 PDF + 1 条缺 PDF 的记录）
        # ⇒ 两个数都对，只是口径不同。**宁可少报，也不要把对的判成错的。**
        (r'语料登记\s*\*{0,2}(\d+)\*{0,2}\s*条', 'corpus_total', '语料登记条数'),
    )
    for rel in corpus_docs:
        p = ROOT / rel
        if not p.exists():
            print(f'  ⚠️ {rel} 不存在，跳过')
            continue
        txt = p.read_text(encoding='utf-8', errors='replace')
        ok = True
        for pat, key, why in CORPUS_PATS:
            val = cur[key]
            for m in re.finditer(pat, txt):
                if int(m.group(1)) != val:
                    line_no = txt[:m.start()].count('\n') + 1
                    fails.append(f'{rel}:{line_no} {why}写成 {m.group(1)}，'
                                 f'实际是 {val} —— 语料口径过期')
                    print(f'  ❌ {rel}:{line_no} {why} {m.group(1)} ≠ {val}')
                    ok = False
        if ok:
            print(f'  ✅ {rel} 的语料口径与 corpus-index.json 一致')

    # ⑥ **`NUMBERS_CARD.md` 专有断言**（2026-09-19 新增）
    #
    # 这一页的定位是「需要说一个数的时候，从这一页取」，写法与 README 不同
    # （`**16 类**` 这种），所以单独配断言。它此前**完全不在 DOCS 里**，
    # 于是整页规模数字停在上一个图快照上。
    print('\n=== ⑥ 数字卡是否反映当前规模 ===')
    card_p = ROOT / 'docs' / 'NUMBERS_CARD.md'
    if not card_p.exists():
        print('  ⚠️ docs/NUMBERS_CARD.md 不存在，跳过')
    else:
        card = card_p.read_text(encoding='utf-8')
        card_need = {
            str(cur['corpus_total']): f"语料总条数（{cur['corpus_total']} 条）",
            str(cur['corpus_in_graph']): f"入库条数（{cur['corpus_in_graph']} 条）",
            str(cur['nodes']): f"节点数（{cur['nodes']}）",
            str(cur['edges_eval']): f"评测全集边数（{cur['edges_eval']}）",
            str(cur['edges_overview']): f"自然规模边数（{cur['edges_overview']}）",
            f"{cur['l3_closed_pct']}%": f"L3 闭合口径（{cur['l3_closed_pct']}%）",
            f"{cur['l3_extracted_pct']}%": f"L3 抽取口径（{cur['l3_extracted_pct']}%）",
            f"{cur['l5_pct']}%": f"L5（{cur['l5_pct']}%）",
            f"{cur['l7_pct']}%": f"L7 合规率（{cur['l7_pct']}%）",
            str(cur['isolated_nodes']): f"孤立节点（{cur['isolated_nodes']}）",
            str(int(cur['clustering_ratio'])): f"聚类系数倍数（{cur['clustering_ratio']}）",
        }
        # ⚠️ **pytest 数改成条件断言**（2026-09-19 定稿）：
        #
        # 实测代价对比 ——
        #   · 强制同步：写这一项时 `pytest --collect-only` 在 **73 分钟内从 204
        #     涨到 406**（另一个 agent 正在成批补 `tests/`），每涨一次数字卡就报一次红；
        #   · 不守：它又会像 README 的 `204` 一样悄悄过期。
        #
        # 所以判据是「**有人动文档时顺手同步**」而不是「每次 collect 都必须相等」：
        # 默认只画一条 ⚠️ 提示，设 `KG_STRICT_PYTEST=1` 时升级为错误。
        # 为什么这样选：**回归项数与规模/指标数字仍然是硬断言** ——
        # 那些数字变了几乎一定意味着文档该改；而测试条数在开发期本来就抖动。
        if _N_TESTS:
            if f'pytest **{_N_TESTS} 条**' in card:
                print(f'  ✅ 数字卡含 pytest 测试数（{_N_TESTS} 条）')
            else:
                msg = (f'docs/NUMBERS_CARD.md 的 pytest 条数与本机 collect 数'
                       f'（{_N_TESTS}）不一致 —— 改测试时顺手同步数字卡')
                if os.environ.get('KG_STRICT_PYTEST'):
                    fails.append(msg)
                    print(f'  ❌ {msg}')
                else:
                    print(f'  ⚠️ {msg}（设 KG_STRICT_PYTEST=1 可升级为错误）')
        if n_checks:
            card_need[f'回归 **{n_checks} 项**'] = f'回归项数（{n_checks} 项）'
        for tok, why in card_need.items():
            if tok in card:
                print(f'  ✅ 数字卡含 {why}')
            else:
                fails.append(f'docs/NUMBERS_CARD.md 未反映{why}（{tok}）'
                             f' —— 数字卡又落后了')
                print(f'  ❌ 数字卡缺 {why}（{tok}）')

    # ⑦ **项目手册 §2「现状盘点」的代码事实**（2026-09-19 新增，对应 TODO N15）
    #
    # **为什么加**：`docs/已移除的旧版手册` §2 的标题挂着「（**已核实**）...」，
    # 而那是 **8 月**的核实 —— `src/app.py` 写 260 行（实测 1364）、
    # `src/kg_extractor.py` 写 808 行（实测 1751）、`src/graph_store.py` 写
    # 「**Neo4jStore 类已写但从未接通**」（实测 `src/app.py:113-123` 已按
    # `KG_BACKEND` 默认 `neo4j` 接通）、`tests/` 写「4 文件 346 行」（实测 32 文件）。
    #
    # **为什么这比普通文档过时严重**：它**自称已核实** —— 会被当成可信来源引用
    # （演示口径、论文现状章节都可能照抄）。而它此前**完全不在本脚本的覆盖范围**：
    # 上面 ① 的 `STALE` 只禁「图规模 / 六层指标」的旧值，**不看代码行数**；
    # ② 的 `RETRACTED` 只看作废结论，**不看「某功能是否接通」这类非数字断言**
    # —— 而 N15 失真的两半，恰恰就是这两类。
    #
    # **做法**：§2 正文里每个会被引用的代码事实都写成一行哨兵
    #   `| `src/app.py` 行数 <!-- fact:lines:src/app.py --> | **1364 行** | 来源 |`
    # 守门**从哨兵之后**取第一个 `**数字**`，与实时实测比对。
    #
    # ⚠️ **为什么不裸扫整节里的所有数字**：§2 下方那张 8 月表**按
    # `docs/ARCHIVE_POLICY.md` 必须原样留存旧值**（808 / 260 / 346 行都在里面），
    # 裸扫会把整张档案表判成错。本脚本已经栽过一次「过度收紧」（裸禁 63.8%
    # 误报本体内子集）和一次「形同虚设」（只认 `**92.8%**` 一种载体），
    # 所以这里改用**显式哨兵**：要受守就必须写明标记，档案区不写标记。
    print('\n=== ⑦ 项目手册 §2 的代码事实是否与实测一致 ===')
    PLAYBOOK = ROOT / 'docs' / '已移除的旧版手册'
    if not PLAYBOOK.exists():
        print('  ⚠️ docs/已移除的旧版手册 不存在，跳过')
    else:
        pb = PLAYBOOK.read_text(encoding='utf-8', errors='replace')
        FACT_RE = re.compile(r'<!--\s*fact:([a-z_]+):(\S+?)\s*-->')

        def _fact_value(kind, key):
            """把哨兵 `(kind, key)` 换算成**实测值**；测不出返回 None（跳过而非误报）。"""
            try:
                if kind == 'lines':
                    return len((ROOT / key).read_text(
                        encoding='utf-8', errors='replace').splitlines())
                if kind == 'glob_files':
                    return len([p for p in ROOT.glob(key) if p.is_file()])
                if kind == 'glob_lines':
                    return sum(len(p.read_text(encoding='utf-8', errors='replace').splitlines())
                               for p in ROOT.glob(key) if p.is_file())
            except Exception:
                return None
            if kind == 'graph':
                return {
                    'nodes': cur['nodes'],                    # 概览层节点（l8）
                    'edges_overview': cur['edges_overview'],  # 概览层自然规模边（l8）
                    'edges_eval': cur['edges_eval'],          # 评测全集边（l5）
                    'edges_supported': cur['edges_supported'],  # L5 支持子集边
                    'edges_nebula': cur['edges_nebula'],      # 星云抽样导出上限
                }.get(key)
            return None

        # 必须存在的哨兵 —— 列在这里是为了防「把哨兵行删掉让守门失效」
        # （删了就没得比对，等于回到改造之前）。
        PB_REQUIRED = [
            'lines:src/app.py', 'lines:src/kg_extractor.py',
            'lines:src/graph_store.py', 'lines:src/pdf_parser.py',
            'lines:web/index3d-bounce.html',
            'glob_files:tests/*.py', 'glob_lines:tests/*.py',
            'graph:nodes', 'graph:edges_overview', 'graph:edges_eval',
            'graph:edges_supported', 'graph:edges_nebula',
        ]
        # ⚠️ **`glob_*`（tests/ 的规模）只做软断言**，与上面 ⑥ 里 pytest 条数
        # 同一个理由：另一个 agent 正在成批补 `tests/`（实测 73 分钟内
        # 204 → 406 条），硬断言会让守门长期报红，红着红着就没人看了。
        # 默认画 ⚠️，设 `KG_STRICT_PYTEST=1` 时升级为错误 —— 与 ⑥ 保持同一开关。
        seen, n_ok, n_soft = set(), 0, 0
        for m in FACT_RE.finditer(pb):
            kind, key = m.group(1), m.group(2)
            seen.add(f'{kind}:{key}')
            line_no = pb[:m.start()].count('\n') + 1
            want = _fact_value(kind, key)
            got_m = re.match(r'\s*\*\*\s*([\d,]+)', pb[m.end():])
            if want is None:
                print(f'  ⚠️ PLAYBOOK:{line_no} 哨兵 {kind}:{key} 的实测值取不到，跳过')
                continue
            if not got_m:
                fails.append(f'docs/已移除的旧版手册:{line_no} 哨兵 {kind}:{key} '
                             f'后面没有 `**数字**` —— 守门读不到值')
                print(f'  ❌ PLAYBOOK:{line_no} 哨兵 {kind}:{key} 后缺 `**数字**`')
                continue
            got = int(got_m.group(1).replace(',', ''))
            if got == want:
                n_ok += 1
                continue
            if kind.startswith('glob_'):
                msg = (f'docs/已移除的旧版手册:{line_no} 写着 {got}，实测 {want}'
                       f'（{kind}:{key}）—— tests/ 正在增长，改完顺手同步 §2')
                if os.environ.get('KG_STRICT_PYTEST'):
                    fails.append(msg)
                    print(f'  ❌ {msg}')
                else:
                    n_soft += 1
                    print(f'  ⚠️ {msg}（设 KG_STRICT_PYTEST=1 可升级为错误）')
            else:
                fails.append(f'docs/已移除的旧版手册:{line_no} 写着 {got}，'
                             f'实测 {want}（{kind}:{key}）'
                             f' —— **一份自称「已核实」的演示材料写错了**')
                print(f'  ❌ PLAYBOOK:{line_no} {kind}:{key} 写 {got} ≠ 实测 {want}')
        missing = [k for k in PB_REQUIRED if k not in seen]
        if missing:
            fails.append('docs/已移除的旧版手册 §2 缺事实哨兵：'
                         + ', '.join(missing) + ' —— 哨兵被删或漏写，守门失效')
            print('  ❌ PLAYBOOK §2 缺哨兵：' + ', '.join(missing))
        if n_ok and not missing:
            extra = f'（另有 {n_soft} 个 tests/ 规模哨兵待同步）' if n_soft else ''
            print(f'  ✅ {n_ok} 个代码事实哨兵与实测一致{extra}')

        # (2) **非数字断言**：「某功能是否接通」只能读代码。
        app_src = (ROOT / 'src' / 'app.py').read_text(encoding='utf-8', errors='replace')
        if ('Neo4jStore' in app_src or 'KG_BACKEND' in app_src) and '已接通' not in pb:
            fails.append('docs/已移除的旧版手册 §2 未写「已接通」，'
                         '但 src/app.py 已按 KG_BACKEND 接通 Neo4j（读代码，别信文档）')
            print('  ❌ PLAYBOOK §2 缺「已接通」结论（app.py 里 Neo4j 确实已接）')
        else:
            print('  ✅ Neo4j 接通状态与 §2 的写法一致')

        # (3) 分支合并状态同理：只能问 git。
        br = 'codex/semantic-filament-prototype'
        try:
            r = subprocess.run(['git', '-C', str(ROOT), 'rev-list', '--count',
                                f'main..{br}'], capture_output=True, text=True, timeout=30)
            ahead = r.stdout.strip() if r.returncode == 0 else None
        except Exception:
            ahead = None
        if ahead is None:
            print(f'  ⚠️ 取不到 {br} 相对 main 的提交数（无 git？），跳过')
        else:
            want_word = '已合并进 main' if ahead == '0' else '未合并'
            if want_word in pb:
                print(f'  ✅ 丝状体分支状态与 §2 一致（{br} 领先 main {ahead} 个提交）')
            else:
                fails.append(f'docs/已移除的旧版手册 §2 未写「{want_word}」，'
                             f'而 git 实测 {br} 相对 main 领先 {ahead} 个提交')
                print(f'  ❌ PLAYBOOK §2 的丝状体分支状态与 git 不符（应为「{want_word}」）')

    # ⑧ **F2 `diff.json` 的数据源自洽 + 前端可见数字同步**（2026-09-19 新增）
    #
    # **为什么加**（这一条是本脚本存在理由的最纯粹形态）：
    # `web/kg-data/diff.json` 里 `design.measured.pairs_with_zero_shared_edges`
    # 报 **87**，而**同一个 `measured` 对象**的 `note` 写「有 **28** 对」——
    # 一份产物自己打自己的脸。根因是 `tools/build_diff_data.py` 把 note
    # 写成了**硬编码字符串**，语料从 17 篇涨到 21 篇后 `measured` 重算了、
    # `note` 没有。而 `web/index.html` 恰好取了 note 里的 28
    # ⇒ **前端可见数字错了一整轮，没有任何检查发现**。
    #
    # **为什么放在本脚本而不是新写一个**：守门已有统一入口
    # （`verify_all.py:145` 调用本脚本），`web/index.html` 本来就在 `DOCS` 里；
    # 另起一个脚本还要再接一次 verify_all、再维护一份白名单 —— 只会多一处漏。
    #
    # 判据三层，**从强到弱**：
    #   a. **产物自洽**：`note` 里说的那个数 == `measured` 里的数，
    #      且两者都 == 由 `pairs[]` **独立重算**的结果。
    #      ⚠️ 只比对"两个字段是否相等"是不够的 —— **两个字段一起错就查不出来**，
    #      必须有一个**不依赖它们**的重算基准（这与 `cdp_verify_diff.py`
    #      用 Python 独立复算 KPI 是同一条纪律）。
    #   b. **前端可见数字**：`web/index.html` 的「N 对是「共享实体、零共享关系」」
    #      必须等于产物值 —— 这才是"前端可见事实"的守门。
    #   c. **不许硬编码**：`web/diff.html` 的可见正文里不许再出现
    #      `<b>N/M 对…` 这种写死的形态（该页已经 fetch 产物，数字改为现填）。
    #      这一条**不依赖具体数值**，所以对数将来再变它仍然有效。
    print('\n=== ⑧ F2 多图对比：diff.json 自洽 + 前端数字同步 ===')
    diff_p = ROOT / 'web' / 'kg-data' / 'diff.json'
    if not diff_p.exists():
        print('  ⚠️ web/kg-data/diff.json 不存在，跳过（先跑 tools/build_diff_data.py）')
    else:
        dj = json.loads(diff_p.read_text(encoding='utf-8'))
        dm = ((dj.get('design') or {}).get('measured') or {})
        want_zero = dm.get('pairs_with_zero_shared_edges')
        # (a) 由 `pairs[]` 独立重算 —— 不看 measured，也不看 note
        recomputed = sum(1 for p in (dj.get('pairs') or []) if p.get('zero_shared_edges'))
        note = str(dm.get('note') or '')
        nums_note = [int(x) for x in re.findall(r'(\d+)\s*对', note)]
        print(f'  产物 measured.pairs_with_zero_shared_edges = {want_zero}')
        print(f'  由 pairs[] 独立重算 = {recomputed} · note 里提到的对数 = {nums_note}')
        if recomputed != want_zero:
            fails.append(f'web/kg-data/diff.json：measured 写 {want_zero}，'
                         f'而由 pairs[] 重算得 {recomputed} —— **产物内部不自洽**')
            print(f'  ❌ measured {want_zero} ≠ 独立重算 {recomputed}')
        else:
            print(f'  ✅ measured 与 pairs[] 独立重算一致（{recomputed} 对）')
        if nums_note != [want_zero]:
            fails.append(f'web/kg-data/diff.json：note 写 {nums_note}，'
                         f'而 measured 是 {want_zero} —— **同一份产物自相矛盾**'
                         f'（即 28 vs 87 那次事故的形态：数字写在字符串里没人核对）')
            print(f'  ❌ note {nums_note} ≠ measured {want_zero}')
        else:
            print(f'  ✅ note 与 measured 同源（都取自同一份数据：{want_zero}）')

        # (b) `web/index.html` 的可见数字
        idx_html = ROOT / 'web' / 'index.html'
        if idx_html.exists() and want_zero is not None:
            itxt = idx_html.read_text(encoding='utf-8', errors='replace')
            hits = re.findall(r'(\d+)\s*对是「共享实体、零共享关系」', itxt)
            if not hits:
                fails.append('web/index.html 里找不到「N 对是「共享实体、零共享关系」」'
                             ' —— 守门失去目标（页面被改写？请同步更新本检查）')
                print('  ❌ index.html 里守门目标缺失')
            else:
                print(f'  web/index.html 的可见写法：{hits}')
                bad_b = [h for h in hits if int(h) != want_zero]
                if bad_b:
                    fails.append(f'web/index.html 写「{bad_b[0]} 对是「共享实体、'
                                 f'零共享关系」」，而 diff.json 是 {want_zero}'
                                 f' —— **前端可见数字过期**')
                    print(f'  ❌ index.html 写 {bad_b} ≠ {want_zero}')
                else:
                    print(f'  ✅ index.html 的可见数字与产物一致（{want_zero} 对）')

        # (c) `web/diff.html` 不许再写死分式（去注释后看可见正文）
        diff_html = ROOT / 'web' / 'diff.html'
        if diff_html.exists():
            raw = diff_html.read_text(encoding='utf-8', errors='replace')
            visible = re.sub(r'<!--.*?-->', '', raw, flags=re.S)
            hard = re.findall(r'(\d+)\s*/\s*(\d+)\s*对', visible)
            if hard:
                fails.append(f'web/diff.html 的可见正文仍写死「{hard[0][0]}/{hard[0][1]} 对」'
                             f' —— 这一页已经 fetch 了 diff.json，数字应现填（见 ⑧c）')
                print(f'  ❌ diff.html 可见正文仍写死 {hard}')
            else:
                print('  ✅ diff.html 可见正文没有写死的「N/M 对」')
            miss = [s for s in ('figZeroEdges', 'figPairsA', 'figPairsB')
                    if f'id="{s}"' not in raw]
            if miss:
                fails.append(f'web/diff.html 缺填充锚点 {miss} —— '
                             f'数字又回到"写死在 HTML 里"的状态，守门失效')
                print(f'  ❌ diff.html 缺锚点 {miss}')

    # ⑨ **依赖 esm.sh 的页面名单**（2026-09-19 新增；节名「依赖 esm.sh 的页面名单」，
    #    编号若被后来者挤动，请按节名找它）
    #
    # **为什么加**：`README.md §8` 曾写着「5 个历史原型页仍依赖 esm.sh
    # （导航页已用 `*` 标注）」—— 这一句上同时躺着三个错：
    #   ① **口径不清**：按"文件里出现过 esm.sh"数是 7 个，按"真有 CDN URL"数是 5 个；
    #   ② **分类错**：那 5 个里 `community-atlas` 是**正式页**，历史原型页只有 4 个；
    #   ③ **承诺没兑现**：`web/index.html` 上**一个 `*` 都没有**（真正标了 `*` 的是
    #      旧版导航页 `index-classic.html`，而它并不是"导航页"）。
    # 三处都活得很好，因为**"哪些页面依赖 CDN"不在任何产物里** ——
    # 与 ⑤（语料篇数）、⑥（数字卡）、⑧（diff.json 前端数字）是同一个缺口：
    # **一个会被引用的数字或名单，只靠人记得改，它就会一直是错的。**
    #
    # **判据（两个口径都算、都断言）**：
    #   · `runtime` = 页面里真有指向 esm.sh 的 URL（`https://esm.sh` / `//esm.sh`）
    #     ⇒ 会真的发外部请求；
    #   · `mention_only` = 只在注释或说明文字里出现 `esm.sh`
    #     ⇒ **不构成依赖**（`index-classic` 是解释 `*` 的脚注，
    #        `official-1-blocks` 是留档"改回本地 UMD"那次弯路的注释）。
    #   ⚠️ 只按"文件里出现过字符串"数（=7）就会把这两个算成依赖页 ——
    #      **字符串在不在，不等于依赖在不在**，这正是那一版错的来源。
    #
    # **怎么守**：与三处**显式哨兵**比对，而不是再抄一份名单：
    #   · `README.md` 的 `<!-- esm-sh-pages: runtime=… mention_only=… -->`；
    #   · `web/index.html` 页脚的 `data-esm-pages="…"`；
    #   · `web/lib/net-guard.js` docstring 里的名单（同一事实的第三份副本）。
    #   **哨兵缺失也报错** —— 删掉哨兵等于让这条守门失效（与 ⑦ 的 `PB_REQUIRED`
    #   同一个理由：**能靠删一行让检查变绿，检查就等于没有**）。
    print('\n=== ⑨ 依赖 esm.sh 的页面名单是否与文档一致 ===')
    _tools = str(ROOT / 'tools')
    if _tools not in sys.path:
        sys.path.insert(0, _tools)
    _OFFICIAL, _EXPERIMENTAL = set(), set()
    try:
        # 分类的**唯一权威**是 `tools/add_legacy_banners.py`（别处不许再抄一份）。
        from add_legacy_banners import EXPERIMENTAL as _EXPERIMENTAL  # noqa: F811
        from add_legacy_banners import OFFICIAL as _OFFICIAL          # noqa: F811
    except Exception as e:                                            # noqa: BLE001
        fails.append(f'取不到 `tools/add_legacy_banners.py` 的正式页名单（{e}）——'
                     f' ⑨ 无法区分"正式页 / 历史原型页"')
        print(f'  ❌ 取不到正式页名单：{e}')

    ESM_URL = re.compile(r'(?:https?:)?//esm\.sh')
    _WEB = ROOT / 'web'
    runtime, mention_only, nested = [], [], []
    for p in sorted(_WEB.glob('*.html')):
        raw = p.read_text(encoding='utf-8', errors='replace')
        # ⚠️ 名字统一用**不带后缀的 stem**（`community-atlas`）——
        #    与 README / `index.html` 里的哨兵写法一致。第一版这里用了 `p.name`
        #    （带 `.html`），于是对着两份**内容其实一样**的名单报了 4 个假警报
        #    —— 又一个"检查本身在误报"的例子（本脚本第五次）。
        if ESM_URL.search(raw):
            runtime.append(p.stem)
        elif 'esm.sh' in raw:
            mention_only.append(p.stem)
    # 口径声明是"顶层 `web/*.html`"（与 `check_design_system.py` 的扫描范围一致）。
    # ⚠️ 顶层以外若也引 CDN，**口径就破了** ⇒ 报错要求改口径或改页面；
    #    不静默忽略 —— 否则"范围"这个词会变成一句没人核对的话。
    for p in sorted(_WEB.rglob('*.html')):
        if p.parent == _WEB:
            continue
        if ESM_URL.search(p.read_text(encoding='utf-8', errors='replace')):
            nested.append(str(p.relative_to(ROOT)).replace('\\', '/'))

    # 权威名单里是**带后缀**的文件名 ⇒ 先取 stem 再比，避免两套写法打架。
    _OFFICIAL_S = {Path(x).stem for x in _OFFICIAL}
    _EXP_S = {Path(x).stem for x in _EXPERIMENTAL}
    hist = [n for n in runtime
            if n not in _OFFICIAL_S and n not in _EXP_S
            and not n.startswith('_lab_')]
    official_esm = [n for n in runtime if n in _OFFICIAL_S]
    print(f'  实测**运行时依赖** esm.sh：{len(runtime)} 个 —— {", ".join(runtime) or "无"}')
    print(f'    分类（权威名单 `add_legacy_banners.py`）：'
          f'历史原型页 {len(hist)} 个（{", ".join(hist) or "无"}）'
          f' · 正式页 {len(official_esm)} 个（{", ".join(official_esm) or "无"}）')
    print(f'  仅文字提及（**不构成依赖**）：{len(mention_only)} 个 —— '
          f'{", ".join(mention_only) or "无"}')

    # ⓪ **自检**：判据坏了会"扫出 0 个、然后一切通过" —— 这就是本脚本已经栽过两次的
    #    "形同虚设"（过度收紧一次、只认一种载体一次）。
    if not runtime:
        fails.append('web/*.html 里一个 esm.sh 运行时依赖都没扫到 —— '
                     '要么页面真的全改完了（那就该撤掉 README 的这句与哨兵），'
                     '要么扫描判据已失效；两种都不该"静默通过"')
        print('  ❌ 扫描结果为 0，判据可能已失效（拒绝静默通过）')

    # ① README 的哨兵 + 正文数字
    _readme = (ROOT / 'README.md').read_text(encoding='utf-8', errors='replace')
    m = re.search(r'<!--\s*esm-sh-pages:(.*?)-->', _readme, re.S)
    if not m:
        fails.append('README.md 缺 esm.sh 哨兵 '
                     '`<!-- esm-sh-pages: runtime=… mention_only=… -->` —— '
                     '哨兵被删，这条守门就再也拦不住这个数字漂移')
        print('  ❌ README 缺 `<!-- esm-sh-pages: … -->` 哨兵')
    else:
        _body = m.group(1)

        def _sentinel_set(key):
            """从哨兵体里取 `key=a,b,c` 的集合（没有该键就返回空列表）。"""
            mm = re.search(key + r'=([^\s]+)', _body)
            return sorted(x for x in (mm.group(1).split(',') if mm else []) if x)

        r_run, r_men = _sentinel_set('runtime'), _sentinel_set('mention_only')
        if r_run == sorted(runtime) and r_men == sorted(mention_only):
            print('  ✅ README 哨兵与实测一致（runtime / mention_only 两个口径）')
        else:
            if r_run != sorted(runtime):
                fails.append(f'README.md 哨兵写 runtime={r_run}，实测 {sorted(runtime)}'
                             f' —— 依赖 esm.sh 的页面名单漂了')
                print(f'  ❌ README 哨兵 runtime：{r_run} ≠ 实测 {sorted(runtime)}')
            if r_men != sorted(mention_only):
                fails.append(f'README.md 哨兵写 mention_only={r_men}，'
                             f'实测 {sorted(mention_only)} —— 文字提及口径漂了')
                print(f'  ❌ README 哨兵 mention_only：{r_men} ≠ 实测 {sorted(mention_only)}')

    # 正文里的数字也要守：读者看的是正文，不是哨兵。
    for pat, why in (
        (rf'\*\*{len(runtime)} 个页面仍依赖 esm\.sh\*\*', '运行时依赖页数'),
        (rf'\*\*历史原型页 {len(hist)} 个\*\*', '历史原型页数'),
        (rf'\*\*正式页 {len(official_esm)} 个\*\*', '正式页数'),
        # ⚠️ 这一条守的是**另一个口径**（"文件里出现过字样"）—— 两个口径都必须写在
        #    README 里，因为**只说一个数就一定会有人按另一个口径读**（这就是当初
        #    「5 个 vs 7 个」互相矛盾的来源）。它是 `runtime + mention_only` 的和：
        #    导航页加了 `*` 脚注后**它自己**也带上了 `esm.sh` 字样
        #    ⇒ 这个数会**因为"修文档"而变**（7 → 8），所以更要守。
        (rf'数是 \*\*{len(runtime) + len(mention_only)} 个\*\*',
         '「出现字样」口径的页数'),
    ):
        if re.search(pat, _readme):
            print(f'  ✅ README 正文的{why}与实测一致')
        else:
            fails.append(f'README.md 正文没写对{why}（期望出现与 `{pat}` 同形的写法）'
                         f' —— 实测 {len(runtime)} 个依赖页：'
                         f'历史原型 {len(hist)} / 正式 {len(official_esm)}')
            print(f'  ❌ README 正文没写对{why}')

    # ② 导航页：`*` 标注**必须真的在页面上**（这次的病灶本身就是"承诺只写在文档里"）
    _idx_p = _WEB / 'index.html'
    if not _idx_p.exists():
        print('  ⚠️ web/index.html 不存在，跳过导航页检查')
    else:
        idx = _idx_p.read_text(encoding='utf-8', errors='replace')
        mm = re.search(r'data-esm-pages="([^"]*)"', idx)
        if not mm:
            fails.append('web/index.html 页脚缺 `data-esm-pages="…"` 哨兵 —— '
                         '名单哨兵被删，导航页上的标注就没人核对了')
            print('  ❌ index.html 缺 `data-esm-pages` 哨兵')
        else:
            got = sorted(x for x in mm.group(1).split(',') if x)
            if got == sorted(runtime):
                print('  ✅ index.html 的 `data-esm-pages` 与实测一致')
            else:
                fails.append(f'web/index.html 的 data-esm-pages 写 {got}，'
                             f'实测 {sorted(runtime)} —— 导航页名单漂了')
                print(f'  ❌ index.html data-esm-pages：{got} ≠ 实测 {sorted(runtime)}')
        # 指向"运行时依赖页"的每个链接都要带 `*` ——
        # README 承诺的是"导航页已用 `*` 标注"，所以必须**逐个链接**核对，
        # 不能只看页脚有没有一段说明文字（那正是当初"承诺没兑现"的形态）。
        for name in runtime:
            for a in re.finditer(r'<a\b[^>]*href="' + re.escape(name) + r'\.html"[^>]*>(.*?)</a>',
                                 idx, re.S):
                if '*' not in a.group(1):
                    fails.append(f'web/index.html 里指向 {name} 的链接没有 `*` 标注 ——'
                                 f' README「导航页已用 `*` 标注」会再次落空')
                    print(f'  ❌ index.html 指向 {name} 的链接缺 `*`')
        fn = re.search(r'<p class="fn">(.*?)</p>', idx, re.S)
        if fn and 'esm.sh' in fn.group(1):
            print('  ✅ index.html 页脚解释了 `*`（写明 esm.sh 依赖与 net-guard 提示）')
        else:
            fails.append('web/index.html 没有解释 `*` 的含义（页脚缺含 `esm.sh` 的 '
                         '`<p class="fn">`）—— 标了星号却不解释，比不标更难懂')
            print('  ❌ index.html 缺 `*` 的含义说明（`<p class="fn">` 里没提 esm.sh）')

    # ③ `web/lib/net-guard.js` 的 docstring 是同一事实的**第三份副本** ——
    #    它此前同样把 `community-atlas` 写成"历史原型"。同一事实抄三遍，就三处都守。
    _guard = _WEB / 'lib' / 'net-guard.js'
    if not _guard.exists():
        print('  ⚠️ web/lib/net-guard.js 不存在，跳过')
    else:
        g = _guard.read_text(encoding='utf-8', errors='replace')
        miss = [n for n in runtime if n not in g]
        if miss:
            fails.append('web/lib/net-guard.js 的依赖名单缺：' + ', '.join(miss)
                         + ' —— 守门文件里的名单与实测漂了')
            print('  ❌ net-guard.js 名单缺：' + ', '.join(miss))
        else:
            print(f'  ✅ net-guard.js 的依赖名单含全部 {len(runtime)} 个实测页面')

    # ④ 口径声明：顶层以外不许有 esm.sh 运行时依赖（见上面的递归扫描）
    if nested:
        fails.append('web/ 顶层以外的页面也依赖 esm.sh：' + ', '.join(nested)
                     + ' —— README 与本守门声明的口径是「顶层 web/*.html」，'
                       '口径破了就得改口径或改页面')
        print('  ❌ 顶层以外的页面依赖 esm.sh：' + ', '.join(nested))
    else:
        print('  ✅ 顶层以外（`web/_archive/` · `web/benchmark/`）无 esm.sh 运行时依赖')

    print('\n' + '=' * 60)
    if fails:
        print(f'发现 {len(fails)} 处问题：')
        for f in fails:
            print('  · ' + f)
        return 1
    print('文档数字与产物一致')
    return 0
if __name__ == '__main__':
    sys.exit(main())
