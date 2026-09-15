# 金标标注工作稿：2012.00456v1

> 共 17 页、111 个文本块。
> **标注时只看本文件与 PDF 原文，不要看系统抽取输出**（防锚定）。
> 规则见 `docs/GOLD_STANDARD.md`。填完把本文件交回，我会转成 `data/gold/2012.00456v1.json`。

## 速查

**实体类型（15 类，必须用其中之一）**
`Concept` `Method` `System` `Component` `Process` `Artifact` `Dataset` `Metric`
`Tool` `Material` `Person` `Organization` `Location` `Event` `Document`

**关系类型（15 类）**
`PART_OF` `USES` `PRODUCES` `MEASURES` `EVALUATES` `COMPARES_WITH` `SUPPORTS`
`CONTRADICTS` `CAUSES` `ENABLES` `APPLIES_TO` `LOCATED_AT` `CREATED_BY` `CITES`
`RELATED_TO`

**填写格式**（直接在下面表格里加行，`|` 分隔，不要删表头）

| 实体名（规范名） | 类型 | 页码 | 原文依据（≤200 字符） |
|---|---|---|---|
| 例：Open Research Knowledge Graph | System | 1 | "The Open Research Knowledge Graph (ORKG)..." |

| 源实体 | 目标实体 | 关系类型 | 页码 | 原文依据 |
|---|---|---|---|---|
| 例：ORKG | Research Contributions | PRODUCES | 1 | "...publishes the research contributions..." |

**判定要点**
- 只标"明确写出来的"：泛指词（the model）、纯编号（Table 1）、参考文献列表条目不标
- 同一实体的不同写法记一条，取论文里的**全称**（如 multi-head attention 而非 attention）
- 关系两端必须是本篇已标注的实体，且句中有明确文字依据
- 没把握的隐含关系**不标**——金标宁缺勿错

---

## 建议标注范围

优先第 1–10 页（前 60%，方法与实验部分信息密度最高）；时间充裕再补后面。每篇目标：实体 50–100 条、关系 30–60 条。

---


## 第 1 页 ⭐建议标注

**[块 1]**

```text
Creating a Scholarly Knowledge Graph from
Survey Article Tables
Allard Oelen1,2[0000−0001−9924−9153], Markus Stocker2[0000−0001−5492−3212], and
S¨oren Auer2,1[0000−0002−0698−2864]
1 L3S Research Center, Leibniz University of Hannover, Germany
oelen@l3s. de
2 TIB Leibniz Information Centre for Science and Technology, Germany
{markus. stocker,auer}@tib. eu
Abstract. Due to the lack of structure, scholarly knowledge remains
hardly accessible for machines.
```

**[块 2]**

```text
Germany
{markus. stocker,auer}@tib. eu
Abstract. Due to the lack of structure, scholarly knowledge remains
hardly accessible for machines.

Scholarly knowledge graphs have been
proposed as a solution. Creating such a knowledge graph requires manual
eﬀort and domain experts, and is therefore time-consuming and cumber-
some. In this work, we present a human-in-the-loop methodology used to
build a scholarly knowledge graph leveraging literature survey articles. Survey articles often contain manually curated and high-quality tabular
information that summarizes ﬁndings published in the scientiﬁc litera-
ture.
```

**[块 3]**

```text
Survey articles often contain manually curated and high-quality tabular
information that summarizes ﬁndings published in the scientiﬁc litera-
ture.

Consequently, survey articles are an excellent resource for generat-
ing a scholarly knowledge graph. The presented methodology consists of
ﬁve steps, in which tables and references are extracted from PDF arti-
cles, tables are formatted and ﬁnally ingested into the knowledge graph. To evaluate the methodology, 92 survey articles, containing 160 survey
tables, have been imported in the graph. In total, 2 626 papers have
been added to the knowledge graph using the presented methodology.
```

**[块 4]**

```text
160 survey
tables, have been imported in the graph. In total, 2 626 papers have
been added to the knowledge graph using the presented methodology.

The results demonstrate the feasibility of our approach, but also indi-
cate that manual eﬀort is required and thus underscore the important
role of human experts. Keywords: Scholarly Communication · Scholarly Knowledge Graphs ·
Tabular Data Extraction
1
Introduction
Scholarly communication is mainly document-based and the communicated schol-
arly knowledge therefore hardly machine-actionable [21].
```

**[块 5]**

```text
communication is mainly document-based and the communicated schol-
arly knowledge therefore hardly machine-actionable [21].

Scholarly knowledge
graphs have the potential to solve these issues by making knowledge structured
and thus more machine processable. Existing initiatives for scholarly informa-
tion systems, e. g. , the Microsoft Academic Graph [8] or Crossref [15] mainly
focus on bibliographic metadata and not on the actual research contributions.
```

**[块 6]**

```text
e. g. , the Microsoft Academic Graph [8] or Crossref [15] mainly
focus on bibliographic metadata and not on the actual research contributions.

The Open Research Knowledge Graph (ORKG) [10] aims to build a knowledge
graph infrastructure that publishes the research contributions of scholarly pub-
lications rather than only the metadata. The approach is to crowdsource struc-
tured paper descriptions by including paper authors and domain experts. ORKG
arXiv:2012. 00456v1  [cs. DL]  1 Dec 2020.
```


## 第 2 页 ⭐建议标注

**[块 1]**

```text
2
Oelen et al. Survey article tables
Scholarly knowledge graph
Presented methodology 
Input
Output
1. Select papers 
2. Table extraction  
3. Table formatting 
4. Reference extraction 
5. Build graph
1
2
3
4
5
Fig. 1: Systematic workﬂow in which survey articles are used to build a scholarly
knowledge graph. The input of our methodology is survey articles in PDF format
and the output is a scholarly knowledge graph.
```

**[块 2]**

```text
to build a scholarly
knowledge graph. The input of our methodology is survey articles in PDF format
and the output is a scholarly knowledge graph.

primarily relies on synergistically combining crowdsourcing and automated ex-
traction rather than, as other systems such as Semantic Scholar3, exclusively on
automated techniques to extract knowledge from scholarly articles. Mainly be-
cause automated extraction methods, for example Natural Language Processing
(NLP), do not have suﬃcient accuracy to generate the high-quality knowledge
graph needed to obtain suitable state-of-the-art overviews for researchers.
```

**[块 3]**

```text
do not have suﬃcient accuracy to generate the high-quality knowledge
graph needed to obtain suitable state-of-the-art overviews for researchers.

In this work, we present a human-in-the-loop methodology to create a schol-
arly knowledge graph by extracting knowledge from survey tables. We leverage
survey tables from literature review papers, speciﬁcally. Tables in survey papers
generally consist of high-quality research data that has been manually curated
by domain experts. Conducting a literature review is a labour-intensive task and
writing a review article is often more time-consuming than writing a research
article [32].
```

**[块 4]**

```text
a literature review is a labour-intensive task and
writing a review article is often more time-consuming than writing a research
article [32].

Compared to natural text, tables present information in a semi-
structured manner, making the creation of a structured graph from such data
less complicated. Additionally, survey tables present relevant information which
is why the survey was conducted and published in the ﬁrst place. We present a
supervised approach to ﬁrstly extract data from survey articles and afterwards
build a knowledge graph from this data.
```

**[块 5]**

```text
the ﬁrst place. We present a
supervised approach to ﬁrstly extract data from survey articles and afterwards
build a knowledge graph from this data.

Compared to sole crowdsourcing, the
approach of extracting knowledge is more eﬃcient because the review has al-
ready been conducted by the authors of the survey paper. Taking into account
the previously mentioned considerations, our work addresses the following re-
search question: How to eﬃciently populate a scholarly knowledge graph with
high-quality knowledge. We propose a methodology for extracting tabular sur-
vey data.
```

**[块 6]**

```text
How to eﬃciently populate a scholarly knowledge graph with
high-quality knowledge. We propose a methodology for extracting tabular sur-
vey data.

This methodology is used to create a scholarly knowledge graph from
survey articles. An overview of the systematic workﬂow is depicted in Figure 1. The rest of this paper is structured as follows. Section 2 discusses the related
work. Section 3 introduces the proposed ﬁve-step methodology for building the
knowledge graph from survey articles. Section 4 presents the results. Section 5
discusses the present and future work. Finally, Section 6 concludes the presented
work. 3 https://www.
```

**[块 7]**

```text
Section 4 presents the results. Section 5
discusses the present and future work. Finally, Section 6 concludes the presented
work. 3 https://www.

semanticscholar. org.
```


## 第 3 页 ⭐建议标注

**[块 1]**

```text
Creating a Scholarly Knowledge Graph from Survey Article Tables
3
2
Related Work
Survey articles provide well-structured overviews of the literature [33]. The terms
“literature review” and “literature survey” are sometimes used interchangeably
in the literature, but we make the following distinction. We refer to the tables
within review articles as literature surveys. Together with a (textual) analysis
and explanation, they form the literature review.
```

**[块 2]**

```text
to the tables
within review articles as literature surveys. Together with a (textual) analysis
and explanation, they form the literature review.

Among other things, literature
reviews are helpful in delimiting the research problem, avoiding fruitless ap-
proaches [5] and to discover new research directions [6]. Conducting a literature
review is a complicated and time-consuming activity [33]. When literature re-
views are not available for certain ﬁelds, its development could be weakened [32].
```

**[块 3]**

```text
complicated and time-consuming activity [33]. When literature re-
views are not available for certain ﬁelds, its development could be weakened [32].

Because of the importance of literature surveys to scientiﬁc research, leveraging
surveys to build a graph results in a high-quality and relevant scholarly knowl-
edge graph. Some existing work with respect to semantifying literature surveys
exists [4,29,23]. However, those approaches are not (semi-)automated and are
therefore not scaling well to larger amounts of survey articles. One aspect of the proposed methodology is table extraction from survey arti-
cles.
```

**[块 4]**

```text
not scaling well to larger amounts of survey articles. One aspect of the proposed methodology is table extraction from survey arti-
cles.

Portable Document Format (PDF) is the most common format for scientiﬁc
articles [12]. Extracting tables from PDF documents is a cumbersome process
since the tabular structure is not stored within the ﬁle itself [11]. This means that
regular PDF extraction tools are only able to extract the text within a table, but
loosing the tabular structure. Tools that speciﬁcally focus on table extraction
from PDF ﬁles use segmentation techniques to estimate the position of rows and
columns [7].
```

**[块 5]**

```text
Tools that speciﬁcally focus on table extraction
from PDF ﬁles use segmentation techniques to estimate the position of rows and
columns [7].

Corrˆea et al. did a literature survey on table extraction tools [3]. They concluded that Tabula4 is the most suitable open-source tool. Based of
these ﬁndings, we decided to use Tabula. Tabula is criticized because of the lack
of documentation [26], but for our use case this is not considered problematic. Another aspect of the proposed methodology is reference extraction from PDFs.
```

**[块 6]**

```text
[26], but for our use case this is not considered problematic. Another aspect of the proposed methodology is reference extraction from PDFs.

Since every individual article referenced within a survey table is imported, meta-
data from this article should be collected. This is done by parsing the references
that are used within a table. For this, we use the state-of-the-art PDF extrac-
tion tool GROBID [13]. GROBID focuses speciﬁcally on extracting bibliographic
data from scholarly articles [19]. Lipinski et al. [17] compared GROBID to other
PDF metadata extraction tools, and found out that GROBID performed best.
```

**[块 7]**

```text
from scholarly articles [19]. Lipinski et al. [17] compared GROBID to other
PDF metadata extraction tools, and found out that GROBID performed best.

Publishing data as structured or semantic data is a well researched topic
among various domains. For example, challenges related to publishing semantic
open government data are similar to the challenges in our research. This includes
extracting data from legacy documents, often in PDF format [2,3]. Furthermore,
in the literature use cases are described on publishing unstructured data as se-
mantic data (e. g. , [9,20,27]).
```

**[块 8]**

```text
in PDF format [2,3]. Furthermore,
in the literature use cases are described on publishing unstructured data as se-
mantic data (e. g. , [9,20,27]).

These existing approaches diﬀer from our approach
since they generally aim to semantify a homogeneous set of documents. This
enables them to create data speciﬁc ontologies. In our case, this is not feasible
since we work with a highly heterogeneous set of survey tables coming from
diﬀerent domains and comparing diﬀerent aspects of papers. Table 1 provides
4 https://tabula. technology.
```


## 第 4 页 ⭐建议标注

**[块 1]**

```text
4
Oelen et al. Table 1: Related work compared to the method presented in our study. The full
comparison is available via the ORKG.
```

**[块 2]**

```text
5
Study
Name
Method automation
Scope
Input format
Output format
KGa creation
Reference extraction
User interface
This
study
ORKG
Semi-Automatic
Survey tables
PDF
JSON, RDFb



[28]
SemAnn
Semi-Automatic
Scholarly articles
PDF
RDFb



[16]
Web Tables
Automatic
Web tables
HTML
JSON



[18]
TableSeer
Automatic
Scholarly articles
PDF
Relational
database



[25]
TEXUS
Automatic
Documents
(application
agnostic)
PDF
Abstract
table repre-
sentation



[1]
None
Automatic
Web tables
HTML,
Spread-
sheet
Relational
schema



a Knowledge Graph; b Resource Description Framework
a related work overview.
```

**[块 3]**

```text
tables
HTML,
Spread-
sheet
Relational
schema



a Knowledge Graph; b Resource Description Framework
a related work overview.

In this overview, our proposed method is compared to
other related approaches. To the best of our knowledge, this work is the ﬁrst to
build a knowledge graph at scale from survey tables. Use Case: Open Research Knowledge Graph. Extracted survey data can
be imported in a variety of diﬀerent (scholarly) knowledge graphs, such as the
Microsoft Academic Graph, Wikidata [14] or ORKG. We chose ORKG as our
use case for the following reasons.
```

**[块 4]**

```text
(scholarly) knowledge graphs, such as the
Microsoft Academic Graph, Wikidata [14] or ORKG. We chose ORKG as our
use case for the following reasons.

The ORKG provides tools that speciﬁcally
focus on building paper comparisons (i. e. , literature surveys), making it the most
suitable infrastructure for this study. By using the extracted survey data, the
ORKG automatically generates a similar tabular survey view as was originally
presented in the review paper [22]. Additionally, the literature surveys within
ORKG are compliant [23] with the FAIR data principles [34] thus making them
Findable, Accessible, Interoperable and Reusable.
```

**[块 5]**

```text
surveys within
ORKG are compliant [23] with the FAIR data principles [34] thus making them
Findable, Accessible, Interoperable and Reusable.

The imported survey tables
are FAIR in contrast to the originally presented ones in the non-FAIR PDF
article. This has several beneﬁts, among others:
– Comparisons can evolve over time, are not static and do not become stale
after publication. – Comparisons do represent a broader community consensus, since many re-
searchers and curators can revise, discuss and annotate. 5 https://www. orkg. org/orkg/comparison/R36099.
```

**[块 6]（表格）**

```text
Table 1
Study | Name | Method automation | Scope | Input format | Output format | KGa creation | Reference extraction | User interface
This study | ORKG | Semi-Automatic | Survey tables | PDF | JSON, RDFb |  |  | 
[28] | SemAnn | Semi-Automatic | Scholarly articles | PDF | RDFb |  |  | 
[16] | Web Tables | Automatic | Web tables | HTML | JSON |  |  | 
[18] | TableSeer | Automatic | Scholarly articles | PDF | Relational database |  |  | 
[25] | TEXUS | Automatic | Documents (application agnostic) | PDF | Abstract table repre- sentation |  |  | 
[1] | None | Automatic | Web tables | HTML, Spread- sheet | Relational schema |  |  | 
```


## 第 5 页 ⭐建议标注

**[块 1]**

```text
Creating a Scholarly Knowledge Graph from Survey Article Tables
5
1. Paper selection 
2. Table extraction 
4. Reference extraction
Manually search for 
review papers that 
contain survey tables. Semi-automatically 
extract survey tables 
from PDF papers. Extract the bibliographic 
metadata for each paper 
in the survey table. [57]: 
Authors: John Doe et al. Title: My research paper 
[43]: …
5. Build graph
Combine the data from 
the previous steps and 
build the graph. 2. Table extraction 
3.
```

**[块 2]**

```text
Doe et al. Title: My research paper 
[43]: …
5. Build graph
Combine the data from 
the previous steps and 
build the graph. 2. Table extraction 
3.

Table formatting
4. Reference  
    extraction
Combine  data from:
Build the knowledge  
graph
= Human-in-the-loop
3. Table formatting 
Clean the extracted 
tables and prepare 
them for import. Reference
Ex1
Ex2
[1]
1. 221
76
[2]
2. 534
65
[3]
0. 123
79
Fig. 2: Methodology for importing survey tables into the scholarly knowledge
graph. – Via the ORKG search interface it is possible to search for speciﬁc compar-
isons and to create dynamic custom comparison views.
```

**[块 3]**

```text
knowledge
graph. – Via the ORKG search interface it is possible to search for speciﬁc compar-
isons and to create dynamic custom comparison views.

– Survey data can be reused by other researchers more easily because of its
machine readable export formats (e. g. , export as CSV or RDF). 3
Methodology
We now present a ﬁve-step methodology for the creation of a scholarly knowledge
graph from survey tables. In order to reach suﬃcient quality, the methodology
takes a human-in-the-loop approach in which multiple steps require human inter-
action. Data quality improves with human evaluation and, if needed, correction
of the extracted data.
```

**[块 4]**

```text
in which multiple steps require human inter-
action. Data quality improves with human evaluation and, if needed, correction
of the extracted data.

The methodology is displayed in Figure 2. The scripts
required to perform the steps are available online. 6
3. 1
Paper Selection
In the ﬁrst step, suitable survey papers are selected based on multiple criteria. The purpose is to ﬁnd survey papers from a diverse range of domains. Therefore,
a protocol has been designed to determine which papers are suitable for data
extraction.
```

**[块 5]**

```text
survey papers from a diverse range of domains. Therefore,
a protocol has been designed to determine which papers are suitable for data
extraction.

The structured nature of the selection process is needed to be able
to make conclusions about the percentage of survey papers that present the
information in such a way that extracting data is relatively straightforward. Search Strategy. Table 2 lists the search engines used to ﬁnd survey arti-
cles. Google Scholar is chosen to ensure that survey papers from various ﬁelds
are searched.
```

**[块 6]**

```text
2 lists the search engines used to ﬁnd survey arti-
cles. Google Scholar is chosen to ensure that survey papers from various ﬁelds
are searched.

Additionally, ACM Digital Library has been selected because the
ORKG currently focuses mainly on the Computer Science domain. The search is
6 https://doi. org/10. 5281/zenodo. 3739427.
```


## 第 6 页 ⭐建议标注

**[块 1]**

```text
6
Oelen et al. Table 2: Search engines used to ﬁnd survey articles. Search engine
Field
Evaluated papers
Google Scholar
All
335
ACM Digital Library Computer Science
80
limited to 100 papers that are suitable for import. The following search criteria
are used:
– Google Scholar: the article title contains the term “literature survey”. – ACM Digital Library: queries “literature review” and “literature survey”. – The survey article has been published after 2002. – The results are sorted by relevance.
```

**[块 2]**

```text
queries “literature review” and “literature survey”. – The survey article has been published after 2002. – The results are sorted by relevance.

The rationale for selecting papers published after the year 2002 is because in
general more recent papers are more interesting for research and should there-
fore have more priority in the scholarly knowledge graph. In the end, articles
published before 2002 can still be part of the graph, since this criterion only
applies to the survey articles themselves, and not to the papers being reviewed
in those articles. Selection Criteria.
```

**[块 3]**

```text
since this criterion only
applies to the survey articles themselves, and not to the papers being reviewed
in those articles. Selection Criteria.

Papers that satisfy the inclusion criteria are selected for
the import process. The inclusion criteria are deﬁned as follows:
1. The article contains at least one table that lists scientiﬁc literature (i. e. , the
literature is presented in a semi-structured manner). 2. The article compares literature based on published results and does not solely
textually summarize the content of original papers. 3. The survey table should be in markup format and not included as raster
image. 4.
```

**[块 4]**

```text
not solely
textually summarize the content of original papers. 3. The survey table should be in markup format and not included as raster
image. 4.

The table structure should be suitable for import (e. g. , one table row should
provide information about one publication). 5. The article is written in English. Inclusion criterion 1 ensures that a survey article does not only textually sum-
marize the literature, but does also provide a semi-structured comparison (in
tabular form). Although papers that are textually reviewing scientiﬁc literature
are interesting for importing as well, it is out of scope for this work.
```

**[块 5]**

```text
form). Although papers that are textually reviewing scientiﬁc literature
are interesting for importing as well, it is out of scope for this work.

Criterion
2 ensures only surveys that compare actual paper results are included. This
excludes surveys researching, for instance, the growth of a ﬁeld. Criterion 3 ex-
cludes tables in image format. This is because of the tabular extraction method
we use, which is based on character extraction and does not use Optical Charac-
ter Recognition (OCR) needed to support image extraction [30]. Criterion 4 only
selects tables that are suitable for import.
```

**[块 6]**

```text
not use Optical Charac-
ter Recognition (OCR) needed to support image extraction [30]. Criterion 4 only
selects tables that are suitable for import.

Our methodology does only support
paper import when one row in a table represents one paper. Although minor.
```

**[块 7]（表格）**

```text
Table 1
Search engine | Field | Evaluated papers
Google Scholar ACM Digital Library | All Computer Science | 335 80
```


## 第 7 页 ⭐建议标注

**[块 1]**

```text
Creating a Scholarly Knowledge Graph from Survey Article Tables
7
changes can be made manually (e. g. merging multiple tables), in case the struc-
ture of the table deviates signiﬁcantly from the required format, the table is
excluded. Finally, criterion 5 ensures a homogeneous semantic integration into
the currently English monolingual knowledge graph. The result of this step is a
set of the selected papers in PDF format. 3.
```

**[块 2]**

```text
integration into
the currently English monolingual knowledge graph. The result of this step is a
set of the selected papers in PDF format. 3.

2
Table Extraction
This step focuses on extracting the tables from the PDF ﬁles collected in the
previous step. Not only the text within the table should be extracted, but the
tabular structure should be preserved as well. As explained in the related work
section, we use Tabula to perform the table extraction. Each PDF article is
uploaded via the Tabula user interface. Afterwards, the regions of the tables are
manually selected within the interface.
```

**[块 3]**

```text
Each PDF article is
uploaded via the Tabula user interface. Afterwards, the regions of the tables are
manually selected within the interface.

Although Tabula provides a functionality
to automatically detect tables, the accuracy is not suﬃcient for our use case. The
performance is especially low for articles with a two-column layout. Additionally,
not all tables within an article have to be extracted since not all of them are
listing and comparing literature. Arguably, the manual selection method is most
useful in this methodology since human judgment is needed in the selection
process.
```

**[块 4]**

```text
literature. Arguably, the manual selection method is most
useful in this methodology since human judgment is needed in the selection
process.

Part of the extraction step is quality assurance after the extraction. When needed, extraction errors are manually ﬁxed. Tabula supports two types
of extraction, namely “Stream” and “Lattice”. The Stream extraction method is
based on white space between columns while Lattice is based on boundary lines
between columns. During the extraction it is possible to switch between the
diﬀerent methods, which allows for selecting the best method for a particular
table.
```

**[块 5]**

```text
During the extraction it is possible to switch between the
diﬀerent methods, which allows for selecting the best method for a particular
table.

The result of this step is a set of CSV ﬁles, in which each ﬁle represents
one survey table from a review article. 3. 3
Table Formatting
The CSV ﬁles containing the extracted tables from the review articles should
be formatted in a structure that is suitable for building a graph. Since the data
from the CSV ﬁle is extracted automatically, all tables should have the same
format. In this step, the formatting of the tables is changed when necessary.
```

**[块 6]**

```text
the CSV ﬁle is extracted automatically, all tables should have the same
format. In this step, the formatting of the tables is changed when necessary.

For
some tables, a considerable amount of changes is required while for other tables
only minor changes are needed. Changes could include merging, splitting, adding
and removing both columns and rows. We use OpenReﬁne [31] to perform bulk
operations on tables. A table is formatted in such a way that it adheres to the
following rules:
1. The ﬁrst row of the table is the header. 2. Each row represents one reviewed paper. 3. Each row has a column called: “Reference”. 4.
```

**[块 7]**

```text
rules:
1. The ﬁrst row of the table is the header. 2. Each row represents one reviewed paper. 3. Each row has a column called: “Reference”. 4.

The reference cell should contain the citation key for a paper. 5. Non-literal values are preﬁxed with “[R]” in the column header. 6. When needed, abbreviations are replaced by the full value from the legend.
```


## 第 8 页 ⭐建议标注

**[块 1]**

```text
8
Oelen et al. For rule 2, in some cases a multidimensional table has to be ﬂattened. This can
often be accomplished by adding additional columns to the table. Also, in some
cases a table has to be transposed to ensure that each row contains one paper. Rules 3 and 4 ensure that bibliographic metadata can be fetched for each paper
in the next step. Rule 5 makes a distinction between literal values and resources.
```

**[块 2]**

```text
4 ensure that bibliographic metadata can be fetched for each paper
in the next step. Rule 5 makes a distinction between literal values and resources.

The default cell type is a literal, and when [R] is preﬁxed to a header label, the
cells are considered as resources. Finally, rule 6 makes the content of the table
readable without requiring the original text from the legend. Often table legends
are used to condense information to improve user readability. 3. 4
Extracting References
As mentioned earlier, each table row represents one paper. For each row, there is
a value that contains the reference key from the original paper.
```

**[块 3]**

```text
mentioned earlier, each table row represents one paper. For each row, there is
a value that contains the reference key from the original paper.

The reference key
is often a numerical reference, in the form of [n], where n represents the reference
number. In another frequently used citation style, the author names combined
with their publication year is used as a reference key. The citation key is used
to automatically capture the bibliographic metadata for an article. In order
to extract references from article, we use the PDF extraction tool GROBID. GROBID processes the full PDF article.
```

**[块 4]**

```text
metadata for an article. In order
to extract references from article, we use the PDF extraction tool GROBID. GROBID processes the full PDF article.

In the ﬁrst place to extract all citations
from the paper’s reference list and then to connect the citation keys used in the
text to their respective citation string. In case a reference key cannot be extracted
from the paper’s text, a reference key is generated automatically based on the
author’s name and publication year. When the citation is extracted and parsed, ﬁve additional columns are ap-
pended to the table: paper title, authors, publication month, publication year
and the DOI7.
```

**[块 5]**

```text
is extracted and parsed, ﬁve additional columns are ap-
pended to the table: paper title, authors, publication month, publication year
and the DOI7.

In case a citation key could not be automatically mapped to an
actual citation, a citation can be provided manually. The full citation text can
be copied directly from the paper (including paper title, authors etc. ) and is
then parsed by GROBID to get structured bibliographic metadata. To perform
the process of adding references, we created a Python script. 8 This script ﬁrst
tries to automatically fetch the metadata.
```

**[块 6]**

```text
metadata. To perform
the process of adding references, we created a Python script. 8 This script ﬁrst
tries to automatically fetch the metadata.

In case the reference is not found, a
command line input ﬁeld is displayed to enter the citation manually. 3. 5
Build Graph
The ﬁnal step is to build a knowledge graph from the previously created CSV
ﬁles. An example of the resulting graph for a single paper is depicted in Figure 3. Firstly, a settings ﬁle is created which lists the table numbers, a suitable title for
the table and a reference to the original survey article.
```

**[块 7]**

```text
3. Firstly, a settings ﬁle is created which lists the table numbers, a suitable title for
the table and a reference to the original survey article.

The reference is required
to attribute the work done by the authors of the survey article. The table title is
manually created based on the original table caption. In case no suitable caption
is available, a more suitable title is written. 7 Digital Object Identiﬁer
8 File 4 reference extraction. py from https://doi. org/10. 5281/zenodo. 3739427.
```


## 第 9 页 ⭐建议标注

**[块 1]**

```text
Creating a Scholarly Knowledge Graph from Survey Article Tables
9
Author
Publication month
Publication year
Contribution
DOI
Author
Time-varying
transmission dynamics of
Novel Coronavirus
Pneumonia in China
 Tao Liu
COVID-19 reproductive
number
Research problem
R0 estimates
Study date
95% CI
Methods
Location
Contribution 1
10. 1101/2020. 01. 25. 919787
1
2020
Study
Location
Study date
Methods
R0 estimates
95% CI
Joseph et al. 1
Wuhan
31 Dec '19 - 28 Jan '20
Stochastic Markov Chain. 2. 68
2. 47-2.
```

**[块 2]**

```text
date
Methods
R0 estimates
95% CI
Joseph et al. 1
Wuhan
31 Dec '19 - 28 Jan '20
Stochastic Markov Chain. 2. 68
2. 47-2.

86
Shen et al. 2
Hubei province
12-22 Jan. '20
Mathematical model, dynamic. 6. 49
6. 31-6. 66
Liu et al. 3
China and overseas
23. Jan '20
Statistical exponential Growth. 2. 90
2. 32-3. 63. 2. 90
23. Jan '20
China
2. 32-3. 63
Statistical exponential Growth,
using SARS generation. Fig. 3: Example of the resulting subgraph for importing a single paper from a
survey table. Metadata captured by reference extraction is displayed in blue.
```

**[块 3]**

```text
Example of the resulting subgraph for importing a single paper from a
survey table. Metadata captured by reference extraction is displayed in blue.

Data coming from the survey table is displayed in orange and ORKG speciﬁc
data is displayed in white. Next, a Python script9 is used to select all rows from the tables. For each row,
a paper is added to the graph via the ORKG API. For each table, a comparison is
created in ORKG. The title and reference from the previously generated settings
ﬁle are attached to this comparison.
```

**[块 4]**

```text
For each table, a comparison is
created in ORKG. The title and reference from the previously generated settings
ﬁle are attached to this comparison.

The comparison can be used later in ORKG
to generate the same tabular literature overview as originally presented in the
survey paper. 3. 6
User Interface
Based on the steps from our methodology, a web User Interace (UI) is created
that integrates all steps into a single interface. The interface provides a stream-
lined process for importing survey tables as depicted in Figure 4.
```

**[块 5]**

```text
integrates all steps into a single interface. The interface provides a stream-
lined process for importing survey tables as depicted in Figure 4.

The UI is
speciﬁcally designed to make importing a table an eﬀortless task without the
need of downloading any tools or the need to be able to operate these tools. In
the background, the same tools from the methodology are used to extract tables
(Tabula) and extract references (GROBID). The ﬁrst step is to upload a PDF
ﬁle and select the survey table within this ﬁle. Afterwards, the table is extracted
and the formatting can be ﬁxed with an integrated spreadsheet editor.
```

**[块 6]**

```text
and select the survey table within this ﬁle. Afterwards, the table is extracted
and the formatting can be ﬁxed with an integrated spreadsheet editor.

Then, for
each row the respective paper reference is extracted. Finally, the data is ingested
in the knowledge graph. The UI is not used to import the surveys tables presented in Section 4. The
interface is designed to import individual survey tables rather than importing
large amounts of tables at once. In the UI, all steps required to import a single
table should be performed consecutively. To increase eﬃciency when importing
9 File 5 build graph. py from https://doi. org/10. 5281/zenodo.
```

**[块 7]**

```text
a single
table should be performed consecutively. To increase eﬃciency when importing
9 File 5 build graph. py from https://doi. org/10. 5281/zenodo.

3739427.
```

**[块 8]（表格）**

```text
Table 1
Study | Location | Study date | Methods | R0 estimates | 95% CI
Joseph et al.1 | Wuhan | 31 Dec '19 - 28 Jan '20 | Stochastic Markov Chain... | 2.68 | 2.47-2.86
Shen et al.2 | Hubei province | 12-22 Jan. '20 | Mathematical model, dynamic... | 6.49 | 6.31-6.66
Liu et al.3 | China and overseas | 23. Jan '20 | Statistical exponential Growth... | 2.90 | 2.32-3.63
```


## 第 10 页 ⭐建议标注

**[块 1]**

```text
10
Oelen et al. (a) Select and extract table from PDF. (b) Fix table formatting, add references and
ingest in graph. Fig. 4: Survey table import User Interface integrating all steps from the method-
ology. large amounts of tables, it helps to ﬁrst ﬁnish a step for all papers before moving
to the next step. The UI provides a method to extend the graph beyond the
extracted surveys from this work. In the future, this interface will therefore be
integrated in the ORKG.
```

**[块 2]**

```text
a method to extend the graph beyond the
extracted surveys from this work. In the future, this interface will therefore be
integrated in the ORKG.

4
Results
In this section, we report the results of the import process for each step of the
methodology. Table 3 summarizes the results for all steps. 4. 1
Paper Selection
The dataset of the results are published online [24]. This set contains the selected
papers, the ORKG comparisons and the ingested papers. The selected papers ﬁle
lists IDs, paper titles, table references, sources and references.
```

**[块 3]**

```text
the ORKG comparisons and the ingested papers. The selected papers ﬁle
lists IDs, paper titles, table references, sources and references.

The IDs are used
to record any additional information about the import process for this speciﬁc
paper. IDs are missing for papers that were selected in the ﬁrst place, but were
excluded after revising the inclusion criteria. Additionally, table references refer
to the original table references used in the survey article. In total, 335 papers from Google Scholar were evaluated against the selection
criteria described in Section 3. 1.
```

**[块 4]**

```text
used in the survey article. In total, 335 papers from Google Scholar were evaluated against the selection
criteria described in Section 3. 1.

Out of these papers, 78 met the criteria and have
therefore been selected for importing. From the ACM Digital Library 80 papers
were evaluated and 14 papers have been selected. In total 22% of the evaluated
review papers are suitable to be imported with the presented approach.
```


## 第 11 页

**[块 1]**

```text
Creating a Scholarly Knowledge Graph from Survey Article Tables
11
Table 3: Summary of the results of all steps.
```

**[块 2]**

```text
Description
Amount
Paper selection
Amount of evaluated papers
415
Amount of selected papers
92
Table extraction
Total amount of extractions (partial tables)
265
Amount of extracted complete tables
160
Reference extraction
Found references
2 069
Not found references
1 137
Build graph
Individual amount of imported papers
2 626
Imported data cells (with metadata)
40 584
Imported data cells (without metadata)
21 240
4. 2
Table Extraction
We extracted 160 tables from the 92 survey articles.
```

**[块 3]**

```text
cells (with metadata)
40 584
Imported data cells (without metadata)
21 240
4. 2
Table Extraction
We extracted 160 tables from the 92 survey articles.

In 22 cases, tables stretched
across multiple pages, which results in a total of 265 extractions performed with
Tabula. Table 4 lists the most frequently occurred issues with the extraction. Issue 1 and 2 occur mostly when no boundary lines are present between table
columns. In this case, the Stream extraction method has to be used, which often
results in rows that are not correctly merged (e. g.
```

**[块 4]**

```text
between table
columns. In this case, the Stream extraction method has to be used, which often
results in rows that are not correctly merged (e. g.

, multi-line sentences are put
in separate rows while in the original table they are in the same row). Also, issue
3 is mostly present when using the Stream method. When the Lattice method
can be used for the extraction, the result is generally of higher quality. When no
table borders (or boundary lines) are present, this method does not work and
the Stream method has to be used.
```

**[块 5]**

```text
generally of higher quality. When no
table borders (or boundary lines) are present, this method does not work and
the Stream method has to be used.

Issue 4 is caused by general extraction errors,
Table 4: Issues that occurred during the extraction of tables from the survey
articles. Issues are counted per article. # Issue
Percentage %
1
Columns are not extracted correctly
26
2
Rows are not extracted correctly
14
3
Empty columns in the extracted table
14
4
Text not correctly recognized (e. g. , missing letters or formulas)
12
5
Issue with table header text
12
6
Vertical text not imported correctly
4
7
Cell value not supported (e. g.
```

**[块 6]**

```text
(e. g. , missing letters or formulas)
12
5
Issue with table header text
12
6
Vertical text not imported correctly
4
7
Cell value not supported (e. g.

, use of image instead of text check marks)
3
8
Table within table not extracted correctly
3.
```

**[块 10]（表格）**

```text
Table 4
# | Issue | Percentage %
1 2 3 4 5 6 7 8 | Columns are not extracted correctly Rows are not extracted correctly Empty columns in the extracted table Text not correctly recognized (e.g., missing letters or formulas) Issue with table header text Vertical text not imported correctly Cell value not supported (e.g., use of image instead of text check marks) Table within table not extracted correctly | 26 14 14 12 12 4 3 3
```


## 第 12 页

**[块 1]**

```text
12
Oelen et al. which can result in tables with wrongly extracted text. Additionally, formulas
and other text styling are not supported, which compounds this issue. Issues 7
and 8 result in tables that are not, or only partially, imported. The other issues
are self-explanatory. 4. 3
Reference Extraction
In total, we extracted unique 2 626 papers from 3 206 rows. For each paper, the
respective citation was retrieved.
```

**[块 2]**

```text
4. 3
Reference Extraction
In total, we extracted unique 2 626 papers from 3 206 rows. For each paper, the
respective citation was retrieved.

In 2 069 cases the citation could be extracted
automatically from the row (65% of the cases). In 1 137 cases it was not possible
to automatically extract the reference (35% of the cases). For those cases, the
citation is manually copied from the paper. There were multiple reasons why
automatic reference extraction was not successful. Most issues occurred for ref-
erences that used a numeric citation key.
```

**[块 3]**

```text
were multiple reasons why
automatic reference extraction was not successful. Most issues occurred for ref-
erences that used a numeric citation key.

GROBID’s performance for extracting
numeric references from tables was low, oftentimes numeric table references were
not recognized. The amount of rows is higher than the amount of extracted pa-
pers because multiple rows could refer to the same paper. Each paper only has
one graph entry and any additional data is added to the existing paper. In case a reference is only used in a table and not somewhere else in the
article, automatic reference extraction was oftentimes not possible.
```

**[块 4]**

```text
paper. In case a reference is only used in a table and not somewhere else in the
article, automatic reference extraction was oftentimes not possible.

When an
author name was used as citation key, problems occurred mostly because of the
diﬀerent citation styles. While some citation formats only use the last name of the
ﬁrst author, suﬃxed by et al. , other formats could list all author names. When
a format was used that deviates from the standard implementation, automatic
extraction was not possible. 4. 4
Build Graph
In total, we added 2 626 papers to the knowledge graph. These papers are used in
160 diﬀerent comparisons.
```

**[块 5]**

```text
was not possible. 4. 4
Build Graph
In total, we added 2 626 papers to the knowledge graph. These papers are used in
160 diﬀerent comparisons.

A complete list of the generated ORKG comparisons
and a list of all ingested papers is available via [24]. In total, 21 240 table cells
have been imported, excluding the bibliographic metadata. Including metadata,
the total is 40 584 data cells. 5
Discussion and Future Work
5. 1
Time Performance
The presented methodology takes a human-in-the-loop approach as opposed to a
fully automated approach. Compared to a fully manual approach, the proposed
approach saves considerable time.
```

**[块 6]**

```text
approach as opposed to a
fully automated approach. Compared to a fully manual approach, the proposed
approach saves considerable time.

In previous work [23], we manually imported
only four survey articles. On average, this process took 4 hours per article. For
each of the papers, a Python script was created speciﬁcally to import the survey
table with its references and data. An example of such a script for one paper can.
```


## 第 13 页

**[块 1]**

```text
Creating a Scholarly Knowledge Graph from Survey Article Tables
13
be found online. 10 For the methodology used in this paper, the time to import
one survey article was on average 15 minutes. Compared to the 4 hours of the
manual approach, this is considerably faster (i. e. , 16 fold increase in speed). The
minimum amount of time needed to import a relatively small table was 2 min-
utes. The table could be extracted without any issues.
```

**[块 2]**

```text
in speed). The
minimum amount of time needed to import a relatively small table was 2 min-
utes. The table could be extracted without any issues.

The maximum amount
of required time was approximately 60 minutes. This was for a table with a
complex layout, stretched across multiple pages. Also, this table did not have
boundary lines. Most time was spend on ﬁxing extraction issues. To further im-
prove time performance, we identiﬁed two tasks that are time-consuming and
can potentially be improved. The ﬁrst task relates to ﬁxing errors occurred dur-
ing the table extraction by Tabula.
```

**[块 3]**

```text
that are time-consuming and
can potentially be improved. The ﬁrst task relates to ﬁxing errors occurred dur-
ing the table extraction by Tabula.

Most errors occurred when tables did not
have boundary lines between columns and rows. A potential solution, and pos-
sible future research direction, is to create an interface that supports manually
drawing boundary lines between rows and columns. The second task is related
to adding missing references, which have to be manually copied from the PDF
article. In total, 65% of the references were extracted automatically.
```

**[块 4]**

```text
adding missing references, which have to be manually copied from the PDF
article. In total, 65% of the references were extracted automatically.

By applying
more advanced heuristics to match reference keys with their respective reference,
this percentage can be improved. 5. 2
Impact of Methodology
The impact of the methodology relates to the amount of survey papers that
are suitable for our approach (i. e. , surveys representing information in tabular
format). To order to provide insights on the impact, a structured search protocol
has been employed in the paper selection step.
```

**[块 5]**

```text
in tabular
format). To order to provide insights on the impact, a structured search protocol
has been employed in the paper selection step.

As the results show, out of the
415 evaluated papers, 92 of them are suitable to be imported. This indicates
that since 2002, 22% of the published survey papers contain comparison tables. Therefore, arguably our methodology can have considerable impact when applied
more broadly. In the paper selection, non-survey papers were excluded. However,
it is not uncommon for research articles to also contain tables with related work
(e. g. , Table 1 in this article).
```

**[块 6]**

```text
papers were excluded. However,
it is not uncommon for research articles to also contain tables with related work
(e. g. , Table 1 in this article).

Thus the paper selection step could be extended
to also include other articles to have a broader impact. 5. 3
Semantics of Data
The extracted knowledge graph consists of structured scholarly data. The quality
of the knowledge graph could be further improved by providing more semantics
to the data. Currently, a primitive method is used to map existing properties
and resources. This is based on a lookup by resource label, in case a result
is found, the resource is mapped.
```

**[块 7]**

```text
is used to map existing properties
and resources. This is based on a lookup by resource label, in case a result
is found, the resource is mapped.

If not, a new resource is created. A more
advanced mapping of resources and properties to existing ontologies improves
the machine readability of the data. Tables containing large amounts of natural
text (e. g. , textually describing a methodology) could be further processed using
10 https://gitlab. com/TIBHannover/orkg/orkg-papers/-/blob/master/
question-answering-import. py.
```


## 第 14 页

**[块 1]**

```text
14
Oelen et al. named entity recognition and linking. This results in more structured data and
therefore a higher quality knowledge graph. Approaches to improve the overall
quality of the graph are part of future work. 5. 4
Future Research Directions
In total, we extracted 92 survey articles from a variety of domains. In the future,
more survey articles will be ingested in ORKG. This will be done for multiple
domains. The User Interface (UI) presented in Section 3.
```

**[块 2]**

```text
In the future,
more survey articles will be ingested in ORKG. This will be done for multiple
domains. The User Interface (UI) presented in Section 3.

6 can be used to support
users to import survey tables. The UI will be further improved to make to process
more eﬃcient. Due to the dynamic nature of the interface (especially compared
to a regular spreadsheet editor), mapping properties and resources to existing
concepts is better supported. In the end, we aim to import as many surveys from
a speciﬁc domain as possible. There are several reasons why such an approach
is useful.
```

**[块 3]**

```text
supported. In the end, we aim to import as many surveys from
a speciﬁc domain as possible. There are several reasons why such an approach
is useful.

In the ﬁrst place, ORKG can serve as a digital library for literature
surveys. As discussed in the related work, the platform provides tools to better
ﬁnd and organize surveys. Additionally, when all existing reviews for a domain
are imported, the ORKG can be used as a source to ﬁnd literature surveys. In
case a survey is not present in the ORKG, it means that is does not (yet) exist. This can be used as a basis to start working on new literature surveys.
```

**[块 4]**

```text
a survey is not present in the ORKG, it means that is does not (yet) exist. This can be used as a basis to start working on new literature surveys.

6
Conclusions
Knowledge graphs are useful to make scholarly knowledge more machine action-
able. Manually building such a knowledge graph is time-consuming and requires
the expertise of paper authors and domain experts. In order to eﬃciently build
a high-quality scholarly knowledge graph, we leverage survey tables from review
articles.
```

**[块 5]**

```text
authors and domain experts. In order to eﬃciently build
a high-quality scholarly knowledge graph, we leverage survey tables from review
articles.

Generally, survey tables contain high-quality, relevant, semi-structured
and manually curated data, and are therefore an excellent source for building a
scholarly knowledge graph. We presented a methodology used to extract 2 626
papers from 92 survey articles. The methodology adopts a human-in-the-loop
approach to ensure the quality and usefulness of the extracted data.
```

**[块 6]**

```text
2 626
papers from 92 survey articles. The methodology adopts a human-in-the-loop
approach to ensure the quality and usefulness of the extracted data.

Compared
to manually reviewing and entering research data, or to manually importing
literature surveys, the methodology is considerably more eﬃcient. In conclu-
sion, the presented methodology provides a full pipeline that can be used to
extract knowledge from PDF documents and represent the extracted knowledge
in a knowledge graph. The corresponding evaluation with survey articles demon-
strates the eﬀectiveness and eﬃciency of the proposed methodology. Acknowledgements.
```

**[块 7]**

```text
graph. The corresponding evaluation with survey articles demon-
strates the eﬀectiveness and eﬃciency of the proposed methodology. Acknowledgements.

This work was co-funded by the European Research Coun-
cil for the project ScienceGRAPH (Grant agreement ID: 819536) and the TIB
Leibniz Information Centre for Science and Technology. We want to thank our
colleagues Mohamad Yaser Jaradeh and Kheir Eddine Farfar for their contribu-
tions to this work.
```


## 第 15 页

**[块 1]**

```text
Creating a Scholarly Knowledge Graph from Survey Article Tables
15
References
1. Adelﬁo,
M. D. ,
Samet,
H. :
Schema
extraction
for
tabular
data
on
the
web. Proceedings
of
the
VLDB
Endowment
6,
421–432
(2013). https://doi. org/10. 14778/2536336. 2536343
2. Corrˆea, A. S. , Corrˆea, P. L. P. , Da Silva, F. S. C. : Transparency portals versus
open government data. An assessment of openness in Brazilian municipal-
ities. ACM International Conference Proceeding Series pp. 178–185 (2014). https://doi.
```

**[块 2]**

```text
data. An assessment of openness in Brazilian municipal-
ities. ACM International Conference Proceeding Series pp. 178–185 (2014). https://doi.

org/10. 1145/2612733. 2612760
3. Corrˆea, A. S. , Zander, P. O. : Unleashing tabular content to open data: A survey on
PDF table extraction methods and tools. ACM International Conference Proceed-
ing Series pp. 54–63 (2017). https://doi. org/10. 1145/3085228. 3085278
4. Fathalla, S. , Vahdati, S. , Auer, S. , Lange, C. : Towards a Knowledge Graph Rep-
resenting Research Findings by Semantifying Survey Articles. In: Research and
Advanced Technology for Digital Libraries. pp. 315–327 (2017)
5.
```

**[块 3]**

```text
Rep-
resenting Research Findings by Semantifying Survey Articles. In: Research and
Advanced Technology for Digital Libraries. pp. 315–327 (2017)
5.

Gall, M. D. , Borg, W. R. : Educational Research: An introduction (sixth edition). White Plains, NY: Longman Publishers USA (1996)
6. Hart, C. : Doing a Literature Review: Releasing the Social Science Research Imag-
ination. Sage (1998)
7. Hassan, T. , Baumgartner, R. : Table recognition and understanding from PDF ﬁles. Proceedings of the International Conference on Document Analysis and Recogni-
tion, ICDAR pp. 1143–1147 (2007). https://doi. org/10. 1109/ICDAR. 2007. 4377094
8. Herrmannova, D.
```

**[块 4]**

```text
Conference on Document Analysis and Recogni-
tion, ICDAR pp. 1143–1147 (2007). https://doi. org/10. 1109/ICDAR. 2007. 4377094
8. Herrmannova, D.

, Knoth, P. : An analysis of the microsoft academic graph. D-lib
Magazine 22(9/10) (2016). https://doi. org/10. 1045/september2016-herrmannova
9. Hyv¨onen, E. : Publishing and using cultural heritage linked data on the semantic
web. Synthesis Lectures on the Semantic Web: Theory and Technology 2(1), 1–159
(2012). https://doi. org/10. 2200/S00452ED1V01Y201210WBE003
10. Jaradeh, M. Y. , Oelen, A. , Farfar, K. E. , Prinz, M. , D’Souza, J. , Kismih´ok, G. ,
Stocker, M. , Auer, S.
```

**[块 5]**

```text
2200/S00452ED1V01Y201210WBE003
10. Jaradeh, M. Y. , Oelen, A. , Farfar, K. E. , Prinz, M. , D’Souza, J. , Kismih´ok, G. ,
Stocker, M. , Auer, S.

: Open research knowledge graph: Next generation in-
frastructure for semantic scholarly knowledge. K-CAP 2019 - Proceedings of
the 10th International Conference on Knowledge Capture pp. 243–246 (2019). https://doi. org/10. 1145/3360901. 3364435
11. Jiang, D. , Yang, X. : Converting PDF to HTML approach based on text
detection. Proceedings
of
the
2nd
international
conference
on
interaction
sciences: Information technology, culture and human 403, 982–985 (2009). https://doi. org/10. 1145/1655925.
```

**[块 6]**

```text
Information technology, culture and human 403, 982–985 (2009). https://doi. org/10. 1145/1655925.

1656103
12. Klampﬂ, S. , Granitzer, M. , Jack, K. , Kern, R. : Unsupervised document structure
analysis of digital scientiﬁc articles. International Journal on Digital Libraries 14(3-
4), 83–99 (2014). https://doi. org/10. 1007/s00799-014-0115-1
13. K¨orner, M. , Ghavimi, B. , Mayr, P. , Hartmann, H. , Staab, S. : Evaluating Reference
String Extraction Using Line-Based Conditional Random Fields: A Case Study
with German Language Publications. In: New Trends in Databases and Information
Systems.
```

**[块 7]**

```text
Using Line-Based Conditional Random Fields: A Case Study
with German Language Publications. In: New Trends in Databases and Information
Systems.

pp. 137–145. Cham (2017)
14. Krotzsch,
M. ,
Vrandecic,
D. :
Wikidata
:
A
Free
Collaborative
Knowl-
edge
Base. Communications
of
the
ACM
57(10),
78–85
(2014). https://doi. org/10. 1145/2629489
15. Lammey, R. : CrossRef text and data mining services. Insights: the UKSG Journal
28(2), 62–68 (2015). https://doi. org/10. 1629/uksg. 233.
```


## 第 16 页

**[块 1]**

```text
16
Oelen et al. 16. Lehmberg, O. , Ritze, D. , Meusel, R. , Bizer, C. : A large public corpus of web
tables containing time and context metadata. Proceedings of the 25th Interna-
tional Conference Companion on World Wide Web - WWW ’16 Companion (2016). https://doi. org/10. 1145/2872518. 2889386
17. Lipinski, M. , Yao, K. , Breitinger, C. , Beel, J. , Gipp, B. : Evaluation of header
metadata extraction approaches and tools for scientiﬁc PDF documents.
```

**[块 2]**

```text
M. , Yao, K. , Breitinger, C. , Beel, J. , Gipp, B. : Evaluation of header
metadata extraction approaches and tools for scientiﬁc PDF documents.

Proceed-
ings of the ACM/IEEE Joint Conference on Digital Libraries pp. 385–386 (2013). https://doi. org/10. 1145/2467696. 2467753
18. Liu, Y. , Bai, K. , Mitra, P. , Giles, C. L. : Tableseer: automatic table metadata ex-
traction and searching in digital libraries. Proceedings of the 2007 conference on
Digital libraries - JCDL ’07 (2007). https://doi. org/10. 1145/1255175. 1255193
19. Lopez, P.
```

**[块 3]**

```text
libraries. Proceedings of the 2007 conference on
Digital libraries - JCDL ’07 (2007). https://doi. org/10. 1145/1255175. 1255193
19. Lopez, P.

: GROBID: Combining automatic bibliographic data recognition and term
extraction for scholarship publications. International conference on theory and
practice of digital libraries 5714, 473–474 (2009). https://doi. org/10. 1007/978-
3-642-04346-8 62
20. M¨akel¨a, E. , Hyv¨onen, E. , Ruotsalo, T. : How to deal with massively heterogeneous
cultural heritage data - Lessons learned in CultureSampo. Semantic Web 3(1),
85–109 (2012). https://doi. org/10. 3233/sw-2012-0049
21. Mons, B. , Velterop, J.
```

**[块 4]**

```text
heritage data - Lessons learned in CultureSampo. Semantic Web 3(1),
85–109 (2012). https://doi. org/10. 3233/sw-2012-0049
21. Mons, B. , Velterop, J.

: Nano-publication in the e-science era. In: Workshop on Se-
mantic Web Applications in Scientiﬁc Discourse (SWASD 2009). pp. 14–15 (2009)
22. Oelen, A. , Jaradeh, M. Y. , Farfar, K. E. , Stocker, M. , Auer, S. : Comparing Research
Contributions in a Scholarly Knowledge Graph. In: Proceedings of the Third In-
ternational Workshop on Capturing Scientiﬁc Knowledge (SciKnow19). pp. 21–26
(2019)
23. Oelen,
A. ,
Jaradeh,
M. Y. ,
Stocker,
M. ,
Auer,
S.
```

**[块 5]**

```text
Third In-
ternational Workshop on Capturing Scientiﬁc Knowledge (SciKnow19). pp. 21–26
(2019)
23. Oelen,
A. ,
Jaradeh,
M. Y. ,
Stocker,
M. ,
Auer,
S.

:
Generate
FAIR
Liter-
ature
Surveys
with
Scholarly
Knowledge
Graphs. JCDL
’20:
The
20th
ACM/IEEE
Joint
Conference
on
Digital
Libraries
(In
Press)
(2020). https://doi. org/10. 1145/3383583. 3398520
24. Oelen, A. , Stocker, M. , Auer, S. : Dataset for Creating a Scholarly Knowledge Graph
from Survey Article Tables (2020). https://doi. org/10. 5281/ZENODO. 3735152
25. Rastan,
R. ,
Paik,
H. Y. ,
Shepherd,
J. :
Texus. Proceedings
of
the
2015
ACM
Symposium
on
Document
Engineering
-
DocEng
’15
(2015).
```

**[块 6]**

```text
3735152
25. Rastan,
R. ,
Paik,
H. Y. ,
Shepherd,
J. :
Texus. Proceedings
of
the
2015
ACM
Symposium
on
Document
Engineering
-
DocEng
’15
(2015).

https://doi. org/10. 1145/2682571. 2797069
26. Ros, G. : Analysis of Tabula : A PDF-Table extraction tool (2019)
27. Skjæveland, M. G. , Lian, E. H. , Horrocks, I. : Publishing the Norwegian Petroleum
Directorate’s FactPages as semantic web data. International Semantic Web Con-
ference 8219, 162–177 (2013). https://doi. org/10. 1007/978-3-642-41338-4 11
28. Takis, J. , Islam, A. S. , Lange, C. , Auer, S. : Crowdsourced semantic annota-
tion of scientiﬁc publications and tabular data in pdf.
```

**[块 7]**

```text
11
28. Takis, J. , Islam, A. S. , Lange, C. , Auer, S. : Crowdsourced semantic annota-
tion of scientiﬁc publications and tabular data in pdf.

SEMANTICS ’15 Pro-
ceedings of the 11th International Conference on Semantic Systems (2015). https://doi. org/10. 1145/2814864. 2814887
29. Vahdati, S. , Fathalla, S. , Auer, S. , Lange, C. , Vidal, M. E. : Semantic Represen-
tation of Scientiﬁc Publications. International Conference on Theory and Prac-
tice of Digital Libraries 11799, 375–379 (2019). https://doi. org/10. 1007/978-3-
030-30760-8 37
30. Vasileiadis, M. , Kaklanis, N. , Votis, K. , Tzovaras, D.
```

**[块 8]**

```text
Libraries 11799, 375–379 (2019). https://doi. org/10. 1007/978-3-
030-30760-8 37
30. Vasileiadis, M. , Kaklanis, N. , Votis, K. , Tzovaras, D.

: Extraction of tabular data
from document images. Proceedings of the 14th Web for All Conference, W4A
(2017). https://doi. org/10. 1145/3058555. 3058581
31. Verborgh, R. , De Wilde, M. : Using OpenReﬁne. Packt Publishing Ltd (2013)
32. Webster, J. , Watson, R. T. : Analyzing the Past to Prepare for the Future: Writing
a Literature Review. MIS Quarterly 26(2), xiii – xxiii (2002).
```


## 第 17 页

**[块 1]**

```text
Creating a Scholarly Knowledge Graph from Survey Article Tables
17
33. Wee, B. V. , Banister, D. : How to Write a Literature Review Paper. Transport
Reviews 36(2), 278–288 (2016). https://doi. org/10. 1080/01441647. 2015. 1065456
34. Wilkinson, M. D. , Dumontier, M. , Aalbersberg, I. J. , Appleton, G. , Axton, M. ,
Baak, A. , Blomberg, N. , Boiten, J. W. , da Silva Santos, L. B. , Bourne, P. E. , Bouw-
man, J. , Brookes, A. J. , Clark, T. , Crosas, M. , Dillo, I. , Dumon, O. , Edmunds, S.
```

**[块 2]**

```text
J. W. , da Silva Santos, L. B. , Bourne, P. E. , Bouw-
man, J. , Brookes, A. J. , Clark, T. , Crosas, M. , Dillo, I. , Dumon, O. , Edmunds, S.

,
Evelo, C. T. , Finkers, R. , Gonzalez-Beltran, A. , Gray, A. J. , Groth, P. , Goble, C. ,
Grethe, J. S. , Heringa, J. , t Hoen, P. A. , Hooft, R. , Kuhn, T. , Kok, R. , Kok, J. ,
Lusher, S. J. , Martone, M. E. , Mons, A. , Packer, A. L. , Persson, B. , Rocca-Serra,
P. , Roos, M. , van Schaik, R. , Sansone, S. A. , Schultes, E. , Sengstag, T. , Slater,
T. , Strawn, G. , Swertz, M. A. , Thompson, M. , Van Der Lei, J. , Van Mulligen, E. ,
Velterop, J. , Waagmeester, A. , Wittenburg, P.
```

**[块 3]**

```text
T. , Slater,
T. , Strawn, G. , Swertz, M. A. , Thompson, M. , Van Der Lei, J. , Van Mulligen, E. ,
Velterop, J. , Waagmeester, A. , Wittenburg, P.

, Wolstencroft, K. , Zhao, J. , Mons,
B. : Comment: The FAIR Guiding Principles for scientiﬁc data management and
stewardship. Scientiﬁc Data 3, 1–9 (2016). https://doi. org/10. 1038/sdata. 2016. 18.
```
