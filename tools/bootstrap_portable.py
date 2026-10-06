"""Build the Windows runtime from pinned Python, dependencies and matching upstream assets."""
from __future__ import annotations
import hashlib
import argparse
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
from portable_build_provenance import ENGINE_ASSETS, build_identity, validate_engine_build
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


def bootstrap_engines(meta, version, cache, destination):
    """Verify vendor assets before extracting and retain their immutable identity."""
    identity = build_identity(meta, version)
    cache, destination = Path(cache), Path(destination)
    cache.mkdir(parents=True, exist_ok=True)
    # Only the metadata API receives the runner's token; asset requests never carry it.
    headers = {'User-Agent': 'Strata-T8-build'}
    if os.environ.get('GH_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['GH_TOKEN']
    tag = identity['upstream_release_tag']
    repository = meta['upstream_repository']
    if not isinstance(repository, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Invalid upstream repository')
    req = urllib.request.Request(f'https://api.github.com/repos/{repository}/releases/tags/{tag}', headers=headers)
    with urllib.request.urlopen(req, timeout=60) as response:
        payload = response.read(4*1024**2+1)
    if len(payload) > 4*1024**2:
        raise ValueError('Oversized upstream release metadata')
    release = json.loads(payload)
    if release.get('tag_name') != tag or release.get('draft') or release.get('prerelease'):
        raise ValueError('Upstream release identity or stability differs from package metadata')
    assets = {}
    for asset in release['assets']:
        if asset['name'] in assets:
            raise ValueError('Duplicate upstream asset name')
        assets[asset['name']] = asset
    builds = {}
    for directory, name in ENGINE_ASSETS.items():
        asset = assets[name]
        digest = asset.get('digest', '')
        if not isinstance(digest, str) or not re.fullmatch('sha256:[0-9a-f]{64}', digest):
            raise ValueError(f'Upstream asset has no SHA256: {name}')
        sha = digest.split(':', 1)[1]
        pin = meta.get('engine_assets_sha256', {}).get(name)
        if pin is not None and pin != sha:
            raise ValueError(f'Upstream asset differs from pinned SHA256: {name}')
        url = f'https://github.com/{repository}/releases/download/{tag}/{name}'
        if asset['browser_download_url'] != url:
            raise ValueError(f'Unexpected upstream asset URL: {name}')
        # The hotfix tag can differ from the native engine's three-part version.
        archive = cache/f'{tag[1:]}-{name}'
        download(url, archive, sha)
        destination.mkdir(parents=True, exist_ok=True)
        target = destination/directory
        if target.is_symlink() or getattr(target, 'is_junction', lambda: False)():
            raise ValueError('Linked native engine destination')
        if target.exists() and not target.is_dir():
            raise ValueError('Native engine destination is not a directory')
        # A verified complete tree replaces the old tree, so removed vendor DLLs
        # cannot survive a new release and extraction failure leaves it untouched.
        temporary = Path(tempfile.mkdtemp(prefix='.engine-stage-', dir=destination))
        keep_backup = False
        try:
            staged = temporary/directory
            extract(archive, staged)
            path = staged/'BUILD.json'
            build = json.loads(path.read_text(encoding='utf-8'))
            if build.get('version') != identity['engine_version']:
                raise ValueError(f'Mismatched upstream engine: {directory}')
            build['upstream_asset'] = {'repository': repository, 'release_tag': tag, 'name': name,
                                       'sha256': sha, 'upstream_commit': identity['upstream_commit']}
            path.write_text(json.dumps(build, indent=2)+'\n', encoding='utf-8')
            patch_engine(staged)
            builds[directory] = validate_engine_build(json.loads(path.read_text(encoding='utf-8')), directory, meta, version)
            backup = temporary/'previous'
            if target.exists():
                target.rename(backup)
            try:
                staged.rename(target)
            except BaseException as error:
                if backup.exists():
                    try:
                        backup.rename(target)
                    except OSError as rollback:
                        # Keep the previous complete engine if Windows or an
                        # antivirus prevents restoring the directory name.
                        keep_backup = True
                        raise RuntimeError(f'Engine rollback incomplete; previous engine retained at {backup}: {rollback}') from error
                raise
        finally:
            if not keep_backup:
                shutil.rmtree(temporary)
    return builds


def bootstrap_gguf(cache, destination=ROOT):
    """Install only the fixed gguf-py source and license used by CPU checks."""
    cache, destination = Path(cache), Path(destination)
    cache.mkdir(parents=True, exist_ok=True)
    llama = destination/'third_party/llama.cpp'
    expected = {'repository': 'ggml-org/llama.cpp', 'commit': setup.LLAMA_CPP_COMMIT}
    stamp = llama/'UPSTREAM.json'
    if (llama.is_symlink() or getattr(llama, 'is_junction', lambda: False)()
            or (llama/'gguf-py').is_symlink() or getattr(llama/'gguf-py', 'is_junction', lambda: False)()):
        raise ValueError('Linked gguf-py destination')
    if (llama/'gguf-py/gguf').is_dir() and (llama/'LICENSE').is_file() and stamp.is_file():
        try:
            if json.loads(stamp.read_text(encoding='utf-8')) == expected:
                return llama
        except ValueError:
            pass
    archive = cache/f'llama-{setup.LLAMA_CPP_COMMIT}.zip'
    download(setup.LLAMA_CPP_ZIP, archive)
    llama.parent.mkdir(parents=True, exist_ok=True)
    # Extract into a fresh tree so an earlier partial or changed revision cannot
    # retain stale Python modules; never copy the C++ build or model artifacts.
    staging = Path(tempfile.mkdtemp(prefix='.gguf-stage-', dir=llama.parent))
    keep_backup = False
    try:
        extract(archive, staging/'source')
        source = staging/'source'/f'llama.cpp-{setup.LLAMA_CPP_COMMIT}'
        if not (source/'gguf-py/gguf').is_dir() or not (source/'LICENSE').is_file():
            raise ValueError('Pinned llama.cpp archive is missing gguf-py or its license')
        incoming = staging/'incoming'
        incoming.mkdir()
        shutil.copytree(source/'gguf-py', incoming/'gguf-py',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copy2(source/'LICENSE', incoming/'LICENSE')
        (incoming/'UPSTREAM.json').write_text(json.dumps(expected, indent=2)+'\n', encoding='utf-8')
        # Keep existing native sources if this is a developer checkout rather
        # than the minimal portable build layout.
        llama.mkdir(exist_ok=True)
        old = llama/'gguf-py'
        backup = staging/'previous-gguf-py'
        if old.exists():
            old.rename(backup)
        try:
            (incoming/'gguf-py').rename(old)
        except BaseException as error:
            if backup.exists():
                try:
                    backup.rename(old)
                except OSError as rollback:
                    keep_backup = True
                    raise RuntimeError(f'gguf-py rollback incomplete; previous source retained at {backup}: {rollback}') from error
            raise
        shutil.copy2(incoming/'LICENSE', llama/'LICENSE')
        shutil.copy2(incoming/'UPSTREAM.json', stamp)
    finally:
        if not keep_backup:
            shutil.rmtree(staging)
    return llama


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gguf-only', action='store_true',
                        help='Prepare pinned gguf-py source for CPU checks only; no Python, engine or model downloads')
    args = parser.parse_args()
    if args.gguf_only:
        bootstrap_gguf(ROOT/'.portable-build')
        print(f'gguf-py ready at fixed llama.cpp commit {setup.LLAMA_CPP_COMMIT}', flush=True)
        return
    if os.name != 'nt':
        raise SystemExit('Windows x64 runner required')
    meta = metadata()
    version = source_version()
    build_identity(meta, version)
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
    bootstrap_engines(meta, version, cache, ROOT)
    bootstrap_gguf(cache)
    print(f'Runtime ready for Strata {version}', flush=True)


if __name__ == '__main__':
    main()
