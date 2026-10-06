"""Build the Windows runtime from pinned Python, dependencies and matching upstream assets."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from portable_version import ROOT, metadata, source_version
from windows_utf8_manifest import patch_engine
sys.path.insert(0, str(ROOT))
import setup


def download(url, target, sha=None):
    if target.is_file():
        if not sha:
            return
        with target.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() == sha:
                return
    print(f'Downloading {target.name}', flush=True)
    descriptor, name = tempfile.mkstemp(prefix=target.name+'.', suffix='.tmp', dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as dst, urllib.request.urlopen(url, timeout=120) as src:
            shutil.copyfileobj(src, dst, length=4*1024**2)
        if sha:
            with temporary.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != sha:
                    raise ValueError(f'Checksum mismatch: {target.name}')
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def extract(archive, destination):
    destination = Path(destination)
    with zipfile.ZipFile(archive) as z:
        entries = {}
        total = 0
        for member in z.infolist():
            name = member.filename.rstrip('/') if member.is_dir() else member.filename
            parts = name.split('/')
            target = destination.joinpath(*parts)
            if (not name or PurePosixPath(name).is_absolute() or ':' in name or '\\' in name
                    or any(not p or p in ('.', '..') or p.endswith((' ', '.')) or PureWindowsPath(p).is_reserved()
                           or re.search(r'[<>"|?*\x00-\x1f\x7f]', p) for p in parts)
                    or not target.resolve().is_relative_to(destination.resolve())):
                raise ValueError('Unsafe upstream ZIP path')
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Linked upstream ZIP path')
            for parent in [target, *target.parents]:
                if parent == destination.parent: break
                if parent.is_symlink() or getattr(parent, 'is_junction', lambda: False)():
                    raise ValueError('Linked upstream extraction path')
                if parent.exists() and ((parent == target and member.is_dir() and not parent.is_dir())
                        or (parent == target and not member.is_dir() and parent.is_dir())
                        or (parent != target and not parent.is_dir())):
                    raise ValueError('Conflicting upstream extraction path')
            key = name.casefold()
            if key in entries:
                raise ValueError('Duplicate upstream ZIP path')
            entries[key] = member.is_dir()
            total += member.file_size
            if total > 8*1024**3:
                raise ValueError('Upstream ZIP expands beyond 8 GiB')
        for key in entries:
            if any(entries.get(parent.as_posix()) is False for parent in PurePosixPath(key).parents if parent.as_posix() != '.'):
                raise ValueError('Conflicting upstream ZIP paths')
        z.extractall(destination)


def main():
    if os.name != 'nt':
        raise SystemExit('Windows x64 runner required')
    meta = metadata()
    version = source_version()
    if version != meta['upstream_version']:
        raise ValueError('Source and distribution metadata versions differ')
    cache = ROOT/'.portable-build'
    cache.mkdir(exist_ok=True)
    pyversion = meta['python_version']
    archive = cache/f'python-{pyversion}-embed-amd64.zip'
    download(f'https://www.python.org/ftp/python/{pyversion}/{archive.name}', archive, meta['python_zip_sha256'])
    runtime = ROOT/'runtime/python'
    runtime.mkdir(parents=True, exist_ok=True)
    extract(archive, runtime)
    (runtime/'python312._pth').write_text('python312.zip\n.\nLib\\site-packages\n..\\..\n..\\..\\tools\n', encoding='ascii')
    getpip = cache/'get-pip.py'
    download('https://bootstrap.pypa.io/get-pip.py', getpip)
    python = runtime/'python.exe'
    subprocess.run([str(python), str(getpip), '--no-warn-script-location'], check=True)
    subprocess.run([str(python), '-m', 'pip', 'install', '--no-warn-script-location', '-r', str(ROOT/'requirements.txt'), '-r', str(ROOT/'requirements-portable.txt'), *setup.CUDA_WHEELS], check=True)
    subprocess.run([str(python), '-m', 'pip', 'check'], check=True)
    # Only the metadata API receives the runner's token; asset requests never carry it.
    headers = {'User-Agent': 'Strata-T8-build'}
    if os.environ.get('GH_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['GH_TOKEN']
    req = urllib.request.Request(f'https://api.github.com/repos/{meta["upstream_repository"]}/releases/tags/v{version}', headers=headers)
    with urllib.request.urlopen(req, timeout=60) as response:
        assets = {a['name']: a for a in json.load(response)['assets']}
    for name, directory in [('strata-windows-x64.zip', 'engine'), ('strata-windows-x64-hip.zip', 'engine-hip')]:
        asset = assets[name]
        digest = asset.get('digest', '')
        if not digest.startswith('sha256:'):
            raise ValueError(f'Upstream asset has no SHA256: {name}')
        archive = cache/name
        # Cache names include the source version to avoid reusing an old engine.
        archive = archive.with_name(f'{version}-{name}')
        download(asset['browser_download_url'], archive, digest.split(':', 1)[1])
        target = ROOT/directory
        target.mkdir(exist_ok=True)
        extract(archive, target)
        build = json.loads((target/'BUILD.json').read_text())
        if build['version'] != version:
            raise ValueError(f'Mismatched upstream engine: {directory}')
        patch_engine(target)
    llama = ROOT/'third_party/llama.cpp'
    if not (llama/'gguf-py/gguf').exists():
        archive = cache/f'llama-{setup.LLAMA_CPP_COMMIT}.zip'
        download(setup.LLAMA_CPP_ZIP, archive)
        temporary = cache/'llama-extracted'
        temporary.mkdir(exist_ok=True)
        extract(archive, temporary)
        source = temporary/f'llama.cpp-{setup.LLAMA_CPP_COMMIT}'
        llama.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source/'gguf-py', llama/'gguf-py', dirs_exist_ok=True)
        shutil.copy2(source/'LICENSE', llama/'LICENSE')
    print(f'Runtime ready for Strata {version}', flush=True)


if __name__ == '__main__':
    main()
