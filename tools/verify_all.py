# -*- coding: utf-8 -*-
"""串行跑全部验证脚本并汇总（回归入口）。

为什么串行：每个前端验证都要起一个带 CDP 的 Chrome 实例，串行最稳，
也避免多个实例同时读同一份 2.6MB 的 nebula.json 造成误判。

前置：本地静态服务已在 8098 跑起来
      `cd D:\\kg-demo && python -m http.server 8098 --bind 127.0.0.1`

用法：
  python tools/verify_all.py            # 全部
  python tools/verify_all.py --fast     # 跳过耗时的 PDF 截图对比
"""
import subprocess
import sys
import time
from pathlib import Path

CHECKS = [
    ("导航页 · 链接可达 · 示意图未溢出", ["tools/cdp_verify_nav.py"]),
    ("星云交互 · 标签 · 证据面板", ["tools/cdp_verify_ui.py", "0"]),
    ("标签分配与深度遮挡（节点级门槛）", ["tools/cdp_verify_label_detail.py"]),
    ("统一着色引擎（含默认外观等价性回归）", ["tools/cdp_verify_color.py", "0"]),
    ("方法能力与局限视图", ["tools/cdp_verify_method_limits.py"]),
    ("图谱分析面板（数字可追溯）", ["tools/cdp_verify_analytics.py"]),
    ("问题子图 PathQA", ["tools/cdp_verify_pathqa.py"]),
    ("证据深链 PDF（页码片段生效性）", ["tools/cdp_verify_pdflink.py"], "slow"),
]

# 纯数据侧的检查：不依赖浏览器，快
DATA_CHECKS = [
    ("社区布局数据自检", ["tools/diag_comm.py"]),
    ("claims 诊断（矛盾视图是否成立）", ["tools/diag_claims.py"]),
    ("结构指标与随机图基线", ["tools/l8_structure_audit.py"]),
    # 离线可用性是「离线演示铁律」（内部记录 §1 第 6 条）的守门检查：
    # 主视图曾因 three.js 走 unpkg CDN 而断网白屏，现已本地化。
    ("主视图离线可用（屏蔽外部域名）", ["tools/cdp_verify_offline.py", "web/kg-nebula.html"]),
]


def run(label, args, timeout=300):
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable] + args, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, cwd=str(Path(__file__).parent.parent))
        out = (p.stdout or "") + (p.stderr or "")
        ok = p.returncode == 0
    except subprocess.TimeoutExpired:
        out, ok = "超时", False
    dt = time.time() - t0
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-1:] or [""]
    print(f"{'✅' if ok else '❌'} {label:38s} {dt:6.1f}s  {tail[0][:88]}")
    if not ok:
        print("   ── 失败输出末尾 ──")
        for ln in [x for x in out.strip().splitlines() if x.strip()][-8:]:
            print("   " + ln[:150])
    return ok


def main():
    fast = "--fast" in sys.argv
    print("=" * 100)
    print("数据侧检查")
    print("-" * 100)
    results = []
    for label, args in DATA_CHECKS:
        results.append(run(label, args))
    print()
    print("=" * 100)
    print("前端检查（每个都会起一个带 CDP 的 Chrome）")
    print("-" * 100)
    for item in CHECKS:
        label, args = item[0], item[1]
        slow = len(item) > 2 and item[2] == "slow"
        if slow and fast:
            print(f"⏭  {label:38s}   (--fast 跳过)")
            continue
        results.append(run(label, args))
    print()
    print("=" * 100)
    ok = sum(1 for r in results if r)
    print(f"通过 {ok} / {len(results)}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
