# -*- coding: utf-8 -*-
"""I2V 链式生成星云旅程：保证"上一段的末帧 = 下一段的首帧"。

为什么必须这么做：
  各自独立生成的片段之间毫无关系，接起来必然跳变 —— 用户已经明确指出过
  「每个画面的首帧和末帧，上一个画面的末帧与下一个画面的首帧要合并」。
  `Wan22ImageToVideoLatent` 的 `start_image` 正是"作为第一帧"，所以只要把
  上一段的**末帧**喂给下一段，段间就是物理上的同一画面，天然闭合，
  不需要任何溶解或缝合。

链条：
  段1  T2V（不接 start_image）        → 视频1 → 末帧 last1
  段2  I2V(start_image=last1)         → 视频2 → 末帧 last2
  段3  I2V(start_image=last2)         → 视频3 → 末帧 last3
  段4  I2V(start_image=last3)         → 视频4

用法：
  python tools/gen_nebula_chain.py [--steps 20] [--length 121] [--width 832] [--height 480]
"""
import argparse
import json
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

HOST = 'http://127.0.0.1:8189'
COMFY_OUT = Path(r'D:\AI\ComfyUI\output')
COMFY_IN = Path(r'D:\AI\ComfyUI\input')
WORK = Path(r'D:\kg-demo\output\nebula-chain')

NEG = ('blurry, low quality, worst quality, jpeg artifacts, watermark, text, '
       'oversaturated, noise, grainy, deformed, cartoon, illustration, static, frozen')

# 四个分镜：从静谧星空 → 星云逼近 → 穿越云层 → 橙金亮核
SHOTS = [
    dict(name='s1_vast',
         prompt='vast deep space starfield, dozens of bright stars scattered across pitch black '
                'space, a faint teal nebula glowing far in the distance, calm and quiet, '
                'astrophotography, cinematic, ultra detailed'),
    dict(name='s2_approach',
         prompt='slowly flying toward a large glowing teal and golden nebula, billowing gas '
                'clouds growing larger and brighter, camera pushing forward through space, '
                'volumetric light, astrophotography, cinematic'),
    dict(name='s3_through',
         prompt='flying through a luminous cosmic nebula, swirling teal and warm golden gas '
                'clouds surrounding the camera, glowing dust and light rays, bright stars '
                'behind the clouds, cinematic, epic'),
    dict(name='s4_core',
         prompt='a brilliant glowing golden orange stellar core at the heart of a nebula, '
                'radiant bright light, dense star cluster, teal gas clouds around it, '
                'luminous, awe inspiring, cinematic'),
]


def api(path, payload=None, timeout=60):
    if payload is not None:
        req = urllib.request.Request(HOST + path, data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
    else:
        req = urllib.request.Request(HOST + path)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def build(prompt, width, height, length, steps, seed, prefix, start_image=None):
    wf = {
        '1': {'class_type': 'UnetLoaderGGUF',
              'inputs': {'unet_name': 'Wan2.2-TI2V-5B-Q4_K_M.gguf'}},
        '2': {'class_type': 'CLIPLoader',
              'inputs': {'clip_name': 'umt5_xxl_fp8_e4m3fn_scaled.safetensors',
                         'type': 'wan', 'device': 'default'}},
        '3': {'class_type': 'VAELoader', 'inputs': {'vae_name': 'wan2.2_vae.safetensors'}},
        '4': {'class_type': 'CLIPTextEncode', 'inputs': {'text': prompt, 'clip': ['2', 0]}},
        '5': {'class_type': 'CLIPTextEncode', 'inputs': {'text': NEG, 'clip': ['2', 0]}},
        '8': {'class_type': 'KSampler',
              'inputs': {'seed': seed, 'steps': steps, 'cfg': 5.0,
                         'sampler_name': 'euler', 'scheduler': 'simple', 'denoise': 1.0,
                         'model': ['1', 0], 'positive': ['4', 0], 'negative': ['5', 0],
                         'latent_image': ['7', 0]}},
        '9': {'class_type': 'VAEDecode', 'inputs': {'samples': ['8', 0], 'vae': ['3', 0]}},
        '10': {'class_type': 'CreateVideo', 'inputs': {'images': ['9', 0], 'fps': 24}},
        '11': {'class_type': 'SaveVideo',
               'inputs': {'video': ['10', 0], 'filename_prefix': prefix,
                          'format': 'auto', 'codec': 'auto'}},
    }
    lat = {'width': width, 'height': height, 'length': length,
           'batch_size': 1, 'vae': ['3', 0]}
    if start_image:
        # 关键：接上 start_image，这一段就从上一段的末帧开始演化
        wf['6'] = {'class_type': 'LoadImage', 'inputs': {'image': start_image}}
        lat['start_image'] = ['6', 0]
    wf['7'] = {'class_type': 'Wan22ImageToVideoLatent', 'inputs': lat}
    return wf


def last_frame(video_path, out_png):
    """提取视频最后一帧。"""
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-sseof', '-0.05',
                    '-i', str(video_path), '-vframes', '1', str(out_png)], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--width', type=int, default=832)
    ap.add_argument('--height', type=int, default=480)
    ap.add_argument('--length', type=int, default=121)
    ap.add_argument('--steps', type=int, default=20)
    ap.add_argument('--seed', type=int, default=900)
    a = ap.parse_args()

    WORK.mkdir(parents=True, exist_ok=True)
    prev_last = None

    for i, shot in enumerate(SHOTS):
        tag = f'{i + 1}'
        prefix = f'chain/{shot["name"]}'
        print(f'\n===== 分镜 {tag}/{len(SHOTS)}  {shot["name"]}  '
              f'{"[I2V 接上一段末帧]" if prev_last else "[T2V 起点]"} =====', flush=True)

        wf = build(shot['prompt'], a.width, a.height, a.length, a.steps,
                   a.seed + i * 7, prefix, start_image=prev_last)
        r = api('/prompt', {'prompt': wf, 'client_id': f'chain{i}'})
        pid = r['prompt_id']
        t0 = time.time()
        while True:
            time.sleep(15)
            h = api('/history/' + pid)
            if pid in h:
                st = h[pid].get('status', {})
                if st.get('completed'):
                    print(f'  完成 {round(time.time() - t0)}s', flush=True)
                    break
                if st.get('status_str') == 'error':
                    print('  失败:', json.dumps(st, ensure_ascii=False)[:500], flush=True)
                    return 1
            el = round(time.time() - t0)
            if el % 60 < 15:
                print(f'  ... {el}s', flush=True)

        # 找到产出文件
        outs = h[pid].get('outputs', {})
        fn = None
        for node in outs.values():
            for im in (node.get('images') or []):
                fn = im
        if not fn:
            print('  找不到产出文件', flush=True)
            return 1
        src = COMFY_OUT / (fn.get('subfolder') or '') / fn['filename']
        dst = WORK / f'{tag}_{shot["name"]}.mp4'
        shutil.copy2(src, dst)
        print(f'  保存 -> {dst}', flush=True)

        # 提取末帧，作为下一段的 start_image
        last_png = WORK / f'last{i + 1}.png'
        last_frame(dst, last_png)
        # 复制进 ComfyUI 的 input 目录（LoadImage 只能读那里）
        prev_last = f'chain_last{i + 1}.png'
        shutil.copy2(last_png, COMFY_IN / prev_last)
        print(f'  末帧 -> {last_png}  (已注册为 {prev_last})', flush=True)

    print('\nCHAIN_DONE', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
