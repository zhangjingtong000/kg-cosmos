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
| ② | Cinematic 3D Scroll（滚动驱动的电影式推进） | 首页第 4 屏 12 个入口做成"跟轴似的滚动" | 「**有用**」；但要 **"跟轴似的滚动"**，**不要**"推进/放大"那一半 | ✅ `53dc2c9`：上下两层各一条轴依次滑入（**没有**做 zoom 推进） |
| ③ | Telescope Zoom（滚轮拉近镜头） | 主视图滚轮推拉 | 「**不要**」 | ✅ 未做；且 `kg-nebula.html:2384` 原有注释记着「相机补间已移除：进入/退出都直接落位」 |
| ④ | 《WebGL Shaders with GSAP》（把 GSAP 的缓动喂给 shader 参数） | 用 GSAP 缓动替换首页光场的**点击涟漪** | 「**用**」 | ✅ `a0364ae`：涟漪改由 GSAP 时间轴驱动 `age`/`amp`，**shader 一行未动** |

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
与「不跑云端模型」。CDN 引 GSAP ⇒ 演示现场断网时动效全灭。
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

1. **首页卡片上那条"轴"的轨道线还没画**：`.entries.flow .entry::after` 是
   hover 时 `scaleX(0→1)` 的暖金短线。想在入场时**画出来**，
   因为 GSAP 不能直接补间伪元素，需要一层 CSS 变量间接
   （`.entry::after{transform:scaleX(var(--rail,0))}`），**目前刻意没做**。
2. **`DrawSVGPlugin` 尚未使用**：曾提议给主视图跨社区纤维做"生长"动画，
   但**纤维是 three.js 对象、不是 SVG**，`DrawSVG` 无法直接用 —— 要用得换做法。
3. **时间轴"多轴对齐"只做到 0.14s 错开**：如果观感上还嫌不够齐，
   把 `53dc2c9` 里的 `delay: 0.14` 改小即可，**参数只有一个**。
4. **推送状态**：本工作全在分支 `feat/gsap-motion` 上，**尚未推到远端**
   （远端凭据不可用，见会话记录）。相对 `origin/main` 的提交数用
   `git rev-list --count origin/main..HEAD` 复算
   （**在提交 `06275f0` 上实测 360**；本文件自身那次提交会让它再加 1）。
   ⚠️ 这个数**不是**"领先多少个提交"的成果断言 —— `origin/main` 停在 09-11，
   中间隔着一整条 2D 探索线，把 360 当成"我做了 360 个提交"是错的。
5. **`tools/_probe/_verify_nav_layers.py` 带 UTF-8 BOM**（既有的，非本次引入）：
   Python 能跑，但 `ast.parse(open(...,encoding='utf-8'))` 会报
   `invalid non-printable character U+FEFF`。本次未动，仅记录。

---

## 八、一句话结论

**GSAP 3.15.0 已完整本地化（12 个插件 / 220.4 KB，全部非 trial），
三处落点全部落地，导航页之外的页面只动 `opacity` 且带三重兜底，
离线检查已覆盖导航页，新增守门保证"页面写了的本地文件必须真的在"，
全量回归 66 / 66。** 每一步都有独立提交与三级回退梯子。
