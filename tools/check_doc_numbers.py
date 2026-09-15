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
import re
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

    def m(k):
        return metrics.get(k, {}).get('value')

    return {
        'nodes': l8['scale']['nodes'],
        'edges_overview': l8['scale']['edges'],
        'edges_eval': six['edges_total'],
        'edges_supported': six['edges_supported'],
        'l1_pct': round(six['L1_entity_presence']['rate'] * 100, 1),
        'l3_closed_pct': round(six['L3_endpoints']['all'] * 100, 1),
        'l3_extracted_pct': round(
            six.get('L3_endpoints_extracted_caliber', {}).get('all', 0) * 100, 1),
        'l5_pct': round(six['L5_triplet']['all'] * 100, 1),
        'l2_cumulative': six['L2_entity_clustering'].get('cumulative_merged_groups'),
        'l7_pct': round(l7['compliance_rate'] * 100, 1),
        'l9_pct': round((m('L9_endpoint_recall') or 0) * 100, 1),
    }


DOCS = [
    'README.md', 'RUNBOOK.md', 'docs/L5_SIX_LAYER_REPORT.md',
    # ── 前端页面也必须扫 ──
    # 初版只扫 Markdown，于是 `web/index.html` 的首屏 facts 里同时躺着两个问题：
    # `63.7% 关系原文支持率`（说法错误）与 `L1 92.8%`（旧值，应为 94.4%）。
    # 而这两个恰恰是**评委第一眼看到的**，比 docs 里的数字更该被守住。
    'web/index.html', 'web/analytics.html', 'web/kg-nebula.html',
    'web/method-limits.html', 'web/pathqa.html',
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
]

# 明确作废、不该再作为依据出现的表述（出现过一次就要报，除非同行有"已证伪"字样）
RETRACTED = [
    (r'实验④', '实验④（人工标注金标 P/R/F1）已被证伪'),
    (r'三通道抽取对比', '三通道抽取对比实验未成立（标注一致性 α≈0）'),
]


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
    STALE = {
        '15116': f"节点数应为 {cur['nodes']}",
        '20869': f"概览层边数应为 {cur['edges_overview']}",
        '15322': '实体名数已变（反建后为 19427）',
        '16287': 'L5 支持子图节点数已变',
        # L1 的三种写法（Markdown / 纯文本 / HTML）
        'L1 实体存在 92.8%': f"L1 应为 {L1}%",
        '**92.8%**': f"L1 应为 {L1}%",
        'L1 92.8%': f"L1 应为 {L1}%",
        '<b>92.8%</b>': f"L1 应为 {L1}%",
        '32 条 pytest': '测试数应为 74 条',
        '32 条，含': '测试数应为 74 条',
        '27981': f"L5 支持边数应为 {cur['edges_supported']}",
        '归并 384 组': f"L2 累计归并应为 {cur['l2_cumulative']} 组",
        '**归并 384**': f"L2 累计归并应为 {cur['l2_cumulative']} 组",
    }
    print('=== ① 旧值扫描 ===')
    for doc in DOCS:
        p = ROOT / doc
        if not p.exists():
            continue
        txt = p.read_text(encoding='utf-8', errors='replace')
        for bad, why in STALE.items():
            for m in re.finditer(re.escape(bad), txt):
                line_no = txt[:m.start()].count('\n') + 1
                line = txt.splitlines()[line_no - 1]
                # 允许出现在"历史/已修正"语境里；
                #
                # **2026-09-15 追加两个白名单词**：L1 现在**有两个合法口径** ——
                # 「含反建节点」18336/19427 = 94.4%（权威产物的 rate）
                # 与「纯抽取」14034/15123 = 92.8%。
                # 并列展示时 92.8% 是**真值而不是旧值**，所以不能报。
                #
                # 用**精确词**而不是泛化的"口径"——后者会让真正的旧值漏网
                # （这个脚本已经栽过一次"过度收紧"和一次"形同虚设"，
                # 见 :88-98 的两段教训）。
                if any(k in line for k in (
                        '已改', '修正', '此前', '曾', '旧', '❌',
                        '纯抽取', '不含反建', '抽取口径')):
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
    }
    for tok, why in need.items():
        if tok in readme:
            print(f'  ✅ README 含 {why} ({tok})')
        else:
            fails.append(f'README 未反映{why}（{tok}）——文档可能又落后了')
            print(f'  ❌ README 缺 {why} ({tok})')

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
