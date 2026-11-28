"""把导航页与各页面的用字提取出来，向 Google Fonts 请求**只含这些字**的子集。

【为什么不用完整中文字体】
  思源宋体完整版 10 MB+（还被 Google 切成 100 多片，每片一个 unicode-range）。
  而本项目的汉字总量很小（页面标题 + 说明文字），用 `&text=` 参数可以让
  Google Fonts 直接返回**只含这些字**的字体——通常几十 KB。

  这一步同时满足三个约束：艺术字体、离线（下载到本地）、体积可控。

【注意】
  字体会随文案变化而失效（新增的字不在子集里会回退到系统字体）。
  所以这个脚本要能重跑——文案改了就再跑一次。已挂进 RUNBOOK。
"""
import re
import json
import urllib.parse
import urllib.request
from pathlib import Path

# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
_HERE = Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 都在根目录下一层
WEB = ROOT / 'web'
FONTS = WEB / 'lib' / 'fonts'
FONTS.mkdir(parents=True, exist_ok=True)
PROXY = 'http://127.0.0.1:7897'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

# 参与子集化的页面（导航页 + 主要内页）
PAGES = ['index.html', 'analytics.html', 'method-limits.html',
         'pathqa.html', 'refs.html']

opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({'http': PROXY, 'https': PROXY}))


def collect_chars():
    """只收集**标题类元素**里的汉字。

    为什么不收正文：
      · 设计上，艺术字体只该用在标题——正文用系统无衬线更易读；
      · 工程上，正文汉字有 500+ 个，拼进 `&text=` 会让 URL 超过 4 KB，
        Google Fonts 直接返回 400（这是实测踩过的坑）。
    只取标题后约 60–90 个字，URL 短、字体小。
    """
    chars = set()
    for name in PAGES:
        p = WEB / name
        if not p.exists():
            continue
        t = p.read_text(encoding='utf-8', errors='replace')
        t = re.sub(r'<script[\s\S]*?</script>', ' ', t, flags=re.I)
        t = re.sub(r'<style[\s\S]*?</style>', ' ', t, flags=re.I)
        t = re.sub(r'<!--[\s\S]*?-->', ' ', t)
        # 抓 h1 / h2 / h3 的内容，以及 .kick / .eyebrow 那类小标题
        # 注意：两个分支的捕获组编号不同（h 标签是 1/2，class 是 3）
        for m in re.finditer(
                r'<(h1|h2|h3)[^>]*>([\s\S]*?)</\1>'
                r'|class="(?:kick|eyebrow)"[^>]*>([\s\S]*?)<',
                t, re.I):
            seg = m.group(2) or m.group(3) or ''
            seg = re.sub(r'<[^>]+>', '', seg)
            for ch in seg:
                if '\u4e00' <= ch <= '\u9fff':
                    chars.add(ch)
    # 标题里会用到的标点与符号
    for ch in '·—…「」（）《》、，。：；！？％':
        chars.add(ch)
    # ⚠️ **必须收数字与拉丁字母**：
    #   Playfair Display 是**拉丁字体**。上一版只把汉字放进 `&text=`，
    #   Google 对这个字体就返回了"近似空字体"——落盘的 playfair-*-0.woff2
    #   只有 1164 B / 4 个字形（仅 ·—…），标题里的数字与英文全都不生效。
    #   而页面里到处是数字（63.7%、18780、κ = 0.722），所以这一条是硬需求。
    chars.update('0123456789')
    chars.update('ABCDEFGHIJKLMNOPQRSTUVWXYZ')
    chars.update('abcdefghijklmnopqrstuvwxyz')
    chars.update('.,:;%()[]{}<>/\\-–—+±×÷=≈≠~^*&#@!?\'"°μκ')
    return ''.join(sorted(chars))


def fetch_css(family, weights, text):
    """向 Google Fonts 取子集 CSS。

    ⚠️ 这里**不能**用 urllib.parse.urlencode：
      它会把空格编成 `+`、把 `:` `@` `,` 编成 `%3A %40 %2C`，
      而 Google Fonts 只接受 `+` 作字面量、`%20` 作空格、`:` `@` `,` 原样。
      实测 urlencode 会直接返回 400。所以手工拼，只对 text 做百分号编码。
    """
    fam = family.strip().replace(' ', '+')
    w = ';'.join(weights)
    url = (f'https://fonts.googleapis.com/css2'
           f'?family={fam}:wght@{w}'
           f'&text={urllib.parse.quote(text, safe="")}'
           f'&display=swap')
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with opener.open(req, timeout=60) as r:
        return r.read().decode('utf-8')


def download(url, out):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with opener.open(req, timeout=60) as r:
        data = r.read()
    out.write_bytes(data)
    return len(data)


def main():
    text = collect_chars()
    print(f'收集到 {len(text)} 个不同字符')
    chars_json = FONTS / '_subset-chars.json'
    chars_json.write_text(json.dumps({'pages': PAGES, 'chars': text},
                                     ensure_ascii=False), encoding='utf-8')

    jobs = [
        ('Noto Serif SC', ['400', '600', '700', '900'], 'notoserifsc'),
        ('Noto Sans SC',  ['300', '400', '500'],        'notosanssc'),
        ('Playfair Display', ['400', '700', '900'],     'playfair'),
    ]

    css_all = []
    for family, weights, slug in jobs:
        print(f'\n=== {family} {weights} ===')
        try:
            css = fetch_css(family, weights, text)
        except Exception as e:
            print(f'  取 CSS 失败: {e}')
            continue
        # 找到每个 @font-face 块里的 url 与 font-weight
        blocks = re.findall(r'@font-face\s*\{([^}]+)\}', css)
        print(f'  返回 {len(blocks)} 个 @font-face 块')
        # 合并同字重的多个 url（同一字重可能仍分片）
        by_weight = {}
        for b in blocks:
            w = re.search(r'font-weight:\s*(\d+)', b)
            u = re.search(r'url\((https://[^)]+)\)', b)
            if w and u:
                by_weight.setdefault(w.group(1), []).append(u.group(1))
        for w, urls in sorted(by_weight.items()):
            # 把分片按序拼成一个本地文件（woff2 不能拼接，所以只取第一片，
            # 并用 text= 请求保证一片就够；若真有多片，逐片落盘再在 CSS 里列全）
            parts = []
            for i, u in enumerate(urls):
                out = FONTS / f'{slug}-{w}-{i}.woff2'
                try:
                    n = download(u, out)
                    parts.append((out.name, n))
                except Exception as e:
                    print(f'  {w} 第 {i} 片下载失败: {e}')
            if parts:
                total = sum(n for _, n in parts)
                print(f'  字重 {w}: {len(parts)} 片, 合计 {total/1024:.1f} KB')
                # 生成 @font-face（本地路径）
                for name, _ in parts:
                    css_all.append(
                        '@font-face{font-family:"%s";font-style:normal;'
                        'font-weight:%s;font-display:swap;'
                        'src:url("./%s") format("woff2");}'
                        % (family, w, name))

    # 英文标题用 Playfair，也一并落盘
    out_css = FONTS / 'fonts.css'
    out_css.write_text(
        '/* 由 tools/build_fonts.py 生成 —— 不要手改。\n'
        '   字体已下载到本地（lib/fonts/*.woff2），页面零外部请求。\n'
        '   文案改动后请重跑：python tools/build_fonts.py */\n'
        + '\n'.join(css_all) + '\n', encoding='utf-8')
    print(f'\n已写出 {out_css}（{len(css_all)} 条 @font-face）')
    total = sum(f.stat().st_size for f in FONTS.glob('*.woff2'))
    print(f'字体总体积: {total/1024:.1f} KB')


if __name__ == '__main__':
    main()
