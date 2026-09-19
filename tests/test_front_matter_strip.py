# -*- coding: utf-8 -*-
"""分块前的版式噪声裁剪 —— 断言「该裁的裁掉、不该动的逐字节不变」。

## 为什么值得一组断言

`jsjkx2025-kg-rag-qa.pdf` 第 1 页的「引用本文 / 相似文章推荐」块里印着
**同刊其它 5 篇论文的标题**，首版抽取把它们当成了本文实体，生成 12 条虚假
RELATED_TO 边（占全图 13%）。**它不是幻觉** —— 那些字确实在 PDF 上，
所以"抽查看到真实存在的标题"抓不到它，只能靠**结构化的断言**：

  ① 有标记 ⇒ 标记块消失，**标题/作者留下**；
  ② 无标记 ⇒ **逐字节不变**（用 `assertIs` 钉到"同一个字符串对象"，
     连一次重写都没有）；
  ③ 标记块之后紧跟摘要时 ⇒ **摘要必须活下来**（块边界是空行，不是页尾）；
  ④ 整页只有噪声 ⇒ **放弃裁剪**（宁可留噪声，也不清空一页）；
  ⑤ 清洗必须发生在 `chunk_document` **之前**（假边是抽取阶段生成的，
     抽完再过滤就晚了）。
"""
import os
import tempfile
import unittest
from pathlib import Path

from pdf_parser import strip_layout_noise

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / 'data' / 'corpus'
# ⚠️ 2026-09-19：原来这里的 JSJKX 指向 `jsjkx2025-kg-rag-qa.pdf`（军事情报
# 方向），那篇已经**归档**到 `data/corpus/_archive-military/`（见该目录的
# README）⇒ 真实 PDF 的断言改用**替代它的那一篇**
# （`jsjkx2025-kg-kbqa.pdf`），并补上"首页白名单裁剪"的新断言。
JSJKX = CORPUS / 'jsjkx2025-kg-kbqa.pdf'
PLM_SURVEY = CORPUS / 'jsjkx2025-kg-plm-survey.pdf'
COMPLETION_LLM = CORPUS / 'jsjkx2025-kg-completion-llm.pdf'

# 真实英文 arXiv 段落（无任何版式标记）。
EN_SAMPLE = """Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks

We explore a general-purpose fine-tuning recipe for retrieval-augmented generation
(RAG) -- models which combine pre-trained parametric and non-parametric memory for
language generation. We introduce RAG models where the parametric memory is a
pre-trained seq2seq model and the non-parametric memory is a dense vector index of
Wikipedia, accessed with a pre-trained neural retriever."""


class LayoutNoiseStripTests(unittest.TestCase):

    def test_text_without_markers_is_the_same_object(self):
        """无标记 ⇒ 返回同一个字符串对象（比"内容相等"更强的逐字节保证）。"""
        for sample in (
            EN_SAMPLE,
            "摘要：本文提出一种基于知识图谱的检索增强生成问答技术。\n关键词：知识图谱；问答",
            '正文里提到"引用本文"这四个字，但它不在行首，不该被当成标记。',
            "",
        ):
            with self.subTest(sample=sample[:24]):
                self.assertIs(strip_layout_noise(sample), sample)

    def test_switch_off_returns_original(self):
        text = "标题\n引用本文\n某期刊, 2025, 52(1): 87-93."
        os.environ['KG_STRIP_LAYOUT_NOISE'] = '0'
        try:
            self.assertIs(strip_layout_noise(text), text)
        finally:
            os.environ.pop('KG_STRIP_LAYOUT_NOISE', None)

    def test_marker_must_start_the_line(self):
        text = "本文引用本文的方法做对比，结果更好。\n第二行正文。"
        self.assertIs(strip_layout_noise(text), text)

    def test_citation_block_is_dropped_and_title_authors_survive(self):
        text = (
            "一种基于知识图谱的检索增强生成情报问答技术\n"
            "成志宇, 陈星霖, 王菁, 周中元, 张志政\n"
            "引用本文\n"
            "成志宇, 陈星霖, 王菁, 周中元, 张志政. 一种基于知识图谱的检索增强生成情报问答技术[J]. 计算机科学,\n"
            "2025, 52(1): 87-93.\n"
            "相似文章推荐（请使用火狐或 IE 浏览器查看文章）\n"
            "Similar articles recommended (Please use Firefox or IE to view the article)\n"
            "面向联邦大语言模型训练的传输优化技术综述\n"
            "计算机科学, 2025, 52(1): 42-55. https://doi.org/10.11896/jsjkx.240500095"
        )
        out = strip_layout_noise(text)
        self.assertIn("一种基于知识图谱的检索增强生成情报问答技术", out)
        self.assertIn("成志宇, 陈星霖, 王菁, 周中元, 张志政", out)
        for noise in ("引用本文", "相似文章推荐", "Similar articles recommended",
                      "面向联邦大语言模型训练的传输优化技术综述", "87-93"):
            self.assertNotIn(noise, out)

    def test_body_after_a_blank_line_survives(self):
        """块边界是**空行**，不是页尾 ⇒ 引用块后面的摘要/关键词不会被误伤。"""
        text = (
            "标题\n作者\n"
            "引用本文\n某某. 标题[J]. 期刊, 2025, 52(1): 87-93.\n"
            "\n"
            "摘要：本文提出一种方法。\n关键词：知识图谱；问答"
        )
        out = strip_layout_noise(text)
        self.assertIn("摘要：本文提出一种方法。", out)
        self.assertIn("关键词：知识图谱；问答", out)
        self.assertNotIn("引用本文", out)
        self.assertNotIn("87-93", out)

    def test_multiple_noise_blocks_are_all_dropped(self):
        text = (
            "标题\n引用本文\n格式A\n\n正文第一段。\n\n"
            "相似文章推荐（请使用火狐或 IE 浏览器查看文章）\n别的论文的标题\n期刊, 2025."
        )
        out = strip_layout_noise(text)
        self.assertIn("正文第一段。", out)
        self.assertIn("标题", out)
        self.assertNotIn("引用本文", out)
        self.assertNotIn("相似文章推荐", out)
        self.assertNotIn("别的论文的标题", out)

    def test_empty_result_falls_back_to_original(self):
        """整页都是噪声 ⇒ 不裁（安全阀：宁可留噪声，也不清空一页）。"""
        text = "引用本文\n某某. 标题[J]. 期刊, 2025, 52(1): 87-93."
        self.assertIs(strip_layout_noise(text), text)

    @unittest.skipUnless(JSJKX.exists(), "语料 PDF 不在本机")
    def test_real_front_page_drops_other_papers(self):
        import fitz
        doc = fitz.open(str(JSJKX))
        page1 = doc[0].get_text("text")
        doc.close()
        out = strip_layout_noise(page1)
        self.assertIn("基于可微知识图谱的多跳知识库问答", out)
        self.assertIn("魏谦强", out)
        for other_paper in (
            "面向医学领域的文本特征增强多任务学习模型",
            "基于深度时间对比的中断航迹关联方法",
            "基于异质信息网的短文本特征扩充方法",
            "基于解耦-检索-生成的图像风格化描述生成模型",
            "基于Transformer的汉字到盲文端到端自动转换",
            "Multi-task Learning Model for Text Feature Enhancement",
            "Track Segment Association Based on Deep Temporal Contrasting",
        ):
            self.assertNotIn(other_paper, out)

    @unittest.skipUnless(JSJKX.exists(), "语料 PDF 不在本机")
    def test_real_pipeline_strips_before_chunk_document(self):
        """清洗必须发生在 `chunk_document` **之前**（假边在抽取阶段才生成）。"""
        import pipeline
        captured = {}
        real = pipeline.chunk_document

        def fake_chunk_document(parsed, max_chars=500, overlap_ratio=0.3):
            captured['text'] = "\n".join(p['text'] for p in parsed['pages'])
            return {'file': parsed['file'], 'total_pages': parsed['total_pages'],
                    'total_chunks': 0, 'tables': [], 'chunks': []}

        pipeline.chunk_document = fake_chunk_document
        try:
            with tempfile.TemporaryDirectory() as tmp:
                pipeline.extract_graph(
                    str(JSJKX), tmp, source_id='probe', source_name=JSJKX.name)
        finally:
            pipeline.chunk_document = real

        self.assertIn("基于可微知识图谱的多跳知识库问答",
                      captured.get('text', ''))
        self.assertNotIn("基于深度时间对比的中断航迹关联方法",
                         captured.get('text', ''))
        self.assertNotIn("引用本文", captured.get('text', ''))
        # 首页白名单同样生效：单位 / 基金 / DOI / 收稿日期都不该进分块器。
        self.assertNotIn("河北师范大学", captured.get('text', ''))
        self.assertNotIn("基金项目", captured.get('text', ''))
        self.assertNotIn("到稿日期", captured.get('text', ''))


# ── 2026-09-19 升级：中文期刊首页的**白名单**裁剪 ─────────────────────
#
# 为什么加这一组：旧规则只删「引用本文 / 相似文章推荐」两个块，实测**漏过
# 两次** —— 基金项目实体（`军科委国防科技重点实验室基金` /
# `Pre-research Key Laboratory Fund for Equipment`）与**作者单位署名**
# （`国防科技大学系统工程学院`）都进过图。
#
# 新规则（`src/pdf_parser.py`）：中文期刊论文首页变成白名单 ——
# **标题 + 作者 + 中文摘要 + 中文关键词 + 英文标题/摘要/关键词 + 正文**；
# 页眉页脚 / DOI / 到稿日期 / 基金项目（含中英文续行）/ 通信作者 / 单位 /
# 邮箱 / 中图分类号 / 引用本文 / 相似文章推荐 全部裁掉。

# 一份"仿真"的中文期刊首页：行序照抄实测 PDF（kbqa 第 2 页）。
CN_FRONT_PAGE = (
    "http://www．\n"
    "jsjkx．com\n"
    "DOI:１０．１１８９６/jsjkx．２４０６０００９５\n"
    "到稿日期:２０２４Ｇ０６Ｇ１７　返修日期:２０２４Ｇ０９Ｇ１８\n"
    "基金项目:国家社会科学基金重大项目(１８ZDA２００)\n"
    "ThisworkwassupportedbytheNationalSocialScienceFoundationofChina\n"
    "(C２０２３０３３９)andSpecialScienceandTechnologyFundofHebei\n"
    "通信作者:赵书良(zhaoshuliang＠sina．com)\n"
    "基于可微知识图谱的多跳知识库问答\n"
    "魏谦强１,２,３\n"
    "赵书良１,２,３\n"
    "张思漫４\n"
    "１河北师范大学计算机与网络空间安全学院　石家庄０５００２４\n"
    "　(１５０７５８０５８８２＠１６３．com)\n"
    "　\n"
    "摘　要　知识库问答是一个具有挑战性的热门研究方向.\n"
    "关键词:多跳知识库问答;可微知识图谱\n"
    "中图分类号　TP３９１\n"
    "　\n"
    "MultiＧhopKnowledgeBaseQuestionAnswering\n"
    "WEIQianqiang\n"
    "１CollegeofComputerandCyberSecurity,HebeiNormalUniversity,Shijiazhuang,China\n"
    "Abstract　Knowledgebasequestionansweringisachallengingdirection．\n"
    "Keywords　Knowledgebasequestionanswering\n"
    "　\n"
    "１　引言\n"
    "知识图谱是一种结构化的语义网络模型.\n"
)

# arXiv 首页（**没有**中文摘要行）——白名单**绝不能**在这里触发。
EN_ARXIV_FRONT = (
    "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks\n"
    "Patrick Lewis, Ethan Perez, Aleksandra Piktus\n"
    "Facebook AI Research; University College London\n"
    "\n"
    "Abstract\n"
    "We explore a general-purpose fine-tuning recipe for retrieval-augmented\n"
    "generation (RAG) -- models which combine pre-trained parametric and\n"
    "non-parametric memory for language generation.\n"
    "\n"
    "1 Introduction\n"
    "Pre-trained neural language models have been shown to learn a substantial\n"
    "amount of in-depth knowledge from data.\n"
)


class ChineseFrontPageWhitelistTests(unittest.TestCase):
    """白名单：**该留的一条不少、该裁的一条不留**。"""

    def test_keeps_title_authors_abstract_keywords_and_body(self):
        out = strip_layout_noise(CN_FRONT_PAGE)
        for keep in (
            "基于可微知识图谱的多跳知识库问答",     # 中文标题
            "魏谦强１,２,３",                        # 作者（带全角上标）
            "张思漫４",
            "摘　要　知识库问答是一个具有挑战性的热门研究方向.",
            "关键词:多跳知识库问答;可微知识图谱",
            "MultiＧhopKnowledgeBaseQuestionAnswering",   # 英文标题
            "Abstract　Knowledgebasequestionanswering",   # 英文摘要
            "Keywords　Knowledgebasequestionanswering",
            "１　引言",                                    # ★ 正文必须留下
            "知识图谱是一种结构化的语义网络模型.",
        ):
            with self.subTest(keep=keep):
                self.assertIn(keep, out)

    def test_drops_affiliation_fund_doi_date_and_corresponding_author(self):
        out = strip_layout_noise(CN_FRONT_PAGE)
        for noise in (
            "http://",                 # 页眉
            "jsjkx",
            "DOI:",
            "到稿日期",
            "基金项目",                 # 基金项目（中文）
            "Thisworkwassupportedby",   # 基金项目（英文首行）
            "FundofHebei",              # 基金项目的**英文续行**（连写、无空格）
            "通信作者",
            "河北师范大学",              # 中文单位署名
            "HebeiNormalUniversity",    # 英文单位署名
            "CollegeofComputer",
            "＠",                       # 邮箱
            "中图分类号",
        ):
            with self.subTest(noise=noise):
                self.assertNotIn(noise, out)

    def test_english_arxiv_front_page_is_the_same_object(self):
        """英文 arXiv 首页（有 Abstract/Keywords、**没有**中文摘要行）逐字节不变。"""
        self.assertIs(strip_layout_noise(EN_ARXIV_FRONT), EN_ARXIV_FRONT)

    def test_switch_off_also_disables_the_whitelist(self):
        os.environ['KG_STRIP_LAYOUT_NOISE'] = '0'
        try:
            self.assertIs(strip_layout_noise(CN_FRONT_PAGE), CN_FRONT_PAGE)
        finally:
            os.environ.pop('KG_STRIP_LAYOUT_NOISE', None)

    def test_without_english_block_the_whitelist_does_not_trigger(self):
        """**保守退化**：没有英文区块就不敢认定"这是期刊首页" ⇒ 退回标记块规则。

        代价是可能留下单位行；收益是**绝不误删**正常中文正文。
        """
        text = ("某某标题\n某某作者\n摘　要　一段中文摘要\n"
                "关键词:甲;乙\n１　引言\n正文第一段。")
        self.assertIs(strip_layout_noise(text), text)

    def test_front_page_of_pure_noise_does_not_crash(self):
        """整页只有元数据带 + 摘要标记 ⇒ 不崩，且摘要/关键词必然留下。"""
        text = ("基金项目:某某基金\n１某某大学　北京\n"
                "摘　要\n关键词:甲\nAbstract\nKeywords\n")
        out = strip_layout_noise(text)
        self.assertIn("摘　要", out)
        self.assertIn("关键词:甲", out)
        self.assertIn("Keywords", out)
        self.assertNotIn("基金项目", out)
        self.assertNotIn("某某大学", out)

    def test_blank_page_still_returns_original(self):
        self.assertIs(strip_layout_noise(""), "")


class RealChineseFrontPageTests(unittest.TestCase):
    """对**真实 PDF** 的断言：新版裁剪在真语料上确实生效。"""

    @staticmethod
    def _page(pdf: Path, num: int) -> str:
        import fitz
        doc = fitz.open(str(pdf))
        try:
            return doc[num - 1].get_text("text")
        finally:
            doc.close()

    @unittest.skipUnless(JSJKX.exists(), "语料 PDF 不在本机")
    def test_kbqa_front_page_keeps_abstract_drops_affiliation(self):
        out = strip_layout_noise(self._page(JSJKX, 2))
        self.assertIn("摘　要", out)
        self.assertIn("关键词:多跳知识库问答", out)
        self.assertIn("Abstract", out)
        for noise in ("基金项目", "DOI:", "到稿日期", "通信作者",
                      "河北师范大学", "中图分类号", "＠", "jsjkx"):
            self.assertNotIn(noise, out)

    @unittest.skipUnless(PLM_SURVEY.exists(), "语料 PDF 不在本机")
    def test_plm_survey_front_page_drops_military_affiliation(self):
        """**本次升级的直接动因**：单位署名 `国防科技大学系统工程学院` 曾会进图。"""
        out = strip_layout_noise(self._page(PLM_SURVEY, 2))
        self.assertIn("摘　要", out)
        self.assertIn("基于预训练语言模型的知识图谱研究综述", out)
        for noise in ("国防科技大学系统工程学院", "福建省军区",
                      "国防科技大学大数据与决策实验室",
                      "基金项目", "通信作者", "到稿日期"):
            self.assertNotIn(noise, out)

    @unittest.skipUnless(COMPLETION_LLM.exists(), "语料 PDF 不在本机")
    def test_completion_llm_keeps_body_after_english_keywords(self):
        """★ **不许裁过头**：英文 Keywords 之后紧跟「1 引言」，那是正文。"""
        out = strip_layout_noise(self._page(COMPLETION_LLM, 2))
        self.assertIn("Keywords", out)
        self.assertIn("１　引言", out)
        self.assertIn("知识图谱(KnowledgeGraph,KG)", out)
        self.assertNotIn("安徽大学计算机科学与技术学院", out)
        self.assertNotIn("基金项目", out)


if __name__ == '__main__':
    unittest.main()
