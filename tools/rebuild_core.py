"""M3 核心论文的批量本地重跑（修复端点后的管线）。

选择依据：M3 的社区星云、跨文献关系、综述生成需要
  ① 主题覆盖（构建 / 嵌入与表示 / LLM 交叉）
  ② 单篇规模适中（0.5–1.5 小时可跑完，避免一篇吃掉整晚）
  ③ 已有 2 篇金标（2012.00456v1、2401.04507v1）作为质量锚点

用法：
  python tools/rebuild_core.py            # 跑全部核心篇
  python tools/rebuild_core.py --dry-run  # 只打印计划
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# M3 核心篇：覆盖构建/嵌入/LLM 三条主线，规模从小到大
CORE = [
    ("2506.09566v1", "From Symbolic to Neural and Back（KG-LLM 协同）", 141),
    ("2406.08223v2", "LLM 与 KG 交叉的研究趋势（已有金标）", 138),
    ("2510.20345v1", "LLM 驱动的知识图谱构建综述", 126),
    ("1703.08098v9", "KG 补全的嵌入模型综述", 137),
    ("2202.07412v1", "KG 推理：逻辑与嵌入", 110),
    ("2302.05019v1", "自动知识图谱构建综述", 478),
]


def main():
    dry = "--dry-run" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    mode = "local"
    if "--cloud" in sys.argv:
        mode = "cloud"
    todo = [c for c in CORE if not only or c[0] in only]

    print(f"M3 核心重跑：{len(todo)} 篇（通道 {mode}，端点已修复）")
    total_chunks = sum(c[2] for c in todo)
    per_chunk = {"local": 10, "cloud": 3}[mode]
    print(f"预计总块数 {total_chunks}，按 {mode} 约 {per_chunk} 秒/块，"
          f"估计 {total_chunks*per_chunk/60:.0f} 分钟\n")
    for pid, desc, chunks in todo:
        print(f"  {pid:16} {chunks:4} 块  {desc}")
    if dry:
        return

    results = []
    for i, (pid, desc, chunks) in enumerate(todo, 1):
        print(f"\n{'='*60}\n[{i}/{len(todo)}] 开始 {pid} —— {desc}\n{'='*60}", flush=True)
        t0 = time.time()
        proc = subprocess.run(
            [sys.executable, "-u", str(ROOT / "tools" / "extract_channel.py"), mode, pid],
            cwd=str(ROOT), capture_output=False,
        )
        dt = time.time() - t0
        ok = proc.returncode == 0
        results.append((pid, ok, dt))
        print(f"[{i}/{len(todo)}] {pid} {'完成' if ok else '失败'}，用时 {dt/60:.1f} 分钟",
              flush=True)
        time.sleep(3)

    print(f"\n{'='*60}\n汇总\n{'='*60}")
    for pid, ok, dt in results:
        print(f"  {pid:16} {'✓' if ok else '✗'}  {dt/60:5.1f} 分钟")


if __name__ == "__main__":
    main()
