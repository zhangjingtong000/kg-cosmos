# 动效体系与 GSAP —— 调研 · 方案 · 实施 · 证据

> **这份文档的任务**：把「引入动效」这件事的**调研结论、授权核查、方案取舍、
> 逐步实施与验证读数**落成一份可复核的台账。
>
> 为什么必须单独立一份：这些结论此前**只存在于对话里**。
> 本项目已经吃过"同一事实靠人记 ⇒ 必然漂"的亏（同一份名单抄三遍、
> 审计文档里的状态标记过期、`FRONTEND_DISCUSSION_PREP.md` 写着「① 动效仍未做」
> 而其实已经做完）。**对话不是档案。**
>
> 范围：`D:\kg-demo` 的前端动效。日期：**2026-09-20**（含 09-19 的方案输入）。

---

## 一、参考调研与逐条裁决

调研对象：动态/动效站与教程（Awwwards / Godly / Codrops / GSAP 官方 demos 等）。
**调研出来的东西没有一条是"整段照搬"** —— 每一条都由用户逐条裁决，记录如下。
（下表"你的裁决"是用户原话的意思归纳，不是转述性美化。）

| # | 参考 | 我提的用法 | 你的裁决 | 落地状态 |
|---|---|---|---|---|
| ① | Codrops《GSAP + Three.js》合用一条时钟（`gsap.ticker` 驱动 three 的 `requestAnimationFrame`） | 把主视图的渲染心跳换成 GSAP ticker，让时间轴与 three 共用一个时钟 | 「**不适合，否掉**」（指整篇的炫技画廊） | ✅ **但底层手法落了地** —— 见 `2d90701`：主视图心跳已换成 `gsap.ticker` |
| ② | **Cinematic 3D Scroll**（滚动驱动的电影式推进）<br>`https://tympanus.net/Tutorials/Cinematic3DScroll/` | 首页第 4 屏 12 个入口做成"跟轴似的滚动" | 「**有用**」；但要 **"跟轴似的滚动"**，**不要**"推进/放大"那一半 | ✅ `53dc2c9`：上下两层各一条轴依次滑入（**没有**做 zoom 推进） |
| ③ | **Telescope Zoom**（滚轮拉近镜头） | 主视图滚轮推拉 | 「**不要**」 | ✅ 未做；且 `kg-nebula.html:2384` 原有注释记着「相机补间已移除：进入/退出都直接落位」 |
| ④ | 《**WebGL Shaders with GSAP**》（把 GSAP 的缓动喂给 shader 参数）<br>`https://tympanus.net/Tutorials/ShaderAnimationGSAP/` | 用 GSAP 缓动替换首页光场的**点击涟漪** | 「**用**」 | ✅ `a0364ae`：涟漪改由 GSAP 时间轴驱动 `age`/`amp`，**shader 一行未动** |

> ⚠️ **2026-09-26 补上两条 URL**：这张表原来**只有名字、没有地址** ——
> 于是"想回头再看一眼那个站"时**翻这张表是找不到的**（`②` 的地址此前只出现在
> `docs/MOTION_REFS_STUDY.md:17`）。补上后两处一致。
>
> **⚠️ 站点可达性（2026-09-26 实测）**：`tympanus.net/Tutorials/…` 两个 demo 页
> 现在都返回 **HTTP 403**（Cloudflare 人机验证，`Just a moment...`）——
> **用真实浏览器打开通常仍可过验证**，但脚本/无头浏览器会被挡住。
> 更可靠的替代入口：
> - **本地已有源码**：`%TEMP%\cinematic.js`（**1.38 MB**，2026-09-20 抓的构建产物；
>   实测含 `CylinderGeometry` / `ScrollTrigger` / `CustomEase` ⇒ 就是"圆柱贴图集 + 自转"那套）
>   与 `%TEMP%\demo1-Clt9E7K4.js`（参考二，5.4 KB，可整读）
> - **GitHub 源码**：[codrops-cinematic-scroll-animations](https://github.com/JosephASG/codrops-cinematic-scroll-animations)
> - **教程正文**：[How to Build Cinematic 3D Scroll Experiences with GSAP](https://tympanus.net/codrops/2025/11/19/how-to-build-cinematic-3d-scroll-experiences-with-gsap/)
>
> ⚠️ 另注：本地那份构建产物里 `OGL` **出现 0 次**、`three` 的 `CylinderGeometry`
> 出现 3 次 —— 与 `docs/MOTION_REFS_STUDY.md §1.1` 记录的"meta 自称 OGL"**不一致**
> （实际打包进去的是 three.js）。该节结论（"画面全在画布、需真 GPU"）不受影响，
> 但"用哪个库"这一句按本地产物为准。

**从裁决里读出来的口径**（决定了后面所有取舍）：

- 你要的是**"一定要产生那个效果"**（原话：「我加这个动效就是为了炫技的」），
  但**能产生效果的只有导航页**；其余页面你自己划了范围：
  > 「我们只改导航页就好了，其他的页面做一些比较稳妥的动效就可以，
  > 不需要做得太炫，因为很容易出问题，毕竟很多功能嘛，哪些页面里边都有。」
- 你的另一句是整套做法的总约束：
  > 「我比较担心你给我改坏掉。」

⇒ 于是本项目的动效原则定为三条：
**① 导航页可以炫；② 其余页面只做"不可能改坏"的那种；③ 每一步都能单独回退。**

---

## 二、GSAP 事实核查（版本 / 授权 / 是不是 trial 版）

这一节是**证据**，不是转述 —— 每条都给出复核方法。

| 项 | 结论 | 怎么复核 |
|---|---|---|
| 版本 | **3.15.0** | 页面里读 `gsap.version`（探针 `tools/_probe/_eval_on_page.py`） |
| 授权 | **Standard "no charge" license**（Webflow，2025-04-30 起）| 包内 `LICENSE.md` / 官网授权页 |
| 原"会员专属"插件 | **现已全部免费**（含 `SplitText` / `MorphSVG` / `DrawSVG` / `ScrambleText`） | 同上；这也是本项目敢用 `SplitText` 做标题字母拆分的前提 |
| **是不是 trial 版** | **不是**。12 个文件全部 `trial=False` | 下载 npm 包后 `grep -i 'trial\|_gsapTrial'` 逐个文件看；trial 版会带 `_gsapTrial` 标记与"水印/限时"逻辑 |
| 包体量 | npm 包 179 个文件 / 约 6.0 MB（**未打包**），实际只取需要的 12 个 | `npm pack` 后数 |

**本地化清单**（`web/lib/gsap/`，共 **12 个 / 220.4 KB**，
数字复算：`Get-ChildItem web/lib/gsap/*.js | Measure-Object Length -Sum`）：

| 文件 | 体积 | 用在哪 |
|---|---|---|
| `gsap.min.js` | 71.2 KB | 核心（三处都用） |
| `ScrollTrigger.min.js` | 43.5 KB | 首页第 4 屏两轴 + 其余 11 页滚入淡入 |
| `Flip.min.js` | 24.9 KB | 备用（尚未使用） |
| `MotionPathPlugin.min.js` | 21.5 KB | 备用 |
| `ScrollSmoother.min.js` | 13.1 KB | 备用 |
| `ScrambleTextPlugin.min.js` | 11.4 KB | 备用 |
| `Observer.min.js` | 9.8 KB | 备用 |
| `SplitText.min.js` | 7.6 KB | 首页标题字母拆分 |
| `CustomEase.min.js` | 7.0 KB | 备用 |
| `DrawSVGPlugin.min.js` | 4.2 KB | 备用（见 §七 未决） |
| `ScrollToPlugin.min.js` | 4.0 KB | 备用 |
| `CustomWiggle.min.js` | 2.2 KB | 备用 |

**为什么必须本地化（不是偏好，是硬约束）**：
项目有「**离线演示铁律**」（`docs/早先的内部记录` §1 第 6 条）
与「不跑云端模型」。CDN 引 GSAP ⇒ 现场演示断网时动效全灭。
⇒ 这条已由守门固定下来：`tools/check_local_assets.py` 的 ② 组
「**GSAP 只许本地引**」，任何页面出现含 `gsap` 的外部 URL 即失败。

---

## 三、方案（结合项目现状）

**起点现状**（`FRONTEND_DISCUSSION_PREP.md` ① 的原始盘点）：
只有三类动效来源 —— WebGL 光场（`lib/atmosphere.js` / `lib/voyage.js`）、
CSS 过渡（`design.css` 的 `--ease` / `--dur`）、three.js 场景自身的动画。
**缺**：时间轴编排、滚动驱动的叙事、元素级缓动差异化。

**方案（三处落点，按"能改坏的风险"从低到高排）**：

| 落点 | 做什么 | 风险控制 |
|---|---|---|
| **A. 主视图心跳统一** | 把 `kg-nebula.html` 的裸 `requestAnimationFrame` 换成 `gsap.ticker`，让时间轴与 three 共用一个时钟 | 只替换心跳，**不碰任何渲染逻辑**；GSAP 缺失时回落到原 rAF 循环 |
| **B. 导航页首屏编排** | 揭幕遮罩（`clip-path: circle()` 收缩）+ 标题 8 个字 3D 飞入（SplitText 逐字）+ 副文案/事实/CTA 依次入场 | **防白屏**：`<head>` 里同步加 `html.js-motion`，CSS 初态只在该类下生效；任何一步失败就 `bail()` 摘掉这个类 ⇒ **最坏是"没有动效"，不可能是"白屏"** |
| **C. 滚动叙事** | 首页第 4 屏 12 个入口做成**两条轴**依次滑入；其余 11 个官方页只做"滚入视野淡入" | 导航页之外的 11 页：**只动 `opacity`**、只对**已在视口下方**的元素加隐藏态、三重兜底（见 §五） |

**两条被现状"逼出来"的技术约束**（写进代码注释，避免下次重踩）：

1. **`CSS2DObject` 的标签只能改 `opacity`** —— `CSS2DRenderer` 每帧重写
   标签的 `style.transform`，用 GSAP 补间 `x/y/scale` 会被逐帧覆盖。
2. **两个轴 ≠ 接力**：用户最担心「上面滚动完，下面还在滚动」。
   实测口径是**整条轴的结束时间**，不是首张卡片的结束时间（见 §六）。

---

## 四、实施清单（每一步都可单独回退）

锚点：`git tag pre-gsap-20260920` → `acd47f7`（**已验证**其 `web/index.html`
502 行、不含 `lib/gsap`、不含 `js-motion`）。以下 13 个提交在其之后，按时间序：

| # | 提交 | 做了什么 |
|---|---|---|
| 1 | `be1ed71` | GSAP 3.15.0 本地化到 `web/lib/gsap/`（7 个插件 / 177 KB，纯准备） |
| 2 | `d01f78a` | `web/_lab_gsap.html`：在真实浏览器里验证 9 项（版本/注册/tween/SplitText/ScrollTrigger/timeline…） |
| 3 | `c6bbab4` | 首屏入场编排（第一版，后被 4 取代） |
| 4 | `d319b0c` | 首屏升级为**标题字母 3D 飞入**；新增只读探针 `tools/_probe/_eval_on_page.py` |
| 5 | `2d90701` | **主视图心跳换成 `gsap.ticker`**（落点 A） |
| 6 | `6b0a27a` | 主视图 18 个社区标签依次淡入 |
| 7 | `9ab40be` | 首页开场**揭幕**（遮罩按 `circle()` 收缩成一点） |
| 8 | `c494aa7` | 补齐 5 个插件（共 12 个），并**实测**确认全是完整版而非 trial |
| 9 | `a0364ae` | 点击涟漪改由 GSAP 缓动驱动（`shader` 一行未动） |
| 10 | `53dc2c9` | 首页第 4 屏**两条轴**（落点 C 的导航页部分） |
| 11 | `1b80ba0` | 其余 **11 个官方页**加「稳妥版」动效（落点 C 的其余页部分） |
| 12 | `c33e79a` | 守门扩面：设计系统 ② 原先**不扫任何外部 CSS**（B2 关闭）· 顺带修掉错报行号 · 删掉一处过期第二真源 |
| 13 | `06275f0` | 把 GSAP 真正纳入守门：离线检查扩到**导航页** + 新增「本地引用存在性 + GSAP 只许本地引」 |

**回退梯子**（三级，越靠后越彻底）：

```bash
git checkout pre-gsap-20260920 -- web/index.html          # 只退一个文件
git reset --hard pre-gsap-20260920                        # 整条分支回到动效之前
git checkout -b <新分支名> pre-gsap-20260920               # 最干净：另起一条不含动效的分支
```

---

## 五、导航页之外 11 个页面的"稳妥版"做法

先派只读子代理把 12 个页面的 `position:fixed/sticky`、`<main>` 直接子元素、
**被 JS 管着 `opacity` 的元素**、`body > *` 层叠规则、`reduced-motion` 覆盖情况
全部查了一遍。五条结论直接改掉了原本的做法：

| 审计发现 | 对实现的影响 |
|---|---|
| `upload` / `kg-nebula` / `pathqa` / `bipartite` 四页 body `overflow:hidden`，**没有页面滚动** | 这 4 页改用**纯 CSS 落屏淡入**，滚入视野对它们没有意义 |
| 4 页有 `body > *:where(:not(#atmo)):where(:not(#veil)){position:relative;z-index:1}` | **不拿 body 直接子元素当目标**（再叠 `opacity` 会新建层叠上下文，扰动 fixed 浮层叠放） |
| `#demoRail` / `#a11y-home` / `#a11y-skip` 是**脚本运行时注入**的 fixed 元素，源码里 grep 不到 | 排除清单里必须写上它们（`#a11y-skip` 还是 body 的**第一个**子元素） |
| `transform` 会新建 containing block：`kg-nebula` 有 17 个 fixed 浮层；`paper-table` 的 `th`、`method-limits` 的 `.bar` 是 sticky | **只动 `opacity`**，不碰 `transform` / `filter` / `will-change` / `contain` |
| `bipartite` / `temporal` / `diff` **不引任何外部 CSS** | `reduced-motion` 降级必须自带，不能假设 `a11y.css` 会加载 |

审计还点出一条要命的：`a11y.css` 与 `design.css` 的 `prefers-reduced-motion` 兜底
**只盖得住 CSS transition / animation**，用 JS 改 `opacity` 是拦不住的
⇒ `motion.js` 自己判一次 `matchMedia`，`motion.css` 再补一层 `!important`。

**实现文件**：`web/lib/motion.css` + `web/lib/motion.js` +
一次性幂等迁移脚本 `tools/_apply_motion_tags.py`（默认干跑，`--apply` 才写）。

**三重兜底**（目的是让"永久看不见"不可能发生）：
① 初始化整体 `try/catch`，抛错即把已隐藏的全部揭开；
② `window` 的 `error` 事件 → 全量揭开；
③ 2.5 秒定时器：`IntersectionObserver` **一次回调都没发生**（说明它没工作）→ 全量揭开；
否则只揭开"此刻本应可见却还藏着"的个别元素。另有 `MutationObserver`
负责"骨架先出、数据后填"的页面（12 秒后自动断开）。

---

## 六、验证证据（读数与复算命令）

### 6.1 首屏编排（导航页）

`web/_lab_gsap.html` 自检项全过（含 `gsap.version` · `registerPlugin` ·
core tween · `SplitText` 拆分 · `ScrollTrigger.create` · `gsap.timeline`，
以及本地插件**逐项点名**）；探针另读数：`gsap.version = 3.15.0`、
`SplitText` / `ScrollTrigger` 均为 `function`、标题拆成 **8 个字**
（`KG` 2 + `COSMOS` 6）。

### 6.2 主视图心跳（落点 A）

`kg-nebula.html` 的 `animate()` 不再自调 `requestAnimationFrame`，
改为 `gsap.ticker.add(animate)`；GSAP 缺失时回落到原循环。

### 6.3 两轴滚动（导航页第 4 屏，用户最担心的那点）

滚到第 4 屏后每 100ms 采一次两轴首张卡片的 `opacity`：

| 时间 | 上轴首张 | 下轴首张 |
|---|---|---|
| 235ms | 0.5702 | **0** |
| 443ms | 0.9646 | **0** |
| **536ms** | **0.9939（还在动）** | **0.4568（已开始）** |
| 824ms | 1 | 0.6854 |
| 1342ms | 1 | 0.7000 |

**按整条轴看才是关键**：上轴整体 `0 → 0.995s`，下轴 `0.14 → 1.135s`
⇒ **整体只差 0.14 秒、重叠 0.86 秒**。
真做成"接力"的话下轴要到 **1.99 秒**才结束（差整整 1 秒）。

### 6.4 其余 11 页的滚入淡入

探针 `tools/_probe/_js_motion_check.js`（先读初态 → 滚到底 → 再读末态）：

| 页面 | 初始 `.mo`（已隐藏） | 滚到底后 `.mo` | 隐形残留 | 非 1 的 `opacity` |
|---|---|---|---|---|
| `analytics.html` | 13 | 0 | 0 | 0 |
| `temporal.html` | 2 | 0 | 0 | 0 |
| `method-limits.html` | 7 | 0 | 0 | 0 |
| `papers.html` | 7 | 0 | 0 | 0 |

- `method-limits` 那 7 个目标**全是 JS 注入的** `section.group`
  ⇒ `MutationObserver` 那条路径确实生效。
- 渐变是真的（`analytics` 的 `#s-six`，每 80ms 一次）：
  `0 → 0.247 → 0.632 → 0.814 → 0.942 → 0.995 → 1`（约 640ms），
  随后 class 被摘干净。
- **减少动效负向测试**（`EVAL_REDUCED=1`，先改媒体再强制重载 —— 不重载测到的是假绿）：
  `analytics` 16 块 / `method-limits` 9 块 / `papers` 19 块，`.mo` 计数 **0**、隐藏块 **0**。

### 6.5 离线与守门

- `tools/cdp_verify_offline.py`：星云页 + **导航页**双双通过；
  导航页 `window.__ready = True` · `canvas = 1` · **外部资源请求 0 个**。
- `tools/check_local_assets.py`：**39 个页面 / 192 条相对引用全部存在**；
  `web/lib/gsap/` 12 个插件；**无任何页面从外部引 GSAP**。
- `tools/check_design_system.py`：扩面后扫 **38 个页面 + 6 个外部 CSS**，两条判据全过
  （扩面当刻就抓到 `voyage.css` 一处真违规，详见 `docs/NEXT_STEPS.md` §7.17）。

### 6.6 全量回归

`python tools/verify_all.py --fast` → **66 / 66**（2026-09-20，提交 `06275f0` 之后）。
其中与本工作直接相关的三项：
「本地引用存在性 + GSAP 只许本地引」、「主视图与导航页离线可用（屏蔽外部域名）」、
「全站可访问性（键盘可达 · 回导航页 · **动效可关**）」。

复算命令：

```bash
python tools/verify_all.py --fast
python tools/check_local_assets.py
python tools/cdp_verify_offline.py web/kg-nebula.html web/index.html
```

---

## 七、未决与边界（写清楚，免得下次当成"已做"）

1. **首页卡片那条"轴"的轨道线**：`--rail` 已接上（见 §八.1），现在**是**"轴"的可见载体。
   仍可再进一步的是把它做成**连续不断**的一条线（现在 6 段之间有 26px 的网格空隙，
   看起来是"沿轴的 6 段刻度"而不是一条实线）—— 需要把除最后一张之外的
   `::after` 向右伸出 26px，而响应式断点下 gutter 会变，**暂不做**，等你看过再说。
2. **`DrawSVGPlugin` 尚未使用**：曾提议给主视图跨社区纤维做"生长"动画，
   但**纤维是 three.js 对象、不是 SVG**，`DrawSVG` 无法直接用 —— 要用得换做法。
3. **两条轴的对齐方式已从"错开 0.14s"改为"绑滚动位置"**：`scrub` 版下两轴
   由滚动位置直接决定，天然同步；上层只比下层早 8% 起跑（`top 88%` vs `top 80%`），
   仍然保持"上层先动、下层跟随"的层次。
4. **推送状态**：本工作全在分支 `feat/gsap-motion` 上，**尚未推到远端**
   （远端凭据不可用，见会话记录）。相对 `origin/main` 的提交数用
   `git rev-list --count origin/main..HEAD` 复算
   （**在提交 `06275f0` 上实测 360**；本文件自身那次提交会让它再加 1）。
   ⚠️ 这个数**不是**"领先多少个提交"的成果断言 —— `origin/main` 停在 09-11，
   中间隔着一整条 2D 探索线，把 360 当成"我做了 360 个提交"是错的。
5. **`tools/_probe/_verify_nav_layers.py` 带 UTF-8 BOM**（既有的，非本次引入）：
   Python 能跑，但 `ast.parse(open(...,encoding='utf-8'))` 会报
   `invalid non-printable character U+FEFF`。本次未动，仅记录。
6. **涟漪的"快速荡开 + 亮得久"做不到**（除非动 shader）：见 §八.2，
   shader 里年龄同时是半径与亮度的自变量，二者被绑死。

---

## 八、2026-09-20 返工记录：用户说"看着一模一样，一点没变"

用户截图后原话：

> 「不是我让你做的那个导航页，那两个轴你做了吗？我看你没做啊，还是说咱俩理解的
> 不一样呢？我请问。为什么我这边看着还是原来的模样，一模一样，一点没变呢？
> 我给你截个图，好吧。之前让你做的那个点击，你也没做吧？」

**先说结论：代码确实在、也确实跑了，但我做的两件事都有实质问题，
用户的两条质疑都成立。** 逐条对账：

### 8.1 "两个轴" —— 我做的是"一次性滑入"，不是"轴"

| | 第一版（被否掉） | 现在 |
|---|---|---|
| 触发方式 | `scrollTrigger: { once: true }` | `scrub: 0.45`（**绑定滚动位置**） |
| 效果 | 播一次就结束，**结束即无痕** | 往下滚推进、**往回滚退回**、停下就停住 |
| 可见载体 | **没有**（轨道线被跳过了） | 卡片顶部 2px / 1px 暖金线（`--rail`）随滚动生长 |
| 卡片位移 | `translateY(26px)` | `translateY(42px)` |

**为什么静态截图必然看不出差别**：`once` 版是一次性过程，播完就停在最终状态 ——
滚完之后画面上与"没有动效"逐像素相同。**所以"截图一模一样"是必然结果，
不是错觉**；但从"看着没变"这个观察出发的怀疑方向是对的：
**用户要的是"跟轴似的滚动"，而绑定滚动位置才是那个东西。**

### 8.2 点击涟漪 —— 我把它改弱了约 4.8 倍（这是真错误）

派只读子代理把 `voyage.js` 与 shader 查了一遍，结论（带行号）：

```glsl
165:   for (int i = 0; i < 3; i++){
166:     vec4 r = u_r[i];
167:     if (r.w > 0.0 && r.z < 2.2){
168:       float dd = length(c - r.xy);
169:       float ring = exp(-28.0 * abs(dd - r.z * 0.55)) * exp(-1.8 * r.z) * r.w;
170:       col += mix(cyan, gold, warm) * ring * 0.5;
```

**年龄 `r.z` 同时是"环的半径"（`r.z*0.55`）和"亮度衰减"（`exp(-1.8*r.z)`）的自变量。**
我给它套了一层 `expo.out`（以为那是"快速荡开、缓慢停下"），后果是：

| 点击后 | 旧（expo.out）年龄 | 旧峰值亮度 | 新（线性 3.6s）年龄 | 新峰值亮度 |
|---|---|---|---|---|
| 0.05 s | 0.32 | 0.281 | 0.031 | 0.473 |
| 0.22 s | **1.09** | **0.070** | 0.134 | **0.393** |
| 0.44 s | 1.65 | 0.026 | 0.269 | 0.309 |
| 0.91 s | — | — | 0.556 | 0.184 |

⇒ 环在约 0.15 秒就撞到屏幕内切圆（可见行程 `z: 0→0.91`）、**全程约 12 帧**，
0.22 秒时峰值只剩 1/4.8；再叠上我当时加的 `amp` 二次淡出（0.55s 起），
就是"双重变暗"。**观感是贴着鼠标极快地微闪一下，不是荡开一圈。**

**修法**（`voyage.js`，并把理由写进代码注释）：
`.to(r, { age: 2.2, duration: 3.6, ease: 'none' })` —— 线性、拉长到 3.6 秒，
**去掉 amp 二次淡出**。并明确写死一条禁令：
**这个 age 上不许用前段压缩型缓动**（`expo.out` / `power4.out` / `back.out`），
它们每一个都会原样重现同一个 bug。

**实测（`tools/_probe/_js_ripple_check.js`，点一下再逐档读 `r.age`）**：
期望速率 `2.2/3.6 = 0.611/s`，实测 `0.649 / 0.627 / 0.600 / 0.611 / 0.615 / 0.613 / 0.612`，
年龄单调递增、强度全程 1.00。
⇒ 环在屏幕内的可见行程从约 **0.20 秒**变成约 **1.49 秒**（7.5 倍），
同时刻亮度约 **7.4 倍**。

### 8.3 顺带查出的第三条：**点击涟漪此前没有任何测试覆盖**

`grep -r "ripple|spawnRipple|u_r"` **只命中 `web/lib/voyage.js` 自己** ——
`tools/cdp_verify_nav.py` 的量/帧/`u_t` 三个断言一个都碰不到它。
所以"涟漪弱了 4.8 倍"这件事**不可能被回归发现**，只能靠用户肉眼。

两处补救：
1. `voyage.js` 暴露只读快照 `window.__voyageRipple()`（纯读，不改行为）；
2. `tools/cdp_verify_nav.py` 新增 **2b-2 点击涟漪**断言：
   ① 点下去必须真的产生 1 个涟漪；② 年龄必须单调递增；
   ③ **年龄推进速率必须落在 `[0.35, 0.95]`** —— 这一条正是"前段压缩型缓动"的捕手
   （线性是 0.611/s；`expo.out` 会在 0.2 秒内冲到 1.0+/s）。

**读数**：`cdp_verify_nav.py` → 数量 1 · 年龄 0.774 → 1.538（速率 **0.637/s** · 期望 0.611）·
强度 1.00 · 全部通过。

**负向测试**（先证明这条断言真的会红）：
把那一行临时改回 `duration: 2.2, ease: 'expo.out'` ⇒ 守门输出
`年龄 2.169 → 2.200（速率 0.026/s）` 并 **`未通过 1 项` · `exit 1`**；
还原后立刻回到 `0.637/s · 全部通过 · exit 0`。

### 8.4 第四条：**"减少动效"这个偏好会把整站动效安静关掉**（最可能的现场原因）

这条是并列的头号嫌疑，而且**页面上一点异常都看不出来**：

| 位置 | 行为 |
|---|---|
| `index.html` 编排脚本 | `if (matchMedia('(prefers-reduced-motion: reduce)').matches) { bail(); return; }` ⇒ 摘掉 `js-motion`、整页零动效 |
| `lib/motion.js`（11 个页面） | 同样提前 `return` ⇒ 一个元素都不淡入 |
| `voyage.js:32 / 362` | `REDUCED` ⇒ **点击连涟漪都不生成**；时间还被冻结（只随滚动重绘） |

⇒ **用户看到的现象与"代码完全没生效"完全一致。**
处理办法（三层）：

1. **`?motion=force` 开关**：`index.html`、`lib/motion.js`、`lib/voyage.js`
   三处都加了 —— 显式带参数时忽略该偏好。
   `voyage.js` 里把"是否抑制动效"统一收敛成一个 `MOTION_OFF = REDUCED && !FORCE_MOTION`
   （原先 9 处各写各的 `REDUCED`，改一次要改九个地方）。
   **普通访问永远尊重偏好**，这个参数只用于诊断"代码没生效"与"偏好按设计关掉了"
   这两种在页面上看起来一模一样的情况。
2. **`web/_lab_motion_diag.html`**（内部诊断页，`_lab_` 前缀、不进正式页名单）：
   一屏给出人话结论 + 环境表（偏好是否开着 / `js-motion` 有没有 / GSAP 三个库
   是否加载 / 6 个本地文件是否 200 / 地址参数）+ 一排"强制动效版"直达按钮，
   并且在页内**复刻一次"两条轴"**，让你不离开这页就能看到那个效果。
3. **实测两种情形**（诊断页 + 涟漪，都用 CDP 模拟"系统开了减少动画"再强制重载）：

| 情形 | 期望 | 实测 |
|---|---|---|
| 减少动效，不带参数 | 不该有动效（尊重偏好） | 点击后涟漪数 **0** · 诊断页结论正确指出"是这个偏好关掉的" |
| 减少动效，带 `?motion=force` | 应该能动 | 涟漪数 **1**、年龄单调递增 · 诊断页照常工作 |

### 8.5 这一节的教训（写给下次的自己）

**"我做了"与"用户看得见"是两件事。** 我这一轮犯的是两个不同的错：
① 把"触发一次"当成"轴"（**没有可见载体、结束即无痕**）；
② 给一个"年龄同时驱动半径与亮度"的量套了前段压缩缓动（**只看着公式漂亮，没算过亮度**）。
两个错都有一个共同点：**我验证的是"代码跑没跑"，不是"效果对不对"。**
而"效果对不对"这一半，这次是靠用户肉眼发现的 —— 靠人的地方就该补机器：
所以 8.3 那两个补救（只读钩子 + 速率断言）比修涟漪本身更重要。

---

## 九、一句话结论

**GSAP 3.15.0 已完整本地化（12 个插件 / 220.4 KB，全部非 trial）；
导航页的"两条轴"已改成绑定滚动位置（往回滚会退回）+ 补上轨道线这个可见载体；
其余 11 个页面只动 `opacity` 且带三重兜底；点击涟漪的"前段压缩缓动"已修回线性
（可见行程从 0.20 秒变成 1.49 秒）并补上速率断言；
离线检查覆盖星云页 + 导航页；新增守门保证"页面写了的本地文件必须真的在"。
全量回归 66 / 66。** 每一步都有独立提交与三级回退梯子。

**如果你（用户）仍然看不到动效，请先打开这个页面**：

```
http://127.0.0.1:8000/_lab_motion_diag.html
```

它会直接告诉你是不是「减少动画」偏好把整站动效关掉了，并给你一排
`?motion=force` 的直达链接 —— 带上参数就能强制播放，用来区分
"代码没生效"与"偏好按设计关掉了"。这页里还复刻了一次"两条轴"，当场就能看到。
