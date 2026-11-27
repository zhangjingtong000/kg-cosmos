"""生成导航页背景视频的 6 个关键帧：一条「从静谧星空深入星云」的旅程。

【要解决什么】
  上一版 6 帧的 YAVG（全帧平均亮度，0-255）是
    22.86 / 23.13 / 24.07 / 25.90 / 27.68 / 28.86
  跨度只有 6/255（2.4%）。6 张拼起来看全是「深蓝星空 + 一条斜向星云带」，
  只是星云逐渐变亮变宽 —— 用户滚完整个页面几乎看不出背景在变。

【第一轮尝试（失败）与实测到的两条硬事实】
  按「提高 steps + 提高 denoise」的思路跑了一轮（steps=10，
  denoise 0.70/0.78/0.85/0.88/0.80），结果 YAVG 仍是
    19.50 / 19.78 / 20.29 / 22.14 / 23.97 / 25.89（跨度 6.4）。
  于是做了几组一次性探针，
  得到两条决定性结论：

  (1) **ComfyUI 的 denoise 不是「采样步数折扣」。**
      comfy/samplers.py 的 KSampler.set_steps():
          new_steps = int(steps / denoise)
          sigmas = calculate_sigmas(new_steps)[-(steps + 1):]
      也就是说 steps 才是**实际执行步数**（恒定，所以每帧耗时都是 69.3s），
      denoise 只决定起始 sigma 的位置。提高 denoise 并不会多跑几步。

  (2) **FLUX 的 img2img 会把 init 图的全局曝光几乎原样透传。**
      同一个 K4 prompt：
        · txt2img（denoise=1.0）          → YAVG 47.18（换成满屏光的措辞 74.69）
        · img2img，init 是暗帧，d=0.88    → YAVG 23.97
        · img2img，init 先标定到 78，d=0.85 → YAVG **75.36**（忠实跟随 init）
        · img2img，init 先标定到 78，d=0.92 → YAVG 58.67（prompt 开始主导内容）
      即：**亮度由 init 决定，内容由 denoise 决定**。

【这一版的参数设计（围绕上面两条事实）】
  · steps = 10（实际执行 10 步）
  · denoise 分档 = 0.90 / 0.88 / 0.89 / 0.92 / 0.93
    —— 比第一轮高得多，因为只有 denoise ≥ 0.9 时 prompt 才真正主导画面内容
    （探针：denoise 0.92 把「提亮后的青蓝星空」重画成了「金色恒星核心爆发」）。
    K1=0.90 是必须的：init 是 K0 的纯黑星空，0.80 时模型什么都不画
    （实测只多出几颗星）；K5=0.93 也是必须的：要从 K4 的橙金真的
    "冷却"成稀薄冷色，0.88 时模型会把暖金原样留着。
  · **init 曝光预标定**：生成第 i 帧前，先用 ffmpeg 二分搜索把
    frame-(i-1) 的 YAVG 标定到第 i 帧的目标亮度，再送进 img2img。
    这样模型是在「正确曝光」的起点上继续画，输出亮度自然跟上。
  · **生成后微调**：生成完再测一次 YAVG，偏差 > --post-tol（默认 6）时
    才做一次小幅标定（幅度小，不会把暗部拉成灰雾）。
  · prompt 结构：**固定词根 BASE + 阶段词**，且 BASE 里**去掉了
    "dark background"** —— 探针显示这个词对亮帧有害（同一 prompt
    K4：带它 txt2img 只有 47.18，去掉它 58.06）。
  · cfg=1.0、euler、simple、1280x720 不变。

【目标亮度曲线（YAVG，允许 ±8）】
  ★ 刻度必须先钉死：signalstats 对 RGB 输入走 limited range，
    纯黑 = 16.00、中灰 = 126、纯白 = 235（实测见 _cal_scale.py）。
    所以 YAVG 下限是 16，原目标里的 K0 ≈ 10 物理不可达，改标 16；
    K1 从 18 提到 23（18 距黑电平只有 2，肉眼与 K0 无从区分）。
  K0 ≈ 16  极暗，几乎纯黑（实测就是纯黑），只有稀疏星点 —— 首屏白字极清晰
  K1 ≈ 23  远处浮现一小团冷色（青蓝）星云，只占画面一角
  K2 ≈ 32  星云逼近，约占 1/3 画面，有体积感
  K3 ≈ 52  穿过云层，青绿中透出暖金，云气充满画面
  K4 ≈ 78  明亮核心 / 星团爆发，橙金为主 —— 旅程高潮
  K5 ≈ 48  核心扩散、变稀薄，亮度回落，边缘化开成光尘
  硬性下限：max(YAVG) - min(YAVG) >= 55

【不做首尾闭合（用户明确要求）】
  「不要为了首尾帧去合并，首尾帧不需要刻意去做，整段拉下来是一段完整的
    视频就行。」所以 K5 是"回落"（≈48）而不是回到 K0（≈16），
  build_video **不再在链尾追加 frame-00 副本**，也不再做闭环校验。

【帧间因果】
  第 0 帧 txt2img；第 i 帧以 frame-(i-1) 为 init 图（链式）。
  因此重做上游帧后必须 --force 连带重做全部下游帧，否则链会断。

【显存 / 耗时】
  RTX 4060 Laptop 8 GB，FLUX fp8 权重靠 ComfyUI 分块换入，
  所以必须**串行**提交，不要并发。1280x720 / 10 步约 70s/帧。

【产出】
  web/assets/voyage-chain/frame-00..05.png   关键帧
  <chain-dir>/contact-sheet.png              横向拼接总览（肉眼验收用）
  web/assets/voyage.mp4 / voyage-poster.webp 仅在**不加** --gen-only 时写

【用法】
  python tools/gen_voyage_chain.py --help
  python tools/gen_voyage_chain.py --gen-only --force   # 只重做关键帧 + 亮度报表
  python tools/gen_voyage_chain.py                      # 全流程（含视频合成）
  python tools/gen_voyage_chain.py --only 3 --force     # 只重做第 3 帧
  python tools/gen_voyage_chain.py --chain-dir .\voyage-explore1
                                                        # 试参数，不覆盖正式帧
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

# 固定词根：所有帧共用同一套「这个世界是什么样」的描述。
# 阶段词怎么变，材质、星点、颗粒、色调倾向都还是同一个世界 —— 这是
# 「推得动」和「连贯」能同时成立的关键。
#
# ★ 注意这里**没有 "dark background"**：探针实测同一个 K4 prompt
#   带它 txt2img 只有 YAVG 47.18，去掉它就有 58.06 —— 对亮帧是明确的
#   负向词，所以暗部改由 K0/K1 各自用 "very dark / low key" 单独描述。
BASE = (
    "deep space, astrophotography, volumetric nebula, cinematic, "
    "subtle film grain, high detail"
)

# 负面词全程一致（FLUX-schnell cfg=1，负面词影响有限，但保持一致无害）
NEG = (
    "text, words, letters, watermark, signature, frame, border, "
    "low quality, blurry, jpeg artifacts, cartoon, illustration, "
    "painting, flat poster, harsh contrast"
)

# ── 6 帧叙事表 ────────────────────────────────────────────────────────────
# 每帧 = 阶段词 + BASE + 「拉开跨度用」的补充描述。
# 补充描述是有意为之：上一版的失败教训是「只说变亮一点点，模型就只变一点点」，
# 所以这里把亮度 / 占画比例 / 色温写成明确的、可执行的描述。
#   · t 只用于 contact sheet 标注与前端叙事锚点，不参与采样
#   · denoise=None 表示该帧走 txt2img（只有 K0）
FRAMES_6 = [
    dict(
        t=0.00, denoise=None, tag='K0 静谧星空：极暗几乎纯黑 + 稀疏星点',
        # calib='linear'：这一幕是"纯黑 + 少量饱和星点"，幂律 gamma 对纯白
        # 像素无效（pow(1,·)=1），压不到 YAVG≈10，只能线性缩放。
        calib='linear',
        prompt=(
            "almost empty starfield, faint distant nebula in one corner, "
            + BASE + ", "
            "extremely dark and underexposed, the frame is almost entirely "
            "black, only a handful of tiny faint white star points, vast "
            "empty darkness dominating the whole image, very low key, calm "
            "and silent, no bright areas, no light source")),
    dict(
        t=0.20, denoise=0.90, tag='K1 远处浮现一小团冷青星云（只占一角）',
        # denoise 从 0.80 提到 0.90：init 是 K0 的纯黑星空，0.80 时模型
        # 什么都不画（实测只多了几颗星），必须让 prompt 主导才会"无中生有"
        # 地浮出那团青蓝星云。
        prompt=(
            "distant cold blue nebula approaching, small, "
            + BASE + ", "
            "a small but clearly visible glowing cyan blue nebula patch in "
            "the upper right corner, distinct saturated cold teal color "
            "standing out against the black sky, faint luminous gas with "
            "soft edges, the rest of the frame is still very dark with "
            "sparse stars, dim overall, low key")),
    dict(
        t=0.40, denoise=0.88, tag='K2 星云逼近：约占 1/3 画面，有体积感',
        prompt=(
            "nebula filling a third of the frame, volumetric, cold teal, "
            + BASE + ", "
            "a clearly visible teal cyan nebula with real volume and depth, "
            "towers of cold cyan interstellar gas, darker dust lanes inside, "
            "dense stars around it, the nebula occupies about one third of "
            "the picture, moderately bright luminous teal gas, the rest of "
            "the frame is still dark space")),
    dict(
        t=0.60, denoise=0.89, tag='K3 穿过云层：青绿中透出暖金，充满画面',
        prompt=(
            "passing through luminous gas clouds, teal and warm gold, "
            + BASE + ", "
            "glowing gas clouds now fill the entire frame, warm golden light "
            "shining through from behind the teal clouds, large luminous "
            "cloud structures, dust rims catching the warm light, layered "
            "depth, immersive, bright overall, no large empty dark areas")),
    dict(
        t=0.80, denoise=0.92, tag='K4 明亮核心 / 星团爆发：橙金为主（高潮）',
        prompt=(
            "brilliant golden stellar core, radiant, dense star cluster, "
            "bright glowing golden nebula gas cloud filling the entire frame, "
            "luminous orange and gold plasma, intense warm light everywhere, "
            + BASE + ", "
            "high key, well lit, bright exposure, vivid, glowing, no dark "
            "areas, uplifting climax, blinding radiant golden core")),
    dict(
        t=1.00, denoise=0.93, tag='K5 核心扩散、变稀薄：亮度回落，化开成光尘',
        prompt=(
            "core dissipating into thin drifting dust, fading, sparse, "
            + BASE + ", "
            "the hot gold and red glow has cooled down, colors shifting to "
            "dim cold blue and violet, the bright core is gone completely, "
            "only thin translucent wisps and veils of cold dust spread across "
            "the frame, dim scattered glow, brightness clearly falling back, "
            "large empty black areas of deep space, faint stars, no bright "
            "core left, no orange glow, quiet and dispersed")),
]

# denoise 分档（见文件头的事实 (1)(2)）：
#   亮度由 init 决定（另有曝光预标定兜底），内容由 denoise 决定。
#   第一轮 0.70~0.88 推不动内容，这一版整体抬高到 0.88~0.93：
#     · K1 从纯黑星空里"无中生有"浮出一团星云，必须让 prompt 主导 → 0.90
#     · K2 星云逼近、要有体积感 → 0.88
#     · K3 穿过云层、青绿透暖金 → 0.89
#     · K4 高潮"金色爆核"，prompt 完全主导 → 0.92
#     · K5 要从 K4 的橙金**冷却**成稀薄冷色光尘（色调真翻过来）→ 0.93
DENOISE_6 = [None, 0.90, 0.88, 0.89, 0.92, 0.93]

# ── 目标亮度曲线 ──────────────────────────────────────────────────────────
# ★ 这里必须先用 signalstats 在三张基准图上钉死刻度 ★
#   用户给的验收命令走 signalstats，它工作在 YUV 上，RGB 输入会被按
#   **limited range（16-235）** 转过去，实测三张基准图：
#       纯黑 → YAVG 16.00   中灰 → 126.00   纯白 → 235.00
#   也就是说 YAVG 的物理下限是 16，目标曲线里的「K0 ≈ 10」根本不可达。
#   所以：
#     · K0 的目标改成 16.0（= 纯黑，能做到的极限）—— 用户的 10 与它
#       偏差 +6，落在用户允许的 ±8 以内；
#     · K1 的目标由 18 提到 23。18 距离黑电平只有 2，画面上和 K0 完全
#       分不出来（这正是"滑下来看不出变化"的翻版），23 仍在 18±8 内，
#       但已经能把"角落里那团青蓝星云"露出来。
TARGET_YAVG = [16.0, 23.0, 32.0, 52.0, 78.0, 48.0]

# 生成后亮度允许的偏差。超过才做 gamma 微调 —— 幅度小才不会把暗部拉成灰雾。
POST_TOL = 6.0


def pick_frames(n: int) -> list:
    """返回帧表。denoise=None 表示该帧走 txt2img。"""
    if n != 6:
        raise SystemExit(f'--frames 只支持 6（收到 {n}）：'
                         '导航页只用了 6 个关键帧，多一套叙事表只会互相带偏')
    return [dict(f) for f in FRAMES_6]


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


def calibrate_yavg(src: Path, dst: Path, target: float, tol: float = 1.0,
                   label: str = 'calib', mode: str = 'gamma') -> float:
    """把 src 的 YAVG 标定到 target 后写 dst，返回实测值。

    为什么这一步是必要的（而不是"作弊"）：
      实测 FLUX 的 img2img 会把 init 的全局曝光**原样透传** ——
      init YAVG=78 → 输出 75.36（denoise 0.85）/ 58.67（denoise 0.92）。
      也就是说链路里唯一能决定亮度的旋钮就是 init 的曝光。
      所以每帧生成前先把 init 摆到目标亮度，输出才会跟上去。

    mode='gamma'（默认）：`eq=gamma=G`，G 越大越亮（实测 G=6 → YAVG≈169；
        第一版把方向写反，直接冲到过曝，记在这里）。
        幂律对**纯白像素无效**（pow(1,·)=1），所以星空里饱和的星点压不下去，
        K0 这种"纯黑 + 稀疏亮星"的画面用它只能压到 16 左右。
    mode='linear'：`lutrgb=val*K`，线性缩放，连星点一起压。
        K0 要求 YAVG≈10 的"几乎纯黑"，只能靠它。
    """
    if mode == 'linear':
        lo, hi = 0.05, 4.0

        def vf(k):
            return f'lutrgb=r=val*{k:.4f}:g=val*{k:.4f}:b=val*{k:.4f}'
    else:
        lo, hi = 0.25, 6.0

        def vf(k):
            return f'eq=gamma={k:.4f}'

    got = float('nan')
    for _ in range(12):
        mid = (lo + hi) / 2
        if run_ff(['-i', str(src), '-vf', vf(mid),
                   '-frames:v', '1', '-update', '1', str(dst)], label) != 0:
            return float('nan')
        got = measure_yavg(dst)
        if abs(got - target) <= tol:
            break
        if got < target:
            lo = mid          # 太暗 → 参数还要更大（更亮）
        else:
            hi = mid
    return got


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
            # ★ init 曝光预标定：把上一帧的 YAVG 摆到本帧目标亮度，再送 img2img。
            #   不做这一步，整条链会被第一帧的暗部锁死 —— 这正是上一版
            #   6 帧 YAVG 只差 6 的根因。
            init_src = prev
            if args.init_calib:
                y0 = measure_yavg(prev)
                init_src = WORK / f'init-calib-{i:02d}.png'
                y1 = calibrate_yavg(prev, init_src, TARGET_YAVG[i],
                                    label=f'init-{i:02d}')
                log(f'  · init 曝光标定：YAVG {y0:.2f} → {y1:.2f}'
                    f'（目标 {TARGET_YAVG[i]:.0f}）')
            if args.direct_input:
                COMFY_INPUT.mkdir(parents=True, exist_ok=True)
                init_name = f'kg_voyage_init_{i:02d}.png'
                shutil.copyfile(init_src, COMFY_INPUT / init_name)
            else:
                init_name = upload_image(init_src, f'kg_voyage_init_{i:02d}.png')
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

        # ★ 生成后微调：只在偏差超过容差时做一次 gamma 校正。
        #   幅度小（通常 <20%），所以不会把暗部噪声拉成灰雾；
        #   这一层是"标定曲线"的收口，保证最终 YAVG 落在目标附近。
        if args.post_calib:
            y = measure_yavg(dest)
            if abs(y - TARGET_YAVG[i]) > args.post_tol:
                tmp = WORK / f'post-calib-{i:02d}.png'
                y2 = calibrate_yavg(dest, tmp, TARGET_YAVG[i], tol=1.5,
                                    label=f'post-{i:02d}',
                                    mode=f.get('calib', 'gamma'))
                shutil.copyfile(tmp, dest)
                log(f'  · 生成后亮度微调：{y:.2f} → {y2:.2f}'
                    f'（目标 {TARGET_YAVG[i]:.0f}，容差 {args.post_tol:.0f}）')
            else:
                log(f'  · 生成后亮度已达标：{y:.2f}（目标 {TARGET_YAVG[i]:.0f}）')
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
    """把 6 个关键帧缩到 420 宽、**横向拼成一行**，用来肉眼验收"旅程跨度"。

    为什么是横向一行：这一版要验证的是「6 帧之间的视觉跨度」，
    横排一行最接近用户滚动页面的实际感受（左 = 刚进页面，右 = 滚到底）。
    每张左上角标 YAVG 目标值，方便和实测值对照。
    """
    WORK.mkdir(parents=True, exist_ok=True)
    sheet = WORK / 'contact-sheet.png'
    n = len(frames)
    W = 420                      # 单张宽度（1280x720 等比 → 420x236）
    ins = []
    for i in range(n):
        ins += ['-i', str(CHAIN / frame_name(i))]
    fc = ''.join(
        f'[{i}:v]scale={W}:-2:flags=lanczos,'
        f'drawtext=text=\'K{i} tgt={TARGET_YAVG[i]:.0f}\':x=8:y=8:fontsize=20:'
        f'fontcolor=white:box=1:boxcolor=black@0.6{_FONT}[a{i}];'
        for i in range(n))
    graph = fc + ''.join(f'[a{i}]' for i in range(n)) + f'hstack=inputs={n}[out]'
    if run_ff_graph(ins, graph,
                    ['-map', '[out]', '-frames:v', '1', '-update', '1',
                     str(sheet)], 'contact sheet') != 0:
        raise SystemExit(1)
    log(f'  ✅ contact sheet: {sheet}')
    return sheet


def build_video(args, frames) -> bool:
    """N 张关键帧 → xfade 交叉溶解 + 缓慢 Ken Burns → H.264 视频。

    ★ 这一版**不做首尾闭合**（上一版做，被用户否掉了）★
      上一版在链尾追加 frame-00 的副本，让视频末帧溶回首帧，追求"无缝循环"。
      但代价是：为了让首尾对得上，K0 被迫做得不够暗、K5 被迫拉回 K0 的构图，
      整条曲线的两端被"缝合"动机绑住，反而压扁了帧间跨度。
      用户明确要求「首尾帧不需要刻意去做，整段拉下来是一段完整的视频」，
      所以这里就是朴素的 f0 - f1 - ... - fN-1，走到头就结束。

    时间轴（段长 S、溶解 X，共 M = N 个节点）：
      总长 = M*S - (M-1)*X
      第 i 次溶解 offset = i*(S-X)
    """
    n = len(frames)
    chain_idx = list(range(n))       # 朴素线性链，不追加首帧副本
    M = len(chain_idx)
    S, X = args.seg, args.xfade
    dur = M * S - (M - 1) * X

    ins = []
    for k in chain_idx:
        ins += ['-loop', '1', '-t', f'{S:.3f}', '-i', str(CHAIN / frame_name(k))]

    # Ken Burns：所有节点用**同一条**缩放曲线（1.00 → 1.05）。
    # 不做交替推拉 —— 推拉方向一变，交叉溶解处就会出现"回弹"，
    # 滚动驱动的观感会显得抖。运动感交给交叉溶解本身。
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
    for i in range(M):
        parts.append(
            f'[{i}:v]fps={args.fps},'
            f'zoompan=z=\'{z}\':x=\'{zx}\':y=\'{zy}\':d=1:s={args.w}x{args.h}:'
            f'fps={args.fps},'
            f'format=yuv420p,setsar=1[v{i}]')

    prev = 'v0'
    for i in range(1, M):
        off = i * (S - X)
        out = f'x{i}'
        parts.append(
            f'[{prev}][v{i}]xfade=transition=fade:duration={X:.3f}:'
            f'offset={off:.3f}[{out}]')
        prev = out

    log(f'  链路: {" - ".join(f"f{k:02d}" for k in chain_idx)}（线性，不闭合）')
    log(f'  时间轴: {M} 段 × {S:.2f}s，交叉溶解 {X:.2f}s → 总长 {dur:.2f}s')

    out_path = out_video(args)
    if out_path.resolve() == VIDEO.resolve() and args.guard_default:
        log('  ⚠ 输出就是 web/assets/voyage.mp4 —— 前端已接入的成品。'
            '确需覆盖请加 --allow-overwrite-voyage。')
        return False

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
         str(out_path)], 'voyage.mp4') == 0


def make_poster(args) -> bool:
    """海报 = 第 0 帧，等比缩到 1280 宽，WebP。"""
    WORK.mkdir(parents=True, exist_ok=True)
    out = Path(args.poster_out) if getattr(args, 'poster_out', None) else POSTER
    if getattr(args, 'poster_out', None) and not out.is_absolute():
        out = ROOT / out
    g = '[0:v]scale=1280:-2:flags=lanczos[out]'
    return run_ff_graph(['-i', str(CHAIN / frame_name(0))], g,
                        ['-map', '[out]', '-c:v', 'libwebp', '-quality', '88',
                         '-compression_level', '6', str(out)],
                        'voyage-poster.webp') == 0


def _run_capture(argv: list) -> tuple[int, str]:
    """需要读子进程输出时统一走这里：写临时文件再读回来，绝不用管道。"""
    WORK.mkdir(parents=True, exist_ok=True)
    lf = WORK / '_cap.log'
    with open(lf, 'wb') as fh:
        r = subprocess.run(argv, stdout=fh, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, cwd=str(WORK))
    return r.returncode, lf.read_text(encoding='utf-8', errors='replace')


def out_video(args) -> Path:
    """本次实际写入的视频路径（--out 优先，默认 web/assets/voyage.mp4）。

    相对路径按项目根解析：ffmpeg 的 cwd 被设成工作目录（见 run_ff_graph），
    传相对路径会落到工作目录里。
    """
    if not getattr(args, 'out', None):
        return VIDEO
    p = Path(args.out)
    return p if p.is_absolute() else (ROOT / p)


def measure_yavg(p: Path) -> float:
    """用 ffmpeg signalstats 读一张 PNG 的全帧平均亮度 YAVG（0-255）。

    这是这一版的**唯一验收指标**：上一版 6 帧 YAVG 全挤在 22.9~28.9，
    跨度 6/255，所以用户滚完整页也看不出背景在变。
    注意子进程输出一律写文件（见 _run_capture），不走管道。
    """
    rc, out = _run_capture(
        ['ffmpeg', '-v', 'info', '-i', str(p),
         '-vf', 'signalstats,metadata=print:key=lavfi.signalstats.YAVG',
         '-f', 'null', '-'])
    for ln in out.splitlines():
        if 'lavfi.signalstats.YAVG=' in ln:
            try:
                return float(ln.rsplit('=', 1)[1].strip())
            except ValueError:
                pass
    return float('nan')


def frame_psnr(a: Path, b: Path) -> str:
    """两张图的 PSNR（越小差异越大）。用来量化"相邻帧到底差多少"。"""
    rc, out = _run_capture(
        ['ffmpeg', '-hide_banner', '-i', str(a), '-i', str(b),
         '-lavfi', 'psnr', '-f', 'null', '-'])
    for ln in out.splitlines():
        if 'average:' in ln:
            return ln.strip()
    return '（PSNR 读取失败）'


def report_span(frames) -> dict:
    """实测各帧 YAVG，对照目标曲线，并给出跨度和相邻帧 PSNR。

    返回 dict 便于上层（或人）复核；同时把报表打屏 + 落盘。
    """
    n = len(frames)
    got = []
    for i in range(n):
        p = CHAIN / frame_name(i)
        got.append(measure_yavg(p) if p.exists() else float('nan'))

    log('── YAVG 实测 vs 目标 ──')
    for i in range(n):
        tgt = TARGET_YAVG[i] if i < len(TARGET_YAVG) else float('nan')
        d = got[i] - tgt
        flag = '✅' if abs(d) <= 8 else '⚠'
        log(f'  frame-{i:02d}  YAVG={got[i]:6.2f}   目标={tgt:5.1f}   '
            f'偏差={d:+6.2f} {flag}')

    ok = [v for v in got if v == v]     # 去掉 nan
    span = (max(ok) - min(ok)) if ok else float('nan')
    log(f'  跨度 max-min = {span:.2f}（硬性下限 55）'
        f' {"✅ 达标" if span >= 55 else "❌ 不达标"}')

    log('  相邻帧差异（PSNR，越小差得越多）：')
    for i in range(1, n):
        a, b = CHAIN / frame_name(i - 1), CHAIN / frame_name(i)
        if a.exists() and b.exists():
            log(f'    {i-1}->{i}: ΔYAVG={got[i]-got[i-1]:+6.2f}   {frame_psnr(a, b)}')

    return dict(yavg=got, span=span)


def probe_video(args) -> dict:
    rc, out = _run_capture(
        ['ffprobe', '-v', 'error', '-show_entries',
         'format=duration,size,bit_rate:stream=width,height,'
         'avg_frame_rate,nb_frames,codec_name,pix_fmt',
         '-of', 'json', str(out_video(args))])
    try:
        return json.loads(out)
    except Exception:               # noqa: BLE001
        return {}


# ═══════════════════════════════════════════════════════════════════════════
# 六、入口
# ═══════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(
        description='生成「从静谧星空深入星云」的 6 个关键帧，并合成导航页背景视频',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split('【用法】')[-1])
    ap.add_argument('--frames', type=int, default=6, choices=(6,),
                    help='关键帧数量，只支持 6（导航页实际使用的帧数）')
    ap.add_argument('--w', type=int, default=1280, help='帧宽（默认 1280）')
    ap.add_argument('--h', type=int, default=720, help='帧高（默认 720）')
    ap.add_argument('--steps', type=int, default=10,
                    help='KSampler 步数 = **实际执行步数**（ComfyUI 内部是 '
                         'new_steps=int(steps/denoise) 再截取尾部，见 samplers.py '
                         'set_steps）。上一版 8 步偏少，这一版默认 10')
    ap.add_argument('--seed', type=int, default=20260912,
                    help='固定 seed：FLUX 在低 denoise 下 seed 只影响纹理细节，固定可减少帧间细节漂移')
    ap.add_argument('--ckpt', default=CKPT, help='FLUX 权重名')
    ap.add_argument('--timeout', type=float, default=900, help='单帧等待上限（秒）')
    ap.add_argument('--only', type=int, default=None, help='只重做第 N 帧（需上一帧已在磁盘上）')
    ap.add_argument('--force', action='store_true', help='已存在的帧也重做')
    ap.add_argument('--gen-only', action='store_true',
                    help='只生成关键帧 + 打印 YAVG/拼 contact sheet，'
                         '不合成 voyage.mp4、不写 poster（改帧时的推荐用法）')
    ap.add_argument('--chain-dir', default=None,
                    help='关键帧输出目录（默认 web/assets/voyage-chain）。'
                         '试参数时指向临时目录就不会动正式帧')
    ap.add_argument('--init-calib', action=argparse.BooleanOptionalAction,
                    default=True,
                    help='img2img 前把上一帧的曝光标定到本帧目标亮度（默认开；'
                         '实测这是唯一能推动整条链亮度的旋钮）')
    ap.add_argument('--post-calib', action=argparse.BooleanOptionalAction,
                    default=True,
                    help='生成后若 YAVG 偏差超过 --post-tol 就做一次小幅 gamma 校正（默认开）')
    ap.add_argument('--post-tol', type=float, default=POST_TOL,
                    help=f'生成后允许的亮度偏差，超过才校正（默认 {POST_TOL}）')
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
    ap.add_argument('--out', default=None,
                    help='视频输出路径（默认 web/assets/voyage.mp4）。'
                         '想另存试用 --out web/assets/voyage-v3.mp4')
    ap.add_argument('--poster-out', default=None,
                    help='海报输出路径（默认 web/assets/voyage-poster.webp）')
    ap.add_argument('--allow-overwrite-voyage', action='store_true',
                    help='允许覆盖 web/assets/voyage.mp4。默认**拒绝**覆盖，'
                         '因为前端已接入该文件，后处理过的成片不该被脚本静默冲掉。')
    args = ap.parse_args()
    args.guard_default = not args.allow_overwrite_voyage

    # --chain-dir：试参数时把关键帧写到别处，绝不覆盖正式帧
    global CHAIN
    if args.chain_dir:
        p = Path(args.chain_dir)
        CHAIN = p if p.is_absolute() else (ROOT / p)

    frames = pick_frames(args.frames)

    CHAIN.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)

    log(f'=== {time.strftime("%H:%M:%S")} 开始 ===')
    log(f'ROOT   = {ROOT}')
    log(f'关键帧 = {CHAIN}（{len(frames)} 帧）')
    log(f'视频   = {out_video(args)}（--out 可改）')
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

    log('── YAVG 实测（验收跨度用）──')
    report_span(frames)

    log('── 拼 contact sheet（肉眼验收旅程跨度用）──')
    make_contact_sheet(frames)

    # --gen-only：只重做关键帧 + 验收报表，绝不碰 voyage.mp4 / poster。
    # 改关键帧时默认走这条，避免顺手覆盖前端已接入的成品。
    if args.gen_only:
        log()
        log(f'=== {time.strftime("%H:%M:%S")} 完成（--gen-only，未合成视频）===')
        log(f'  {CHAIN}')
        return

    log('── 合成视频 ──')
    if not build_video(args, frames):
        raise SystemExit(1)

    log('── 生成海报 ──')
    poster = Path(args.poster_out) if args.poster_out else POSTER
    if poster.resolve() == POSTER.resolve() and args.guard_default:
        log(f'  ⚠ 跳过：{POSTER} 是前端已接入的成品，默认不覆盖'
            f'（确需覆盖加 --allow-overwrite-voyage，或用 --poster-out 另存）。')
    elif not make_poster(args):
        raise SystemExit(1)

    log('── 校验 ──')
    info = probe_video(args)
    st = (info.get('streams') or [{}])[0]
    fmt = info.get('format') or {}
    log(f'  视频: {st.get("codec_name")} {st.get("width")}x{st.get("height")} '
        f'{st.get("avg_frame_rate")} pix_fmt={st.get("pix_fmt")} '
        f'frames={st.get("nb_frames")}')
    if fmt.get('duration'):
        log(f'  时长: {float(fmt["duration"]):.2f}s   '
            f'体积: {int(fmt.get("size", 0))/1024/1024:.2f} MB   '
            f'码率: {int(fmt.get("bit_rate", 0))/1000:.0f} kbps')
    log()
    log(f'=== {time.strftime("%H:%M:%S")} 完成 ===')
    for p in (out_video(args), poster, CHAIN):
        log(f'  {p}')


if __name__ == '__main__':
    main()
