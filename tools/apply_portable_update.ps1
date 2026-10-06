param([Parameter(Mandatory=$true)][string]$PlanPath)
$ErrorActionPreference = 'Stop'
$plan = Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class T8Paths {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern uint GetLongPathName(string shortPath, StringBuilder longPath, uint capacity);
}
'@
function LongPath([string]$path) {
    $buffer = [Text.StringBuilder]::new(32768)
    $full = [IO.Path]::GetFullPath($path)
    if ([T8Paths]::GetLongPathName($full, $buffer, 32768) -eq 0) { throw "Cannot resolve path: $path" }
    return $buffer.ToString().TrimEnd('\')
}
$appRoot = LongPath $plan.root
$stageRoot = LongPath $plan.stage
$backupRoot = [IO.Path]::GetFullPath($plan.backup).TrimEnd('\')
$resultPath = Join-Path $appRoot '.portable-update/result.json'
function ScopedPath([string]$base, [string]$relative) {
    if ($relative -match '(^/|\\|:|(^|/)\.\.?(/|$))') { throw "Unsafe path: $relative" }
    $target = [IO.Path]::GetFullPath((Join-Path $base $relative))
    if (!$target.StartsWith($base + '\', [StringComparison]::OrdinalIgnoreCase)) { throw "Path outside application: $relative" }
    $parent = $target
    while ($parent -and $parent.StartsWith($base, [StringComparison]::OrdinalIgnoreCase)) {
        if ((Test-Path -LiteralPath $parent) -and ((Get-Item -LiteralPath $parent -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Linked path: $relative" }
        $parent = [IO.Path]::GetDirectoryName($parent)
    }
    return $target
}
$changes = [Collections.Generic.List[object]]::new()
function FileDigest([string]$path) {
    $stream = [IO.File]::OpenRead($path)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '').ToLower() }
    finally { $stream.Dispose(); $hasher.Dispose() }
}
try {
    if (($appRoot -eq $stageRoot) -or ($appRoot -eq $backupRoot) -or (Test-Path -LiteralPath $backupRoot)) { throw 'Invalid or reused update directory' }
    $busy = @(Get-CimInstance Win32_Process | Where-Object {
        $processPath = $_.ExecutablePath
        if (!$processPath -or ![IO.Path]::IsPathRooted($processPath)) { return $false }
        try { $exePath = LongPath $processPath }
        catch { $exePath = [IO.Path]::GetFullPath($processPath) }
        $exePath.StartsWith($appRoot + '\', [StringComparison]::OrdinalIgnoreCase)
    })
    if ($busy.Count) { throw 'Strata is running. Close its window and try again.' }
    $manifestPath = ScopedPath $stageRoot 'PACKAGE-MANIFEST.json'
    if (!$plan.manifest_sha256 -or (FileDigest $manifestPath) -ne $plan.manifest_sha256) { throw 'Staged manifest failed verification' }
    $newNames = @{}
    $oldNames = @{}
    foreach ($entry in $plan.old) { $oldNames[$entry.path] = $true }
    foreach ($entry in $plan.new) {
        $source = ScopedPath $stageRoot $entry.path
        $destination = ScopedPath $appRoot $entry.path
        if (!$oldNames.ContainsKey($entry.path) -and (Test-Path -LiteralPath $destination -PathType Leaf)) { throw "Update would overwrite a user file: $($entry.path)" }
        if (!(Test-Path -LiteralPath $source -PathType Leaf) -or (Get-Item -LiteralPath $source).Length -ne $entry.size -or (FileDigest $source) -ne $entry.sha256) { throw "Staged file failed verification: $($entry.path)" }
        $newNames[$entry.path] = $true
    }
    $managed = @($plan.new | ForEach-Object { $_.path }) + @($plan.old | Where-Object { !$newNames.ContainsKey($_.path) } | ForEach-Object { $_.path }) + @('PACKAGE-MANIFEST.json')
    foreach ($relative in $managed) {
        $target = ScopedPath $appRoot $relative
        $backup = ScopedPath $backupRoot $relative
        $source = ScopedPath $stageRoot $relative
        if ((Test-Path -LiteralPath $target) -and !(Test-Path -LiteralPath $target -PathType Leaf)) { throw "Target is not a file: $relative" }
        $hadOld = Test-Path -LiteralPath $target -PathType Leaf
        $change = @{ target=$target; backup=$backup; hadOld=$hadOld; saved=$false; installed=$false }
        $changes.Add($change)
        if ($hadOld) {
            $null = New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($backup)) -Force
            Move-Item -LiteralPath $target -Destination $backup
            $change.saved = $true
        }
        if ($newNames.ContainsKey($relative) -or $relative -eq 'PACKAGE-MANIFEST.json') {
            $null = New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($target)) -Force
            Copy-Item -LiteralPath $source -Destination $target
            $change.installed = $true
        }
    }
    @{ success=$true; version=$plan.version; backup=$backupRoot } | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
    Remove-Item -LiteralPath $PlanPath
    Write-Host "Updated to $($plan.version). Backup: $backupRoot"
    exit 0
} catch {
    $failure = $_.Exception.Message
    $rollbackErrors = @()
    for ($i=$changes.Count-1; $i -ge 0; $i--) {
        $change = $changes[$i]
        try {
            if (($change.saved -or !$change.hadOld) -and (Test-Path -LiteralPath $change.target -PathType Leaf)) { Remove-Item -LiteralPath $change.target }
            if ($change.saved) { Move-Item -LiteralPath $change.backup -Destination $change.target }
        } catch { $rollbackErrors += $_.Exception.Message }
    }
    @{ success=$false; error=$failure; rollback_errors=$rollbackErrors; backup=$backupRoot } | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
    Write-Host "Update failed: $failure"
    if ($rollbackErrors.Count) { Write-Host 'Rollback needs attention. See .portable-update/result.json and the backup.' }
    else { Write-Host 'Previous installation preserved.' }
    exit 1
}
