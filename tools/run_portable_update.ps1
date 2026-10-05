param([Parameter(Mandatory=$true)][string]$AppRoot)
$ErrorActionPreference = 'Stop'
$appDirectory = [IO.Path]::GetFullPath($AppRoot)
$env:PYTHONHOME = $null
$env:PYTHONPATH = $null
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$options = @()
if ($env:T8_UPDATE_OPTIONS) {
    if ($env:T8_UPDATE_OPTIONS.Trim() -ne '--check') {
        Write-Host 'Supported option: --check'
        Read-Host 'Press Enter to close' | Out-Null
        exit 2
    }
    $options = @('--check')
}
& (Join-Path $appDirectory 'runtime/python/python.exe') -X utf8 -u (Join-Path $appDirectory 'tools/portable_update.py') @options
$updateResult = $LASTEXITCODE
if ($updateResult -eq 0 -and !$options.Count) {
    $planFile = Join-Path $appDirectory '.portable-update/plan.json'
    if (Test-Path -LiteralPath $planFile) {
        # PowerShell parses this complete script before replacing any application files.
        & (Join-Path $appDirectory '.portable-update/apply.ps1') -PlanPath $planFile
        $updateResult = $LASTEXITCODE
    }
}
Read-Host 'Press Enter to close' | Out-Null
exit $updateResult
