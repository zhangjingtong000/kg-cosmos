"""云通道行格式解析的回归测试。

背景（2026-09-10 实测）：ENTITY_PROMPT 的样例输出是四段
`Name<|>Name<|>Type<|>Description`，但旧 parse_entities 假定三段并把第二段
当类型做白名单校验，导致**云端抽取的实体 100% 被丢弃**——这才是"111 个 chunk
全部 0 实体"的真正原因。本文件把每种真实输出形态钉住。
"""

from kg_extractor import ENTITY_TYPES, parse_entities, parse_relations


def test_four_field_cloud_format_is_parsed():
    """真实云端输出：名字重复两次，类型在第三段。"""
    raw = (
        "Microsoft Academic Graph<|>Microsoft Academic Graph<|>System<|>Bibliographic metadata system.\n"
        "Crossref<|>Crossref<|>System<|>A DOI registration agency.\n"
        "ORKG<|>ORKG<|>System<|>Publishes research contributions.\n"
    )
    entities = parse_entities(raw)
    assert [e["name"] for e in entities] == [
        "Microsoft Academic Graph", "Crossref", "ORKG"
    ]
    assert [e["type"] for e in entities] == ["System", "System", "System"]
    assert entities[0]["description"] == "Bibliographic metadata system."


def test_legacy_three_field_format_still_parses():
    raw = "TransE<|>Method<|>Translational embedding model."
    entities = parse_entities(raw)
    assert entities == [
        {"name": "TransE", "type": "Method", "description": "Translational embedding model."}
    ]


def test_entity_prefix_is_tolerated():
    raw = "entity<|>Convolutional Network<|>Method<|>A neural architecture."
    entities = parse_entities(raw)
    assert entities[0]["name"] == "Convolutional Network"
    assert entities[0]["type"] == "Method"


def test_type_first_order_is_tolerated():
    raw = "Dataset<|>WMT 2014<|>English-German benchmark."
    entities = parse_entities(raw)
    assert entities[0]["name"] == "WMT 2014"
    assert entities[0]["type"] == "Dataset"


def test_out_of_ontology_type_is_dropped():
    """类型不在 15 类本体内的行必须丢弃，不能污染下游。"""
    raw = (
        "TransE<|>TransE<|>ORKG<|>Not a valid type.\n"
        "TransE<|>TransE<|>Method<|>Valid one.\n"
    )
    entities = parse_entities(raw)
    assert len(entities) == 1
    assert entities[0]["type"] == "Method"


def test_blank_lines_completed_marker_and_markdown_bullets():
    raw = (
        "\n"
        "- TransE<|>TransE<|>Method<|>Bulleted line.\n"
        "<|COMPLETE|>\n"
        "   \n"
        "Not a pipe line at all\n"
    )
    entities = parse_entities(raw)
    assert len(entities) == 1
    assert entities[0]["name"] == "TransE"


def test_empty_input():
    assert parse_entities("") == []
    assert parse_entities("no delimiters here") == []


def test_all_ontology_types_are_recognised():
    """15 类本体每一类都能被识别（防止白名单拼写漂移）。"""
    for t in ENTITY_TYPES:
        entities = parse_entities(f"Thing<|>Thing<|>{t}<|>desc")
        assert len(entities) == 1, f"{t} 未被识别"
        assert entities[0]["type"] == t


def test_relation_four_field_format():
    raw = "TransE<|>WMT 2014<|>EVALUATES<|>Reported BLEU here."
    relations = parse_relations(raw)
    assert len(relations) == 1
    assert relations[0]["source"] == "TransE"
    assert relations[0]["target"] == "WMT 2014"
    assert relations[0]["type"] == "EVALUATES"


def test_relation_type_first_order_is_reordered():
    """部分模型会把类型放在第一段，解析器需要自动换位。"""
    raw = "EVALUATES<|>TransE<|>WMT 2014<|>desc"
    relations = parse_relations(raw)
    assert relations[0]["source"] == "TransE"
    assert relations[0]["target"] == "WMT 2014"
    assert relations[0]["type"] == "EVALUATES"
