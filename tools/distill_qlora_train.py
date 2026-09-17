# -*- coding: utf-8 -*-
r"""H5 · 知识蒸馏微调：**QLoRA 微调 4B 学生模型**。

## 这个脚本在整个项目里的位置

TODO 对 H5 的定位：
> 「升为 **M2 第四通道（主推）**」：**云端输出作蒸馏集，QLoRA 微调 4B 学生模型**，
> 实验章升为**四通道对比**

**四通道** = 云端大模型 / 本地 `qwen3.5:9b`（零样本）/ **本模型（蒸馏微调）** /
判别器过滤后的子集。而本项目已有的实测是：
**本地 9B 在结构化抽取上赢了云端大模型**（实体 F1 0.624 vs 0.403）。

**⇒ 蒸馏要回答的是**：**把云端的输出"教"给一个 4B 模型之后，
它能不能超过直接零样本的 9B？** 如果 4B 蒸馏版 ≈ 或 > 9B 零样本，
那就是**"小模型 + 蒸馏"胜过"更大的模型 + 零样本"** —— 一条对部署有实际意义的结论。

## 三个刻意的设计

**① 学的是"格式遵从"，不是"知识"**
`prompt` 里已经把角色、规则、输出格式（`entity<|>Name<|>Type<|>desc`）写全了。
7251 条样本的规模学不到新知识，但**足够把"照格式输出"这件事压进去**。
⇒ 所以这个模型的定位是**抽取器**，不是"更懂知识图谱的模型"。

**② 评测必须用"没见过的论文"**
训练集来自 `data/distill/`（云端在某批语料上的输出），
而项目自己的图来自那 17 篇。**如果拿那 17 篇评测，就是测记忆。**
⇒ 评测要用 `data/gold/` 之外、且**训练集里没出现过**的论文
（具体口径与 `docs/H5_DISTILL_RESULT.md` 一起定）。

**③ 训练日志要能回答"值不值"**
每步记 loss、每轮记耗时与显存峰值 ——
**因为这条道的价值判断是"4B + 蒸馏 vs 9B 零样本"，那是个成本-效果比较**，
没有这些数就只剩一句"我微调了一个模型"。

## 用法

    # 先看数据与配置（不训练）
    python tools/distill_qlora_train.py --dry-run

    # 真训练（RTX 4060 8GB 约 1–2 小时/epoch）
    python tools/distill_qlora_train.py --epochs 2

    # 只跑 50 步，验证管线（约 3 分钟）
    python tools/distill_qlora_train.py --max-steps 50 --out data/distill/adapter-smoke
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLEAN = ROOT / 'data' / 'distill' / 'clean'


def parse_args():
    ap = argparse.ArgumentParser(description='H5 QLoRA 蒸馏微调（4B 学生）')
    ap.add_argument('--base', default=os.environ.get('KG_DISTILL_BASE', 'Qwen/Qwen3-4B'),
                    help='基座模型（默认 Qwen/Qwen3-4B）')
    ap.add_argument('--data', default=str(CLEAN),
                    help='清洗后的 jsonl 目录（tools/clean_distill_data.py --write 的产物）')
    ap.add_argument('--tasks', nargs='+', default=['entities', 'relations', 'combined'],
                    help='用哪几个任务的数据')
    ap.add_argument('--out', default=str(ROOT / 'data' / 'distill' / 'adapter'),
                    help='LoRA adapter 输出目录')
    ap.add_argument('--epochs', type=float, default=2.0)
    ap.add_argument('--max-steps', type=int, default=-1, help='>0 时覆盖 epochs（冒烟用）')
    ap.add_argument('--batch-size', type=int, default=1)
    ap.add_argument('--grad-accum', type=int, default=8)
    ap.add_argument('--lr', type=float, default=2e-4)
    ap.add_argument('--max-len', type=int, default=2048)
    ap.add_argument('--lora-r', type=int, default=16)
    ap.add_argument('--lora-alpha', type=int, default=32)
    ap.add_argument('--val-ratio', type=float, default=0.05)
    ap.add_argument('--seed', type=int, default=20260916)
    ap.add_argument('--dry-run', action='store_true', help='只看数据与配置，不加载模型')
    return ap.parse_args()


ARGS = parse_args()


def load_rows():
    """读清洗后的 jsonl，拼成 SFT 样本 `(prompt, response, task)`。

    **如果没有 `clean/`** ⇒ 提示先跑清洗脚本，**不偷偷用未经清洗的原始数据** ——
    原始数据里有 22.7% 的空 response，用它训练会让模型学会"输出空"，
    而那种失败**训练时不报错**。
    """
    rows = []
    for t in ARGS.tasks:
        p = Path(ARGS.data) / f'{t}.jsonl'
        if not p.exists():
            print(f'  ⚠️ 缺 {p}（先跑：python tools/clean_distill_data.py --write）')
            continue
        n0 = len(rows)
        with p.open(encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                pr, resp = (r.get('prompt') or '').strip(), (r.get('response') or '').strip()
                if pr and resp:
                    rows.append({'prompt': pr, 'response': resp, 'task': t})
        print(f'  {t:10} {len(rows) - n0:>5} 条')
    return rows


def fmt(rec, tok):
    """按 chat 模板拼成一条训练文本。

    ⚠️ **不自己拼 prompt 字符串** —— Qwen3 有自己的模板（含 `<|im_start|>` 等），
    手拼会与推理时的模板不一致，表现为"训练 loss 下降但推理输出乱"。
    用 `apply_chat_template` 保证训练与推理同模板。
    """
    msgs = [{'role': 'user', 'content': rec['prompt']},
            {'role': 'assistant', 'content': rec['response']}]
    return tok.apply_chat_template(msgs, tokenize=False)


def main():
    t0 = time.time()
    print('=' * 84)
    print('H5 · QLoRA 蒸馏微调（4B 学生）')
    print('=' * 84)
    print(f'  基座：{ARGS.base}')
    print(f'  数据：{ARGS.data}')
    print(f'  输出：{ARGS.out}')
    print()
    print('  读数据：')
    rows = load_rows()
    if not rows:
        print()
        print('  ❌ 没有可训练样本。先跑：')
        print('       python tools/clean_distill_data.py --write')
        return 1
    print(f'  ⇒ 合计 {len(rows)} 条')

    import random
    rng = random.Random(ARGS.seed)
    rng.shuffle(rows)
    n_val = max(1, int(len(rows) * ARGS.val_ratio))
    val, train = rows[:n_val], rows[n_val:]
    print(f'  训练 {len(train)} · 验证 {len(val)}')

    import torch
    dev = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f'  设备 {dev}'
          + (f' · {torch.cuda.get_device_name(0)}'
             f' · 显存 {torch.cuda.get_device_properties(0).total_memory / 1024**3:.1f} GB'
             if dev == 'cuda' else '  ⚠️ 没有 CUDA，4B 在 CPU 上跑不动'))
    print()

    if ARGS.dry_run:
        # 不加载模型也要能看"样本长什么样"，否则没法判断格式对不对
        print('  （--dry-run：不加载模型。样本样例：）')
        r = train[0]
        print(f'    task = {r["task"]}')
        print(f'    prompt 前 200 字符：{r["prompt"][:200]!r}')
        print(f'    response 前 200 字符：{r["response"][:200]!r}')
        print(f'  预计 {ARGS.epochs} epoch × {len(train)} 条 / '
              f'（batch {ARGS.batch_size} × accum {ARGS.grad_accum}）'
              f' ≈ {int(len(train) * ARGS.epochs / (ARGS.batch_size * ARGS.grad_accum))} 步')
        return 0

    if dev != 'cuda':
        print('  ⚠️ 没有 CUDA：4B 的 QLoRA 在 CPU 上不可行（会以小时/步计）。中止。')
        return 2

    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from trl import SFTTrainer, SFTConfig
    from datasets import Dataset

    tok = AutoTokenizer.from_pretrained(ARGS.base, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    print('  加载基座（4-bit NF4）…')
    bnb = BitsAndBytesConfig(
        load_in_4bit=True, bnb_4bit_quant_type='nf4',
        bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
    model = AutoModelForCausalLM.from_pretrained(
        ARGS.base, quantization_config=bnb, device_map={'': 0}, trust_remote_code=True)
    model = prepare_model_for_kbit_training(model)
    # **梯度检查点**：8 GB 显存跑 2048 长度的必需项（换时间省显存）
    model.gradient_checkpointing_enable()
    model.config.use_cache = False

    lora = LoraConfig(
        r=ARGS.lora_r, lora_alpha=ARGS.lora_alpha, lora_dropout=0.05,
        bias='none', task_type='CAUSAL_LM',
        # Qwen 系列的注意力/MLP 投影名
        target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj',
                        'gate_proj', 'up_proj', 'down_proj'])
    model = get_peft_model(model, lora)
    n_train_p = sum(p.numel() for p in model.parameters() if p.requires_grad)
    n_all = sum(p.numel() for p in model.parameters())
    print(f'  可训练参数 {n_train_p / 1e6:.1f} M / 总 {n_all / 1e6:.0f} M '
          f'（{n_train_p / n_all * 100:.2f}%）')

    def to_ds(rs):
        return Dataset.from_dict(
            {'text': [fmt(r, tok) for r in rs], 'task': [r['task'] for r in rs]})

    ds_train, ds_val = to_ds(train), to_ds(val)
    print(f'  序列化后：训练 {len(ds_train)} · 验证 {len(ds_val)}')
    print(f'  训练开始（{ARGS.epochs} epoch'
          + (f' / max_steps {ARGS.max_steps}' if ARGS.max_steps > 0 else '') + '）…')

    cfg = SFTConfig(
        output_dir=ARGS.out, num_train_epochs=ARGS.epochs,
        max_steps=ARGS.max_steps,
        per_device_train_batch_size=ARGS.batch_size,
        gradient_accumulation_steps=ARGS.grad_accum,
        learning_rate=ARGS.lr, lr_scheduler_type='cosine', warmup_ratio=0.03,
        logging_steps=1, save_strategy='no', save_total_limit=1,
        bf16=True, max_length=ARGS.max_len,
        dataset_text_field='text', report_to=[],
        gradient_checkpointing=True, seed=ARGS.seed,
        # ⚠️⚠️ **Windows 上必须显式禁用 DataLoader worker**（2026-09-17 实测踩到）：
        #   第一次跑到一半**卡死** —— `CPU 20 秒只涨 0.3 秒`、内存不变、无产物、
        #   92 个线程挂着。那与 Windows 上 `num_workers>0` 的
        #   spawn/信号量问题**表现一致**（子进程起来后与主进程互相等）。
        #   ⇒ 显式设 0（**不是"默认应该就是 0"** —— 那种假设正是这次踩坑的原因）。
        dataloader_num_workers=0,
        # 同理：**不要 pin memory**（它在 Windows 上也会拉一堆线程）
        dataloader_pin_memory=False,
    )
    trainer = SFTTrainer(model=model, args=cfg, train_dataset=ds_train,
                         eval_dataset=ds_val, processing_class=tok,
                         peft_config=None)
    t1 = time.time()
    trainer.train()
    train_time = time.time() - t1

    def _final_loss(tr):
        """从 `log_history` 里取最终训练 loss。

        ⚠️ **不能取 `[-1]`** —— 实测踩过：最后一条是 Trainer 的**汇总记录**
        （`{'train_runtime', 'train_samples_per_second', 'train_steps_per_second',
        'train_loss', 'epoch'}`），**它没有 `loss` 键**（有的是 `train_loss`）。
        于是 `[-1].get('loss')` 恒为 `None` —— 而跑完的日志里明明有 1.5167。
        ⇒ 优先 `train_loss`（汇总里那个），再回退到**最后一个含 `loss` 的**记录。
        """
        hist = getattr(tr.state, 'log_history', None) or []
        for rec in reversed(hist):
            if 'train_loss' in rec:
                return rec['train_loss']
        for rec in reversed(hist):
            if 'loss' in rec:
                return rec['loss']
        return None

    peak = torch.cuda.max_memory_allocated() / 1024 ** 3 if dev == 'cuda' else 0
    Path(ARGS.out).mkdir(parents=True, exist_ok=True)
    trainer.save_model(ARGS.out)
    tok.save_pretrained(ARGS.out)

    summary = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'base': ARGS.base, 'out': str(ARGS.out),
        'n_train': len(train), 'n_val': len(val), 'tasks': ARGS.tasks,
        'config': {'epochs': ARGS.epochs, 'max_steps': ARGS.max_steps,
                   'batch_size': ARGS.batch_size, 'grad_accum': ARGS.grad_accum,
                   'lr': ARGS.lr, 'max_len': ARGS.max_len,
                   'lora_r': ARGS.lora_r, 'lora_alpha': ARGS.lora_alpha,
                   'seed': ARGS.seed},
        'trainable_params_M': round(n_train_p / 1e6, 2),
        'total_params_M': round(n_all / 1e6, 1),
        'train_time_s': round(train_time, 1),
        'peak_vram_gb': round(peak, 2),
        'final_train_loss': _final_loss(trainer),
        'elapsed_s': round(time.time() - t0, 1),
        'next': '评测：与"云端 / 本地 9B 零样本 / 本模型"做四通道对比，'
                '**必须用训练集里没出现过的论文**',
    }
    (Path(ARGS.out) / 'train-summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')

    print()
    print('=' * 84)
    print(f'  训练完成：{train_time / 60:.1f} 分钟 · 显存峰值 {peak:.2f} GB')
    print(f'  adapter → {ARGS.out}')
    print(f'  摘要 → {Path(ARGS.out) / "train-summary.json"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
