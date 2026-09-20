# -*- coding: utf-8 -*-
r"""H5 的守门：**训练脚本必须显式禁 DataLoader worker**，且产物必须完整。

## 为什么这条守门值得写

H5 的实现里踩了一个**花掉 40 分钟才定位**的坑：

```
进程跑了 40 分钟 ⇒ CPU **20 秒只涨 0.3 秒**、内存不变、无产物、92 个线程
⇒ **它不是慢，是卡死**
```

**根因**：`SFTConfig` **没有显式设 `dataloader_num_workers=0`** ——
而 Windows 上 `num_workers>0` 的 spawn/信号量问题会让子进程与主进程互相等。
**修掉之后，5 步训练 7.1 分钟跑完，loss / token accuracy / 学习率全都正常。**

**⇒ 而"默认应该就是 0"这种假设，正是那次踩坑的原因。**
⇒ 所以这条守门**不看默认值**，而是**断言源码里显式写着**。

## 断言五组

1. **源码里有 `dataloader_num_workers=0`**（**显式**，不是靠默认）；
2. **源码里有 `dataloader_pin_memory=False`**（它在 Windows 上也会拉线程）；
3. **`--base` 支持本地路径**（第一版硬用 HF id ⇒ 权重在本地却去网上找）；
4. **若 `adapter-smoke` 存在，它必须完整**：
   `adapter_model.safetensors` + `adapter_config.json` + `train-summary.json`；
5. **★ 若摘要存在，`final_train_loss` 不许是 `null`** ——
   第一版取 `log_history[-1]['loss']`，而最后一条是汇总记录（**只有 `train_loss`**）
   ⇒ 恒为 `None`。**跑完了却说"没有 loss"，那是记录在说谎。**

用法：
    python tools/check_distill_train.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'tools' / 'distill_qlora_train.py'
ADAPTER = ROOT / 'data' / 'distill' / 'adapter-smoke'


def main():
    print('=' * 92)
    print('H5 · 蒸馏微调（**训练脚本的默认值必须显式** · 产物必须完整）')
    print('=' * 92)
    if not SRC.exists():
        print(f'  ❌ 缺 {SRC}')
        return 1
    src = SRC.read_text(encoding='utf-8')
    fails = []

    # ── ①② 显式禁用 DataLoader worker / pin_memory ───────────────
    m1 = re.search(r'dataloader_num_workers\s*=\s*0', src)
    m2 = re.search(r'dataloader_pin_memory\s*=\s*False', src)
    print(f'  ① 显式 `dataloader_num_workers=0`：{"✅" if m1 else "❌"}')
    print(f'  ② 显式 `dataloader_pin_memory=False`：{"✅" if m2 else "❌"}')
    if not m1:
        fails.append('源码里没有显式的 dataloader_num_workers=0 '
                     '（**这正是那次卡死 40 分钟的根因**）')
    if not m2:
        fails.append('源码里没有 dataloader_pin_memory=False')
    print()

    # ── ③ --base 支持本地路径 ─────────────────────────────────────
    m3 = re.search(r"add_argument\(\s*'--base'", src)
    print(f'  ③ `--base` 是参数（可传本地路径）：{"✅" if m3 else "❌"}')
    if not m3:
        fails.append('--base 不是命令行参数 ⇒ 无法指向本地权重')
    print()

    # ── ④ 若产物存在，必须完整 ───────────────────────────────────
    need = ['adapter_model.safetensors', 'adapter_config.json', 'train-summary.json']
    if ADAPTER.exists():
        sizes = {f.name: f.stat().st_size for f in ADAPTER.rglob('*') if f.is_file()}
        miss = [n for n in need if n not in sizes]
        # ⚠️ **0 字节不算"有"**（2026-09-20 修，守门假绿审计 §三·B5）：
        #    原来只收文件名，于是 **0 字节**的 `adapter_model.safetensors`
        #    也算「缺的产物：无」（镜像实测过）。同项目 `check_paper_registry.py`
        #    早就写着"0 字节不算入库"，这里与它对齐。
        empty = [n for n in need if n in sizes and sizes[n] == 0]
        print(f'  ④ `adapter-smoke` 存在 ⇒ 缺的产物：{miss or "无"}'
              + (f' · **0 字节的产物：{empty}**' if empty else ''))
        for n in need:
            if n in sizes:
                print(f'       {n:<32}{sizes[n] / 1024:>9.0f} KB')
        if miss:
            fails.append(f'adapter-smoke 缺产物：{miss}')
        if empty:
            fails.append(f'adapter-smoke 有 0 字节产物（不算完整）：{empty}')
    else:
        print('  ④ `adapter-smoke` 不存在 —— **没跑过就不算错**（冒烟是可选步骤）')
    print()

    # ── ⑤★ 摘要里的 final_train_loss 不许是 null ─────────────────
    sm = ADAPTER / 'train-summary.json'
    if sm.exists():
        d = json.loads(sm.read_text(encoding='utf-8'))
        loss = d.get('final_train_loss')
        print(f'  ⑤ train-summary: train_loss={loss} · '
              f'peak_vram={d.get("peak_vram_gb")} GB · '
              f'time={d.get("train_time_s")} s')
        print(f'     可训练 {d.get("trainable_params_M")} M / '
              f'总 {d.get("total_params_m") or d.get("total_params_M")} M')
        ok5 = loss is not None
        print(f'     **`final_train_loss` 不是 null**：{"✅" if ok5 else "❌"}')
        if not ok5:
            fails.append('final_train_loss 是 null —— '
                         '**跑完了却说"没有 loss"，那是记录在说谎**'
                         '（第一版取 log_history[-1]["loss"]，'
                         '而最后一条是汇总记录，只有 train_loss）')
    else:
        print('  ⑤ 没有 train-summary.json（没跑过 ⇒ 跳过这一组）')
    print()

    print('=' * 92)
    if fails:
        print(f'❌ {len(fails)} 项未通过：')
        for f in fails:
            print(f'   · {f}')
        return 1
    print('✅ 五组断言全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
