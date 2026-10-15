param(
    [Parameter(Mandatory=$true)][string]$AppRoot,
    [Parameter(Mandatory=$true)][ValidateRange(1,2147483647)][int]$ParentId,
    [Parameter(Mandatory=$true)][ValidateRange(1,65535)][int]$UIPort,
    [switch]$NonInteractive,
    [ValidatePattern('^[0-9a-f]{32}$')][string]$RestoreId,
    [string]$StatusFile,
    [ValidatePattern('^[0-9a-f]{32}$')][string]$HandoffNonce,
    [string]$LogPath
)
$ErrorActionPreference = 'Stop'
$env:PYTHONHOME = $null
$env:PYTHONPATH = $null
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$appDirectory = [IO.Path]::GetFullPath($AppRoot)
$resultCode = 1
$transcribing = $false
function Write-UpdateStatus([string]$State, [string]$Detail = '') {
    if (!$StatusFile) { return }
    $record = @{state=$State; error=$Detail; nonce=$HandoffNonce; app_root=$appDirectory; parent_id=$ParentId}
    $pending = $StatusFile + '.tmp'
    [IO.File]::WriteAllText($pending, ($record | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $pending -Destination $StatusFile -Force
}
try {
    if ($StatusFile -and !$HandoffNonce) { throw 'Missing update handoff nonce.' }
    if ($LogPath) { Start-Transcript -LiteralPath $LogPath -Force | Out-Null; $transcribing = $true }
    Write-UpdateStatus 'waiting_parent'
    Write-Host 'Strata-T8 | By T8star'
    Write-Host 'Waiting for the launcher to exit; models and settings are preserved.'
    $parent = Get-Process -Id $ParentId -ErrorAction SilentlyContinue
    if ($parent -and !$parent.WaitForExit(60000)) { throw 'Launcher is still running. No update was applied.' }
    Write-UpdateStatus 'downloading'
    $python = Join-Path $appDirectory 'runtime/python/python.exe'
    if ($RestoreId) { & $python -X utf8 -u (Join-Path $appDirectory 'tools/portable_update.py') --restore $RestoreId }
    else { & $python -X utf8 -u (Join-Path $appDirectory 'tools/portable_update.py') }
    if ($LASTEXITCODE -ne 0) { throw 'Download or verification failed. Previous installation is preserved.' }
    $planFile = Join-Path $appDirectory '.portable-update/plan.json'
    if (Test-Path -LiteralPath $planFile) {
        Write-UpdateStatus 'applying'
        # The verified updater creates this executor; it performs the existing
        # topology, process, checksum and rollback checks before replacement.
        & (Join-Path $appDirectory '.portable-update/apply.ps1') -PlanPath $planFile
        if ($LASTEXITCODE -ne 0) { throw 'Update failed. See .portable-update/result.json.' }
    }
    Write-Host 'Opening model settings...'
    Write-UpdateStatus 'restarting'
    $child = Start-Process -FilePath $python -ArgumentList @('-X','utf8','-u','portable.py','settings','--ui-port',"$UIPort") -WorkingDirectory $appDirectory -WindowStyle Hidden -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    $opened = $false
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($child.HasExited) { throw 'Settings could not restart. Run START-HERE.bat.' }
        try {
            $identity = Invoke-RestMethod -Uri "http://127.0.0.1:$UIPort/api/identity" -TimeoutSec 2
            if ($identity.service -eq 'strata-portable-ui' -and $identity.app_root -eq $appDirectory) { $opened = $true; break }
        } catch { }
        Start-Sleep -Milliseconds 200
    }
    if (!$opened) { throw 'Settings did not become ready. Run START-HERE.bat.' }
    Write-Host 'Update complete. Choose a model or start the service in the settings page.'
    Write-UpdateStatus 'completed'
    $resultCode = 0
} catch {
    try { Write-UpdateStatus 'error' $_.Exception.Message } catch { }
    Write-Host $_.Exception.Message
    Write-Host 'You can retry with UPDATE-PORTABLE.bat. Models and settings are kept.'
}
if ($transcribing) { Stop-Transcript | Out-Null }
if (!$NonInteractive) { Read-Host 'Press Enter to close this update window' | Out-Null }
exit $resultCode
