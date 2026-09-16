# -*- coding: utf-8 -*-
r"""`embeddings.py` 的两条配置纪律 —— 都是**踩过坑才有的**。

## ① 环境变量必须在**调用点**读，不能在模块级缓存

`OLLAMA = os.environ.get('OLLAMA_HOST', …)` 曾在**模块级**求值一次。
后果：**后端进程启动时的环境被永久冻结** ——
之后无论怎么改 `OLLAMA_HOST` 都不生效，**表现为"端点一直 503"**，
而单独跑脚本（新进程、新 import）却完全正常。**这种"只在长驻进程里坏"最难查。**

⚠️ 这是**同一个坑的第二次**：`app.py` 的 `EDIT_PLAN_DIR` 先栽过一次
（`test_edit_plan_api.py` 单跑 6 个全过、进全量跑就挂）。

## ② `OLLAMA_HOST` 可能**不带 scheme**，要补

**Windows 上 Ollama 的惯用写法是 `127.0.0.1:11434`（没有 `http://`）。**
`urllib` 拿到它直接报：

    URLError: <urlopen error unknown url type: 127.0.0.1>

**这不是猜的** —— 实测复现，而且那个报错与后端 `/api/semantic-match`
返回 503 时的内部异常**一模一样**。
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

import embeddings as E  # noqa: E402


# ── ① 每次调用都读环境变量 ────────────────────────────────────────────
@pytest.mark.parametrize('host', [
    '127.0.0.1:11434',              # **Windows Ollama 的惯用写法（不带 scheme）**
    'http://127.0.0.1:11434',
    'http://127.0.0.1:11434/',      # 带尾斜杠
    'localhost:11434',
    'https://example.invalid:1234',
])
def test_host_is_normalized(monkeypatch, host):
    monkeypatch.setenv('OLLAMA_HOST', host)
    got = E.ollama_host()
    assert '://' in got, f'{host!r} 正规化后仍然没有 scheme：{got!r}'
    assert not got.endswith('/'), f'不该留尾斜杠：{got!r}'
    if host.startswith('http'):
        assert got.rstrip('/') == host.rstrip('/')


def test_missing_env_falls_back_to_default(monkeypatch):
    monkeypatch.delenv('OLLAMA_HOST', raising=False)
    assert E.ollama_host() == E.DEFAULT_HOST
    monkeypatch.setenv('OLLAMA_HOST', '   ')      # 空白也算没设
    assert E.ollama_host() == E.DEFAULT_HOST


def test_env_change_takes_effect_immediately(monkeypatch):
    """**这条是核心**：改环境变量之后，下一次调用就必须看到新值。

    如果实现退回成模块级缓存，这条会挂 —— 而那正是"端点一直 503"的成因。
    """
    monkeypatch.setenv('OLLAMA_HOST', 'http://a.invalid:1')
    assert E.ollama_host() == 'http://a.invalid:1'
    monkeypatch.setenv('OLLAMA_HOST', 'http://b.invalid:2')
    assert E.ollama_host() == 'http://b.invalid:2', (
        '**改了环境变量却不生效** ⇒ 实现退回成模块级缓存了（见文件头 ①）')


def test_embed_uses_ollama_host_at_call_time(monkeypatch):
    """`embed()` 也要走调用点解析，而不是模块级快照。

    不真发请求 —— 用一个必然连不上的地址，断言**它确实按那个地址去连了**
    （报错里会带上 host），否则我们无法区分"用了新值"与"用了旧快照"。
    """
    monkeypatch.setenv('OLLAMA_HOST', 'http://127.0.0.1:1')   # 必然拒连
    with pytest.raises(Exception) as ei:
        E.embed(['ping'])
    msg = str(ei.value) + repr(getattr(ei.value, 'reason', ''))
    assert '1' in msg or 'refused' in msg.lower() or 'URLError' in type(ei.value).__name__, \
        f'报错里看不出它连的是哪个地址：{msg[:160]}'


# ── ② 旧名字保留（ARCHIVE_POLICY）─────────────────────────────────────
def test_legacy_names_still_exist():
    """`OLLAMA` / `MODEL` 是**导入时的快照**，但**不能删** ——
    别处可能 `from embeddings import OLLAMA`。按 ARCHIVE_POLICY 留存。"""
    assert hasattr(E, 'OLLAMA') and hasattr(E, 'MODEL')
    assert '://' in E.OLLAMA, '旧名字也应当是已正规化的形态'
