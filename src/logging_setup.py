# -*- coding: utf-8 -*-
r"""统一日志配置 —— 这个项目此前**零 logging**（`src/` 下 0 处），
只有 44 处 `print`。

## 为什么需要它（而不是继续用 print）

`print` 有三个硬问题：
  ① **没有时间戳** —— 出错时无法判断"什么时候开始不对的"；
  ② **没有级别** —— 分不清"正常进度"和"出错了"；
  ③ **没有落盘** —— 关掉终端就没了。而本项目**抽一篇要 36 分钟**，
     中途出的错如果不落盘，事后只能靠状态里那一行 `error` 字符串。

## 设计上的三条克制

1. **不动那 44 处 `print`。** 它们承担的是"给人看的进度"，
   混在日志里反而更乱。**logging 与 print 并存**，各管一段：
   print = 交互式进度；logging = 可追溯的记录。
2. **日志不往仓库里写。** 落在 `output/logs/`，而 `/output/` 已在 `.gitignore` 里
   —— 日志是运行时产物，不该进版本控制。
3. **失败也不抛。** `setup()` 里任何异常都吞掉并降级为"只有控制台" ——
   **日志系统不该成为新的故障点**（这与 `pipeline.py` 里
   "进度上报绝不能反过来把抽取搞挂"是同一条纪律）。

用法：
    from logging_setup import setup, get_logger
    setup()                      # 进程入口调一次
    log = get_logger(__name__)
    log.info('...')
"""
import logging
import logging.handlers
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_LOG_DIR = _ROOT / 'output' / 'logs'

_configured = False

# 控制台只显示 INFO 及以上；文件记 DEBUG 及以上（便于事后追）
_FMT = '%(asctime)s %(levelname)-7s [%(name)s] %(message)s'
_DATEFMT = '%Y-%m-%d %H:%M:%S'


def setup(level=logging.INFO, filename='kg-cosmos.log'):
    """配置根 logger。**幂等**（重复调用只生效一次）。永不自爆。"""
    global _configured
    if _configured:
        return
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        root = logging.getLogger()
        root.setLevel(logging.DEBUG)

        # ① 控制台（只 INFO+）
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(level)
        ch.setFormatter(logging.Formatter(_FMT, _DATEFMT))
        root.addHandler(ch)

        # ② 文件（DEBUG+，按大小轮转 —— 抽取跑几十分钟，日志会长）
        fh = logging.handlers.RotatingFileHandler(
            _LOG_DIR / filename, maxBytes=8 * 1024 * 1024,
            backupCount=3, encoding='utf-8')
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(_FMT, _DATEFMT))
        root.addHandler(fh)

        _configured = True

        # 第三方库降噪：httpx 每个请求都记一行、asyncio 在 Windows 上会反复
        # 打印 proactor 选择 —— 它们会把我们自己的记录淹掉。
        # （实测：4 个请求产生 8 行，其中 4 行是 httpx / asyncio 的。）
        for noisy in ('httpx', 'httpcore', 'asyncio', 'urllib3',
                      'neo4j', 'matplotlib', 'PIL'):
            logging.getLogger(noisy).setLevel(logging.WARNING)

        logging.getLogger(__name__).info('日志已就绪 → %s', _LOG_DIR / filename)
    except Exception as e:          # noqa: BLE001 —— 故意吞掉
        # 日志系统不该成为新的故障点：配置失败就"只有控制台"，继续跑
        print(f'[warn] 日志配置失败（已降级为仅控制台）：{type(e).__name__}: {e}',
              file=sys.stderr)


def get_logger(name):
    """取 logger。**没调 setup() 也能用**（会自动配置一次）。"""
    if not _configured:
        setup()
    return logging.getLogger(name)


def log_dir():
    """日志目录（供启动器打印"日志在哪"）。"""
    return _LOG_DIR
