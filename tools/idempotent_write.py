"""幂等写文件：内容没变就不碰 mtime。

【为什么需要】
  项目里有一批「检查 + 写报告」的脚本（`check_l7_role_violation.py`、
  `check_l5_validity.py`、`check_doc_numbers.py` …），它们既产出给人看的
  JSON 报告，又被 `verify_all.py` 当回归项反复执行。

  于是出现一个自指的怪圈：
      跑回归 → 检查脚本重写报告 → 报告 mtime 更新
             → 以它为上游的产物（如 analytics.json）变成 STALE
             → freshness 检查报红

  实测被这个坑连咬三次。根因不是"忘了重建"，而是**检查脚本不该刷新时间戳**。

  修法：写之前先比内容，一样就跳过。这样回归可以反复跑而不扰动任何下游。

用法：
    from idempotent_write import write_if_changed
    write_if_changed(path, json.dumps(obj, ensure_ascii=False, indent=2))
"""
from pathlib import Path


def write_if_changed(path, text: str) -> bool:
    """若内容与现有文件一致则不写。返回是否真的写了。"""
    p = Path(path)
    try:
        if p.exists() and p.read_text(encoding="utf-8") == text:
            return False
    except Exception:
        pass          # 读不了就当内容不同，照写
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return True


def dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, indent=2)
