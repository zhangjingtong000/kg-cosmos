# -*- coding: utf-8 -*-
"""`_load_registry_env()` 的回归测试（2026-11-28）。

## 为什么需要这个文件

`src/kg_extractor.py` 里 `_load_registry_env()` 定义了 `locations` 就结束，
后面那段「扫注册表 → `os.environ.setdefault`」的 `for` 循环被**错误地缩进**进了
`relation_types_block()`，而且粘在它的 `return` 之后：

```python
def relation_types_block(bare=False):
    ...
    return "\\n".join(lines)

    for hive, path in locations:      # ← 永不执行
        ...winreg.OpenKey...
```

两个后果，且**都不会报错**：

1. `import kg_extractor` 时 `_load_registry_env()` 是个空操作 —— 批量脚本从非交互
   shell 启动时拿不到注册表里的变量，表现为「本地明明有 key，脚本报 401」；
2. 那段循环成了不可达死代码，却引用 `locations` / `BLOCKED` / `winreg` 这三个
   在该函数里**未定义**的名字 —— 一旦有人把它挪出来复用，立刻 `NameError`。

本文件同时钉住「行为」和「结构」两件事：前者证明兜底真的生效，后者防止死代码再次
出现（只测行为的话，把循环删掉也能过；只测结构的话，逻辑写错也能过）。
"""
import inspect
import os
import sys
import types

import pytest

import kg_extractor


class _FakeKey:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_fake_winreg(monkeypatch, values, inaccessible=()):
    """把 `winreg` 换成一个只返回 `values` 的假模块。

    `_load_registry_env()` 内部是 `import winreg`，所以只要塞进 `sys.modules`
    就会取到假模块 —— 不必真的读本机注册表（那既不可重复，也没法断言）。
    """
    fake = types.ModuleType('winreg')
    fake.HKEY_LOCAL_MACHINE = 'HKLM'
    fake.HKEY_CURRENT_USER = 'HKCU'
    opened = []

    def OpenKey(hive, path):
        opened.append((hive, path))
        if hive in inaccessible:
            raise OSError(5, 'access denied')
        return _FakeKey()

    def EnumValue(key, index):
        if index < len(values):
            name, value = values[index]
            return name, value, 1
        raise OSError(259, 'no more data')      # 真实注册表迭代结束时抛的就是 OSError

    fake.OpenKey = OpenKey
    fake.EnumValue = EnumValue
    monkeypatch.setitem(sys.modules, 'winreg', fake)
    return opened


def test_load_registry_env_actually_populates_missing_vars(monkeypatch):
    """注册表里的变量要真的补进 `os.environ`；黑名单里的密钥一个都不许注入。"""
    var = 'KG_TEST_ONLY_REGISTRY_VAR'
    blocked = 'DEEPSEEK_API_KEY'
    monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv(blocked, raising=False)

    opened = _install_fake_winreg(
        monkeypatch,
        values=[(var, 'from-registry'), (blocked, 'sk-should-not-be-injected')],
        inaccessible=('HKLM',),                 # 顺带覆盖「一处打不开不影响另一处」
    )

    kg_extractor._load_registry_env()

    assert os.environ.get(var) == 'from-registry', (
        '注册表兜底没有生效 —— 循环多半又不在 _load_registry_env() 里了')
    assert blocked not in os.environ, (
        '云端密钥仍被注入进程：本项目禁止任何云端 API 支出（见函数 docstring）')
    # 两处 location 都要扫过，且 HKLM 抛 OSError 后要 continue 到 HKCU
    assert [hive for hive, _ in opened] == ['HKLM', 'HKCU']


def test_load_registry_env_does_not_override_existing_vars(monkeypatch):
    """`setdefault` 语义：进程内已有的值（命令行显式 export）优先级更高。"""
    var = 'KG_TEST_ONLY_PRESET_VAR'
    monkeypatch.setenv(var, 'from-shell')
    _install_fake_winreg(monkeypatch, values=[(var, 'from-registry')])

    kg_extractor._load_registry_env()

    assert os.environ[var] == 'from-shell'


def test_registry_loop_lives_in_loader_not_after_relation_block():
    """结构断言：死代码不许再回到 `relation_types_block()` 的 `return` 之后。"""
    loader_src = inspect.getsource(kg_extractor._load_registry_env)
    block_src = inspect.getsource(kg_extractor.relation_types_block)

    assert 'for hive, path in locations:' in loader_src, '循环必须回到 _load_registry_env()'
    assert 'EnumValue' in loader_src
    assert 'for hive, path in locations:' not in block_src, (
        'relation_types_block() 里又出现了注册表循环 —— 那是 return 之后的死代码')
    # `locations` 也只在 loader 里有定义：出现在别处就说明循环被拆散过。
    assert 'locations = [' not in block_src
