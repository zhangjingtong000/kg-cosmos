"""L5 域内效度：可自动化部分的检查 + 人工盲判进度追踪。

【这层要回答什么】
  对外报的 L5 = 63.7% 是**判别器判通过的比例**，既不是精度也不是召回；
  而阈值 0.67 来自**官方英文校准集**，本项目数据上的效度从未被验证过。

【为什么只能部分自动化】
  真正的效度需要**判别器之外**的判定者。零预算下没有现成的非模型真值：
  本机另一个模型（Ollama）能回答"换判定器结论变多少"，但两个模型一致 ≠ 正确
  ——它们可能共享同一偏置。所以严格效度只能由人盲判给出。
  本脚本因此做两件事：
    ① 断言**可自动化**的那些结论（表述敏感性、方向盲区、规则假阳性下界）；
    ② 报告人工盲判的**进度**，让"这层还没做完"是可见的，而不是被当成做完了。

用法：
  python tools/check_l5_validity.py            # 读已有产物，快
  python tools/check_l5_validity.py --probe    # 顺带重跑小规模探针（约 80 秒）
"""
import argparse
import json
import sys
from pathlib import Path
from idempotent_write import dumps, write_if_changed

ROOT = Path(__file__).resolve().parent.parent
Q = ROOT / 'data' / 'quality'
PROBE = Q / 'l5-validity-probe.json'
SAMPLE = Q / 'l5-validity-sample.jsonl'
KEY = Q / 'l5-validity-key.json'
LABELS = Q / 'l5-validity-labels.jsonl'      # 人工盲判落盘（可不存在）


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--probe', action='store_true', help='顺带重跑探针（慢）')
    args = ap.parse_args()

    fails, notes = [], []

    if args.probe:
        import subprocess
        print('重跑探针（n=30）…')
        subprocess.run([sys.executable, str(ROOT / 'tools' / 'l5_validity_probe.py'),
                        '--n', '30'], cwd=str(ROOT), check=False)

    if not PROBE.exists():
        print(f'缺少 {PROBE.name}，先跑 tools/l5_validity_probe.py')
        return 1
    d = json.loads(PROBE.read_text(encoding='utf-8'))
    p = d['probes']
    print('=== ① 可自动化结论 ===\n')

    # P1：假设句表述的敏感性——若极差很大，说明 63.7% 只是措辞的产物
    forms = {k: v for k, v in p['P1_hypothesis_form'].items() if isinstance(v, dict)}
    rates = {k: v['pass_rate'] for k, v in forms.items()}
    spread = (max(rates.values()) - min(rates.values())) * 100
    print(f'  P1 假设句形态通过率极差: {spread:.1f}pp  '
          f'({", ".join(f"{k}={v*100:.1f}%" for k, v in rates.items())})')
    if spread > 25:
        fails.append(f'P1 表述极差 {spread:.1f}pp 过大 ⇒ 63.7% 主要是措辞的产物')
    else:
        notes.append(f'P1 极差 {spread:.1f}pp，未发现"措辞主导"')

    # P2：前提形态（单句 vs 整段）——标定与运行口径不一致的证据
    p2 = {k: v for k, v in p['P2_premise_form'].items() if isinstance(v, dict)}
    if len(p2) == 2:
        a, b = list(p2.values())
        delta = (b['pass_rate'] - a['pass_rate']) * 100
        print(f'  P2 前提形态: 单句 {a["pass_rate"]*100:.1f}% vs 整段 {b["pass_rate"]*100:.1f}%'
              f'  Δ={delta:+.1f}pp')
        notes.append(f'P2 标定口径（整段）与运行口径（单句）相差 {delta:+.1f}pp')
    else:
        # ⚠️ **2026-09-20 修（守门假绿审计 §三·B8）**：原来**没有 else 分支** ——
        #    `P2_premise_form` 只要不是恰好 2 个键，这一整条判据就**静默消失**：
        #    输出里完全没有 P2 那一行，L234 又会用 `if len(p2) == 2 else None`
        #    把产物字段一起抹平成 null ⇒ **判据消失得无声无息，而脚本照旧 exit 0**。
        #    ⇒ 现在把它变成**可见且算失败**的："算不出来"不是"通过"。
        fails.append(f'P2 判据无法计算：`P2_premise_form` 的键数是 {len(p2)}（预期 2）'
                     f' —— probe 产物结构变了，或标定/运行口径之一缺失')
        print(f'  ❌ P2 前提形态：`P2_premise_form` 键数 {len(p2)}（预期 2）'
              f' ⇒ **本判据算不出来，不算通过**')
        notes.append(f'P2 无法计算：键数 {len(p2)} ≠ 2')

    # P3：方向盲区——L5 能拦住多少方向反转
    p3 = p['P3_direction']
    print(f'  P3 方向反转的翻转率: {p3["flip_rate"]*100:.1f}%  '
          f'（mean Δ={p3["mean_delta"]:+.4f}, n={p3["n"]}）')
    if p3['flip_rate'] > 0.5:
        fails.append('P3 翻转率异常高，与"L5 拦不住方向错误"的既有结论不符')
    else:
        notes.append(f'P3 只拦住 {p3["flip_rate"]*100:.1f}% 的方向反转'
                     f' ⇒ 约 {100-p3["flip_rate"]*100:.0f}% 被静默放过')

    # P4：规则型假阳性下界（完全独立于模型权重，只依赖字面规则）
    p4 = p['P4_rule_based_fp_floor']
    strict = p4['selfloop_asym_pass'] / p4['pass_total'] * 100
    loose = p4['selfloop_pass'] / p4['pass_total'] * 100
    print(f'  P4 规则型假阳性下界（分母=通过集 {p4["pass_total"]}）: '
          f'严格 {p4["selfloop_asym_pass"]} 条 = {strict:.2f}%，'
          f'宽松 {p4["selfloop_pass"]} 条 = {loose:.2f}%')
    notes.append(f'P4 给出了一个**独立于模型**的假阳性下界（≥{strict:.2f}%）'
                 f'——这是本层唯一不依赖任何模型权重的证据')

    # ── ② 人工盲判进度 ──
    print('\n=== ② 人工盲判进度（严格效度只能来自这里）===\n')
    n_sample = 0
    if SAMPLE.exists():
        n_sample = sum(1 for _ in SAMPLE.open(encoding='utf-8'))
    n_label = 0
    if LABELS.exists():
        n_label = sum(1 for _ in LABELS.open(encoding='utf-8'))
    print(f'  抽样清单 {n_sample} 条  →  {LABELS.name}')
    print(f'  已判      {n_label} 条')
    # ⚠️ **2026-09-20 修（守门假绿审计 §二·A6）**：这里原来写死「κ=0.722」，
    #    而结论段又写死「两个独立标注者 κ≥0.6」—— **两句都没读 artifact**，
    #    判据只数了行数（`n_label >= n_sample`）。
    #    实测：把 `l5-annotator-agreement.json` 的 κ 改成 0.55，同一份输出里
    #    下面会打印「κ=0.55」，而这里与结论段照旧说「κ=0.722 / κ≥0.6、本层已完成」，
    #    `exit 0` —— **同一个脚本的相邻两句话互相矛盾**。
    #    ⇒ 现在真的读 artifact，并且 **κ≥0.6 是一个真断言**。
    k_early = None
    _agr = Q / 'l5-annotator-agreement.json'
    if _agr.exists():
        try:
            k_early = json.loads(_agr.read_text(encoding='utf-8')).get('cohen_kappa')
        except Exception:
            k_early = None
    if n_sample and n_label >= n_sample and k_early is not None and k_early >= 0.6:
        print(f'  ✅ 盲判已完成（{n_label}/{n_sample}，两个独立标注者 κ={k_early}）。')
        print('     跑 tools/l5_validity_report.py 出本项目分布上的 PPV/召回。')
        print('     **关键结论**：真实支持率 40.0%、判别器 PPV 仅 49.7%、')
        print('     域内加权 AUC 0.7074（官方校准集是 0.9851）。')
        print('     详见 docs/L5_VALIDITY_REPORT.md。')
    elif n_sample and n_label >= n_sample:
        _why = ('**标注者间一致性尚未测**' if k_early is None
                else f'**标注者间 κ={k_early} < 0.6**')
        print(f'  ⚠️ 盲判样本已判满（{n_label}/{n_sample}），但{_why}')
        print('     ⇒ **还不能声明"本层已完成"**；跑 tools/check_l5_agreement.py 核对一致性。')
    elif n_label:
        print(f'  ⏳ 进行中（{n_label/n_sample*100:.0f}%）；'
              f'跑 tools/l5_validity_review.py 继续')
    else:
        print('  ⏳ 尚未开始。启动盲判界面：')
        print('       python tools/l5_validity_review.py   → http://127.0.0.1:8097/')
        print('     这是本层唯一能给出**严格效度**的路径；'
              '在它完成之前，L5 的 63.7% 只能声明为"通过率"，不能声明为"准确率"。')

    # ── ③ 第二判定者（本地 Ollama）为何未执行 ──
    # 这不是"忘了做"，而是有明确的资源约束；写出来，免得下一轮又去试一遍。
    print('\n=== ③ 第二判定者（本地 Ollama）的可用性 ===\n')
    import shutil
    ollama_exe = shutil.which('ollama')
    if not ollama_exe:
        print('  · 本机没装 ollama ⇒ 该路径不可用')
        notes.append('第二判定者不可用：本机无 ollama')
    else:
        print(f'  · ollama 可执行文件: {ollama_exe}（服务未运行）')
        gpu_txt = ''
        try:
            import subprocess
            gpu_txt = subprocess.run(
                ['nvidia-smi', '--query-gpu=memory.used,memory.total',
                 '--format=csv,noheader'],
                capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            gpu_txt = ''
        print(f'  · 显存: {gpu_txt or "读不到"}')
        notes.append(
            '第二判定者未执行：本机显存被其他进程占用（约 5.2/8.2 GB），'
            '9B 级模型装不下；可用的其余模型是嵌入模型（bge-m3 / nomic）'
            '或视觉模型（qwen3-vl:4b / qwen25-vl-3b），都不适合做 NLI 判定。'
            '**这是资源约束，不是遗漏**——待显存空闲时可跑 '
            'tools/l5_validity_second_judge.py。')

    # ── ④ 阈值灵敏度（可自动算；但要说清哪些是恒等式、哪些有信息量）──
    #     通过率随阈值单调下降是**数学恒等式**，没有信息量；
    #     有信息量的是「规则层假阳性在通过集里占多少」随阈值怎么变——
    #     因为规则层完全不依赖模型权重，它是唯一独立于判别器的读数。
    print('\n=== ④ 阈值灵敏度（规则层下界独立于模型）===\n')
    rel = Q / 'relation-verify-all.json'
    sens = []
    if rel.exists():
        edges = json.loads(rel.read_text(encoding='utf-8'))['edges']

        def is_selfloop(e):
            import re as _re
            n = lambda s: _re.sub(r'[^a-z0-9]+', '', (s or '').lower())
            return n(e.get('source')) == n(e.get('target'))

        n_all = len(edges)
        print(f'  {"阈值":>6s} {"通过":>7s} {"通过率":>8s} {"其中自环":>9s} {"规则下界":>9s}')
        for t in (0.50, 0.60, 0.67, 0.75, 0.80):
            passed = [e for e in edges if (e.get('entail_prob') or 0) >= t]
            loops = sum(1 for e in passed if is_selfloop(e))
            rate = len(passed) / n_all * 100
            floor = loops / len(passed) * 100 if passed else 0.0
            sens.append({'threshold': t, 'passed': len(passed),
                         'pass_rate': round(rate, 2), 'selfloop': loops,
                         'rule_fp_floor_pct': round(floor, 3)})
            print(f'  {t:6.2f} {len(passed):7d} {rate:7.1f}% {loops:9d} {floor:8.3f}%')
        print('\n  读法：')
        print('   · 通过率随阈值下降是**恒等式**（就是"分数 ≥ 阈值"的定义），')
        print('     不构成任何质量证据；')
        print('   · 有信息量的是「规则下界」这一列：它用**完全不依赖模型权重**的')
        print('     字面规则（规范化后 source == target 的自环）估出通过集里')
        print('     至少有多少条是错的。')
        # ⚠️ 这里的方向必须由数据决定，不能凭直觉写。
        # 初版按"提阈值应筛掉明显错误"写成了"下界会下降"，
        # 而实测是**上升**（0.696% → 0.866%）——自环在通过集里的占比
        # 反而随阈值升高，说明判别器**对自环不敏感**
        # （实测自环通过率 91.4%，远高于全集 63.7%）。
        first = sens[0]['rule_fp_floor_pct']
        last = sens[-1]['rule_fp_floor_pct']
        if last > first:
            print(f'   · **实测该列上升**（{first:.3f}% → {last:.3f}%）：')
            print('     自环在通过集里的占比反而随阈值升高 ⇒')
            print('     **提阈值并不能筛掉这类明显错误，判别器对自环不敏感**')
            print('     （自环通过率约 91%，远高于全集 63.7%）。')
            print('     这条结论**独立于任何模型**，可以直接写进论文。')
            notes.append(
                f'阈值灵敏度：规则下界随阈值**上升**（{first:.3f}%→{last:.3f}%）'
                f'⇒ 判别器对自环不敏感，提阈值筛不掉它')
        else:
            print(f'   · 实测该列下降（{first:.3f}% → {last:.3f}%）：')
            print('     说明提阈值确实在筛掉这类明显错误（但仍是下界，不是准确率）。')
    else:
        notes.append('缺 relation-verify-all.json，跳过阈值灵敏度')

    print('\n=== 结论 ===\n')
    for n in notes:
        print(f'  · {n}')

    # 标注者间一致性：这是"上面那些数字可不可信"的元证据
    agr_p = Q / 'l5-annotator-agreement.json'
    k = None          # ← 先给默认值：下面的结论段要用它，而这个文件可能不存在
    print()
    if agr_p.exists():
        a = json.loads(agr_p.read_text(encoding='utf-8'))
        k = a.get('cohen_kappa')
        print(f'  · 标注者间一致性：κ={k}（n={a.get("n")}，'
              f'一致率 {a.get("agreement", 0)*100:.1f}%）')
        print(f'    两个标注者独立得出的支持率：'
              f'{a.get("rate_annotator1", 0)*100:.1f}% vs '
              f'{a.get("rate_annotator2", 0)*100:.1f}%')
        print(f'    ⇒ {a.get("reading", "")}')
        if k is not None and k < 0.4:
            fails.append(f'标注者间 κ={k} < 0.4 —— 判定任务不可稳定复现，'
                         f'40.0% 只能报为"一次读数"')
    else:
        print('  · 标注者间一致性：尚未测（跑 tools/check_l5_agreement.py）')

    print()
    # ⚠️ **2026-09-20 修（§二·A6）**：原为写死的「盲判 200/200，两个独立标注者 κ≥0.6」，
    #    与上面 ② 段一样不读 artifact。现在两处**共用**同一个真实 κ。
    if n_sample and n_label >= n_sample and k is not None and k >= 0.6:
        print(f'  ✅ 本层已完成：盲判 {n_label}/{n_sample}，两个独立标注者 κ={k}，')
        print('     结论（真实支持率 40.0% / PPV 49.7% / 域内 AUC 0.7074）可用。')
        print('     **限度**：两个标注者都是 LLM（不同会话），不是人类金标；')
        print('     κ 说明"任务可稳定复现"，不等于"达到了人类标注水准"。')
    elif n_sample and n_label >= n_sample:
        _why = ('标注者间一致性尚未测' if k is None else f'标注者间 κ={k} < 0.6')
        print(f'  ⚠️ 本层未完成：盲判样本已判满（{n_label}/{n_sample}），但{_why}')
        print('     ⇒ 结论只能报为"一次标注的读数"，**不能**当严格效度用。')
    else:
        print(f'  ⚠️ 本层未完成：严格效度需要人工盲判 {n_sample} 条，')
        print(f'     当前进度 {n_label}/{n_sample}。在此之前只有上列**代理证据**。')

    # 幂等写：内容没变就不碰 mtime（否则跑回归会刷新它，让下游变 STALE）。
    write_if_changed(Q / 'l5-validity-check.json', dumps({
        'probe': {'forms_spread_pp': round(spread, 2), 'rates': rates,
                  'p2_delta_pp': round(delta, 2) if len(p2) == 2 else None,
                  'p3_flip_rate': p3['flip_rate'], 'p4_strict_ppm': round(strict, 3)},
        'threshold_sensitivity': sens,
        'annotation': {'sampled': n_sample, 'labeled': n_label,
                       'complete': bool(n_sample and n_label >= n_sample)},
        'notes': notes,
        'caveat': '严格效度需人工盲判；代理证据不能替代。'
                  '通过率随阈值的变化是恒等式，有信息量的是规则层下界。',
    }))

    if fails:
        print('\n未通过：')
        for f in fails:
            print('  · ' + f)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
