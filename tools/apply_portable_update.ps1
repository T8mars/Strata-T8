param([Parameter(Mandatory=$true)][string]$PlanPath)
$ErrorActionPreference = 'Stop'
$plan = $null
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
$appRoot = $null
$stageRoot = $null
$backupRoot = $null
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
        if ([IO.File]::Exists($resultPath)) { [IO.File]::Replace($temporary, $resultPath, [NullString]::Value) }
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
function ReadJsonObject([string]$path, [string]$label) {
    $document = Get-Content -LiteralPath $path -Raw -Encoding UTF8
    # Windows PowerShell unwraps a one-element JSON array when assigning pipeline output.
    if ($document -notmatch '^\s*\{') { throw "$label must be a JSON object" }
    $value = $document | ConvertFrom-Json
    if ($value -isnot [pscustomobject]) { throw "$label must be a JSON object" }
    return $value
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
function CheckEntries($entries, [string]$label) {
    if ($entries -isnot [Array]) { throw "$label files must be an array" }
    $names = @{}
    foreach ($entry in $entries) {
        if ($entry -isnot [pscustomobject] -or $entry.path -isnot [string] -or !$entry.path -or
            ($entry.size -isnot [int] -and $entry.size -isnot [long]) -or $entry.size -lt 0 -or
            $entry.sha256 -isnot [string] -or $entry.sha256 -cnotmatch '^[0-9a-f]{64}$') { throw "Invalid $label file entry" }
        $relative = $entry.path
        if ($relative -match '(^/|\\|:|(^|/)\.\.?(/|$)|[<>"|?*\x00-\x1f\x7f])' -or
            $relative -match '^(Strata-data|models|packs|mtp|\.git|logs|\.portable-update)(/|$)' -or
            $relative -match '^(portable-settings\.json|strata-.*\.json|PACKAGE-MANIFEST\.json)$') { throw "Unsafe $label file path: $relative" }
        foreach ($part in $relative.Split('/')) {
            if (!$part -or $part.EndsWith(' ') -or $part.EndsWith('.') -or $part -match '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)') { throw "Invalid $label Windows file path: $relative" }
        }
        if ($names.ContainsKey($relative)) { throw "Duplicate $label file path: $relative" }
        $names[$relative] = $true
    }
    if (!$names.ContainsKey('meta.json')) { throw "$label manifest lacks application metadata" }
    foreach ($relative in $names.Keys) {
        $parent = [IO.Path]::GetDirectoryName($relative).Replace('\', '/')
        while ($parent) {
            if ($names.ContainsKey($parent)) { throw "Conflicting $label file paths: $relative" }
            $parent = [IO.Path]::GetDirectoryName($parent).Replace('\', '/')
        }
    }
}
function CheckMetadata($metadata, $manifest) {
    if ($metadata -isnot [pscustomobject] -or $metadata.version -isnot [string] -or
        $metadata.version -cne $manifest.version) { throw 'Application metadata version differs from package manifest' }
    $edition = 'Portable-NoModels'
    if ($manifest.PSObject.Properties['edition']) {
        if ($manifest.edition -isnot [string]) { throw 'Package edition must be a string' }
        $edition = $manifest.edition
    }
    if ($edition -cne 'Portable-NoModels' -and $edition -cne 'VisionReady-NoMainModel') { throw 'Unknown package edition' }
    $applicationEdition = 'Portable-NoModels'
    if ($metadata.PSObject.Properties['edition']) {
        if ($metadata.edition -isnot [string]) { throw 'Application metadata edition must be a string' }
        $applicationEdition = $metadata.edition
    }
    if ($applicationEdition -cne $edition) { throw 'Application metadata edition differs from package manifest' }
    $vision = $edition -ceq 'VisionReady-NoMainModel'
    if ($manifest.models_included -isnot [bool] -or $manifest.models_included -ne $vision) { throw 'Manifest models_included differs from edition' }
    foreach ($value in @($manifest, $metadata)) {
        if ($value.PSObject.Properties['models_included'] -and
            ($value.models_included -isnot [bool] -or $value.models_included -ne $vision)) { throw 'Application metadata models_included differs from package manifest' }
        if ($value.PSObject.Properties['weights']) {
            $roles = $value.weights
            if ($roles -isnot [pscustomobject] -or @($roles.PSObject.Properties).Count -ne 3 -or
                $roles.main -isnot [bool] -or $roles.main -ne $false -or
                $roles.mtp -isnot [bool] -or $roles.mtp -ne $false -or
                $roles.vision -isnot [bool] -or $roles.vision -ne $vision) { throw 'Application metadata weight roles differ from package manifest' }
        } elseif ($value -eq $manifest -and $vision) { throw 'VisionReady manifest lacks weight roles' }
    }
}
function CompareVersion([string]$first, [string]$second) {
    # Compare arbitrarily large integer components without float or lexical ordering.
    foreach ($value in @($first, $second)) {
        if ($value -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+-t8\.[0-9]+$') { throw 'Invalid installed or staged release version' }
    }
    $left = $first -split '\.|-t8\.'
    $right = $second -split '\.|-t8\.'
    for ($index = 0; $index -lt 4; $index++) {
        $a = $left[$index] -replace '^0+(?=.)', ''
        $b = $right[$index] -replace '^0+(?=.)', ''
        if ($a.Length -ne $b.Length) { return [Math]::Sign($a.Length - $b.Length) }
        $compared = [string]::CompareOrdinal($a, $b)
        if ($compared) { return [Math]::Sign($compared) }
    }
    return 0
}
function InstallFile([string]$source, [string]$target, [long]$size, [string]$digest) {
    # Publish a complete file without overwriting a file created after preflight.
    $temporary = Join-Path ([IO.Path]::GetDirectoryName($target)) ('.t8-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    try {
        Copy-Item -LiteralPath $source -Destination $temporary
        # Preflight verifies staging; verify the actual copy before publishing it too.
        if ((Get-Item -LiteralPath $temporary).Length -ne $size -or (FileDigest $temporary) -cne $digest) { throw "Copied update file failed verification: $target" }
        [IO.File]::Move($temporary, $target)
        $installed.Add($target)
    } finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) { Remove-Item -LiteralPath $temporary -Force }
    }
}
try {
    $plan = ReadJsonObject $PlanPath 'Update plan'
    if ($plan -isnot [pscustomobject]) { throw 'Update plan must be an object' }
    foreach ($field in @('root','stage','backup')) {
        if ($plan.$field -isnot [string] -or !$plan.$field -or ![IO.Path]::IsPathRooted($plan.$field)) { throw "Invalid update plan directory: $field" }
    }
    if ($plan.version -isnot [string] -or $plan.version -notmatch '^\d+\.\d+\.\d+-t8\.\d+$' -or
        $plan.manifest_sha256 -isnot [string] -or $plan.manifest_sha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'Invalid update plan identity' }
    CheckEntries $plan.new 'New plan'
    CheckEntries $plan.old 'Installed plan'
    $appRoot = LongPath $plan.root
    $stageRoot = LongPath $plan.stage
    $backupRoot = [IO.Path]::GetFullPath($plan.backup).TrimEnd('\')
    if (![IO.Directory]::Exists($appRoot) -or ![IO.Directory]::Exists($stageRoot)) { throw 'Application and staging roots must be directories' }
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
    $manifest = ReadJsonObject $manifestPath 'New manifest'
    $manifestSize = (Get-Item -LiteralPath $manifestPath).Length
    CheckEntries $manifest.files 'New manifest'
    if ($plan.version -ne $manifest.version) { throw 'Plan version differs from staged manifest' }
    MatchEntries $plan.new $manifest.files 'New'
    $installedManifestPath = ScopedPath $appRoot 'PACKAGE-MANIFEST.json'
    if ($plan.PSObject.Properties['installed_manifest_sha256'] -and
        ($plan.installed_manifest_sha256 -isnot [string] -or $plan.installed_manifest_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
         (FileDigest $installedManifestPath) -cne $plan.installed_manifest_sha256)) { throw 'Installed manifest changed after update preparation' }
    $installedManifest = ReadJsonObject $installedManifestPath 'Installed manifest'
    CheckEntries $installedManifest.files 'Installed manifest'
    MatchEntries $plan.old $installedManifest.files 'Installed'
    $installedMetadata = ReadJsonObject (ScopedPath $appRoot 'meta.json') 'Installed application metadata'
    CheckMetadata $installedMetadata $installedManifest
    $comparison = CompareVersion $manifest.version $installedManifest.version
    $incomingEdition = if ($manifest.PSObject.Properties['edition']) { $manifest.edition } else { 'Portable-NoModels' }
    $installedEdition = if ($installedManifest.PSObject.Properties['edition']) { $installedManifest.edition } else { 'Portable-NoModels' }
    if ($comparison -lt 0 -or ($comparison -eq 0 -and $incomingEdition -ceq $installedEdition)) { throw 'Update would downgrade or reapply the installed version and edition' }
    $newNames = @{}
    $newEntries = @{}
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
        $newEntries[$entry.path] = $entry
    }
    $stagedMetadata = ReadJsonObject (ScopedPath $stageRoot 'meta.json') 'Application metadata'
    CheckMetadata $stagedMetadata $manifest
    # An apply may run much later than prepare; recheck the entire staged inventory.
    foreach ($item in @(Get-ChildItem -LiteralPath $stageRoot -Recurse -Force)) {
        $relative = $item.FullName.Substring($stageRoot.Length + 1).Replace('\', '/')
        $null = ScopedPath $stageRoot $relative
        if (!$item.PSIsContainer -and !$newNames.ContainsKey($relative) -and $relative -ne 'PACKAGE-MANIFEST.json') { throw "Unlisted staged file: $relative" }
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
            # A file move must name an exact vacant file, never a directory into
            # which Move-Item can silently place it after a concurrent change.
            $null = ScopedPath $backupRoot $relative
            $null = ScopedPath $appRoot $relative
            [IO.File]::Move($target, $backup)
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
        if ($relative -eq 'PACKAGE-MANIFEST.json') { InstallFile $source $target $manifestSize $plan.manifest_sha256 }
        else { $entry = $newEntries[$relative]; InstallFile $source $target $entry.size $entry.sha256 }
    }
    WriteResult @{ success=$true; version=$plan.version; backup=$backupRoot }
    Remove-Item -LiteralPath $PlanPath
    Write-Host "Updated to $($plan.version). Backup: $backupRoot"
    exit 0
} catch {
    $failure = $_.Exception.Message
    $rollbackErrors = @()
    for ($i=$installed.Count-1; $i -ge 0; $i--) {
        try { if (Test-Path -LiteralPath $installed[$i] -PathType Leaf) { Remove-Item -LiteralPath $installed[$i] -Force } }
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
                $relative = $change.target.Substring($appRoot.Length + 1).Replace('\', '/')
                $null = ScopedPath $appRoot $relative
                $null = ScopedPath $backupRoot $relative
                $null = [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($change.target))
                [IO.File]::Move($change.backup, $change.target)
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
