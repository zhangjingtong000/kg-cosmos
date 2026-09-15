# -*- coding: utf-8 -*-
r"""盲态保护的真实端点测试。

## 这个文件替代了什么

此前这 13 个 case 住在 `tmp/_audit_check_protect.py`（**`tmp/` 被 `.gitignore` 排除**），
所以它**不可交付、不可移植**，而且：
  · 写死 `Path(r"D:\kg-demo")` —— 而 `tools/check_doc_numbers.py:18` 明确写过
    「不写死盘符，演示机不一定把仓库放在 D 盘」；
  · **它只是把 `src/app.py` 的 `_BLIND_PROTECTED` 读出来、自己再用
    `any(p in low for p in prot)` 算一遍** —— 从不打真实端点。
    也就是说：**如果 `api_quality()` 忘了用那个名单，这个脚本照样全绿。**
  · 它不在 `tests/`（85 个 `def test_` 中 0 处提 l5/盲态），也不在 `verify_all` 的 31 项里。

**现在改成真发 HTTP 请求**（`fastapi.testclient.TestClient`）：
拦不拦得住由**端点自己的行为**决定，而不是由测试自己重算一遍。

## 为什么必须区分两种 404

`/api/quality/{name}` 有两种 404：
  · **被盲态保护** → detail 含「**不对外提供**」（`app.py:204`）
  · **文件不存在** → detail 含「**未找到**」（`_serve_json`）

**如果不区分，测试会"假通过"** —— 一个不存在的文件名也会得 404，
看起来"保护生效了"，其实什么都没验证。所以下面 `_blocked()` 只认前者。
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

# 避开 Neo4j：app.py:62 按 KG_BACKEND 决定是否连库。
# 测试只关心路由行为，不需要真图库。
os.environ.setdefault('KG_BACKEND', 'memory')

# ---- 13 个 case（沿用 tmp 里那份的清单，但判据换成真端点行为）----
# 键 = 文件名，值 = 是否**应当**被盲态保护拦截
CASES = {
    # 含盲判答案 / 分层 / 逐条分数 —— 必须拦
    'l5-validity-key.json': True,
    'l5-validity-design.json': True,
    'l5-validity-report.json': True,
    'l5-validity-sample.jsonl': True,
    'l5-annotator-agreement.json': True,
    'l5-single-ad690dfb-dump.json': True,
    'l5-labels-ad690dfb.json': True,
    'relation-verify-all.json': True,
    # 不含盲判答案 —— 不该拦
    'l5-six-layer.json': False,
    'l5-filter-coverage.json': False,
    'l5-filter-strategy.json': False,
    'claims.json': False,
    'kg-stats.json': False,
}

BLOCK_MARK = '不对外提供'
MISS_MARK = '未找到'


@pytest.fixture(scope='module')
def client():
    """整个模块共用一个 TestClient（import app 有开销）。"""
    from fastapi.testclient import TestClient
    import app as appmod
    return TestClient(appmod.app, raise_server_exceptions=False)


def _detail(resp):
    try:
        return str(resp.json().get('detail', ''))
    except Exception:
        return resp.text or ''


def _blocked(resp):
    """**只认"被盲态保护"这一种 404**，不认"文件不存在"。"""
    return resp.status_code == 404 and BLOCK_MARK in _detail(resp)


@pytest.mark.parametrize('name,should_block', sorted(CASES.items()))
def test_blind_protection(client, name, should_block):
    r = client.get(f'/api/quality/{name}')
    got = _blocked(r)
    if should_block:
        assert got, (
            f'{name} 应被盲态保护拦截，实际 status={r.status_code} detail={_detail(r)[:80]!r}')
    else:
        # 不该拦：要么 200，要么是"文件不存在"（那也是没拦）
        assert not got, (
            f'{name} 不该被盲态保护拦截（它是可公开的产物），'
            f'实际 detail={_detail(r)[:80]!r}')


def test_path_traversal_rejected(client):
    """路径穿越必须被拒（400 或 404），不能读到 data/ 之外。"""
    for bad in ('../README.md', '..%2FREADME.md', 'a/b.json', 'a\\b.json'):
        r = client.get(f'/api/quality/{bad}')
        assert r.status_code in (400, 404), f'{bad} 未被拒（{r.status_code}）'


def test_real_public_artifact_is_served(client):
    """反向验证：一个确实该公开的产物必须能取到，否则"全 404"会伪装成"保护生效"。"""
    r = client.get('/api/quality/l5-six-layer.json')
    assert r.status_code == 200, (
        f'l5-six-layer.json 是公开产物，应能取到；实际 {r.status_code} {_detail(r)[:80]!r}')
