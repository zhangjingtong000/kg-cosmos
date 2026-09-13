"""生成一条**连续、可无缝循环**的星云演化链，并合成导航页背景视频。

【为什么不用原来那 5 张图】
  web/assets/sky-{0..4}.png 是 5 次**互相独立**的 txt2img：每张各自构图、
  各自配色。它们不是一条演化链，而是 5 张矛盾的宇宙风景 ——
  sky-2 全屏橙金、sky-3 爆亮环核、sky-4 又变成中景盘状星系，
  于是交叉淡化时必然看到"突变"，而且首尾帧构图完全不同，**闭不了环**。

【这一版的思路：img2img 逐帧演化 + 首尾溶接】
  · 第 0 帧用 txt2img 生成"静"（极暗深空 + 细密星点 + 淡银河痕）；
  · 之后每一帧以**上一帧**为 LoadImage 的 init image，denoise ~0.55
    继续采样。低 denoise 意味着模型只能"改写"上一帧，不能推翻它 ——
    相邻帧天然连贯，色调与结构的漂移是一小步一小步累积的；
  · prompt 沿叙事推移（静 → 冷青远处浮现 → 逼近 → 穿过 → 明亮核心
    → 扩散稀薄 → 归于暗），但**词根固定**（深空/astrophotography/
    星点/尘埃/深黑背景），保证各帧是"同一个地方的不同时刻"；
  · 闭环：视频最后一段不是硬切回第 0 帧，而是把**末帧 → 第 0 帧**
    做成一段连续的交叉溶解（xfade 链的最后一次转换天然就是它）。
    并且第 0 帧在时间轴上前后共出现两次、用**同一条 Ken Burns 曲线**，
    所以视频末帧与首帧在数学上逐像素相同 —— 真正闭合。

【关键帧数量】
  默认 6 帧（--frames 6，t = 0.00/0.28/0.50/0.68/0.88/1.00）。
  这是"先拿到能用的 mp4"的最小可信链路；--frames 10 会用完整叙事表
  （t = 0.00/0.15/0.35/0.47/0.55/0.70/0.80/0.90/0.96/1.00）重跑，
  但每帧约 80 秒，10 帧约 14 分钟。叙事弧线（静→深入→亮核→归于暗→闭环）
  两者完全一致，只是采样密度不同。

【为什么 4 steps 改成 8 steps】
  KSampler 的 denoise=d 等价于"从第 (1-d) 个时间步开始采样"，
  真正跑的步数是 steps*d。FLUX-schnell 是蒸馏模型，d=0.55 时
  steps=4 只剩约 2.2 步，画面会糊、会碎；steps=8 约 4.4 步，稳定。
  代价是每帧约 75~85 秒（4 steps 约 63 秒）。

【显存】
  RTX 4060 Laptop 8 GB。FLUX fp8 权重 16 GB，靠 ComfyUI 的权重换入
  分块加载，所以必须**串行**提交，不要并发。

【产出】
  web/assets/voyage-chain/frame-00..NN.png  关键帧（也是复现的输入）
  web/assets/voyage.mp4                     背景视频（H.264/yuv420p/小 GOP）
  web/assets/voyage-poster.webp             首帧海报（poster + reduced-motion 回退）
  tools/_probe/voyage/contact-sheet.png     拼接总览（肉眼验收连贯性用）

【用法】
  python tools/gen_voyage_chain.py --help
  python tools/gen_voyage_chain.py                     # 全流程（6 帧）
  python tools/gen_voyage_chain.py --frames 10         # 完整 10 帧叙事
  python tools/gen_voyage_chain.py --only 3            # 只重做第 3 帧
  python tools/gen_voyage_chain.py --skip-gen          # 只重跑合成
  python tools/gen_voyage_chain.py --contact-sheet-only # 只重拼 contact sheet
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

# ── 路径：本文件在 <根>/tools/ 下，根目录由 __file__ 反推，不硬编码 ──────────
_HERE = Path(__file__).resolve()
ROOT = _HERE.parent.parent

ASSETS = ROOT / 'web' / 'assets'
CHAIN = ASSETS / 'voyage-chain'               # 关键帧存档目录
WORK = ROOT / 'tools' / '_probe' / 'voyage'   # 中间产物（contact sheet / 抽帧）
VIDEO = ASSETS / 'voyage.mp4'
POSTER = ASSETS / 'voyage-poster.webp'

COMFY = 'http://127.0.0.1:8188'
COMFY_INPUT = Path(r'E:\comfyui\input')       # 用于 --direct-input（跳过 /upload/image）

CKPT = 'flux1-schnell-fp8.safetensors'


def log(msg: str = '') -> None:
    """实时落盘 + 实时刷屏。

    之前吃过亏：脚本的输出被管道缓冲，进程被掐掉后日志全丢，
    完全看不出卡在哪一步。所以这里每行都 flush，并同步追加到日志文件。
    """
    print(msg, flush=True)
    try:
        WORK.mkdir(parents=True, exist_ok=True)
        with open(WORK / 'gen.log', 'a', encoding='utf-8') as f:
            f.write(msg + '\n')
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# 一、Prompt 设计
# ═══════════════════════════════════════════════════════════════════════════

# 词根：所有帧都带同一套"这个世界是什么样"的描述。
# 这样阶段词怎么变，材质、星点、噪点、色调倾向都是同一个世界。
ROOT_WORDS = (
    "deep space starfield, photographic astrophotography, dark background, "
    "fine sharp star points, subtle interstellar dust and faint dust lanes, "
    "cinematic, ultra detailed, subtle film grain, "
    "no text, no watermark, no frame, no border, no signature"
)

# 负面词全程一致（FLUX-schnell 的 cfg=1，负面词影响有限，但保持一致无害）
NEG = (
    "text, words, letters, watermark, signature, frame, border, "
    "low quality, blurry, jpeg artifacts, cartoon, illustration, "
    "painting, oversaturated, harsh contrast"
)

# 完整叙事表（10 帧版）：t 是滚动进度锚点，denoise 是 img2img 强度。
#   第 0 帧 txt2img（denoise 1.0）；
#   其余帧以**上一帧**为 init，denoise 0.55 —— 单帧变化小、累积变化大。
#   denoise 调高 → 变化快但容易断层；调低 → 更稳但旅程感弱。
FRAMES_FULL = [
    dict(
        t=0.00, denoise=None, tag='静：极暗深空',
        prompt=(
            "almost empty deep space, an extremely dim and empty starfield, "
            "fine scattered pinprick stars, a very faint pale milky way band, "
            "the frame is nearly black with a lot of empty darkness, "
            "no nebula visible yet, very dim and dark, calm and silent"),
    ),
    dict(
        t=0.15, denoise=0.62, tag='远处浮现冷青星云（很小、偏一侧）',
        prompt=(
            "a tiny very distant faint teal cyan nebula appears far away in the "
            "upper right of the frame, small and dim, surrounded by a vast field "
            "of stars, the rest of the frame still dark and empty, cold blue "
            "teal tone, glowing softly"),
    ),
    dict(
        t=0.35, denoise=0.68, tag='逼近：冷青星云占一部分画面，有体积感',
        prompt=(
            "a large teal cyan nebula clearly visible in the upper right and "
            "middle of the frame, occupying about a third of the picture, "
            "towers of cold blue cyan interstellar gas with real volume and "
            "depth, darker dust lanes inside it, dense stars around it, "
            "the rest of the frame still mostly dark empty space"),
    ),
    dict(
        t=0.47, denoise=0.74, tag='更近：云墙充满画面下半',
        prompt=(
            "moving much closer to the teal cyan nebula, the glowing gas clouds "
            "now fill most of the lower and middle part of the frame, detailed "
            "wispy filaments and dark dust structures, a first faint hint of "
            "warmth deep inside the cloud, cyan still dominant"),
    ),
    dict(
        t=0.55, denoise=0.76, tag='穿过云层：青绿中透出暖金',
        prompt=(
            "passing through the nebula clouds, teal cyan gas surrounds the "
            "viewer and warm golden light now shines through from behind the "
            "clouds, cyan and gold mixed together, dust rims catching the warm "
            "light, layered depth, immersive"),
    ),
    dict(
        t=0.70, denoise=0.82, tag='明亮核心：橙金发光（亮但不爆）',
        prompt=(
            "a bright glowing golden orange core of the nebula directly ahead, "
            "luminous warm amber light with teal cyan cloud edges around it, "
            "dust filaments silhouetted against the glow, bright but still "
            "readable detail, not blown out, holds shape"),
    ),
    dict(
        t=0.80, denoise=0.78, tag='核心开始扩散、变稀薄',
        prompt=(
            "the golden core is expanding and thinning out, its light softening "
            "and spreading, the clouds becoming more transparent and hazy, "
            "brightness falling, warm amber fading back toward teal"),
    ),
    dict(
        t=0.90, denoise=0.74, tag='亮度回落，边缘化开成光尘',
        prompt=(
            "the nebula has dispersed into thin fading veils of dust and cold "
            "teal gas, dim scattered glow, no bright core left, mostly dark "
            "space with soft remains of cloud, light dust and faint stars"),
    ),
    dict(
        t=0.96, denoise=0.62, tag='归于暗：只剩稀薄星尘',
        prompt=(
            "a calm almost empty stretch of deep space, only extremely faint "
            "and thin traces of dust left over, dim scattered stars, very dark "
            "and quiet, the nebula is gone into the distance"),
    ),
    dict(
        t=1.00, denoise=0.50, tag='回静：接近第 0 帧',
        prompt=(
            "an extremely faint almost invisible wisp of cold dust high in the "
            "frame slowly disappearing into the distance, vast dark empty "
            "starfield, fine pinprick stars, a very faint pale milky way band, "
            "the frame is nearly black and empty, calm and silent"),
    ),
]

# 6 帧版：同样从"静"出发、深入、到亮核、再归于暗——只是采样更稀。
# 叙事锚点与 10 帧版一致：0.00 / 0.35 / 0.55 / 0.70 / 0.90 / 1.00。
_FULL_BY_T = {f['t']: f for f in FRAMES_FULL}

FRAMES_6 = [
    _FULL_BY_T[0.00],          # t=0.00 静：极暗深空 —— txt2img
    _FULL_BY_T[0.35],          # t=0.35 逼近：冷青星云占一部分画面
    _FULL_BY_T[0.55],          # t=0.55 穿过云层：青绿透出暖金
    _FULL_BY_T[0.70],          # t=0.70 明亮核心：橙金发光
    _FULL_BY_T[0.90],          # t=0.90 亮度回落：化开成光尘
    _FULL_BY_T[1.00],          # t=1.00 回静：接近第 0 帧，闭环
]

# 6 帧版的 denoise：起步/收尾低，中间高（理由见上）
DENOISE_6 = [None, 0.68, 0.76, 0.82, 0.78, 0.55]


def pick_frames(n: int) -> list:
    """返回 (frames, denoise 表)。denoise=None 表示该帧走 txt2img。"""
    if n == 10:
        return FRAMES_FULL, [f['denoise'] for f in FRAMES_FULL]
    if n == 6:
        # 6 帧版复用完整叙事表的 prompt/tag，只把 denoise 换成实测标定的一组
        frames = [dict(f, denoise=DENOISE_6[i]) for i, f in enumerate(FRAMES_6)]
        return frames, DENOISE_6
    raise SystemExit(f'--frames 只支持 6 或 10（收到 {n}）')


# ═══════════════════════════════════════════════════════════════════════════
# 二、ComfyUI HTTP 客户端（照 gen_nebula.py 的调用方式）
# ═══════════════════════════════════════════════════════════════════════════

# 显式绕开系统代理：只走 127.0.0.1，装个 proxy opener 少一层不确定性。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _urlopen(req, timeout):
    return _OPENER.open(req, timeout=timeout)


def _retry(fn, tries: int, what: str):
    """带退避的重试。ComfyUI 在换入权重时会短暂拒绝连接，重试比失败好。"""
    last = None
    for k in range(tries):
        try:
            return fn()
        except Exception as e:      # noqa: BLE001
            last = e
            log(f'    · {what} 第 {k+1}/{tries} 次失败: {type(e).__name__}: {e}')
            time.sleep(2 + 2 * k)
    raise last


def _post_json(path: str, obj) -> dict:
    data = json.dumps(obj).encode('utf-8')
    req = urllib.request.Request(COMFY + path, data=data,
                                 headers={'Content-Type': 'application/json'})
    with _urlopen(req, 120) as r:
        return json.load(r)


def _get_json(path: str) -> dict:
    with _urlopen(COMFY + path, 120) as r:
        return json.load(r)


def server_alive() -> bool:
    try:
        _get_json('/system_stats')
        return True
    except Exception:
        return False


def vram_report() -> str:
    """取一次显存读数，写进日志（排查 OOM 用）。"""
    try:
        d = _retry(lambda: _get_json('/system_stats'), 3, 'system_stats')
        dev = (d.get('devices') or [{}])[0]
        free = dev.get('vram_free', 0) / 1024**3
        total = dev.get('vram_total', 0) / 1024**3
        ram = d.get('system', {}).get('ram_free', 0) / 1024**3
        return f'显存 {free:.2f}/{total:.2f} GB 可用，内存 {ram:.2f} GB 可用'
    except Exception as e:          # noqa: BLE001
        return f'显存读数失败: {e}'


def upload_image(path: Path, remote_name: str) -> str:
    """把 init 图传给 ComfyUI 的 input 目录，返回 LoadImage 要用的文件名。

    手写 multipart/form-data：overwrite=1 必须显式带上，否则同名文件会被
    ComfyUI 改名成 "xxx (1).png"，LoadImage 就找不到我们要的那张。
    """
    boundary = '----kgcosmos' + uuid.uuid4().hex
    body = bytearray()

    def field(name, value):
        body.extend(f'--{boundary}\r\n'.encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(str(value).encode('utf-8'))
        body.extend(b'\r\n')

    field('type', 'input')
    field('subfolder', '')
    field('overwrite', '1')
    body.extend(f'--{boundary}\r\n'.encode())
    body.extend(
        f'Content-Disposition: form-data; name="image"; filename="{remote_name}"\r\n'
        f'Content-Type: image/png\r\n\r\n'.encode())
    body.extend(path.read_bytes())
    body.extend(b'\r\n')
    body.extend(f'--{boundary}--\r\n'.encode())

    def go():
        req = urllib.request.Request(
            COMFY + '/upload/image', data=bytes(body),
            headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
        with _urlopen(req, 300) as r:
            return json.load(r)

    return _retry(go, 3, 'upload/image')['name']


# ═══════════════════════════════════════════════════════════════════════════
# 三、工作流组装
# ═══════════════════════════════════════════════════════════════════════════

def wf_txt2img(prompt, neg, w, h, steps, seed, ckpt, prefix):
    """第 0 帧：标准 FLUX-schnell txt2img（steps=8，denoise=1.0）。"""
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
              "inputs": {"filename_prefix": prefix, "images": ["6", 0]}},
    }


def wf_img2img(prompt, neg, w, h, steps, seed, ckpt, prefix, init_name, denoise):
    """第 1..N 帧：以上一帧为 init 的 img2img。

    LoadImage → ImageScale（统一到目标尺寸，避免尺寸漂移累积）
             → VAEEncode → KSampler(denoise<1) → VAEDecode → SaveImage
    """
    return {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "CLIPTextEncode",
              "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"text": neg, "clip": ["1", 1]}},
        "8": {"class_type": "LoadImage",
              "inputs": {"image": init_name}},
        "9": {"class_type": "ImageScale",
              "inputs": {"image": ["8", 0], "upscale_method": "lanczos",
                         "width": w, "height": h, "crop": "disabled"}},
        "10": {"class_type": "VAEEncode",
               "inputs": {"pixels": ["9", 0], "vae": ["1", 2]}},
        "5": {"class_type": "KSampler",
              "inputs": {"seed": seed, "steps": steps, "cfg": 1.0,
                         "sampler_name": "euler", "scheduler": "simple",
                         "denoise": denoise, "model": ["1", 0],
                         "positive": ["2", 0], "negative": ["3", 0],
                         "latent_image": ["10", 0]}},
        "6": {"class_type": "VAEDecode",
              "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage",
              "inputs": {"filename_prefix": prefix, "images": ["6", 0]}},
    }


def wait_and_fetch(pid: str, dest: Path, timeout_s: float, prefix: str = ''):
    """轮询 /history/<pid>，成功后用 /view 下载第一张图到 dest。

    必须处理的两种"假成功"：
      · outputs 为空 —— 整个节点图命中 ComfyUI 缓存（execution_cached），
        SaveImage 压根没执行，所以永远等不到图。靠 filename_prefix 唯一化
        从根上避免；这里再兜一层，见到就报错退出而不是干等。
    """
    t0 = time.time()
    tick = 0
    while time.time() - t0 < timeout_s:
        time.sleep(3)
        tick += 1
        try:
            hist = _get_json(f'/history/{pid}')
        except Exception as e:      # noqa: BLE001
            log(f'    · history 查询失败（继续等）: {type(e).__name__}: {e}')
            continue
        if pid not in hist:
            if tick % 10 == 0:
                log(f'    · 等待中… {time.time()-t0:.0f}s')
            continue
        entry = hist[pid]
        status = (entry.get('status') or {}).get('status_str', '?')
        if status == 'error':
            log('  ❌ 生成报错：')
            for m in ((entry.get('status') or {}).get('messages') or [])[-4:]:
                log('     ' + str(m)[:300])
            return None

        outs = entry.get('outputs') or {}
        images = [img for node in outs.values() for img in (node.get('images') or [])]
        if not images:
            cached = [m for m in ((entry.get('status') or {}).get('messages') or [])
                      if m and m[0] == 'execution_cached']
            if status == 'success':
                log('  ❌ 提示词"成功"但没有任何输出图 —— 节点图命中缓存。')
                log(f'     execution_cached 节点: {cached[-1][1].get("nodes") if cached else "?"}')
                log('     原因是 SaveImage 的 filename_prefix 与上次完全相同；'
                    '本脚本已用唯一前缀规避。')
                return None
            continue

        img = images[0]
        q = urllib.parse.urlencode({
            'filename': img['filename'],
            'subfolder': img.get('subfolder', ''),
            'type': img.get('type', 'output')})

        def go():
            with _urlopen(f'{COMFY}/view?{q}', 300) as r:
                return r.read()

        data = _retry(go, 3, '/view')
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        dt = time.time() - t0
        log(f'  ✅ {dest.name}  {len(data)/1024:.0f} KB  用时 {dt:.1f}s  源={img["filename"]}')
        return dest
    log(f'  ⏱ 超时（{timeout_s:.0f}s）')
    return None


# ═══════════════════════════════════════════════════════════════════════════
# 四、生成演化链
# ═══════════════════════════════════════════════════════════════════════════

def frame_name(i: int) -> str:
    return f'frame-{i:02d}.png'


def gen_chain(args, frames) -> bool:
    for i, f in enumerate(frames):
        dest = CHAIN / frame_name(i)
        if args.only is not None and i != args.only:
            continue
        if dest.exists() and not args.force:
            log(f'[frame-{i:02d}] 已存在，跳过（--force 可重做）')
            continue
        # frame N 依赖 frame N-1：上游一变，下游必须跟着重做，否则链就断了
        if i > 0 and not (CHAIN / frame_name(i - 1)).exists():
            log(f'[frame-{i:02d}] ❌ 需要上一帧 {frame_name(i-1)} 作为 init，但它不存在')
            return False

        # 唯一化 filename_prefix —— **必须**。
        # ComfyUI 按"节点 inputs 是否变化"决定是否复用缓存。如果前缀与上一次
        # 完全相同（比如中途失败后原样重跑），整张图会命中 execution_cached，
        # SaveImage 不执行、outputs 为空、history 里却写着 success，
        # 于是轮询永远拿不到图。加一个时间戳后缀即可根治。
        prefix = f'kg_voyage_f{i:02d}_{time.strftime("%H%M%S")}'
        dn = 1.0 if f['denoise'] is None else f['denoise']
        log(f'[frame-{i:02d}] t={f["t"]:.2f}  {f["tag"]}')
        log(f'  denoise={dn}  steps={args.steps}  seed={args.seed}  {vram_report()}')

        if i == 0:
            wf = wf_txt2img(f['prompt'], NEG, args.w, args.h, args.steps,
                            args.seed, args.ckpt, prefix)
        else:
            prev = CHAIN / frame_name(i - 1)
            if args.direct_input:
                COMFY_INPUT.mkdir(parents=True, exist_ok=True)
                init_name = f'kg_voyage_init_{i:02d}.png'
                shutil.copyfile(prev, COMFY_INPUT / init_name)
            else:
                init_name = upload_image(prev, f'kg_voyage_init_{i:02d}.png')
            wf = wf_img2img(f['prompt'], NEG, args.w, args.h, args.steps,
                            args.seed, args.ckpt, prefix,
                            init_name, f['denoise'])

        # 只有第 0 帧用 txt2img（denoise=1.0），其余 denoise 由表里给
        if i == 0 and f['denoise'] is not None:
            wf['5']['inputs']['denoise'] = f['denoise']

        try:
            res = _retry(lambda: _post_json('/prompt', {'prompt': wf}), 3, '/prompt')
        except urllib.error.HTTPError as e:
            log(f'  ❌ 提交失败 HTTP {e.code}: {e.read()[:400]!r}')
            return False
        pid = (res or {}).get('prompt_id')
        if not pid:
            log(f'  ❌ 提交失败：没有 prompt_id，返回 {str(res)[:300]}')
            return False
        log(f'  · 已提交 prompt_id={pid}，等待生成…')

        if wait_and_fetch(pid, dest, args.timeout, prefix) is None:
            return False
        log()
    return True


# ═══════════════════════════════════════════════════════════════════════════
# 五、ffmpeg 合成
# ═══════════════════════════════════════════════════════════════════════════

def _pick_font() -> str:
    """contact sheet 上要标 t 值，准备一个可用的字体，返回 drawtext 的 fontfile 参数。

    这里踩过两次坑，记下来：
      1. `font=Consolas`（按名字选）会因为本机没有 fontconfig 配置而让
         ffmpeg 直接崩掉，不能用；
      2. `fontfile=C\\:/Windows/...` 的转义层数取决于滤镜串怎么传给 ffmpeg。
         滤镜串走文件时，ffmpeg 对 fontfile 的反斜杠转义处理会让
         `C\\:` 被吃成 `C:`，盘符冒号没转义 → 路径被截断成 `/Windows/...`
         → 整条 filterchain 解析失败。
    所以最稳的做法是**绕开转义**：把字体拷到工作目录，用不带盘符的
    相对文件名。ffmpeg 的 cwd 设成工作目录即可。
    """
    WORK.mkdir(parents=True, exist_ok=True)
    for cand in (r'C:\Windows\Fonts\consola.ttf', r'C:\Windows\Fonts\arial.ttf',
                 r'C:\Windows\Fonts\segoeui.ttf'):
        src = Path(cand)
        if src.exists():
            dst = WORK / 'label.ttf'
            try:
                if not dst.exists() or dst.stat().st_size != src.stat().st_size:
                    shutil.copyfile(src, dst)
            except Exception:       # noqa: BLE001
                continue
            return ':fontfile=label.ttf'
    return ''


_FONT = _pick_font()


def run_ff(args, label: str) -> int:
    """跑 ffmpeg，stdout/stderr **写文件**而不是走管道。

    踩过的坑：harness 的 pwsh 任务本身跑在管道 stdio 上，Python 再用
    capture_output=True 开孙子进程（管道里套管道）会被环境拒掉，
    子进程立刻失败而且 stderr 抓不到 —— 表面看就是"卡住不动"。
    写文件既绕开管道，又保证日志一定落到磁盘上。
    """
    WORK.mkdir(parents=True, exist_ok=True)
    lf = WORK / '_ff.log'
    print(f'$ ffmpeg {" ".join(args)}', flush=True)
    with open(lf, 'wb') as fh:
        r = subprocess.run(['ffmpeg', '-hide_banner', '-y', *args],
                           stdout=fh, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, cwd=str(WORK))
    if r.returncode != 0:
        log(f'  ❌ {label} 失败（exit={r.returncode}），ffmpeg 日志尾部：')
        log(lf.read_text(encoding='utf-8', errors='replace')[-2000:])
    return r.returncode


def run_ff_graph(inp: list, graph: str, tail: list, label: str) -> int:
    """把 filter_complex 写进临时文件再交给 ffmpeg（`-/filter_complex <file>`）。

    为什么不用命令行直接传：滤镜串里的方括号、引号、反斜杠在
    PowerShell 5.1 下会被反复解释，Windows 路径的盘符冒号更是重灾区。
    走文件只剩 ffmpeg 自己一层转义，可复现得多。
    """
    WORK.mkdir(parents=True, exist_ok=True)
    gf = WORK / '_filter.txt'
    gf.write_text(graph, encoding='utf-8')
    args = [*inp, '-/filter_complex', str(gf), *tail]
    print(f'$ ffmpeg {" ".join(inp)} -/filter_complex {gf.name} {" ".join(tail)}',
          flush=True)
    lf = WORK / '_ff.log'
    with open(lf, 'wb') as fh:
        # cwd 设为 WORK：drawtext 用相对文件名引用字体（见 _pick_font）
        r = subprocess.run(['ffmpeg', '-hide_banner', '-y', *args],
                           stdout=fh, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, cwd=str(WORK))
    if r.returncode != 0:
        log(f'  ❌ {label} 失败（exit={r.returncode}），ffmpeg 日志尾部：')
        log(lf.read_text(encoding='utf-8', errors='replace')[-2500:])
        log('  滤镜串：' + graph[:800])
    return r.returncode


def make_contact_sheet(frames) -> Path:
    """把所有关键帧缩到 512 宽、按行拼成一张 contact sheet，用来肉眼验收连贯性。

    顺序即叙事顺序（左→右、上→下），每张左上角标 t 值。
    """
    WORK.mkdir(parents=True, exist_ok=True)
    sheet = WORK / 'contact-sheet.png'
    n = len(frames)
    cols = 5 if n % 5 == 0 else 3
    ins = []
    for i in range(n):
        ins += ['-i', str(CHAIN / frame_name(i))]
    fc = ''.join(
        f'[{i}:v]scale=512:288:flags=lanczos,'
        f'drawtext=text=\'t={frames[i]["t"]:.2f}\':x=10:y=10:fontsize=22:'
        f'fontcolor=white:box=1:boxcolor=black@0.6{_FONT}[a{i}];'
        for i in range(n))
    parts = [fc.rstrip(';')]
    prev = None
    for r0 in range(0, n, cols):
        idx = list(range(r0, min(r0 + cols, n)))
        tag = f'r{r0//cols}'
        parts.append(''.join(f'[a{i}]' for i in idx)
                     + f'hstack=inputs={len(idx)}[{tag}]')
        prev = tag if prev is None else prev
        if r0 // cols > 0:
            parts.append(f'[{prev}][{tag}]vstack=inputs=2[v{r0//cols}]')
            prev = f'v{r0//cols}'
    parts.append(f'[{prev}]null[out]')
    if run_ff_graph(ins, ';'.join(parts),
                    ['-map', '[out]', '-frames:v', '1', '-update', '1',
                     str(sheet)], 'contact sheet') != 0:
        raise SystemExit(1)
    log(f'  ✅ contact sheet: {sheet}')
    return sheet


def build_video(args, frames) -> bool:
    """N 张关键帧 → xfade 交叉溶解 + 缓慢 Ken Burns → H.264 无缝循环视频。

    时间轴设计（段长 S、溶解 X，共 N 帧）：
      frame-00 占 0.00 .. S，最后一次溶解的 offset 是 (N-1)*(S-X)，
      视频总长 = N*S - (N-1)*X，**最后 X 秒完全等于 frame-00** ——
      也就是末帧与首帧逐像素相同，滚动到 1.0 与 0.0 观感一致。
      同时末尾这一次溶解本身就是 frame-(N-1) → frame-00，
      即"从最亮的余晖溶回静"，闭环不需要额外素材。
    """
    n = len(frames)
    S, X = args.seg, args.xfade
    dur = n * S - (n - 1) * X

    ins = []
    for i in range(n):
        ins += ['-loop', '1', '-t', f'{S:.3f}', '-i', str(CHAIN / frame_name(i))]

    # Ken Burns：所有帧用**同一条**缩放曲线（1.00 → 1.05，段长 S 秒）。
    # 关键：首尾必须是同一变换，否则末帧 vs 首帧不等，闭环就破了 ——
    # 所以这里不做交替推拉，改由交叉溶解本身提供"运动感"。
    #
    # 表达式里**不能写 fps**：zoompan 的变量只有 in/on/duration/pd/
    # in_time/out_time/it/iw/ih/zoom/px/py/x/y/a/sar/dar 等，没有 fps。
    # 写了会报 "Undefined constant or missing '(' in 'fps*2.0)'"，
    # 而且是在 configure 阶段失败 —— 整个滤镜图直接初始化不了。
    n_frames = max(1, round(args.fps * S))
    z = f'1+0.05*on/{n_frames}'
    zx = '(iw-iw/zoom)/2'
    zy = '(ih-ih/zoom)/2'
    parts = []
    for i in range(n):
        parts.append(
            f'[{i}:v]fps={args.fps},'
            f'zoompan=z=\'{z}\':x=\'{zx}\':y=\'{zy}\':d=1:s=1280x720:'
            f'fps={args.fps},'
            f'format=yuv420p,setsar=1[v{i}]')

    prev = 'v0'
    for i in range(1, n):
        off = i * (S - X)
        out = f'x{i}'
        parts.append(
            f'[{prev}][v{i}]xfade=transition=fade:duration={X:.3f}:'
            f'offset={off:.3f}[{out}]')
        prev = out

    log(f'  时间轴: {n} 帧 × {S:.2f}s，交叉溶解 {X:.2f}s → 总长 {dur:.2f}s')

    # CRF 20：暗场+星点，太高会糊掉星点，太低会超 20 MB。
    # GOP=8 + keyint_min=8 + sc_threshold=0：滚动 seek 要跟手，必须常驻关键帧。
    return run_ff_graph(
        ins, ';'.join(parts),
        ['-map', f'[{prev}]',
         '-c:v', 'libx264', '-profile:v', 'high', '-level', '4.1',
         '-preset', args.preset, '-crf', str(args.crf),
         '-pix_fmt', 'yuv420p',
         '-g', '8', '-keyint_min', '8', '-sc_threshold', '0',
         '-movflags', '+faststart',
         '-an',
         str(VIDEO)], 'voyage.mp4') == 0


def make_poster(args) -> bool:
    """海报 = 第 0 帧，等比缩到 1280 宽，WebP。"""
    WORK.mkdir(parents=True, exist_ok=True)
    g = '[0:v]scale=1280:-2:flags=lanczos[out]'
    return run_ff_graph(['-i', str(CHAIN / frame_name(0))], g,
                        ['-map', '[out]', '-c:v', 'libwebp', '-quality', '88',
                         '-compression_level', '6', str(POSTER)],
                        'voyage-poster.webp') == 0


def _run_capture(argv: list) -> tuple[int, str]:
    """需要读子进程输出时统一走这里：写临时文件再读回来，绝不用管道。"""
    WORK.mkdir(parents=True, exist_ok=True)
    lf = WORK / '_cap.log'
    with open(lf, 'wb') as fh:
        r = subprocess.run(argv, stdout=fh, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, cwd=str(WORK))
    return r.returncode, lf.read_text(encoding='utf-8', errors='replace')


def verify_loop(args) -> dict:
    """量化闭环：取视频末帧，和视频开头中点（Ken Burns 中点）比 SSIM。

    末段是纯 frame-00，所以"末帧"本应等于 frame-00 的中点缩放版本；
    误差只来自 H.264 压缩。SSIM 越接近 1 说明闭环越干净。
    """
    WORK.mkdir(parents=True, exist_ok=True)
    S = args.seg
    last = WORK / 'loop-last.png'
    first = WORK / 'loop-first-mid.png'
    res = {}
    if run_ff(['-sseof', '-0.05', '-i', str(VIDEO), '-frames:v', '1',
               '-update', '1', str(last)], '抽末帧') != 0:
        return res
    if run_ff(['-ss', f'{S/2:.3f}', '-i', str(VIDEO), '-frames:v', '1',
               '-update', '1', str(first)], '抽首段中点') != 0:
        return res
    src = CHAIN / frame_name(0)
    for label, other in (('末帧 vs 首段中点(缩放后)', first),
                         ('末帧 vs 原始 frame-00', src)):
        rc, out = _run_capture(
            ['ffmpeg', '-hide_banner',
             '-i', str(last), '-i', str(other),
             '-lavfi', 'ssim', '-f', 'null', '-'])
        for ln in out.splitlines():
            if 'SSIM' in ln:
                res[label] = ln.strip()
    return res


def probe_video() -> dict:
    rc, out = _run_capture(
        ['ffprobe', '-v', 'error', '-show_entries',
         'format=duration,size,bit_rate:stream=width,height,'
         'avg_frame_rate,nb_frames,codec_name,pix_fmt',
         '-of', 'json', str(VIDEO)])
    try:
        return json.loads(out)
    except Exception:               # noqa: BLE001
        return {}


# ═══════════════════════════════════════════════════════════════════════════
# 六、入口
# ═══════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(
        description='生成连续可循环的星云演化链并合成导航页背景视频',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split('【用法】')[-1])
    ap.add_argument('--frames', type=int, default=6, choices=(6, 10),
                    help='关键帧数量：6（默认，约 8 分钟）或 10（完整叙事，约 14 分钟）')
    ap.add_argument('--w', type=int, default=1280, help='帧宽（默认 1280）')
    ap.add_argument('--h', type=int, default=720, help='帧高（默认 720）')
    ap.add_argument('--steps', type=int, default=8,
                    help='KSampler 步数；有效步数 = steps*denoise，FLUX-schnell 建议有效步数 >= 4（默认 8）')
    ap.add_argument('--seed', type=int, default=20260912,
                    help='固定 seed：FLUX 在低 denoise 下 seed 只影响纹理细节，固定可减少帧间细节漂移')
    ap.add_argument('--ckpt', default=CKPT, help='FLUX 权重名')
    ap.add_argument('--timeout', type=float, default=900, help='单帧等待上限（秒）')
    ap.add_argument('--only', type=int, default=None, help='只重做第 N 帧（需上一帧已在磁盘上）')
    ap.add_argument('--force', action='store_true', help='已存在的帧也重做')
    ap.add_argument('--skip-gen', action='store_true', help='跳过 ComfyUI 生成，只用已有帧做合成')
    ap.add_argument('--contact-sheet-only', action='store_true', help='只重拼 contact sheet')
    ap.add_argument('--direct-input', action='store_true',
                    help='直接把 init 图写进 ComfyUI 的 input 目录，跳过 /upload/image')
    ap.add_argument('--seg', type=float, default=2.0, help='每个关键帧的段长（秒，默认 2.0）')
    ap.add_argument('--xfade', type=float, default=0.5, help='相邻帧交叉溶解时长（秒，默认 0.5）')
    ap.add_argument('--fps', type=int, default=30, help='输出帧率（默认 30）')
    ap.add_argument('--crf', type=int, default=20, help='x264 CRF（默认 20，越小越清晰越大）')
    ap.add_argument('--preset', default='medium',
                    help='x264 preset（默认 medium；slow 在 720p/15s 上要多花几分钟，收益很小）')
    args = ap.parse_args()

    frames, _denoise = pick_frames(args.frames)

    CHAIN.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)

    log(f'=== {time.strftime("%H:%M:%S")} 开始 ===')
    log(f'ROOT   = {ROOT}')
    log(f'关键帧 = {CHAIN}（{len(frames)} 帧）')
    log(f'视频   = {VIDEO}')
    log()

    if args.contact_sheet_only:
        make_contact_sheet(frames)
        return

    if not args.skip_gen:
        if not server_alive():
            log('❌ ComfyUI 未响应（http://127.0.0.1:8188）。先确认它还在跑。')
            raise SystemExit(1)
        log('ComfyUI 在线：' + vram_report())
        log()
        # 帧已在磁盘上时不需要重复生成（--force 可强制重做）。
        # 另：filename_prefix 带时间戳，所以同一批参数重跑也不会命中缓存。
        if not args.force and not args.only:
            if all((CHAIN / frame_name(i)).exists() for i in range(len(frames))):
                log(f'✅ {len(frames)} 帧已在 {CHAIN}，跳过生成（--force 可强制重做）')
        if not gen_chain(args, frames):
            raise SystemExit(1)
    else:
        missing = [frame_name(i) for i in range(len(frames))
                   if not (CHAIN / frame_name(i)).exists()]
        if missing:
            log('❌ 缺帧：' + ', '.join(missing))
            raise SystemExit(1)

    log('── 拼 contact sheet（肉眼验收连贯性用）──')
    make_contact_sheet(frames)

    log('── 合成视频 ──')
    if not build_video(args, frames):
        raise SystemExit(1)

    log('── 生成海报 ──')
    if not make_poster(args):
        raise SystemExit(1)

    log('── 校验 ──')
    info = probe_video()
    st = (info.get('streams') or [{}])[0]
    fmt = info.get('format') or {}
    log(f'  视频: {st.get("codec_name")} {st.get("width")}x{st.get("height")} '
        f'{st.get("avg_frame_rate")} pix_fmt={st.get("pix_fmt")} '
        f'frames={st.get("nb_frames")}')
    if fmt.get('duration'):
        log(f'  时长: {float(fmt["duration"]):.2f}s   '
            f'体积: {int(fmt.get("size", 0))/1024/1024:.2f} MB   '
            f'码率: {int(fmt.get("bit_rate", 0))/1000:.0f} kbps')
    for k, v in verify_loop(args).items():
        log(f'  {k}: {v}')
    log()
    log(f'=== {time.strftime("%H:%M:%S")} 完成 ===')
    for p in (VIDEO, POSTER, CHAIN):
        log(f'  {p}')


if __name__ == '__main__':
    main()
