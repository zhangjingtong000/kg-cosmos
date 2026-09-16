# -*- coding: utf-8 -*-
r"""K1 的写入端：`POST /api/kg/edit-plan`（**只落计划，不改图**）。

## 这个端点解决什么

`src/app.py` 里 `/api/flag` 的 docstring 写着「**这些标记没有被应用到图上**」——
那是有意的（改图会改变全库实体数、打破六层指标可比性），
**但"只记录不执行"意味着用户的操作没有出口**。

K1 沿用项目已有的 `plan → apply` 先例（`venue-rewrite-plan.json`）：
**端点只写计划文件（秒回），执行交给 `tools/apply_edit_plan.py`**
（它会先归档指标、快照真源、改完重跑 6 个下游、再归档并打 before/after diff）。

## 测试的写入隔离

`KG_EDIT_PLAN_DIR` 把计划目录指到 `tmp_path` ——
**这是本项目第三次应用同一条纪律**（前两次：归档目录、图目录）。
**两次都是"测试污染了真实数据"才发现要加的**，所以这次一开始就带上。

断言分两组：
  · **行为**：合法提交能落盘、返回里给出下一步命令；
  · **拒绝**：空操作 / 未知操作 / 缺字段都要 400，**且错误信息要具体**
    （不能只说"参数错误"）。
"""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

os.environ.setdefault('KG_BACKEND', 'memory')

REAL_DIR = ROOT / 'data' / 'quality' / 'edit-plans'


@pytest.fixture
def client(tmp_path, monkeypatch):
    """把计划目录指到临时目录，**不污染真实的 edit-plans/**。"""
    monkeypatch.setenv('KG_EDIT_PLAN_DIR', str(tmp_path / 'plans'))
    from fastapi.testclient import TestClient
    import app as appmod
    return TestClient(appmod.app, raise_server_exceptions=False)


def test_valid_plan_is_saved_and_says_what_to_do_next(client, tmp_path):
    before = set(REAL_DIR.glob('*.json')) if REAL_DIR.exists() else set()
    r = client.post('/api/kg/edit-plan', json={
        'operations': [
            {'op': 'rename_entity', 'from': 'TransE', 'to': 'TransE (Bordes 2013)',
             'reason': '区分同名'},
            {'op': 'delete_edge', 'source': 'A', 'target': 'B', 'type': 'USES',
             'reason': '原文不支持'},
        ],
        'note': 'pytest',
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d['operations'] == 2
    assert d['applied'] is False, '端点不该改图 —— 它只写计划'

    # 落盘了，且内容完整
    saved = ROOT / d['saved_to'] if not Path(d['saved_to']).is_absolute() else Path(d['saved_to'])
    assert saved.exists(), f'计划文件没写出来：{saved}'
    plan = json.loads(saved.read_text(encoding='utf-8'))
    assert len(plan['operations']) == 2
    assert plan['operations'][0]['from'] == 'TransE', (
        '`from` 是 Python 关键字，必须用 alias 映射 —— 落盘时丢了')

    # **必须告诉调用者下一步怎么做**，否则"提交完没有出口"
    assert d.get('next') and any('apply_edit_plan.py' in x for x in d['next']), \
        '返回里没有给出执行命令 —— 用户提交完就卡住了'

    # **真实计划目录一份都没多**
    after = set(REAL_DIR.glob('*.json')) if REAL_DIR.exists() else set()
    assert after == before, f'测试污染了真实计划目录：{[p.name for p in after - before]}'


@pytest.mark.parametrize('payload,keyword', [
    ({'operations': []}, '不能为空'),
    ({'operations': [{'op': 'drop_database'}]}, '未知操作'),
    ({'operations': [{'op': 'rename_entity', 'from': 'X'}]}, '缺字段'),
    ({'operations': [{'op': 'delete_edge', 'source': 'A'}]}, '缺字段'),
])
def test_bad_requests_are_rejected_with_specific_errors(client, payload, keyword):
    """坏请求必须在**写文件之前**被拒，且错误信息要具体。"""
    r = client.post('/api/kg/edit-plan', json=payload)
    assert r.status_code == 400, f'应拒绝但返回 {r.status_code}：{r.text[:200]}'
    detail = r.json().get('detail', '')
    assert keyword in detail, f'错误信息不够具体：{detail!r}（期望含 {keyword!r}）'


def test_all_four_ops_are_accepted(client):
    """四种操作都要被接受（列全，免得"允许的操作"与实际实现不一致）。"""
    r = client.post('/api/kg/edit-plan', json={'operations': [
        {'op': 'rename_entity', 'from': 'A', 'to': 'B'},
        {'op': 'merge_entities', 'from': 'A', 'into': 'B'},
        {'op': 'delete_edge', 'source': 'A', 'target': 'B', 'type': 'USES'},
        {'op': 'retag_edge', 'source': 'A', 'target': 'B', 'type': 'PART_OF',
         'to_type': 'PUBLISHED_IN'},
    ]})
    assert r.status_code == 200, r.text
    assert r.json()['operations'] == 4
