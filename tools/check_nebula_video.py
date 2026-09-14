# -*- coding: utf-8 -*-
"""对生成的星云视频做客观检查，对照用户的提示词要求。

用户提示词里的可量化要求：
  · 左侧约 35% 保持较暗、较平静（便于叠白色中文）
  · 主体细节集中在中右区域
  · 色彩以电光青蓝、洋红、紫罗兰、深靛蓝为主，少量冷白
  · 不要满屏金黄或橙色
  · 首尾构图/色温/亮度自然衔接（适合循环）

用法：
  python tools/check_nebula_video.py <video.mp4>
"""
import json
import re
import subprocess
import sys


def sh(args):
    return subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                          errors='replace').stdout + \
           subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                          errors='replace').stderr


def stats(args_filter, path, extra_in=None):
    """跑一次 ffmpeg signalstats，取平均值。"""
    cmd = ['ffmpeg', '-v', 'info']
    if extra_in:
        cmd += ['-ss', extra_in]
    cmd += ['-i', path, '-vf', args_filter, '-f', 'null', '-']
    p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    txt = (p.stdout or '') + (p.stderr or '')
    out = {}
    for m in re.finditer(r'lavfi\.signalstats\.(YAVG|UAVG|VAVG)=([\d.]+)', txt):
        out[m.group(1)] = float(m.group(2))
    return out


def main():
    path = sys.argv[1]
    W, H = 832, 480

    print(f'=== {path} ===')

    # 整帧
    whole = stats('signalstats,metadata=print', path)
    print(f'整帧     Y={whole.get("YAVG")}  U={whole.get("UAVG")}  V={whole.get("VAVG")}')

    # 左侧 35% / 中右 65%
    left = stats(f'crop={int(W*0.35)}:{H}:0:0,signalstats,metadata=print', path)
    right = stats(f'crop={int(W*0.65)}:{H}:{int(W*0.35)}:0,signalstats,metadata=print', path)
    ly, ry = left.get('YAVG', 0), right.get('YAVG', 0)
    print(f'左侧35%  Y={ly}')
    print(f'中右65%  Y={ry}')
    if ry > 0:
        print(f'  → 中右/左 亮度比 = {ry / max(ly, 0.01):.2f}  '
              f'({"✓ 左侧更暗，有负空间" if ly < ry * 0.85 else "✗ 左侧不够暗"})')

    # 色相倾向：V-U 越大越暖（U 是蓝差、V 是红差）
    v, u = whole.get('VAVG', 128), whole.get('UAVG', 128)
    warm = v - u
    print(f'色度     V-U = {warm:.1f}  '
          f'({"偏暖/金橙" if warm > 8 else "偏冷/青蓝紫" if warm < 0 else "中性"})')

    # 首帧 vs 末帧（循环衔接）
    first = stats('signalstats,metadata=print', path)
    last = subprocess.run(['ffmpeg', '-v', 'info', '-sseof', '-0.1', '-i', path,
                           '-vf', 'signalstats,metadata=print', '-f', 'null', '-'],
                          capture_output=True, text=True, encoding='utf-8', errors='replace')
    txt = (last.stdout or '') + (last.stderr or '')
    lv = {}
    for m in re.finditer(r'lavfi\.signalstats\.(YAVG|UAVG|VAVG)=([\d.]+)', txt):
        lv[m.group(1)] = float(m.group(2))
    print(f'首帧     Y={first.get("YAVG")} U={first.get("UAVG")} V={first.get("VAVG")}')
    print(f'末帧     Y={lv.get("YAVG")} U={lv.get("UAVG")} V={lv.get("VAVG")}')
    dy = abs(first.get('YAVG', 0) - lv.get('YAVG', 0))
    du = abs(first.get('UAVG', 0) - lv.get('UAVG', 0))
    dv = abs(first.get('VAVG', 0) - lv.get('VAVG', 0))
    print(f'  → 首末差 ΔY={dy:.1f} ΔU={du:.1f} ΔV={dv:.1f}  '
          f'({"✓ 衔接良好" if dy < 6 and du < 6 and dv < 6 else "⚠ 首末差异偏大，循环会跳"})')


if __name__ == '__main__':
    main()
