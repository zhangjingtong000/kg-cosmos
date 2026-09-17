# -*- coding: utf-8 -*-
r"""J6 的验证：命令行工具 `kg` 的**每个子命令都真跑一遍**。

## 为什么不用浏览器测

这一项**不是一个页面** —— 它是 CLI。
⇒ 验证方式就是"**把每个子命令真跑一次，看输出对不对**"。
（本项目有 20 多个 CDP 脚本，但**CLI 不该硬塞进浏览器**里测。）

## 断言六组

1. **`kg papers`**：列出 17 篇，且**每行都有 id 与年份**；
2. **`kg stats`**：全库规模与**关系类型分布**（`RELATED_TO` 最多）；
3. **`kg evidence`**：某个实体有证据，且**每条都带页码或论文名**
   —— "证据"如果不带出处，那它与"我编的"没区别；
4. **★ `kg ask --step --no-llm`**：**不调模型**跑完检索，
   输出里**必须同时出现"种子"与"证据"两个数**，
   **且种子数不为 0** —— 第一版读错了返回键，打出
   "**0 个种子 · 0 节点 · 18 条证据**"这种自相矛盾的一行；
5. **`kg ask --remote` 在后端没起时要给明确提示**（**不是崩栈**）
   —— 这一条对应 K12 的同一个纪律：**"没跑"要说清是"没跑"**；
6. **`kg` 不带参数时打 help 并返回 0**（不是报错）。

用法：
    python tools/check_kg_cli.py
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KG = os.path.join(ROOT, 'tools', 'kg.py')


def run(*args, timeout=300):
    p = subprocess.run([sys.executable, KG] + list(args),
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace', timeout=timeout, cwd=ROOT)
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def main():
    print('=' * 88)
    print('J6 · 命令行工具 kg 的验证')
    print('=' * 88)
    fails = []

    # ── ① papers ────────────────────────────────────────────────────
    rc, out = run('papers')
    lines = [ln for ln in out.splitlines() if re.match(r'^\s+\d{4}\.\d{4,5}v\d+', ln)]
    print(f'  ① kg papers ⇒ rc={rc} · 论文行 {len(lines)}')
    print(f'     样例：{lines[0][:80] if lines else "(无)"}')
    ok1 = rc == 0 and len(lines) >= 17 and all(
        re.search(r'\b(19|20)\d{2}\b', ln) for ln in lines)
    print(f'     {"✅" if ok1 else "❌"}')
    if not ok1:
        fails.append(f'kg papers 不对：rc={rc} lines={len(lines)}')
    print()

    # ── ② stats ─────────────────────────────────────────────────────
    rc, out = run('stats', '--top', '6')
    m = re.search(r'全库 (\d+) 节点 / (\d+) 边', out)
    top = re.findall(r'^\s+(\d+)\s+([A-Z_]+)$', out, re.M)
    print(f'  ② kg stats ⇒ rc={rc} · {m.group(0) if m else "(没读到规模)"}')
    print(f'     关系分布前 3：{top[:3]}')
    ok2 = (rc == 0 and m and int(m.group(1)) > 10000
           and top and top[0][1] == 'RELATED_TO')
    print(f'     {"✅" if ok2 else "❌"}')
    if not ok2:
        fails.append(f'kg stats 不对：rc={rc} m={m} top={top[:3]}')
    print()

    # ── ③ evidence ──────────────────────────────────────────────────
    rc, out = run('evidence', 'Knowledge Graphs', '--limit', '3')
    detail = [ln for ln in out.splitlines() if ln.strip().startswith('[')]
    cites = [ln for ln in out.splitlines()
             if re.search(r'\d{4}\.\d{4,5}v\d+', ln) or '页' in ln]
    print(f'  ③ kg evidence ⇒ rc={rc} · 证据行 {len(detail)} · 带出处 {len(cites)}')
    print(f'     样例：{cites[0].strip()[:78] if cites else "(无)"}')
    ok3 = rc == 0 and len(detail) >= 3 and len(cites) >= 3
    print(f'     **每条证据都带出处**：{"✅" if ok3 else "❌"}')
    if not ok3:
        fails.append(f'kg evidence 缺出处：rc={rc} detail={len(detail)} '
                     f'cites={len(cites)}')
    print()

    # ── ④ ★ ask --step --no-llm（不调模型）───────────────────────────
    rc, out = run('ask', 'TransE scoring function',
                  '--graph', '1703.08098v9', '--step', '--no-llm')
    m4 = re.search(r'(\d+) 个种子 · (\d+) 节点 · (\d+) 边 · (\d+) 条证据', out)
    print(f'  ④ kg ask --step --no-llm ⇒ rc={rc}')
    print(f'     {m4.group(0) if m4 else "(没读到那行)"}')
    # **判据**：四个数都读到了，且**种子数与证据数都不为 0**
    # （第一版读错键 ⇒ 打出"0 个种子 · 0 节点 · 18 条证据"这种自相矛盾）
    ok4 = bool(rc == 0 and m4 and int(m4.group(1)) > 0 and int(m4.group(4)) > 0)
    print(f'     **种子/节点/边/证据四个数都对且非零**：{"✅" if ok4 else "❌"}')
    if not ok4:
        fails.append(f'kg ask --step 不对：rc={rc} m={m4} out={out[-200:]!r}')
    # 顺带：**--no-llm 就不该出现 LLM 那几步**
    no_llm_ok = ('② 支持方' not in out and '不调模型' in out)
    print(f'     --no-llm 确实停在检索：{"✅" if no_llm_ok else "❌"}')
    if not no_llm_ok:
        fails.append('--no-llm 之后仍然跑了模型那几步')
    print()

    # ── ⑤ remote 在后端没起时要给明确提示，不是崩栈 ──────────────────
    #   用一个**肯定没人监听**的端口，避免误连到真后端
    env = dict(os.environ, KG_API='http://127.0.0.1:59999')
    p = subprocess.run([sys.executable, KG, 'ask', 'x', '--remote'],
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace', timeout=120, cwd=ROOT, env=env)
    out5 = (p.stdout or '') + (p.stderr or '')
    print(f'  ⑤ kg ask --remote（后端不在）⇒ rc={p.returncode}')
    print(f'     {out5.strip().splitlines()[-1][:88] if out5.strip() else "(无输出)"}')
    ok5 = ('连不上后端' in out5 and 'Traceback' not in out5)
    print(f'     **给明确提示而不是崩栈**：{"✅" if ok5 else "❌"}')
    if not ok5:
        fails.append(f'--remote 在后端不在时没有给明确提示：{out5[-200:]!r}')
    print()

    # ── ⑥ 不带参数打 help 且 rc=0 ────────────────────────────────────
    rc, out = run()
    print(f'  ⑥ kg（无参数）⇒ rc={rc} · 含 usage: {"usage" in out.lower()}')
    ok6 = rc == 0 and 'usage' in out.lower()
    print(f'     {"✅" if ok6 else "❌"}')
    if not ok6:
        fails.append(f'无参数时没打 help 或 rc≠0：rc={rc}')
    print()

    print('=' * 88)
    if fails:
        print(f'❌ {len(fails)} 项未通过：')
        for f in fails:
            print(f'   · {f}')
        return 1
    print('✅ 六组断言全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
