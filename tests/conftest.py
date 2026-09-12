"""pytest 全局配置：把 src/ 加进 import 路径。

【为什么需要】
  项目不是一个可安装包（没有 pyproject/setup.py），测试却直接
  `from kg_extractor import ...`。此前只在两个测试文件里各自插了 sys.path，
  其余靠运气——从仓库根跑 `python -m pytest tests` 会直接
  `ModuleNotFoundError: No module named 'kg_extractor'` 而**收集失败**，
  整个测试套件等于没有唯一可跑入口。

  标准做法就是 conftest.py：pytest 在收集前自动导入它，
  于是 `python -m pytest tests` 在任何工作目录下都能跑。

  实测：加之前 `--collect-only` 有 2 errors；加之后 0 errors。
"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
