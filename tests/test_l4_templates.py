"""L4/L5 假设句模板表（`src/ontology.py`）的回归测试。

【背景：这三件事都会**静默**出错】
  新 L4（`tools/l4_predicate_report.py`）用 15 个假设句模板与同一条证据句算
  entail_prob、取 argmax 作预测谓词。因此模板表一旦有问题，argmax 会安静地偏向
  某个错误的类，而所有下游数字看起来都"正常"：

  1. **缺一类** → 候选集少一格，预测分布整体偏移，不报错；
  2. **占位符写错**（如 `{S}`）→ `.format()` 直接 KeyError 是好事，
     但写成 `{s}` 与 `{t}` 之外的东西（如 `{x}`）会在运行时才炸；
  3. **`CREATED_BY` 方向写反** → 这一条**真的发生过**：T3.1 把方向统一为
     Author(source) → Work(target) 后，模板仍是 `"{s} was created by {t}."`，
     该类通过率从 39.6% 掉到 26.0%（docs/L5_SIX_LAYER_REPORT.md §1.1c）。

  另有一条：`relation_verifier.py` 与 `verify_direction.py` 各存了一份
  `REL_TEMPLATE` 字面量（注释写着"改这里必须同步改那边"）。本项目已因
  "同一规则写两处"栽过三次（docs/ERROR_CASES.md 案例 12），所以这里把三处
  逐字相减，让漂移在 CI 里就报出来。
"""
import re
from pathlib import Path

import pytest

from ontology import (
    REL_HYPOTHESIS_TEMPLATE, REL_PHRASE_TEMPLATE, RELATION_TYPES,
    SYMMETRIC_RELATIONS, validate_constraints,
)

ROOT = Path(__file__).resolve().parent.parent

# 两份**独立维护**的字面量副本（唯一来源是 src/ontology.py）
COPIES = ['tools/relation_verifier.py', 'tools/verify_direction.py']


def _placeholders(tpl: str) -> list:
    return re.findall(r'\{[^}]*\}', tpl)


def _parse_str_dict(text: str, name: str):
    """解析 `name = { "KEY": "value", ... }`（与 check_ontology_consistency.py 同法）。"""
    m = re.search(re.escape(name) + r'\s*=\s*\{(.*?)\n\}', text, re.S)
    if not m:
        return None
    return dict(re.findall(r'"([A-Z_]+)"\s*:\s*"([^"]*)"', m.group(1)))


# ── 模板表本身 ──────────────────────────────────────────────

@pytest.mark.parametrize('table', [REL_HYPOTHESIS_TEMPLATE, REL_PHRASE_TEMPLATE])
def test_templates_cover_exactly_the_ontology(table):
    assert set(table) == set(RELATION_TYPES), (
        f'模板表与本体不一致：缺 {sorted(set(RELATION_TYPES) - set(table))}、'
        f'多 {sorted(set(table) - set(RELATION_TYPES))}')


@pytest.mark.parametrize('table', [REL_HYPOTHESIS_TEMPLATE, REL_PHRASE_TEMPLATE])
def test_templates_use_only_s_and_t_placeholders(table):
    for name, tpl in table.items():
        ph = _placeholders(tpl)
        assert '{s}' in ph and '{t}' in ph, f'{name} 缺 s/t 占位符: {tpl!r}'
        assert not [p for p in ph if p not in ('{s}', '{t}')], \
            f'{name} 含非法占位符 {tpl!r}'


@pytest.mark.parametrize('table', [REL_HYPOTHESIS_TEMPLATE, REL_PHRASE_TEMPLATE])
def test_templates_format_without_error(table):
    """每条模板都必须能被 (s, t) 代入——占位符漂移会在这里立刻暴露。"""
    for name, tpl in table.items():
        out = tpl.format(s='SOURCE_X', t='TARGET_Y')
        assert 'SOURCE_X' in out and 'TARGET_Y' in out, f'{name} 代入后丢了端点: {out!r}'


def test_created_by_direction_is_author_to_work():
    """方向守卫：T3.1 统一为 Author(source) → Work(target)。

    句子形态必须读作「作品由作者创作」；名词短语形态必须读作「作者创作作品」。
    这两条被改反过一次，所以**逐字**锁死，不接受"看起来差不多"的改写。
    """
    assert REL_HYPOTHESIS_TEMPLATE['CREATED_BY'] == '{t} was created by {s}.'
    assert REL_PHRASE_TEMPLATE['CREATED_BY'] == '{s} created {t}'
    # 代入后：作者在前、作品在后（被动句中作者出现在 "by" 之后）
    sent = REL_HYPOTHESIS_TEMPLATE['CREATED_BY'].format(
        s='ALICE_AUTHOR', t='PAPER_WORK')
    assert sent == 'PAPER_WORK was created by ALICE_AUTHOR.'
    assert sent.index('ALICE_AUTHOR') > sent.index('PAPER_WORK'), \
        'CREATED_BY 模板的施事/受事位置反了'


def test_symmetric_relations_have_no_directional_verb():
    """对称关系（RELATED_TO/COMPARES_WITH/CONTRADICTS）的模板反转后语义必须不变。

    字符串层面无法判定"语义不变"，所以改成一条可执行的弱判据：
    它们的模板里不能出现明确的单向动词。真正的一致性由
    ontology.validate_constraints() 与 verify_direction.py 的运行结果守。
    """
    directional = ('creates', 'created', 'produces', 'causes', 'enables',
                   'uses', 'cites', 'measures', 'evaluates')
    for name in sorted(SYMMETRIC_RELATIONS):
        tpl = REL_HYPOTHESIS_TEMPLATE[name].lower()
        bad = [w for w in directional if w in tpl]
        assert not bad, f'对称关系 {name} 的模板含单向动词 {bad}: {tpl!r}'


def test_validate_constraints_is_clean():
    """表自身完整性检查必须为空（含模板覆盖性与 CREATED_BY 方向）。"""
    assert validate_constraints() == []


# ── 三处副本必须逐字一致 ────────────────────────────────────

@pytest.mark.parametrize('rel', COPIES)
def test_template_copy_matches_ontology(rel):
    text = (ROOT / rel).read_text(encoding='utf-8')
    if 'REL_HYPOTHESIS_TEMPLATE' in text:
        pytest.skip(f'{rel} 已改为引用 src/ontology.py，无需比对字面量')
    got = _parse_str_dict(text, 'REL_TEMPLATE')
    assert got is not None, f'{rel} 里未解析到 REL_TEMPLATE，定义方式可能变了'
    diff = {k: (got.get(k), REL_HYPOTHESIS_TEMPLATE.get(k))
            for k in set(got) | set(REL_HYPOTHESIS_TEMPLATE)
            if got.get(k) != REL_HYPOTHESIS_TEMPLATE.get(k)}
    assert not diff, (
        f'{rel} 的 REL_TEMPLATE 与 src/ontology.py 的 REL_HYPOTHESIS_TEMPLATE 不一致：'
        f'{diff}（唯一来源是 ontology；这就是案例 12 说的"同一规则写两处"）')
