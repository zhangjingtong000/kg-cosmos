# ═══════════════════════════════════════════════════════════════════════════
#  check.ps1 —— 一次跑完项目的全部检查
# ═══════════════════════════════════════════════════════════════════════════
#
#  **为什么需要它**：`README §5` 写着一条纪律「改数据必须跑回归」，
#  但执行它要记住 4–5 条命令（语法 / 本体一致性 / 文档数字 / 回归 / pytest），
#  **记不全就等于没这条纪律**。
#
#  **而且它补了一个真实缺口**：`tests/` 下的 **85 个单元测试此前完全不在
#  任何回归入口里** —— `tools/verify_all.py` 只跑 `tools/*.py`。
#  也就是说「改坏了单元测试覆盖的东西，没人会发现」。
#
#  用法（**注意本机是 Windows PowerShell 5.1，命令是 `powershell` 不是 `pwsh`**）：
#     powershell -File tools/check.ps1            # 全部检查
#     powershell -File tools/check.ps1 -Quick     # 跳过回归（只跑快的）
#
#  退出码：全部通过 = 0；有任何一项失败 = 1。
# ═══════════════════════════════════════════════════════════════════════════

param(
    [switch]$Quick   # 跳过较慢的 verify_all 回归
)

$ErrorActionPreference = 'Continue'
$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$PY = if (Test-Path 'E:\Python313\python.exe') { 'E:\Python313\python.exe' } else { 'python' }

$script:failed = @()
$script:passed = @()

function Step {
    param([string]$Name, [scriptblock]$Body)
    Write-Host ''
    Write-Host ('─' * 72) -ForegroundColor DarkGray
    Write-Host "▶ $Name" -ForegroundColor Cyan
    Write-Host ('─' * 72) -ForegroundColor DarkGray
    $global:LASTEXITCODE = 0
    & $Body
    if ($LASTEXITCODE -eq 0) {
        $script:passed += $Name
    } else {
        $script:failed += $Name
        Write-Host "  ✗ $Name 失败（exit $LASTEXITCODE）" -ForegroundColor Red
    }
}

Push-Location $ROOT
try {
    Write-Host ''
    Write-Host 'KG Cosmos · 全部检查' -ForegroundColor White
    Write-Host "  根目录: $ROOT" -ForegroundColor DarkGray
    Write-Host "  Python: $PY" -ForegroundColor DarkGray

    # ① 语法（前端内联脚本 + lib 下的本地脚本）
    Step '前端脚本语法' { & $PY tools/check_js_syntax.py }

    # ② 本体一致性（四份拷贝必须一致 —— 这个项目在这上面栽过）
    Step '本体一致性' { & $PY tools/check_ontology_consistency.py }

    # ③ 文档数字（含旧值扫描、作废结论扫描、README 是否反映当前规模）
    Step '文档数字与产物一致' { & $PY tools/check_doc_numbers.py }

    # ④ 单元测试（**此前完全不在任何入口里**）
    Step '单元测试 (pytest)' { & $PY -m pytest tests -q }

    # ⑤ 产物入库（P13b，2026-09-16）
    # **为什么需要**：一次真实的数据丢失 —— 跑 P3 第二篇时第一篇的产物被覆盖，
    # 而我以为"它在 git 里"，核实发现**它从未入库**（`data/` 被 gitignore，
    # 当时只 `add -f` 了 53 个"文档引用到的"文件，而它不在里面）。
    # 这个检查盯的就是那条判据：**文档写了它，它就必须存在且已入库。**
    Step '产物入库（文档引用）' { & $PY tools/check_artifacts_tracked.py }

    # ⑤ 回归（31 项；--fast 跳过慢的 PDF 检查）
    if (-not $Quick) {
        Step '回归 (verify_all --fast)' { & $PY tools/verify_all.py --fast }
    } else {
        Write-Host ''
        Write-Host '（-Quick：跳过 verify_all 回归）' -ForegroundColor DarkGray
    }

    # ── 汇总 ────────────────────────────────────────────────────────────
    Write-Host ''
    Write-Host ('═' * 72) -ForegroundColor DarkGray
    if ($script:failed.Count -eq 0) {
        Write-Host "✅ 全部通过（$($script:passed.Count) 项）" -ForegroundColor Green
        Write-Host ('═' * 72) -ForegroundColor DarkGray
        exit 0
    } else {
        Write-Host "❌ $($script:failed.Count) 项失败：" -ForegroundColor Red
        foreach ($f in $script:failed) { Write-Host "   · $f" -ForegroundColor Red }
        Write-Host "✅ 通过 $($script:passed.Count) 项" -ForegroundColor Green
        Write-Host ('═' * 72) -ForegroundColor DarkGray
        exit 1
    }
} finally {
    Pop-Location
}
