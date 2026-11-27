# 仓库卫生待决清单

> 审计提到「仓库卫生：`tools/` 143 个 py、一批一次性探针脚本、
> `data/quality` 727 MB」。
>
> 处理原则：**属于我的产物我可以处理；别人的产物只记录、不擅自删。**
> 删别人的东西属于判定，不在"自主推进"的范围内。
>
> 盘点时间 2026-09-14。

---

## 1. 已处理

| 项 | 处理 | 理由 |
|---|---|---|
| `web/assets/voyage-chain-full.mp4`（1.47 MB） | **删除** | 我自己用 Wan2.2 生成的星云视频。导航页方案后来改成 WebGL 光场，这个文件**零引用**，且是我自己的产物 |
| 一次性探针目录（早期用于实验的临时脚本目录） | **已移除** | 随仓库瘦身一并删除；可复跑的结论已落在 `tools/` 下的正式脚本与 `data/quality/` 产物里 |
| `docs/nav-handoff.md`、`docs/nav-handoff-codex.md` | **提交** | 给 GLM / Codex 的交接文档，记录了"怎么把一个设计任务交出去"，有价值 |
| 早期一次性分析脚本（`_diag_*`、`_explore_zcode*` 等） | **已移除** | 随探针目录一并清理；需要时按 `docs/` 里记录的方法重写 |
| `tools/check_nebula_video.py` | **提交** | 检查生成视频的构图/色彩，仍在用 |
| `web/_lab_voyage_gl.html` | **提交** | 着色器试验页，记录了"抽象光场"这条技术路线 |

---

## 2. 只记录、不处理（**别人的产物，需你决定**）

| 文件 | 大小 | 情况 | 我的建议 |
|---|---|---|---|
| `web/assets/kg-crab.jpg` | 1.54 MB | 零引用，时间戳 09-13 22:59（Codex/GLM 那轮产出） | 若确认不用可删；我没动 |
| `web/assets/kg-trifid.jpg` | 0.98 MB | 同上 | 同上 |
| `web/assets/nebula-aperture-loop.mp4` | 2.05 MB | 零引用，09-13 21:47 | 同上 |
| `web/benchmark/test/voyage-video.test.mjs` | 0.4 KB | **未跟踪**。它测的是"voyage 视频"方案，而那个方案已经废弃（导航页改成 WebGL 了） | 要么删，要么明确标为"测废弃方案" |

**为什么不动**：这四个都是**别的 agent 那轮工作的产物**。
零引用不等于没用（可能是备选素材）。删掉它们属于内容决策，不是卫生问题。

---

## 3. `web/benchmark/` —— **不要删**

盘点时一度以为它是遗留垃圾，查了 git 才发现它有**三个正经提交**：

```
8c2ba93 fix: isolate benchmark long-task measurements
6abda0d fix: report benchmark renderer pixel ratio
278c4fa feat: add interactive KG performance benchmark
```

它是**交互式 KG 性能基准测试页**（10 个文件 / 17.4 KB），
对应 `docs/PERFORMANCE_RESULTS.md` 与 `tools/verify_all` 里的性能相关项。

**结论：这是功能，不是垃圾。** 顺便说明为什么"零引用"不能当作删除依据 ——
一个页面可以只靠 URL 直接访问，不需要被别处 import。

---

## 4. 数据面体积（**不建议动**）

`data/quality/` 约 727 MB。它是：
- 六层评测的**全部产物**（`docs/verification.md` 里每条证据都指向这里）
- `verify_all` 的"数据新鲜度"检查的对象
- `docs/KNOWN_DATA_DEFECTS.md` 所有数字的来源

**删任何一份都会让台账里的证据路径失效。**
如果确实要瘦身，应当先归档到仓库外，并在 `verification.md` 里改写证据路径 ——
那是另一个决策，不是卫生问题。

---

## 5. 一条待你决定的

`.gitignore` 对一次性脚本的覆盖规则已不再是待决项 —— 临时脚本目录已随仓库瘦身移除。
若将来再引入临时脚本，建议统一放 `tools/_scratch/` 并加入 `.gitignore`。
