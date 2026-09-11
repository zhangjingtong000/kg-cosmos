"""精确下载本地验证器模型（只取 PyTorch 权重，跳过 onnx/ 变体）。

背景（实测）：
  · hf-mirror.com 的 resolve 路径重定向回被墙的 huggingface.co ⇒ 镜像失效
  · Babelscape/mdeberta-v3-base-triplet-critic-xnli → ModelScope 404
  · MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli    → ModelScope 404
  · ✅ cross-encoder/nli-deberta-v3-base 可从 ModelScope 下载
  · 但 snapshot_download 会连带下载 5 个 onnx 变体（约 3GB），本项目只需要 PyTorch 权重

用法：
  python tools/fetch_local_models.py
"""
import os

PROXY = "http://127.0.0.1:7897"
os.environ["HTTP_PROXY"] = PROXY
os.environ["HTTPS_PROXY"] = PROXY

from modelscope.hub.file_download import model_file_download  # noqa: E402

MODEL_ID = "cross-encoder/nli-deberta-v3-base"

# 只取 PyTorch 推理必需的 4 个文件
FILES = [
    "config.json",
    "model.safetensors",
    "tokenizer_config.json",
    "tokenizer.json",
    "special_tokens_map.json",
    "vocab.json",
    "merges.txt",
]

print(f"=== 下载 {MODEL_ID}（仅 PyTorch 权重）===\n")
got = []
for f in FILES:
    try:
        p = model_file_download(model_id=MODEL_ID, file_path=f)
        size = os.path.getsize(p)
        print(f"  ✅ {f:28s} {size/1024/1024:8.2f} MB")
        got.append((f, p))
    except Exception as e:
        print(f"  ⚠️  {f:28s} {type(e).__name__}: {str(e)[:70]}")

if got:
    d = os.path.dirname(got[0][1])
    print(f"\n模型目录: {d}")
    total = sum(os.path.getsize(p) for _, p in got)
    print(f"已取 {len(got)}/{len(FILES)} 个文件，共 {total/1024/1024:.1f} MB")
    print("\n提示：transformers 可直接从这个本地目录加载 ——")
    print(f'  AutoModelForSequenceClassification.from_pretrained(r"{d}")')
else:
    print("\n❌ 未取到任何文件")
