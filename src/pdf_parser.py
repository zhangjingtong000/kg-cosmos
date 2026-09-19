"""
pdf_parser.py — PDF text + image extraction with structure-aware chunking.

Usage:
  python pdf_parser.py input.pdf -o output_dir/
  python pdf_parser.py input.pdf --json  (print JSON to stdout)
"""

import argparse, json, os, re, sys
from pathlib import Path
from typing import Optional


# ── PDF Parsing ──────────────────────────────────────────

def is_usable_table_data(data: list[list[object]]) -> bool:
    """Reject obvious layout artifacts emitted by PDF table detection."""
    if len(data) < 2:
        return False
    width = max((len(row) for row in data), default=0)
    if width < 2 or width > 16:
        return False
    cells = [str(cell).strip() for row in data for cell in row if cell is not None]
    return sum(bool(cell) for cell in cells) >= 4


def parse_pdf(pdf_path: str, output_dir: Optional[str] = None) -> dict:
    """
    Extract text and images from a PDF file.

    Returns:
        dict with keys: pages (list of {num, text, images[]})
    """
    import fitz  # PyMuPDF

    doc = fitz.open(pdf_path)
    out = {"file": pdf_path, "total_pages": len(doc), "pages": []}

    base = Path(output_dir) if output_dir else Path(pdf_path).parent / "extracted"
    base.mkdir(parents=True, exist_ok=True)

    for pi in range(len(doc)):
        page = doc[pi]
        text = page.get_text("text")  # plain text extraction
        images = []

        # Extract embedded images
        for img_idx, img in enumerate(page.get_images(full=True)):
            xref = img[0]
            base_image = doc.extract_image(xref)
            img_bytes = base_image["image"]
            img_ext = base_image["ext"]
            img_name = f"page{pi+1:03d}_img{img_idx+1:02d}.{img_ext}"
            img_path = base / img_name
            img_path.write_bytes(img_bytes)
            images.append({
                "name": img_name,
                "path": str(img_path),
                "ext": img_ext,
                "size": len(img_bytes),
            })

        out["pages"].append({
            "num": pi + 1,
            "text": text.strip(),
            "images": images,
        })

    doc.close()

    # Extract tables using find_tables (simple detection)
    try:
        doc2 = fitz.open(pdf_path)
        tables_out = []
        for pi in range(len(doc2)):
            page = doc2[pi]
            tabs = page.find_tables()
            if tabs and tabs.tables:
                for ti, tab in enumerate(tabs.tables):
                    data = tab.extract()
                    if not is_usable_table_data(data):
                        continue
                    tables_out.append({
                        "page": pi + 1,
                        "table_idx": ti + 1,
                        "rows": tab.row_count if hasattr(tab, 'row_count') else len(tab.extract()),
                        "data": data,
                    })
        doc2.close()
        out["tables"] = tables_out
    except Exception:
        out["tables"] = []

    return out


# ── 版式噪声裁剪 ───────────────────────────────────────────
#
# 2026-09-19 加（第一版，**打补丁式**）：只删「引用本文 / 相似文章推荐」两个
#   块。`jsjkx2025-kg-rag-qa.pdf` 第 1 页抽出 15 个实体，**其中 8 个是别的
#   论文的标题**，并生成 12 条虚假 RELATED_TO 边（占全图 13%）。
#
# 2026-09-19 升级（**本次**）：实测只删那两个块**不够**，中文期刊首页的
#   「非论文内容」其实是一整片，已经漏过两次：
#     · 抽出过 `军科委国防科技重点实验室基金` / `Pre-research Key Laboratory
#       Fund for Equipment` 等**基金项目**实体；
#     · `jsjkx2025-kg-plm-survey.pdf` 的**作者单位**是
#       「国防科技大学系统工程学院」—— 资助字段干净，但**单位署名会进图谱**。
#
#   ⇒ 所以改成**白名单**：中文期刊论文首页**只保留
#       标题 + 作者 + 中文摘要 + 中文关键词 + 英文标题/摘要/关键词，
#       以及摘要之后的正文**；
#     裁掉 页眉页脚 / DOI / 到稿(收稿)日期 / 基金项目（含中英文续行）/
#       通信作者 / 作者单位 / 邮箱 / 中图分类号 / 引用本文 / 相似文章推荐。
#
#   ⚠️ **正文必须留下** —— 中文期刊的"论文首页"（本批 PDF 的**第 2 页**；
#      第 1 页是期刊封面页）在英文 Keywords 之后**直接接「1 引言」**，
#      若连正文一起裁掉，整篇的内容就没了。所以白名单是
#      "裁掉**元数据带**"，不是"只留四行"。
#
#   为什么必须在"文本提取之后、分块之前"裁（`src/pipeline.py`）：
#     **必须在进 LLM 之前**——抽完再过滤就晚了，假边已经生成、节点已经进图。
#     而且它**不是幻觉**：那些字确实印在 PDF 上，只是不属于这篇论文，
#     所以"抽查时看到真实存在的标题"抓不到它，只能靠结构化断言。
#
# 触发条件（**只对中文期刊首页生效，绝不碰英文 arXiv**）：
#   · 存在**行首**的「摘　要 / 摘要」行，且其后 40 行内出现行首「关键词」；
#   · **并且**同一页存在行首 `Abstract` 或 `Keywords`（中文期刊首页的固定版式）。
#   两者都满足才启用白名单；否则**退回到"标记块裁剪"**（第一版行为）。
#   ⇒ 英文 arXiv 首页的摘要行首是 `Abstract`（不是「摘要」），
#     **永远不触发白名单**；没有标记的页面**逐字节原样返回同一个字符串**。
#
# 安全阀：
#   · 不触发 ⇒ 走标记块分支，无标记仍是**同一个字符串对象**（逐字节不变）；
#   · 裁完为空 ⇒ **放弃裁剪**（宁可留噪声，也不把一页清空）；
#   · `KG_STRIP_LAYOUT_NOISE=0` 可整体关闭（两个分支都关），用于对照。
LAYOUT_NOISE_MARKERS = (
    "引用本文",
    "相似文章推荐",
    "Similar articles recommended",
)

# 标记行允许的后缀（"相似文章推荐（请使用火狐或 IE 浏览器查看文章）"这类）。
_MARKER_SUFFIX_LIMIT = 40

# 中文期刊首页的区块标记。注意 `摘　要` 中间是**全角空格**（U+3000），
# 所以用 `[\s\u3000]*` 兜住。
_RE_ZH_ABS = re.compile(r'^[\s\u3000]*摘[\s\u3000]*要[\s\u3000]*[:：]?')
_RE_ZH_KW = re.compile(r'^[\s\u3000]*关键词[\s\u3000]*[:：]')
_RE_EN_ABS = re.compile(r'^[\s\u3000]*Abstract\b')
_RE_EN_KW = re.compile(r'^[\s\u3000]*(?:Keywords|Key\s+words|Index\s+Terms)\b')

# 元数据行的行首标记（去空白后判断）。
_META_PREFIXES = (
    'doi', '到稿日期', '收稿日期', '返修日期', '基金项目', '资助项目',
    '通信作者', '通讯作者', '作者简介', '中图分类号', '文献标识码', '文章编号',
)

# 机构词：**单位署名的判据**（中英文各一组）。
# 为什么用"机构词"而不是"以数字开头"：实测作者行也带全角上标
# （`魏谦强１,２,３`），用"数字开头"会把作者一起删掉。
_CN_ORG_TOKENS = (
    '大学', '学院', '实验室', '研究中心', '研究所', '研究院', '科学院',
    '工程中心', '重点实验室', '军区', '医院', '公司', '编辑部',
)
_EN_ORG_TOKENS = (
    'university', 'college', 'laboratory', 'institute', 'academy',
    'schoolof', 'departmentof', 'china',
)

# 英文字段里的"基金项目"词。**只在标题之前的元数据带里启用**——
# 正文里出现 `foundation` 是正常写作，不能据此删正文行。
_EN_GRANT_TOKENS = (
    'foundation', 'fund', 'grant', 'programof', 'projectof', 'province',
    'supportedby',
)


def _squeeze_ws(line: str) -> str:
    """去掉全部空白（含全角空格）——期刊 PDF 的英文经常连写成一片。"""
    return re.sub(r'[\s\u3000]+', '', line)


def _is_front_matter_noise_line(line: str, head_zone: bool = False) -> bool:
    """这一行是不是中文期刊首页的"非论文内容"。

    `head_zone=True` 表示该行位于中文摘要**之前**的元数据带（那里只会出现
    页眉 / DOI / 日期 / 基金 / 通信作者 / 标题 / 作者 / 单位 / 邮箱），
    于是可以启用更宽的"基金词"判据；正文区不会传 True。
    """
    s = line.strip()
    if not s:
        return True
    flat = _squeeze_ws(s)
    low = flat.lower()
    if low.startswith('http') or 'jsjkx' in low or low.startswith('www.'):
        return True                      # 页眉 / 页脚的网址（域名里的点是全角 ．）
    if low.startswith(_META_PREFIXES):
        return True                      # DOI / 日期 / 基金 / 通信作者 / 中图分类号…
    if '@' in flat or '＠' in flat:
        return True                      # 邮箱行（含全角 ＠）
    if low.startswith('thisworkwassupportedby') or low.startswith('thisstudywassupportedby'):
        return True                      # 英文基金项目首行
    if any(tok in flat for tok in _CN_ORG_TOKENS):
        return True                      # 中文单位署名
    if any(tok in low for tok in _EN_ORG_TOKENS):
        return True                      # 英文单位署名
    if head_zone and any(tok in low for tok in _EN_GRANT_TOKENS):
        return True                      # 英文基金项目的**续行**（连写、无空格）
    return False


def _is_layout_marker_line(line: str) -> bool:
    """整行是否为版式标记（不是正文里夹带的同名字样）。"""
    s = line.strip()
    if not s:
        return False
    for marker in LAYOUT_NOISE_MARKERS:
        if s.startswith(marker) and len(s) <= len(marker) + _MARKER_SUFFIX_LIMIT:
            return True
    return False


def _strip_cn_journal_front_page(text: str):
    """中文期刊论文首页 ⇒ 白名单裁剪；**不适用返回 None**（调用方走旧分支）。"""
    lines = text.split("\n")
    ai = ki = None
    for i, ln in enumerate(lines):
        if not _RE_ZH_ABS.match(ln):
            continue
        # 「关键词」必须紧跟其后（本批实测 7–8 行；留 40 行余量）。
        for j in range(i + 1, min(len(lines), i + 41)):
            if _RE_ZH_KW.match(lines[j]):
                ai, ki = i, j
                break
        if ai is not None:
            break
    if ai is None:
        return None
    # 英文区块也在同一页 ⇒ 这是"中文期刊首页"，不是正文里偶然提到"摘要"。
    if not any(_RE_EN_ABS.match(ln) or _RE_EN_KW.match(ln) for ln in lines):
        return None

    ea = next((j for j in range(ki + 1, len(lines)) if _RE_EN_ABS.match(lines[j])), None)
    ek = None
    if ea is not None:
        ek = next((j for j in range(ea + 1, len(lines)) if _RE_EN_KW.match(lines[j])), None)
    # Keywords 可能折行（`Keywords …candidate set construction,` + 续行）
    # ⇒ 一直留到下一个空行。
    ek_end = ea
    if ek is not None:
        ek_end = ek
        for j in range(ek + 1, len(lines)):
            if not lines[j].strip():
                break
            ek_end = j

    keep = []
    for i, ln in enumerate(lines):
        if ai <= i <= ki:                       # 中文摘要 + 关键词：原样保留
            keep.append(ln)
            continue
        if ea is not None and ea <= i <= ek_end:  # 英文摘要 + 关键词：原样保留
            keep.append(ln)
            continue
        if ek_end is not None and i > ek_end:   # **正文**：原样保留
            keep.append(ln)
            continue
        if _is_front_matter_noise_line(ln, head_zone=(i < ai)):
            continue
        keep.append(ln)                         # 标题 / 作者行

    if not any(l.strip() for l in keep):
        return None                             # 安全阀：裁空了 ⇒ 放弃
    return "\n".join(keep)


def _strip_marker_blocks(text: str) -> str:
    """第一版的"标记块"裁剪：删「引用本文 / 相似文章推荐」块。

    **无标记文本逐字节不变**（返回的是同一个值）。
    """
    lines = text.split("\n")
    changed = False
    while True:
        start = next((i for i, ln in enumerate(lines) if _is_layout_marker_line(ln)),
                     None)
        if start is None:
            break
        # 块到下一个空行（不含）结束；没有空行就到页尾。
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if not lines[j].strip():
                end = j
                break
        if end <= start:
            break
        del lines[start:end]
        changed = True
        if not any(ln.strip() for ln in lines):
            return text          # 安全阀：裁空了 ⇒ 放弃，宁可留噪声
    if not changed:
        return text
    return "\n".join(lines)


def strip_layout_noise(text: str) -> str:
    """裁掉期刊首页/页脚的"非论文内容"。

    两段式：
      ① **中文期刊论文首页** ⇒ 白名单（只保留 标题 + 作者 + 摘要 + 关键词
         + 正文），单位 / 基金 / DOI / 日期 / 通信作者 / 引用 / 推荐全裁；
      ② 其余页面 ⇒ 只删「引用本文 / 相似文章推荐」标记块（第一版行为）。

    **无标记文本逐字节不变**（返回的是同一个值，见
    `tests/test_front_matter_strip.py` 与 `tmp/check_strip_en_unchanged.py`
    对 28 篇英文 arXiv PDF 的实测对比）。
    """
    if not text or os.environ.get("KG_STRIP_LAYOUT_NOISE", "1").strip().lower() \
            in ("0", "false", "no"):
        return text
    front = _strip_cn_journal_front_page(text)
    if front is not None:
        return front
    return _strip_marker_blocks(text)


# ── Chunking ──────────────────────────────────────────────

def chunk_text(text: str, max_chars: int = 500, overlap_ratio: float = 0.3) -> list[str]:
    """
    Structure-aware chunking with sliding window overlap.
    Splits on paragraph boundaries, keeps sections intact.
    """
    if len(text) <= max_chars:
        return [text] if text.strip() else []

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_chars:
            current = (current + "\n\n" + para).strip() if current else para
        else:
            if current:
                chunks.append(current)
            if len(para) > max_chars:
                # Split long paragraph by sentences
                sentences = [s.strip() + "." for s in para.replace("!", ".").replace("?", ".").split(".") if s.strip()]
                sub = ""
                for sent in sentences:
                    if len(sub) + len(sent) <= max_chars:
                        sub = (sub + " " + sent).strip() if sub else sent
                    else:
                        if sub: chunks.append(sub)
                        sub = sent
                current = sub if sub else ""
            else:
                current = para

    if current:
        chunks.append(current)

    # Sliding window overlap
    if overlap_ratio > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        overlap_chars = int(max_chars * overlap_ratio)
        for i in range(1, len(chunks)):
            prev = chunks[i - 1]
            if len(prev) > overlap_chars:
                prefix = prev[-overlap_chars:]
                space_idx = prefix.find(" ")
                if space_idx > 0:
                    prefix = prefix[space_idx:].strip()
                overlapped.append(prefix + "\n\n" + chunks[i])
            else:
                overlapped.append(chunks[i])
        chunks = overlapped

    return chunks


def table_to_text(table: dict) -> str:
    """Serialize a detected PDF table into a compact, model-readable text block."""
    lines = [f"Table {table['table_idx']}"]
    for row in table.get("data", []):
        cells = [" ".join(str(cell or "").split()) for cell in row]
        lines.append(" | ".join(cells))
    return "\n".join(lines)


def chunk_document(parsed: dict, max_chars: int = 500, overlap_ratio: float = 0.3) -> dict:
    """Apply chunking to all pages in a parsed document."""
    all_chunks = []
    tables_by_page = {}
    for table in parsed.get("tables", []):
        tables_by_page.setdefault(table["page"], []).append(table)

    for page in parsed["pages"]:
        page_chunks = chunk_text(page["text"], max_chars, overlap_ratio)
        for ci, chunk in enumerate(page_chunks):
            all_chunks.append({
                "page": page["num"],
                "chunk_idx": ci + 1,
                "kind": "text",
                "char_count": len(chunk),
                "text": chunk,
                "images": page["images"] if ci == 0 else [],  # images only on first chunk of page
            })

        for table in tables_by_page.get(page["num"], []):
            table_text = table_to_text(table)
            all_chunks.append({
                "page": page["num"],
                "chunk_idx": len(page_chunks) + table["table_idx"],
                "kind": "table",
                "table_idx": table["table_idx"],
                "char_count": len(table_text),
                "text": table_text,
                "images": [],
            })

    return {
        "file": parsed["file"],
        "total_pages": parsed["total_pages"],
        "total_chunks": len(all_chunks),
        "tables": parsed.get("tables", []),
        "chunks": all_chunks,
    }


# ── CLI ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PDF parser + chunker")
    parser.add_argument("input", help="Input PDF file")
    parser.add_argument("-o", "--output", help="Output directory")
    parser.add_argument("--json", action="store_true", help="Print JSON to stdout")
    parser.add_argument("--max-chars", type=int, default=500, help="Max chars per chunk")
    parser.add_argument("--overlap", type=float, default=0.3, help="Overlap ratio")
    parser.add_argument("--no-chunk", action="store_true", help="Skip chunking, output raw text")
    args = parser.parse_args()

    # Parse
    out_dir = args.output or str(Path(args.input).with_suffix("")) + "_extracted"
    parsed = parse_pdf(args.input, out_dir)

    # Chunk
    if args.no_chunk:
        result = {"parsed": parsed}
    else:
        chunked = chunk_document(parsed, args.max_chars, args.overlap)
        result = {"parsed": parsed, "chunked": chunked}

    result["config"] = {
        "max_chars": args.max_chars,
        "overlap_ratio": args.overlap,
        "chunking": not args.no_chunk,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        out_path = Path(out_dir) / "output.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved to {out_path}")
        print(f"  Pages: {parsed['total_pages']}")
        if not args.no_chunk:
            print(f"  Chunks: {chunked['total_chunks']}")
        print(f"  Images extracted to: {out_dir}/")


if __name__ == "__main__":
    main()
