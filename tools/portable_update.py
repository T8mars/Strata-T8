"""Download and verify a T8 release. The batch entry applies it after Python exits."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from portable_version import ROOT, metadata, version_key, archive_name
from portable_weights import validate_weights, allowed_weight, allowed_runtime_data, MODEL_SUFFIXES
from portable_io import atomic_json
urlopen = urllib.request.urlopen


def safe_path(root, relative):
    path = PurePosixPath(relative)
    if not relative or '\\' in relative or ':' in relative or path.is_absolute() or any(p in ('..', '.') for p in relative.split('/')):
        raise ValueError(f'Unsafe package path: {relative}')
    if any(not p or p.endswith((' ', '.')) or PureWindowsPath(p).is_reserved() or re.search(r'[<>"|?*]', p) for p in relative.split('/')):
        raise ValueError(f'Invalid Windows package path: {relative}')
    if path.parts[0].lower() in ('strata-data', 'models', 'packs', 'mtp', '.git', 'logs', '.portable-update'):
        raise ValueError(f'User data path in package: {relative}')
    if path.parts[0].lower() == 'portable-settings.json' or re.fullmatch(r'strata-.*\.json', relative, re.I):
        raise ValueError(f'User configuration in package: {relative}')
    result = root.joinpath(*path.parts)
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Path leaves the package: {relative}')
    # Reject symlink/reparse traversal, including links that resolve within the root.
    for ancestor in [result, *result.parents]:
        if ancestor == root.parent:
            break
        if ancestor.is_symlink() or (ancestor.exists() and getattr(ancestor, 'is_junction', lambda: False)()):
            raise ValueError(f'Linked package path: {relative}')
    return result


def validate_manifest(root, manifest, verify=True):
    edition = validate_weights(manifest)
    version_key(manifest['version'])
    seen = set()
    for entry in manifest['files']:
        rel = entry['path']
        file = safe_path(root, rel)
        key = rel.casefold()
        if key in seen or key == 'package-manifest.json':
            raise ValueError(f'Duplicate package path: {rel}')
        seen.add(key)
        if file.suffix.lower() in MODEL_SUFFIXES and not allowed_weight(entry, edition) and not allowed_runtime_data(entry):
            raise ValueError(f'Model in release: {rel}')
        if not re.fullmatch('[0-9a-f]{64}', entry['sha256']) or not isinstance(entry['size'], int) or entry['size'] < 0:
            raise ValueError(f'Invalid file digest or size: {rel}')
        if verify:
            if not file.is_file() or file.stat().st_size != entry['size']:
                raise ValueError(f'Missing or truncated release file: {rel}')
            with file.open('rb') as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            if actual != entry['sha256']:
                raise ValueError(f'Release file checksum failed: {rel}')
    if verify:
        actual = {p.relative_to(root).as_posix().casefold() for p in root.rglob('*') if p.is_file()}
        if actual != seen | {'package-manifest.json'}:
            raise ValueError('Release has unlisted or missing files')


def latest_release(repo=None, timeout=10):
    repo = repo or metadata()['repository']
    req = urllib.request.Request(f'https://api.github.com/repos/{repo}/releases/latest', headers={'User-Agent': 'Strata-T8', 'Accept': 'application/vnd.github+json'})
    with urlopen(req, timeout=timeout) as response:
        release = json.load(response)
    if release.get('draft') or release.get('prerelease'):
        raise ValueError('Release is not stable')
    version_key(release['tag_name'])
    return release


def check_update(root=ROOT, timeout=3):
    try:
        release = latest_release(metadata(root)['repository'], timeout)
        if version_key(release['tag_name']) > version_key(metadata(root)['version']):
            print(f'[Update] {release["tag_name"]} available. Exit Strata and run UPDATE-PORTABLE.bat.', flush=True)
            return release
    except Exception:
        # A disconnected PC must still start its local model.
        pass
    return None


def running_processes(root=ROOT):
    import psutil
    running = []
    for process in psutil.process_iter(['pid', 'exe']):
        try:
            executable = Path(process.info['exe']) if process.info['exe'] else None
            # Windows pseudo-processes such as Registry report a name rather than a file path.
            if process.pid != os.getpid() and executable and executable.is_absolute() and executable.resolve().is_relative_to(root.resolve()):
                running.append(process.pid)
        except (OSError, psutil.Error):
            continue
    return running


def fetch(url, destination=None, limit=2*1024**3):
    if not url.startswith('https://github.com/'):
        raise ValueError('Release asset must come from GitHub over HTTPS')
    req = urllib.request.Request(url, headers={'User-Agent': 'Strata-T8'})
    with urlopen(req, timeout=60) as response:
        if destination is None:
            data = response.read(limit+1)
            if len(data) > limit:
                raise ValueError('Oversized checksum file')
            return data
        size = 0
        with destination.open('wb') as output:
            while block := response.read(4*1024**2):
                size += len(block)
                if size > limit:
                    raise ValueError('Oversized release archive')
                output.write(block)
        return size


def extract_release(archive, destination):
    with zipfile.ZipFile(archive) as z:
        prefixes = {PurePosixPath(i.filename).parts[0] for i in z.infolist()}
        if len(prefixes) != 1:
            raise ValueError('Release must contain one application directory')
        prefix = prefixes.pop()
        safe_path(destination, prefix)
        seen = set()
        total = 0
        for info in z.infolist():
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Symlink in release ZIP')
            relative = info.filename[len(prefix)+1:].rstrip('/')
            if not relative:
                continue
            target = safe_path(destination, relative)
            if relative.casefold() in seen:
                raise ValueError('Duplicate ZIP member')
            seen.add(relative.casefold())
            total += info.file_size
            if total > 8*1024**3:
                raise ValueError('Release expands beyond 8 GiB')
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)
    manifest = json.loads((destination/'PACKAGE-MANIFEST.json').read_text(encoding='utf-8'))
    validate_manifest(destination, manifest)
    return manifest


def prepare(root=ROOT, release=None, edition=None):
    plan_dir = root/'.portable-update'
    plan_dir.mkdir(exist_ok=True)
    (plan_dir/'plan.json').unlink(missing_ok=True)
    if (root/'.git').exists():
        raise RuntimeError('Git checkout: use git pull / upstream sync. Release updates apply to extracted packages.')
    if running := running_processes(root):
        raise RuntimeError(f'Exit Strata before updating. Running processes: {running}')
    release = release or latest_release(metadata(root)['repository'])
    current_edition = metadata(root).get('edition', 'Portable-NoModels')
    edition = edition or current_edition
    release_version, current_version = version_key(release['tag_name']), version_key(metadata(root)['version'])
    if release_version < current_version or (release_version == current_version and edition == current_edition):
        print('Already up to date.', flush=True)
        return None
    name = archive_name(release['tag_name'].removeprefix('v'), edition)
    assets = {a['name']: a for a in release['assets']}
    if name not in assets or name+'.sha256' not in assets:
        raise ValueError('Release lacks the portable ZIP or SHA256 asset')
    checksum = fetch(assets[name+'.sha256']['browser_download_url'], limit=4096).decode('ascii').strip()
    digest, filename = checksum.split(maxsplit=1)
    if filename.strip().lstrip('*') != name or not re.fullmatch('[0-9a-fA-F]{64}', digest):
        raise ValueError('Invalid release checksum metadata')
    stage = Path(tempfile.mkdtemp(prefix='Strata-T8-update-'))
    owned_stage = stage.resolve()
    try:
        archive = stage/name
        print(f'Downloading {name} ({assets[name]["size"]/1e9:.2f} GB) ...', flush=True)
        count = fetch(assets[name]['browser_download_url'], archive)
        with archive.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if count != assets[name]['size'] or actual.lower() != digest.lower():
            raise ValueError('Release archive SHA256 or size mismatch')
        incoming = stage/'incoming'
        incoming.mkdir()
        manifest = extract_release(archive, incoming)
        if manifest.get('edition', 'Portable-NoModels') != edition:
            raise ValueError('Release edition differs from the requested edition')
        if manifest['version'] != release['tag_name'].removeprefix('v') or metadata(incoming)['version'] != manifest['version']:
            raise ValueError('Release, manifest and application versions differ')
        old = json.loads((root/'PACKAGE-MANIFEST.json').read_text(encoding='utf-8'))
        validate_manifest(root, old, verify=False)
        # Never overwrite files a user created that were not managed by their old package.
        old_names = {e['path'].casefold() for e in old['files']}
        for entry in manifest['files']:
            if entry['path'].casefold() not in old_names and safe_path(root, entry['path']).exists():
                raise ValueError(f'New release conflicts with user file: {entry["path"]}')
        plan = {'root': str(root.resolve()), 'stage': str(incoming), 'backup': str(stage/'backup'),
                'new': manifest['files'], 'old': old['files'], 'version': manifest['version'],
                'manifest_sha256': hashlib.sha256((incoming/'PACKAGE-MANIFEST.json').read_bytes()).hexdigest()}
        shutil.copy2(root/'tools/apply_portable_update.ps1', plan_dir/'apply.ps1')
        archive.unlink()
        atomic_json(plan_dir/'plan.json', plan)
    except Exception:
        (plan_dir/'plan.json').unlink(missing_ok=True)
        # Delete only the fresh directory created by this invocation, never a redirected path.
        if stage.resolve() == owned_stage and not stage.is_symlink() and not getattr(stage, 'is_junction', lambda: False)():
            try:
                shutil.rmtree(owned_stage)
            except OSError as error:
                print(f'[Update] Could not remove failed staging directory: {error}', file=sys.stderr)
        raise
    print('Verified. Applying after Python exits; models and user configuration are preserved.', flush=True)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--edition', choices=['Portable-NoModels', 'VisionReady-NoMainModel'])
    args = parser.parse_args()
    if args.check:
        release = check_update()
        print(release['html_url'] if release else 'No new version found, or GitHub is unavailable.')
    else:
        prepare(edition=args.edition)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'[Update] {error}', file=sys.stderr)
        raise SystemExit(2)
