"""为 L9 召回层生成操作性金标：从抽样段落里提候选实体，供复核。

【为什么不用抽取管线的输出】
  召回的定义是"该抽的抽到没有"。如果金标本身来自抽取管线，
  就变成自己给自己打分。所以这里用**与抽取无关的规则**：
  首字母大写的专有短语、全大写缩写、形如 `X et al.` 的引文、含年份的会议串。
  规则必然带来假阳性（把 `This paper` 也提出来），所以本脚本的产物是
  **待复核清单**，不是终稿；复核后写入 l9-gold-spans.json 才算金标。
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import json
import re
from pathlib import Path

Q = ROOT / 'data/quality'
SAMPLE = Q / 'l9-span-sample.json'
OUT = Q / 'l9-candidates.json'

# 专有短语：连续 1~6 个首字母大写的词（允许 and/or/of/the 这类连接词在中间）
PROPER = re.compile(
    r"\b([A-Z][A-Za-z0-9\-']*(?:\s+(?:of|and|or|for|the|in|on|with|to|a|an)\s+)?"
    r"(?:\s*[A-Z][A-Za-z0-9\-']*){0,5})\b")
ACRO = re.compile(r'\b([A-Z]{2,}(?:[-/][A-Za-z0-9]+)*)\b')
CITE = re.compile(r'\b([A-Z][A-Za-z\-]+(?:\s+(?:et\s+al\.?|and\s+[A-Z][A-Za-z\-]+))'
                  r'(?:\s*,?\s*\d{4}[a-z]?)?)\b')

# 明显不是实体的高频词（句首大写、泛指）
STOP = {
    'This', 'The', 'These', 'Those', 'There', 'Their', 'Then', 'They', 'That',
    'However', 'Moreover', 'Furthermore', 'In', 'On', 'For', 'With', 'We', 'Our',
    'It', 'Its', 'As', 'At', 'By', 'From', 'To', 'And', 'Or', 'But', 'If', 'When',
    'While', 'Since', 'Because', 'Although', 'Table', 'Figure', 'Section', 'Eq',
    'In', 'A', 'An', 'Figure', 'Algorithm', 'Theorem', 'Proof', 'Definition',
    'Abstract', 'Introduction', 'Conclusion', 'References', 'Appendix',
    'First', 'Second', 'Third', 'Finally', 'Recently', 'Generally', 'Specifically',
    'Note', 'Both', 'Each', 'Such', 'Some', 'Many', 'Most', 'All', 'One', 'Two',
}


def candidates(text):
    # ⚠️ 先**压平换行**再提候选。PDF 切块保留了大量硬换行，
    # 直接对原文跑正则会把跨行内容粘成一个"专有短语"，
    # 例如 `KG Completion\nLet E`、`Semantics of Data\nThe`——
    # 初版没做这一步，未命中清单里一半是这种拼接碎片，把假阳性率抬得虚高。
    flat = re.sub(r'\s+', ' ', text)
    out = set()
    for m in PROPER.finditer(flat):
        s = m.group(1).strip(' .,;:')
        if not s or s in STOP or len(s) < 3:
            continue
        if s.split()[0] in STOP:
            continue
        # 单个词且只有 2 个字母、又不是全大写缩写 → 多半是断句残留
        if len(s) <= 2 and not s.isupper():
            continue
        out.add(s)
    for rx in (ACRO, CITE):
        for m in rx.finditer(flat):
            s = m.group(1).strip(' .,;:')
            if len(s) >= 2 and s not in STOP:
                out.add(s)
    return sorted(out)


def main():
    sample = json.loads(SAMPLE.read_text(encoding='utf-8'))
    spans = sample['spans']
    print(f'段落 {len(spans)} 个，逐段提候选：\n')
    for s in spans:
        cands = candidates(s['text'])
        s['candidates'] = cands
        print(f"── {s['id']} ({len(cands)} 个候选) ──")
        print('   ' + ', '.join(cands[:22]))
    OUT.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding='utf-8')
    tot = sum(len(s['candidates']) for s in spans)
    print(f'\n候选实体合计 {tot}，平均每段 {tot/len(spans):.1f} 个')
    print(f'→ {OUT}')
    print('\n复核方式：删掉假阳性（泛指词、句首大写），保留真实体，'
          '然后把 spans 另存为 l9-gold-spans.json 并跑 --score')


if __name__ == '__main__':
    main()
