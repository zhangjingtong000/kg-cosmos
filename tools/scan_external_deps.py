"""扫描 web/ 下所有页面的**外部**依赖（CDN 等），为离线化做准备。

背景：`docs/早先的内部记录` §1 第 6 条是「离线演示铁律」，
但主视图 `kg-nebula.html` 的 three.js 走 unpkg CDN。
`tools/cdp_verify_offline.py` 已实测断网后该页完全无法启动。
"""
import re
from pathlib import Path

WEB = Path(r'D:\kg-demo\web')
# 匹配 http(s):// 的资源引用（src / href / importmap / import 语句）
PAT = re.compile(r'https?://[^\s"\'<>()]+')


def main():
    rows = []
    for f in sorted(WEB.glob('*.html')):
        txt = f.read_text(encoding='utf-8', errors='replace')
        urls = set()
        for m in PAT.finditer(txt):
            u = m.group(0).rstrip('.,;')
            # 排除纯文档链接（在注释或正文里出现的说明性 URL）
            if any(k in u for k in ('unpkg.com', 'cdn.jsdelivr.net', 'cdnjs.cloudflare.com',
                                    'esm.sh', 'googleapis.com', 'fonts.gstatic')):
                urls.add(u)
        # 判断是否真的被当作资源加载：出现在 src= / href= / importmap / import 里
        real = set()
        for u in urls:
            for pat in (f'src="{u}"', f"src='{u}'", f'href="{u}"', f"href='{u}'",
                        f'"{u}"', f"'{u}'"):
                if pat in txt:
                    real.add(u)
                    break
        if real:
            rows.append((f.name, sorted(real)))

    if not rows:
        print('没有页面依赖外部 CDN')
        return
    print(f'{len(rows)} 个页面依赖外部资源：\n')
    for name, urls in rows:
        print(f'  {name}')
        for u in urls:
            print(f'      {u[:104]}')
    # 汇总出现的域名/包，便于一次性本地化
    from collections import Counter
    pkgs = Counter()
    for _, urls in rows:
        for u in urls:
            m = re.search(r'(?:unpkg\.com|cdn\.jsdelivr\.net/npm|cdnjs\.cloudflare\.com/ajax/libs)/([^/@]+)', u)
            if m:
                pkgs[m.group(1)] += 1
    print('\n涉及的包：')
    for p, c in pkgs.most_common():
        print(f'  {p}  (被 {c} 处引用)')


if __name__ == '__main__':
    main()
