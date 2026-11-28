"""用本地 ComfyUI + FLUX.1-schnell 生成星云背景图。

【为什么走这条路】
  前面用 WebGL / Canvas 自己画星云，三次都失败——星云的观感来自连续介质的
  高频细节，代码合成不出来。本机已有 ComfyUI 0.25 + FLUX.1-schnell(fp8)，
  这是**真正的生成模型**，质量远高于手写着色器。

  全程本地、零成本、离线（生成完的图存进 web/assets/，页面不再需要任何外部请求）。

【调用方式】
  ComfyUI 的 HTTP API：
    POST /prompt          提交 workflow，返回 prompt_id
    GET  /history/<id>    轮询取结果
    GET  /view?...        下载图片

【显存约束】
  RTX 4060 Laptop 只有 8 GB。FLUX fp8 权重约 16 GB，靠 ComfyUI 的
  async weight offloading 分块换入。分辨率先取 1280×720，稳了再往上加。
"""
import argparse
import json
import time
import urllib.request
import urllib.parse
from pathlib import Path

HOST = 'http://127.0.0.1:8188'
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
_HERE = Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 都在根目录下一层
OUT = ROOT / 'web' / 'assets'
OUT.mkdir(parents=True, exist_ok=True)


def post(path, obj):
    data = json.dumps(obj).encode('utf-8')
    req = urllib.request.Request(HOST + path, data=data,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def get(path):
    with urllib.request.urlopen(HOST + path, timeout=60) as r:
        return json.load(r)


def build_wf(prompt, neg, w, h, steps, seed, ckpt):
    """FLUX schnell 的最小 txt2img 图：checkpoint → 编码 → 采样 → 解码 → 保存。

    FLUX schnell 的采样参数与 SD 不同：**steps=4、cfg=1.0**，
    采样器用 euler + simple。cfg 高于 1 会出问题（schnell 是蒸馏模型）。
    """
    return {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "CLIPTextEncode",
              "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"text": neg, "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": w, "height": h, "batch_size": 1}},
        "5": {"class_type": "KSampler",
              "inputs": {"seed": seed, "steps": steps, "cfg": 1.0,
                         "sampler_name": "euler", "scheduler": "simple",
                         "denoise": 1.0, "model": ["1", 0],
                         "positive": ["2", 0], "negative": ["3", 0],
                         "latent_image": ["4", 0]}},
        "6": {"class_type": "VAEDecode",
              "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage",
              "inputs": {"filename_prefix": "kg_nebula", "images": ["6", 0]}},
    }


def run(prompt, neg, w, h, steps, seed, ckpt, name):
    wf = build_wf(prompt, neg, w, h, steps, seed, ckpt)
    t0 = time.time()
    res = post('/prompt', {'prompt': wf})
    pid = res.get('prompt_id')
    if not pid:
        print(f'  提交失败: {json.dumps(res, ensure_ascii=False)[:400]}')
        return None
    print(f'  已提交 prompt_id={pid}，等待生成…')

    # 轮询 history（最多 10 分钟）
    while time.time() - t0 < 600:
        time.sleep(3)
        try:
            hist = get(f'/history/{pid}')
        except Exception:
            continue
        if pid in hist:
            entry = hist[pid]
            status = (entry.get('status') or {}).get('status_str', '?')
            if status == 'error':
                msgs = (entry.get('status') or {}).get('messages') or []
                print('  ❌ 生成报错：')
                for m in msgs[-3:]:
                    print('     ' + str(m)[:220])
                return None
            outs = entry.get('outputs') or {}
            for node in outs.values():
                for img in (node.get('images') or []):
                    q = urllib.parse.urlencode({
                        'filename': img['filename'],
                        'subfolder': img.get('subfolder', ''),
                        'type': img.get('type', 'output')})
                    with urllib.request.urlopen(f'{HOST}/view?{q}', timeout=120) as r:
                        data = r.read()
                    fp = OUT / name
                    fp.write_bytes(data)
                    dt = time.time() - t0
                    print(f'  ✅ {fp.name}  {len(data)/1024:.0f} KB  用时 {dt:.1f}s')
                    return fp
    print('  ⏱ 超时（10 分钟）')
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ckpt', default='flux1-schnell-fp8.safetensors')
    ap.add_argument('--w', type=int, default=1280)
    ap.add_argument('--h', type=int, default=720)
    ap.add_argument('--steps', type=int, default=4)
    ap.add_argument('--seed', type=int, default=20260912)
    ap.add_argument('--name', default='nebula-hero.png')
    ap.add_argument('--prompt', default=None)
    args = ap.parse_args()

    POS = args.prompt or (
        "deep space nebula photographed by Hubble, vast volumetric clouds of "
        "interstellar gas and dust, teal cyan and warm gold color palette, "
        "dense field of sharp stars, subtle dark dust lanes, cinematic "
        "astrophotography, ultra detailed, deep black background, "
        "no text, no watermark, no frame"
    )
    NEG = ("text, words, letters, watermark, signature, frame, border, "
           "low quality, blurry, jpeg artifacts, cartoon, illustration")

    print(f'模型: {args.ckpt}')
    print(f'尺寸: {args.w}x{args.h}  steps={args.steps}  seed={args.seed}')
    print(f'输出: {OUT / args.name}')
    print()
    run(POS, NEG, args.w, args.h, args.steps, args.seed, args.ckpt, args.name)


if __name__ == '__main__':
    main()
