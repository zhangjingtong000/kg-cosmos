# -*- coding: utf-8 -*-
"""用本地 Wan2.2 TI2V-5B 生成星云视频（ComfyUI @ 127.0.0.1:8189）。

模型与节点全部复用 D:\\AI\\ComfyUI 那套已经跑通过的环境：
  UnetLoaderGGUF(Wan2.2-TI2V-5B-Q4_K_M.gguf) + CLIPLoader(umt5_xxl_fp8, type=wan)
  + VAELoader(wan2.2_vae) → Wan22ImageToVideoLatent → KSampler → VAEDecode
  → CreateVideo(fps=24) → SaveVideo

`Wan22ImageToVideoLatent` 的 start_image 是 optional —— 不接它就是**纯文生视频**，
所以这里不挂 LoadImage，完全是模型自己生成的连续画面，不是帧拼帧。

用法：
  python tools/gen_nebula_wan.py --prompt "..." --out nebula01 [--width 832] [--height 480]
                                [--length 121] [--steps 8] [--seed 42] [--port 8189]
"""
import argparse
import json
import time
import urllib.request

HOST_TMPL = 'http://127.0.0.1:{port}'

NEG = ('blurry, low quality, worst quality, jpeg artifacts, watermark, text, signature, '
       'static, frozen, deformed, oversaturated, cartoon, illustration')

POS_DEFAULT = ('deep space nebula, volumetric cosmic dust clouds drifting slowly, '
               'teal and warm golden light, astrophotography, cinematic, high detail, '
               'dark background, smooth continuous motion')


def api(host, path, payload=None, timeout=60):
    if payload is not None:
        req = urllib.request.Request(host + path, data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
    else:
        req = urllib.request.Request(host + path)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def build(pos, neg, width, height, length, steps, seed, prefix, fps):
    return {
        '1': {'class_type': 'UnetLoaderGGUF',
              'inputs': {'unet_name': 'Wan2.2-TI2V-5B-Q4_K_M.gguf'}},
        '2': {'class_type': 'CLIPLoader',
              'inputs': {'clip_name': 'umt5_xxl_fp8_e4m3fn_scaled.safetensors',
                         'type': 'wan', 'device': 'default'}},
        '3': {'class_type': 'VAELoader', 'inputs': {'vae_name': 'wan2.2_vae.safetensors'}},
        '4': {'class_type': 'CLIPTextEncode', 'inputs': {'text': pos, 'clip': ['2', 0]}},
        '5': {'class_type': 'CLIPTextEncode', 'inputs': {'text': neg, 'clip': ['2', 0]}},
        '7': {'class_type': 'Wan22ImageToVideoLatent',
              'inputs': {'width': width, 'height': height, 'length': length,
                         'batch_size': 1, 'vae': ['3', 0]}},   # 不接 start_image = 纯文生视频
        '8': {'class_type': 'KSampler',
              'inputs': {'seed': seed, 'steps': steps, 'cfg': 5.0,
                         'sampler_name': 'euler', 'scheduler': 'simple', 'denoise': 1.0,
                         'model': ['1', 0], 'positive': ['4', 0], 'negative': ['5', 0],
                         'latent_image': ['7', 0]}},
        '9': {'class_type': 'VAEDecode', 'inputs': {'samples': ['8', 0], 'vae': ['3', 0]}},
        '10': {'class_type': 'CreateVideo', 'inputs': {'images': ['9', 0], 'fps': fps}},
        '11': {'class_type': 'SaveVideo',
               'inputs': {'video': ['10', 0], 'filename_prefix': prefix,
                          'format': 'auto', 'codec': 'auto'}},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--prompt', default=POS_DEFAULT)
    ap.add_argument('--neg', default=NEG)
    ap.add_argument('--out', default='nebula/kg_nebula')
    ap.add_argument('--width', type=int, default=832)
    ap.add_argument('--height', type=int, default=480)
    ap.add_argument('--length', type=int, default=121, help='帧数；121 = 5.04s @24fps')
    ap.add_argument('--steps', type=int, default=8)
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--fps', type=int, default=24)
    ap.add_argument('--port', type=int, default=8189)
    a = ap.parse_args()

    host = HOST_TMPL.format(port=a.port)
    wf = build(a.prompt, a.neg, a.width, a.height, a.length, a.steps, a.seed, a.out, a.fps)

    print(f'[提交] {a.width}x{a.height} / {a.length} 帧 / {a.steps} 步 / seed={a.seed}')
    r = api(host, '/prompt', {'prompt': wf, 'client_id': 'kg_nebula'})
    pid = r['prompt_id']
    print(f'[已入队] prompt_id={pid}')

    t0 = time.time()
    last = -1
    while True:
        time.sleep(10)
        h = api(host, f'/history/{pid}')
        if pid in h:
            st = h[pid].get('status', {})
            if st.get('completed'):
                print(f'[完成] 用时 {round(time.time() - t0)}s  status={st.get("status_str")}')
                outs = h[pid].get('outputs', {})
                print('[产出]', json.dumps(outs, ensure_ascii=False)[:400])
                break
            if st.get('status_str') == 'error':
                print('[失败]', json.dumps(st, ensure_ascii=False)[:600])
                return 1
        el = round(time.time() - t0)
        if el // 30 != last:
            last = el // 30
            try:
                q = api(host, '/queue')
                print(f'  ... {el}s  running={len(q.get("queue_running", []))} '
                      f'pending={len(q.get("queue_pending", []))}')
            except Exception:
                print(f'  ... {el}s')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
