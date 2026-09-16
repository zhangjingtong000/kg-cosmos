# -*- coding: utf-8 -*-
r"""预计算全库实体名的嵌入 —— H4「PathQA 端点匹配失败时的语义兜底」的数据侧。

## 为什么要预计算

PathQA 做的是"字面种子匹配 + 2 跳 BFS"。**字面匹配失败时**（问题里的词
和任何实体名都对不上），现在的行为是"检索不到"。语义兜底就是：
**拿问题里的词去和全库实体名比余弦，找最像的几个。**

但**不能每次请求现算**：全库 19469 个实体名，`bge-m3` 一次批 64 条要
几百次请求 —— 那会把一次交互变成几十秒。**⇒ 预计算一次，存成 `.npy`。**

## 产物与代价

    data/quality/entity-embeddings.npz
      · names   : 19469 个实体名（object 数组）
      · vectors : (19469, 1024) float32  ⇒ 约 80 MB

**80 MB 不入库**（加进 `tools/check_artifacts_tracked.py` 的 `LARGE_KNOWN`），
但**这条命令要能用** —— 所以本脚本自己就是"重建说明"。

## 一个刻意的取舍：**不做 PCA 降维**

降到 256 维能省 60 MB，但**降维会改变余弦**，而 H4 的结论恰好是
"余弦的中间区不可判"（见 `docs/H4_EMBEDDING_RESULT.md`）——
**在本来就脆弱的判别上再叠一层近似，得不偿失。** 存全维度，用磁盘换确定性。

用法：
    python tools/build_entity_embeddings.py                  # 全库
    python tools/build_entity_embeddings.py --limit 2000     # 小规模试跑
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))

GRAPHS = ROOT / 'data' / 'corpus' / 'graphs'
OUT = ROOT / 'data' / 'quality' / 'entity-embeddings.npz'


def parse_args():
    ap = argparse.ArgumentParser(description='预计算实体名嵌入（H4 语义兜底）')
    ap.add_argument('--out', default=str(OUT))
    ap.add_argument('--limit', type=int, default=0, help='只嵌前 N 个（试跑用）')
    ap.add_argument('--batch', type=int, default=64)
    return ap.parse_args()


ARGS = parse_args()


def load_entity_names():
    """全库实体名（去重）。**与图谱口径一致**：含节点名与边两端的名字。"""
    names = set()
    for f in sorted(GRAPHS.glob('*.json')):
        try:
            d = json.loads(f.read_text(encoding='utf-8-sig'))
        except Exception:
            continue
        for n in (d.get('nodes') or []):
            nm = n.get('name') or n.get('id')
            if nm:
                names.add(str(nm))
        for e in (d.get('edges') or []):
            for k in ('source', 'target'):
                if e.get(k):
                    names.add(str(e[k]))
    return sorted(names)


def main():
    import numpy as np
    from embeddings import embed

    t0 = time.time()
    names = load_entity_names()
    if ARGS.limit:
        names = names[:ARGS.limit]
    print(f'  实体名 {len(names)} 个（去重后）')

    vecs = []
    done = 0
    for i in range(0, len(names), ARGS.batch):
        chunk = names[i:i + ARGS.batch]
        vecs.extend(embed(chunk, batch=ARGS.batch))
        done += len(chunk)
        if done % (ARGS.batch * 10) == 0 or done == len(names):
            el = time.time() - t0
            rate = done / el if el else 0
            eta = (len(names) - done) / rate if rate else 0
            print(f'    {done}/{len(names)}  ·  {el:.0f}s  ·  {rate:.0f} 条/秒'
                  f'  ·  预计还需 {eta:.0f}s')

    arr = np.asarray(vecs, dtype=np.float32)
    # 归一化一次 —— 之后余弦就是点积（**省掉每次的开方与除法**）
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    arr = arr / norms

    out = Path(ARGS.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, names=np.array(names, dtype=object), vectors=arr)

    mb = out.stat().st_size / 1024 / 1024
    print()
    print(f'  已写 {out.name}（{mb:.1f} MB · {arr.shape[0]} × {arr.shape[1]}）')
    print(f'  **归一化过了** ⇒ 检索时余弦 = 点积')
    print(f'  总耗时 {time.time() - t0:.0f}s')
    print()
    print('  ⚠️ 这个产物**不入库**（80 MB 级），重建就是重跑本脚本：')
    print('       python tools/build_entity_embeddings.py')
    return 0


if __name__ == '__main__':
    sys.exit(main())
