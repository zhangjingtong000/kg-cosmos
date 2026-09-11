# -*- coding: utf-8 -*-
"""
统计截图里发光点的数量，用于客观核验 L1 到底渲染出多少个实体。

    python tools/count_dots.py <png> [亮度阈值]

做法：把图转灰度，二值化后做连通域标记，统计面积在合理区间内的连通块。
比肉眼数可靠——背景星野关掉时，画面里剩下的亮点就只可能是实体。
"""
import sys
import numpy as np
from PIL import Image
from scipy import ndimage


def main():
    path = sys.argv[1]
    thresh = int(sys.argv[2]) if len(sys.argv) > 2 else 110

    im = Image.open(path).convert('L')
    a = np.asarray(im)
    mask = a > thresh

    lab, n = ndimage.label(mask)
    if n == 0:
        print(f'{path}: 阈值 {thresh} 下没有任何亮点')
        return
    sizes = ndimage.sum(mask, lab, range(1, n + 1))
    # 过小的多半是噪点/抗锯齿碎片，过大的多半是连成一片
    keep = sizes[(sizes >= 3) & (sizes <= 5000)]
    print(f'{path}')
    print(f'  尺寸 {a.shape[1]}x{a.shape[0]}   阈值 {thresh}')
    print(f'  连通块总数 {n}')
    print(f'  面积 3~5000 的块 {len(keep)} 个')
    if len(keep):
        print(f'  面积分布 min/中位/max = {int(keep.min())}/{int(np.median(keep))}/{int(keep.max())}')
    print(f'  亮像素总数 {int(mask.sum())} ({100.0*mask.mean():.2f}%)')


if __name__ == '__main__':
    main()
