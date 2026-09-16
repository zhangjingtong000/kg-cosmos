# -*- coding: utf-8 -*-
r"""K1 改图：`apply_edit_plan.py` 的**安全边界**。

## 这个脚本最要紧的性质不是"能改"，而是"**不碰不该碰的**"

改图会改变全库实体数 ⇒ 会打破六层指标的可比性。所以它的安全要求比功能要求更高：

  1. **默认是 dry-run** —— 不加 `--apply` 一个字节都不写；
  2. **`--graphs-dir` 能把写入重定向** —— 测试因此可以真跑 `--apply`
     而不碰 `data/corpus/graphs/`（那是**唯一的真源**）；
  3. **指定了 `--graphs-dir` 就自动跳过"指标归档"与"下游重跑"**
     —— 它们读的都是真源，在测试模式下跑没有意义、还会污染真实档案；
  4. **没命中的操作要告警**（计划过时是常态，静默忽略最危险）。

**下面每一条都断言"真源没被改"** —— 这是这个文件存在的理由。
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / 'tools' / 'apply_edit_plan.py'
SRC = ROOT / 'data' / 'corpus' / 'graphs'

pytestmark = pytest.mark.skipif(not SRC.exists(), reason='没有 data/corpus/graphs')


def run(*args):
    return subprocess.run([sys.executable, str(TOOL), *args],
                          cwd=str(ROOT), capture_output=True, text=True)


def _fingerprint(d):
    """目录指纹：文件名 → 内容 sha1 前 12 位。用于断言"真源没被改"。"""
    import hashlib
    out = {}
    for f in sorted(Path(d).glob('*.json')):
        out[f.name] = hashlib.sha1(f.read_bytes()).hexdigest()[:12]
    return out


@pytest.fixture
def sandbox(tmp_path):
    """把图快照复制到临时目录，返回 (目录, 一个真实存在的实体名)。"""
    dst = tmp_path / 'graphs'
    dst.mkdir()
    for f in SRC.glob('*.json'):
        shutil.copy2(f, dst / f.name)
    # 从一个真实存在的快照里取一个实体名，保证操作**会命中**
    first = sorted(dst.glob('*.json'))[0]
    d = json.loads(first.read_text(encoding='utf-8-sig'))
    name = (d['nodes'][0].get('name') or d['nodes'][0].get('id'))
    return dst, name, first


def _write_plan(tmp_path, ops):
    p = tmp_path / 'plan.json'
    p.write_text(json.dumps({'generated_by': 'pytest', 'operations': ops},
                            ensure_ascii=False), encoding='utf-8')
    return p


def test_dry_run_writes_nothing(sandbox, tmp_path):
    """**默认必须是 dry-run** —— 一个字节都不写。"""
    dst, name, _ = sandbox
    before = _fingerprint(dst)
    plan = _write_plan(tmp_path, [{'op': 'rename_entity', 'from': name,
                                   'to': name + '-X', 'reason': 'test'}])
    r = run(str(plan), '--graphs-dir', str(dst))
    assert r.returncode == 0, r.stdout[-300:]
    assert 'dry-run' in r.stdout, '没有提示这是 dry-run'
    assert _fingerprint(dst) == before, 'dry-run 竟然改了文件'


def test_apply_changes_only_the_sandbox_and_never_the_real_source(sandbox, tmp_path):
    """**这个文件的中心断言**：`--apply` 只改 `--graphs-dir` 指的地方。"""
    dst, name, target_file = sandbox
    real_before = _fingerprint(SRC)          # ← 真源指纹

    plan = _write_plan(tmp_path, [{'op': 'rename_entity', 'from': name,
                                   'to': name + '-RENAMED', 'reason': 'test'}])
    r = run(str(plan), '--apply', '--graphs-dir', str(dst))
    assert r.returncode == 0, r.stdout[-400:]
    assert '影响' in r.stdout, '没有报告影响条数'

    # ① 沙箱里确实改了
    d = json.loads(target_file.read_text(encoding='utf-8-sig'))
    names = [(n.get('name') or n.get('id')) for n in (d.get('nodes') or [])]
    assert (name + '-RENAMED') in names, '沙箱里没有改名成功'

    # ② **真源一个字节都没变**
    assert _fingerprint(SRC) == real_before, (
        '⚠️ --graphs-dir 模式竟然改了真源 data/corpus/graphs/ —— 这是最严重的问题')


def test_graphs_dir_mode_skips_metric_archive(sandbox, tmp_path):
    """`--graphs-dir` 时必须**跳过指标归档与下游重跑**（它们读的都是真源）。"""
    archive = ROOT / 'data' / 'quality' / 'archive' / 'metric-provenance'
    before = set(archive.glob('*.json')) if archive.exists() else set()

    dst, name, _ = sandbox
    plan = _write_plan(tmp_path, [{'op': 'rename_entity', 'from': name,
                                   'to': name + '-Y', 'reason': 'test'}])
    r = run(str(plan), '--apply', '--graphs-dir', str(dst))
    assert r.returncode == 0, r.stdout[-300:]
    assert '跳过归档' in r.stdout, '测试模式下没有跳过指标归档'
    assert '跳过下游重跑' in r.stdout, '测试模式下没有跳过下游重跑'

    after = set(archive.glob('*.json')) if archive.exists() else set()
    assert after == before, (
        f'测试模式污染了真实指标档案：新增 {[p.name for p in after - before]}')


def test_missing_target_warns_but_does_not_fail(sandbox, tmp_path):
    """没命中的操作**要告警**（计划过时是常态），但不必让整个命令失败。"""
    dst, _name, _ = sandbox
    plan = _write_plan(tmp_path, [{'op': 'rename_entity', 'from': '__NOPE__',
                                   'to': '__X__', 'reason': 'test'}])
    r = run(str(plan), '--graphs-dir', str(dst))
    assert r.returncode == 0
    assert '没有命中' in r.stdout, '没命中的操作没有告警'


def test_unknown_op_is_rejected(sandbox, tmp_path):
    """未知操作必须**直接拒绝**，不能静默忽略。"""
    dst, name, _ = sandbox
    plan = _write_plan(tmp_path, [{'op': 'drop_database', 'from': name}])
    r = run(str(plan), '--graphs-dir', str(dst))
    assert r.returncode != 0, '未知操作竟然被接受了'
    assert '未知操作' in (r.stdout + r.stderr)
