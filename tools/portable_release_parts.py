"""Bounded GitHub assets for a complete offline ZIP; component updates stay unchanged."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re

ASSET_LIMIT = 2 * 1024**3
PART_SIZE = 1900 * 1024**2
MAX_ARCHIVE = 16 * 1024**3


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def names(archive):
    name = Path(archive).name
    if not re.fullmatch(r'Strata-T8-[0-9.]+-t8\.[0-9]+-Windows-x64-(?:Portable-NoModels|VisionReady-NoMainModel)\.zip', name):
        raise ValueError('Unexpected portable archive name')
    return name + '.parts.json', name + '-ASSEMBLE.ps1', name + '-ASSEMBLE.bat'


def scripts(archive):
    manifest, psname, batname = names(archive)
    # The expected filename is fixed by the audited script, never a shell argument.
    ps = r'''$ErrorActionPreference = 'Stop'
function FileDigest([string]$path) {
  $stream = [IO.File]::OpenRead($path)
  $hash = [Security.Cryptography.SHA256]::Create()
  try { return [BitConverter]::ToString($hash.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
  finally { $hash.Dispose(); $stream.Dispose() }
}
try {
  $base = $PSScriptRoot
  $expected = '__ARCHIVE__'
  $metadata = Join-Path $base '__MANIFEST__'
  if ((Get-Item -LiteralPath $metadata).Length -gt 32768) { throw 'Invalid parts manifest' }
  $m = Get-Content -LiteralPath $metadata -Raw -Encoding UTF8 | ConvertFrom-Json
  if ($m.schema -ne 1 -or $m.archive.file -cne $expected -or $m.parts.Count -lt 2 -or $m.parts.Count -gt 9) { throw 'Invalid parts manifest' }
  $total = [int64]0; $number = 0
  foreach ($part in $m.parts) {
    $number++
    if ($part.file -cne ($expected + '.' + $number.ToString('000')) -or $part.size -le 0 -or $part.size -ge 2147483648 -or $part.sha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'Invalid part record' }
    $p = Join-Path $base $part.file
    if ((Get-Item -LiteralPath $p).Length -ne $part.size -or (FileDigest $p) -cne $part.sha256) { throw ('Missing or damaged part: ' + $part.file) }
    $total += $part.size
  }
  if ($total -ne $m.archive.size -or $total -gt 17179869184 -or $m.archive.sha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'Invalid complete ZIP size or checksum' }
  $target = Join-Path $base $expected
  if (Test-Path -LiteralPath $target) { throw 'The complete ZIP already exists; it has not been replaced.' }
  $temporary = $target + '.assembling'
  $output = [IO.File]::Open($temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
  try {
    foreach ($part in $m.parts) {
      Write-Host ('Combining ' + $part.file)
      $inputFile = [IO.File]::OpenRead((Join-Path $base $part.file))
      try { $inputFile.CopyTo($output) } finally { $inputFile.Dispose() }
    }
  } finally { $output.Dispose() }
  if ((FileDigest $temporary) -cne $m.archive.sha256) { throw 'Complete ZIP checksum failed' }
  [IO.File]::Move($temporary, $target)
  Write-Host ('Verified ZIP: ' + $target)
  Write-Host 'Extract this ZIP, then open Strata-T8.exe inside its folder.'
} catch { Write-Error $_ -ErrorAction Continue; exit 1 }
'''.replace('__ARCHIVE__', Path(archive).name).replace('__MANIFEST__', manifest)
    bat = '@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0' + psname + '"\r\npause\r\n'
    return {psname: ps.encode('utf-8'), batname: bat.encode('ascii')}


def split(archive, *, part_size=PART_SIZE):
    archive = Path(archive)
    size = archive.stat().st_size
    if not 0 < part_size < ASSET_LIMIT or size > MAX_ARCHIVE:
        raise ValueError('Portable archive exceeds bounded multipart limits')
    count = (size + part_size - 1) // part_size
    if not 2 <= count <= 9:
        raise ValueError('Multipart archives must have 2 to 9 parts')
    manifest, _, _ = names(archive)
    destinations = [archive, archive.with_name(manifest),
                    *(archive.with_name(name) for name in scripts(archive)),
                    *(archive.with_name(archive.name + f'.{i:03}') for i in range(1, count+1))]
    if any(p.is_symlink() or p.resolve().parent != archive.parent.resolve() for p in destinations):
        raise ValueError('Linked multipart asset path')
    parts = []; whole = hashlib.sha256()
    with archive.open('rb') as stream:
        while stream.tell() < size:
            path = archive.with_name(archive.name + f'.{len(parts)+1:03}')
            remaining = min(part_size, size - stream.tell()); hashed = hashlib.sha256()
            with path.open('wb') as output:
                while remaining:
                    data = stream.read(min(4*1024**2, remaining))
                    if not data: raise ValueError('Archive shortened while splitting')
                    output.write(data); hashed.update(data); whole.update(data); remaining -= len(data)
            parts.append({'file': path.name, 'size': path.stat().st_size, 'sha256': hashed.hexdigest()})
    if not 2 <= len(parts) <= 9:
        raise ValueError('Multipart archives must have 2 to 9 parts')
    record = {'schema': 1, 'archive': {'file': archive.name, 'size': size, 'sha256': whole.hexdigest()}, 'parts': parts}
    archive.with_name(manifest).write_text(json.dumps(record, indent=2)+'\n', encoding='utf-8')
    for name, data in scripts(archive).items(): archive.with_name(name).write_bytes(data)
    return record


def validate(archive, expected_digest):
    """Hash every part together and verify the fixed offline assembly scripts."""
    archive = Path(archive); manifest, psname, batname = names(archive)
    path = archive.with_name(manifest)
    if path.is_symlink() or path.stat().st_size > 32768: raise ValueError('Invalid multipart manifest')
    from portable_update import update_json
    record = update_json(path.read_bytes())
    expected = {'file': archive.name, 'size': archive.stat().st_size, 'sha256': expected_digest}
    if record.get('schema') != 1 or record.get('archive') != expected or expected['size'] > MAX_ARCHIVE:
        raise ValueError('Multipart manifest differs from audited ZIP')
    parts = record.get('parts')
    if not isinstance(parts, list) or not 2 <= len(parts) <= 9: raise ValueError('Invalid multipart count')
    assets = []; hashes = {}; combined = hashlib.sha256(); total = 0
    for index, part in enumerate(parts, 1):
        name = archive.name + f'.{index:03}'
        if not isinstance(part, dict) or part.get('file') != name or type(part.get('size')) is not int or not 0 < part['size'] < ASSET_LIMIT:
            raise ValueError('Invalid multipart entry')
        p = archive.with_name(name)
        if p.is_symlink() or not p.is_file() or p.stat().st_size != part['size']: raise ValueError('Missing or linked ZIP part')
        hashed = hashlib.sha256()
        with p.open('rb') as stream:
            while data := stream.read(4*1024**2): hashed.update(data); combined.update(data)
        if hashed.hexdigest() != part.get('sha256'): raise ValueError('ZIP part checksum mismatch')
        total += part['size']; assets.append(p); hashes[name] = hashed.hexdigest()
    if total != expected['size'] or combined.hexdigest() != expected_digest: raise ValueError('Parts do not recreate audited ZIP')
    for name, data in scripts(archive).items():
        p = archive.with_name(name)
        if p.is_symlink() or not p.is_file() or p.read_bytes() != data: raise ValueError('Assembly script differs from audited source')
    for name in (manifest, psname, batname):
        p = archive.with_name(name); assets.append(p); hashes[name] = digest(p)
    return assets, hashes
