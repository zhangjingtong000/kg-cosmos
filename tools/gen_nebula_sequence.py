"""生成星云演化序列：5 张图，对应滚动的 5 个阶段。

【为什么是一组而不是一张】
  单张图只能当静态背景。用户要的是"整页一条连续动画，滚动到哪就演化到哪"。
  做法：生成**同一世界观的 5 个阶段**（远景 → 靠近 → 进入 → 深处 → 拉远），
  页面按滚动进度在相邻两张之间交叉淡化 —— 于是整页是一次连续的旅程。

【怎么保证 5 张"像同一个地方"】
  · 固定同一套描述词根（interstellar gas / dust lanes / teal and gold）；
  · 只改"镜头距离"与"主体占比"这类叙事性措辞；
  · seed 用同一族的相邻值，减少风格漂移。

【成本】每张约 70 秒（首次加载权重后更快），5 张约 5–6 分钟。全程本地。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(r'D:\kg-demo')
GEN = ROOT / 'tools' / 'gen_nebula.py'

# 词根固定，只有"镜头/主体占比/色温"在变 —— 这样才是同一个地方的不同时刻
ROOT_WORDS = ("interstellar gas and dust, Hubble astrophotography, "
              "cinematic, ultra detailed, deep black background, "
              "sharp starfield, no text, no watermark, no frame, "
              "no border, no signature")

STAGES = [
    # (输出名, seed, 镜头与主体描述)
    ("sky-0-distant.png",  20260912,
     "extremely distant nebula barely visible in the far distance, "
     "vast and mostly empty field of tiny stars, cold deep blue, "
     "very dim, the nebula occupies only a small area near the center"),
    ("sky-1-approach.png", 20260913,
     "approaching a nebula from far away, the nebula now clearly visible "
     "and occupying the lower third of the frame, teal cyan glow starting "
     "to fill the space, still many stars around"),
    ("sky-2-enter.png",    20260914,
     "flying into a nebula, huge volumetric clouds of glowing gas filling "
     "the whole frame, teal cyan and warm gold, visible dark dust lanes "
     "with bright rims, dense stars behind the gas"),
    ("sky-3-deep.png",     20260915,
     "deep inside a nebula, dense glowing gas surrounding the viewer, "
     "dominant warm gold core with cyan edges, heavy dark dust filaments, "
     "strong sense of being immersed in the cloud"),
    ("sky-4-recede.png",   20260916,
     "a beautiful nebula seen from a distance again, now a small glowing "
     "island floating in dark empty space, teal and gold, surrounded by "
     "quiet scattered stars, calm and vast"),
]


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    for name, seed, desc in STAGES:
        if only and only not in name:
            continue
        prompt = f"{desc}, {ROOT_WORDS}"
        print(f'=== {name} (seed={seed}) ===')
        r = subprocess.run(
            [sys.executable, str(GEN),
             '--w', '1280', '--h', '720', '--steps', '4',
             '--seed', str(seed), '--name', name, '--prompt', prompt],
            cwd=str(ROOT), capture_output=True, text=True, encoding='utf-8',
            errors='replace')
        out = (r.stdout or '') + (r.stderr or '')
        for ln in out.splitlines():
            if '✅' in ln or '❌' in ln or '已提交' in ln or '超时' in ln:
                print('  ' + ln.strip())
        print()
    print('输出目录:', ROOT / 'web' / 'assets')


if __name__ == '__main__':
    main()
