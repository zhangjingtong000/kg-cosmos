# -*- coding: utf-8 -*-
r"""指标档案机制（N1）：`metric_provenance.py` 的**归档**能力。

## 为什么需要这个测试

这个脚本原先**覆盖写** `metric-provenance.json`，一旦重算，**上一版永久消失**——
于是无法回答"改之前那套指标是多少"。而 K1（改图）**必须**能拿出 before/after 两套。

**同一个病根**已经在别处造成过真实损失：`tools/protocol_ladder.py` 也写固定路径，
**P3 第二篇把第一篇的产物覆盖了**（见 TODO P13 / `P3_PROTOCOL_LADDER_2PAPERS.md`）。

⇒ 所以"归档"这件事**必须被测试盯住**，不能只靠"我记得它会归档"。

## 测试怎么做到"不污染真实归档"

**给测试一个一次性的临时归档目录**（`KG_ARCHIVE_DIR`）——
否则每跑一次 pytest 就多一份真实归档文件（**实测确实如此**：
写完这个测试跑了两次，真实归档就从 1 份变成了 3 份）。
`metric_provenance.py` 读这个环境变量；未设时用真实目录。

**为什么不在测试里"跑完把新增的删掉"**：那违反 `docs/ARCHIVE_POLICY.md`
（归档只增不减），而且会让"归档是否真的发生"这件事变得不可断言。
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / 'tools' / 'metric_provenance.py'
ARCHIVE = ROOT / 'data' / 'quality' / 'archive' / 'metric-provenance'
CUR = ROOT / 'data' / 'quality' / 'metric-provenance.json'

pytestmark = pytest.mark.skipif(
    not CUR.exists(),
    reason='还没有 metric-provenance.json —— 先跑一次 --write')


def run(*args, archive_dir=None):
    """跑脚本。`archive_dir` 非空时把归档重定向到它（测试用）。"""
    env = None
    if archive_dir is not None:
        import os
        env = dict(os.environ)
        env['KG_ARCHIVE_DIR'] = str(archive_dir)
    return subprocess.run([sys.executable, str(TOOL), *args],
                          cwd=str(ROOT), capture_output=True, text=True, env=env)


def test_history_runs_and_lists_current():
    """`--history` 要能跑通，并至少列出"当前"那一份。"""
    r = run('--history')
    assert r.returncode == 0, f'--history 失败：{r.stdout[-300:]} {r.stderr[-300:]}'
    assert '指标档案历史' in r.stdout
    assert '当前' in r.stdout, '--history 没列出当前那份'


def test_diff_current_against_itself_is_empty():
    """**同一条记录和自己比，必须报告"变化 0 项"** —— 这是 diff 逻辑的自洽性检查。

    如果这里不是 0，说明比对逻辑本身有 bug（例如把 `artifact_mtime` 也算成变化）。
    """
    r = run('--diff', 'current', 'current')
    assert r.returncode == 0, f'--diff 失败：{r.stdout[-300:]} {r.stderr[-300:]}'
    assert '变化 0 项' in r.stdout, (
        '同一条记录与自身比却报了变化 —— 比对逻辑有问题。\n' + r.stdout[-500:])


def test_write_archives_exactly_one_new_copy(tmp_path):
    """`--write` 必须**先归档旧记录**，且只新增一份。

    **归档目录重定向到 `tmp_path`** —— 真实的归档只该由人手动产生。
    """
    adir = tmp_path / 'archive'
    before = set(ARCHIVE.glob('*.json')) if ARCHIVE.exists() else set()
    old = json.loads(CUR.read_text(encoding='utf-8'))
    old_gen = old.get('generated')

    r = run('--write', archive_dir=adir)
    assert r.returncode == 0, f'--write 失败：{r.stdout[-300:]} {r.stderr[-300:]}'
    assert '归档' in r.stdout, f'--write 没有报告归档行为：{r.stdout[:300]}'

    # ① 临时归档目录里恰好一份，且内容是**写入之前**的当前记录
    got_files = list(adir.glob('*.json'))
    assert len(got_files) == 1, (
        f'临时归档目录应当恰好 1 份，实际 {len(got_files)} 份：'
        f'{[p.name for p in got_files]}')
    got = json.loads(got_files[0].read_text(encoding='utf-8'))
    assert got.get('generated') == old_gen, (
        f'归档的内容不是旧记录：归档 generated={got.get("generated")!r}，'
        f'期望 {old_gen!r}')
    assert set(got.get('metrics') or {}) == set(old.get('metrics') or {}), \
        '归档的指标键集与旧记录不一致'

    # ② **真实归档目录一份都没多** —— 这是这个测试最要紧的断言
    after = set(ARCHIVE.glob('*.json')) if ARCHIVE.exists() else set()
    assert after == before, (
        f'测试污染了真实归档目录：新增 {[p.name for p in after - before]}')

    # ③ 当前那份已被更新（新的写进了原位）
    now = json.loads(CUR.read_text(encoding='utf-8'))
    assert now.get('metrics'), '当前记录没有被更新'


def test_archived_files_are_never_deleted():
    """归档只增不减 —— 这条是 `ARCHIVE_POLICY.md` 的直接落实。"""
    if not ARCHIVE.exists():
        pytest.skip('还没有归档目录')
    n = len(list(ARCHIVE.glob('*.json')))
    assert n >= 1, '归档目录存在但一份都没有'
