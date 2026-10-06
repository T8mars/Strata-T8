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
$resultPath = $null
function CheckRoot([string]$path) {
    $parent = $path
    while ($parent) {
        if ((Test-Path -LiteralPath $parent) -and ((Get-Item -LiteralPath $parent -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Linked update directory: $path" }
        $parent = [IO.Path]::GetDirectoryName($parent)
    }
}
function Overlapping([string]$first, [string]$second) {
    return $first.Equals($second, [StringComparison]::OrdinalIgnoreCase) -or $first.StartsWith($second + '\', [StringComparison]::OrdinalIgnoreCase) -or $second.StartsWith($first + '\', [StringComparison]::OrdinalIgnoreCase)
}
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
$installed = [Collections.Generic.List[string]]::new()
$createdDirectories = [Collections.Generic.List[string]]::new()
$removedDirectories = [Collections.Generic.List[string]]::new()
function EnsureDirectory([string]$path) {
    $missing = [Collections.Generic.List[string]]::new()
    $parent = $path
    while (!(Test-Path -LiteralPath $parent)) {
        $missing.Add($parent)
        $parent = [IO.Path]::GetDirectoryName($parent)
    }
    $null = [IO.Directory]::CreateDirectory($path)
    foreach ($directory in $missing) { $createdDirectories.Add($directory) }
}
function WriteResult($value) {
    $temporary = $resultPath + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
    try {
        [IO.File]::WriteAllText($temporary, ($value | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
        if ([IO.File]::Exists($resultPath)) { [IO.File]::Replace($temporary, $resultPath, $null) }
        else { [IO.File]::Move($temporary, $resultPath) }
    } finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) { Remove-Item -LiteralPath $temporary }
    }
}
function FileDigest([string]$path) {
    $stream = [IO.File]::OpenRead($path)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '').ToLower() }
    finally { $stream.Dispose(); $hasher.Dispose() }
}
function MatchEntries($planned, $declared, [string]$label) {
    $expected = @{}
    foreach ($entry in $declared) {
        if (!$entry.path -or $expected.ContainsKey($entry.path)) { throw "Invalid $label manifest entries" }
        $expected[$entry.path] = $entry
    }
    if (@($planned).Count -ne $expected.Count) { throw "$label plan differs from manifest" }
    $seen = @{}
    foreach ($entry in $planned) {
        $original = $expected[$entry.path]
        if (!$original -or $seen.ContainsKey($entry.path) -or $entry.size -ne $original.size -or $entry.sha256 -ne $original.sha256) { throw "$label plan differs from manifest: $($entry.path)" }
        $seen[$entry.path] = $true
    }
}
try {
    CheckRoot $appRoot
    CheckRoot $stageRoot
    CheckRoot $backupRoot
    $expectedPlan = ScopedPath $appRoot '.portable-update/plan.json'
    if (!(LongPath $PlanPath).Equals($expectedPlan, [StringComparison]::OrdinalIgnoreCase)) { throw 'Plan path differs from installation' }
    $resultPath = ScopedPath $appRoot '.portable-update/result.json'
    if ((Overlapping $appRoot $stageRoot) -or (Overlapping $appRoot $backupRoot) -or (Overlapping $stageRoot $backupRoot) -or (Test-Path -LiteralPath $backupRoot)) { throw 'Invalid, overlapping or reused update directory' }
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
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($plan.version -ne $manifest.version) { throw 'Plan version differs from staged manifest' }
    MatchEntries $plan.new $manifest.files 'New'
    $installedManifest = Get-Content -LiteralPath (ScopedPath $appRoot 'PACKAGE-MANIFEST.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    MatchEntries $plan.old $installedManifest.files 'Installed'
    $newNames = @{}
    $oldNames = @{}
    $oldDirectories = @{}
    foreach ($entry in $plan.old) { $oldNames[$entry.path] = $true }
    foreach ($entry in $plan.old) {
        $parent = [IO.Path]::GetDirectoryName($entry.path).Replace('\', '/')
        while ($parent) {
            $oldDirectories[$parent] = $true
            $parent = [IO.Path]::GetDirectoryName($parent).Replace('\', '/')
        }
    }
    foreach ($entry in $plan.new) {
        $source = ScopedPath $stageRoot $entry.path
        $destination = ScopedPath $appRoot $entry.path
        if (!$oldNames.ContainsKey($entry.path) -and (Test-Path -LiteralPath $destination -PathType Leaf)) { throw "Update would overwrite a user file: $($entry.path)" }
        if (!(Test-Path -LiteralPath $source -PathType Leaf) -or (Get-Item -LiteralPath $source).Length -ne $entry.size -or (FileDigest $source) -ne $entry.sha256) { throw "Staged file failed verification: $($entry.path)" }
        $newNames[$entry.path] = $true
    }
    $managed = @($plan.new | ForEach-Object { $_.path }) + @($plan.old | Where-Object { !$newNames.ContainsKey($_.path) } | ForEach-Object { $_.path }) + @('PACKAGE-MANIFEST.json')
    # Detect known path conflicts before replacing a large runtime or moving the first old file.
    foreach ($relative in $managed) {
        $target = ScopedPath $appRoot $relative
        $null = ScopedPath $backupRoot $relative
        if (Test-Path -LiteralPath $target -PathType Container) {
            if (!$newNames.ContainsKey($relative) -or !$oldDirectories.ContainsKey($relative)) { throw "Target is not a file: $relative" }
            foreach ($item in @(Get-ChildItem -LiteralPath $target -Recurse -Force)) {
                $itemRelative = $item.FullName.Substring($appRoot.Length + 1).Replace('\', '/')
                $null = ScopedPath $appRoot $itemRelative
                if (($item.PSIsContainer -and !$oldDirectories.ContainsKey($itemRelative)) -or (!$item.PSIsContainer -and !$oldNames.ContainsKey($itemRelative))) { throw "Update would overwrite a user file or directory: $itemRelative" }
            }
        }
        $parent = [IO.Path]::GetDirectoryName($target)
        while ($parent.StartsWith($appRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
            $parentRelative = $parent.Substring($appRoot.Length + 1).Replace('\', '/')
            if ((Test-Path -LiteralPath $parent -PathType Leaf) -and (!$oldNames.ContainsKey($parentRelative) -or $newNames.ContainsKey($parentRelative))) { throw "Target parent is a file: $relative" }
            $parent = [IO.Path]::GetDirectoryName($parent)
        }
    }
    # Back up old managed files before installing any new files. This permits a
    # managed file to become a directory, or an entirely managed directory a file.
    foreach ($relative in @($plan.old | ForEach-Object { $_.path }) + @('PACKAGE-MANIFEST.json')) {
        $target = ScopedPath $appRoot $relative
        $backup = ScopedPath $backupRoot $relative
        $hadOld = Test-Path -LiteralPath $target -PathType Leaf
        $change = @{ target=$target; backup=$backup; hadOld=$hadOld; saved=$false }
        $changes.Add($change)
        if ($hadOld) {
            $null = New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($backup)) -Force
            Move-Item -LiteralPath $target -Destination $backup
            $change.saved = $true
        }
    }
    foreach ($relative in @($plan.new | ForEach-Object { $_.path }) + @('PACKAGE-MANIFEST.json')) {
        $target = ScopedPath $appRoot $relative
        $source = ScopedPath $stageRoot $relative
        if (Test-Path -LiteralPath $target -PathType Container) {
            $directories = @(Get-ChildItem -LiteralPath $target -Directory -Recurse -Force | Sort-Object { $_.FullName.Length } -Descending | ForEach-Object { $_.FullName }) + @($target)
            foreach ($directory in $directories) {
                # Never recurse during deletion: preflight proved ownership, and
                # any file appearing since preflight makes removal fail safely.
                [IO.Directory]::Delete($directory, $false)
                $removedDirectories.Add($directory)
            }
        }
        EnsureDirectory ([IO.Path]::GetDirectoryName($target))
        $installed.Add($target)
        Copy-Item -LiteralPath $source -Destination $target
    }
    WriteResult @{ success=$true; version=$plan.version; backup=$backupRoot }
    Remove-Item -LiteralPath $PlanPath
    Write-Host "Updated to $($plan.version). Backup: $backupRoot"
    exit 0
} catch {
    $failure = $_.Exception.Message
    $rollbackErrors = @()
    for ($i=$installed.Count-1; $i -ge 0; $i--) {
        try { if (Test-Path -LiteralPath $installed[$i] -PathType Leaf) { Remove-Item -LiteralPath $installed[$i] } }
        catch { $rollbackErrors += $_.Exception.Message }
    }
    foreach ($directory in @($createdDirectories | Select-Object -Unique | Sort-Object Length -Descending)) {
        try { if (Test-Path -LiteralPath $directory -PathType Container) { [IO.Directory]::Delete($directory, $false) } }
        catch { $rollbackErrors += $_.Exception.Message }
    }
    for ($i=$changes.Count-1; $i -ge 0; $i--) {
        $change = $changes[$i]
        try {
            if ($change.saved) {
                $null = [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($change.target))
                Move-Item -LiteralPath $change.backup -Destination $change.target
            }
        } catch { $rollbackErrors += $_.Exception.Message }
    }
    foreach ($directory in $removedDirectories) {
        try { $null = [IO.Directory]::CreateDirectory($directory) }
        catch { $rollbackErrors += $_.Exception.Message }
    }
    if ($resultPath) {
        try { WriteResult @{ success=$false; error=$failure; rollback_errors=$rollbackErrors; backup=$backupRoot } }
        catch { Write-Host "Could not write update result: $($_.Exception.Message)" }
    }
    Write-Host "Update failed: $failure"
    if ($rollbackErrors.Count) { Write-Host 'Rollback needs attention. See .portable-update/result.json and the backup.' }
    else { Write-Host 'Previous installation preserved.' }
    exit 1
}
