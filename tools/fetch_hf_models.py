"""下载首选判别模型 + 其官方校准数据（经本地代理直连 HuggingFace）。

背景修正（重要，推翻了两条前期错误结论）：
  ❌ 错误结论 1：「huggingface.co 经本地代理不可用」
     真实原因：此前测试时 requests 未走代理，且 HF_ENDPOINT 被指到了失效的 hf-mirror。
     实测：requests + proxies(127.0.0.1:7897) 请求 huggingface.co 返回 200，
           且能真正下载权重（64KB 流式测试通过）。
  ❌ 错误结论 2：「Triplet Critic 在 ModelScope 404 ⇒ 不可得」
     真实原因：找错了源。HF 上它一直存在（pipeline=text-classification, 17 个文件）。

为什么它比当前用的 cross-encoder/nli-deberta-v3-base 更合适：
  · 它是**专为「三元组是否被句子支持」训练**的判别器（RED^FM, ACL 2023），
    与我们的任务定义完全一致，而 cross-encoder 是通用 NLI。
  · 仓库自带 **Triplet-Critic-Data/{ar,de,en,es,fr,it,zh}_triplet_entailment.tsv**，
    即 (context, triplet, label) 格式的**带标签校准集**——可以用它算出真实准确率，
    而不是"看样本觉得合理"。

⚠️ 阈值存在文献矛盾，必须用校准数据自行确定：
  · 模型卡原文：「we use the positive score (ie. Label_1) as the confidence score for a
    triplet. For SRED^FM the confidence score thresshold was set at 0.75.」
  · 但 RED^FM 论文附录 A 原文：「If the score was less than 0.75 for the entailment class,
    it was removed to ensure higher precision. In our work, we set a lower threshold, 0.1」
  ⇒ 0.75 属于 cRocoDiLe 的 NLI 过滤阶段；RED^FM 自己用 0.1。**不能照抄，必须校准。**

用法：
  python tools/fetch_hf_models.py
"""
import os

PROXY = "http://127.0.0.1:7897"
os.environ["HTTP_PROXY"] = PROXY
os.environ["HTTPS_PROXY"] = PROXY
os.environ["http_proxy"] = PROXY
os.environ["https_proxy"] = PROXY
os.environ.pop("HF_ENDPOINT", None)  # 默认端点即可（hf-mirror 已失效，别指过去）

from huggingface_hub import hf_hub_download, list_repo_files  # noqa: E402

REPO = "Babelscape/mdeberta-v3-base-triplet-critic-xnli"

# 只要 PyTorch 权重（不要 pytorch_model.bin，避免重复下载 ~560MB）
WANTED = [
    "config.json",
    "model.safetensors",
    "tokenizer_config.json",
    "tokenizer.json",
    "special_tokens_map.json",
    "added_tokens.json",
    "spm.model",
    "README.md",
    "Triplet-Critic-Data/en_triplet_entailment.tsv",
    "Triplet-Critic-Data/zh_triplet_entailment.tsv",
]

print(f"=== 下载 {REPO}（经代理直连 HuggingFace）===\n")
try:
    allf = list_repo_files(REPO)
    print(f"仓库共 {len(allf)} 个文件")
except Exception as e:
    print(f"列文件失败: {type(e).__name__}: {str(e)[:120]}")
    allf = WANTED

got = {}
for f in WANTED:
    if allf and f not in allf:
        print(f"  ⚠️  跳过（仓库中无此文件）: {f}")
        continue
    try:
        p = hf_hub_download(repo_id=REPO, filename=f)
        print(f"  ✅ {f:50s} {os.path.getsize(p)/1024/1024:8.2f} MB")
        got[f] = p
    except Exception as e:
        print(f"  ❌ {f:50s} {type(e).__name__}: {str(e)[:90]}")

if got:
    base = os.path.dirname(got["config.json"])
    total = sum(os.path.getsize(p) for p in got.values())
    print(f"\n模型目录: {base}")
    print(f"共 {len(got)}/{len(WANTED)} 个文件，{total/1024/1024:.1f} MB")
    # 校准数据预览
    tsv = got.get("Triplet-Critic-Data/en_triplet_entailment.tsv")
    if tsv:
        print(f"\n── 校准数据预览（{os.path.basename(tsv)}）──")
        with open(tsv, encoding="utf-8") as fh:
            lines = fh.readlines()
        print(f"  总行数 {len(lines)}")
        for ln in lines[:4]:
            print(f"    {ln.rstrip()[:180]}")
        from collections import Counter
        labels = Counter(ln.split("\t")[2].strip() for ln in lines[1:] if ln.count("\t") >= 2)
        print(f"  标签分布: {dict(labels)}")
