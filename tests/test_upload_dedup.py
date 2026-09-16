# -*- coding: utf-8 -*-
r"""G2：同一份 PDF 传两次，应当**复用**那个已有的 gid。

## 问题

`upload_start` 原先每次都 `uuid.uuid4().hex[:8]` ⇒
**同一篇论文传两次会得到两个互不相识的 gid**，
界面上看起来是两篇不同的东西。用户会问"我传了两次为什么有两份"，
更糟的是**白等 36 分钟重抽一遍**（抽取很贵）。

## 修法与判据

边写盘边算 sha1 ⇒ 在 `data/<task_id>/` 里找**同一个 PDF 且已抽完**的目录
（判据是那个目录里**有 `graph.json`**）⇒ 直接复用。

**只查"已完成"的**：跑到一半的任务不该被复用 ——
那会让用户以为它已经好了（"看起来对了、其实没有"是本项目最忌讳的一类）。

## 测试怎么做到不污染

**用一份真实的、已经抽完的 PDF**（`data/ad690dfb/2506.09566v1.pdf`）传两次。
因为第一次就会命中"已完成"那条路径，**不会写新目录、也不会触发重抽**。
"""
import io
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

os.environ.setdefault('KG_BACKEND', 'memory')

# 一份**已经抽完**的真实 PDF（它所在的目录里有 graph.json）
KNOWN_PDF = ROOT / 'data' / 'ad690dfb' / '2506.09566v1.pdf'


@pytest.fixture(scope='module')
def client():
    from fastapi.testclient import TestClient
    import app as appmod
    return TestClient(appmod.app, raise_server_exceptions=False)


def _upload(client, path):
    data = path.read_bytes()
    return client.post('/api/upload/start',
                       files={'file': (path.name, io.BytesIO(data), 'application/pdf')})


@pytest.mark.skipif(not KNOWN_PDF.exists(),
                    reason=f'需要一份已抽完的 PDF：{KNOWN_PDF}')
def test_same_pdf_twice_reuses_the_same_gid(client):
    r1 = _upload(client, KNOWN_PDF)
    assert r1.status_code == 200, r1.text
    j1 = r1.json()

    r2 = _upload(client, KNOWN_PDF)
    assert r2.status_code == 200, r2.text
    j2 = r2.json()

    assert j1['id'] == j2['id'], (
        f'同一份 PDF 传两次却得到两个 gid：{j1["id"]} vs {j2["id"]} —— '
        f'这正是 G2 记的那个漏态')
    # **如实告知"复用"**，而不是假装开了一个新任务
    assert j2.get('reused') is True, '第二次上传应当如实标 reused=true'
    assert j1.get('sha1_16'), '返回里应当带 sha1_16（否则没法自查是不是同一份）'


def test_reused_response_says_why(client):
    """复用时**必须说清原因** —— 否则用户不知道"为什么没有新图"。"""
    if not KNOWN_PDF.exists():
        pytest.skip('缺 PDF')
    j = _upload(client, KNOWN_PDF).json()
    if j.get('reused'):
        assert '复用' in (j.get('note') or ''), (
            f'复用时应当说明原因，实际 note={j.get("note")!r}')
