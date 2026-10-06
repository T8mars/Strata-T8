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
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from portable_version import ROOT, metadata, version_key, archive_name
from portable_weights import validate_weights, weight_declaration, allowed_weight, allowed_runtime_data, MODEL_SUFFIXES
from portable_io import atomic_json
urlopen = urllib.request.urlopen


def update_json(text):
    """Do not let ambiguous or nonstandard JSON choose update files or versions."""
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('Duplicate update JSON member')
            value[key] = item
        return value
    def constant(value):
        raise ValueError('Non-finite update JSON number')
    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
        json.dumps(value, allow_nan=False, ensure_ascii=False).encode('utf-8')
        return value
    except RecursionError as error:
        raise ValueError('Update JSON is too deeply nested') from error


def update_metadata(root):
    value = update_json((root/'meta.json').read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Application metadata must be an object')
    return value


def validate_metadata(application, manifest):
    edition = validate_weights(manifest)
    if not isinstance(application, dict) or application.get('version') != manifest['version']:
        raise ValueError('Application metadata version differs from package manifest')
    if application.get('edition', 'Portable-NoModels') != edition:
        raise ValueError('Application metadata edition differs from package manifest')
    expected = weight_declaration(edition)
    if ('models_included' in application and application['models_included'] is not expected['vision']):
        raise ValueError('Application metadata models_included differs from package manifest')
    if 'weights' in application:
        roles = application['weights']
        if not isinstance(roles, dict) or set(roles) != set(expected) or any(roles[k] is not expected[k] for k in expected):
            raise ValueError('Application metadata weight roles differ from package manifest')


def discard_owned_pending_stage(root, plan):
    """Remove only a superseded, unapplied stage made by this updater version."""
    if not isinstance(plan, dict) or not isinstance(plan.get('stage_owner'), str):
        return
    temp_root = Path(tempfile.gettempdir()).resolve()
    try:
        original_stage = Path(plan.get('stage', '')).parent
        if original_stage.is_symlink() or getattr(original_stage, 'is_junction', lambda: False)():
            return
        stage = original_stage.resolve()
        if (stage.parent != temp_root or not stage.name.startswith('Strata-T8-update-')
                or (stage/'backup').exists() or Path(plan['stage']).resolve() != stage/'incoming'
                or Path(plan['backup']).resolve() != stage/'backup'):
            return
        marker = stage/'stage-owner.json'
        if marker.is_symlink() or getattr(marker, 'is_junction', lambda: False)():
            return
        owner = update_json(marker.read_text(encoding='utf-8'))
        if owner != {'root': str(root.resolve()), 'token': plan['stage_owner']}:
            return
        incoming = stage/'incoming'
        manifest_path = incoming/'PACKAGE-MANIFEST.json'
        if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != plan.get('manifest_sha256'):
            return
        manifest = update_json(manifest_path.read_text(encoding='utf-8'))
        validate_manifest(incoming, manifest)
        expected = {'stage-owner.json', 'incoming/PACKAGE-MANIFEST.json'}
        expected.update('incoming/'+e['path'] for e in manifest['files'])
        expected.update(p.as_posix() for name in list(expected) for p in PurePosixPath(name).parents if p.as_posix() != '.')
        if {p.relative_to(stage).as_posix() for p in stage.rglob('*')} != expected:
            return  # Preserve anything added by the user, including empty directories.
        # Recheck every component, and only delete this uniquely allocated temp tree.
        for path in stage.rglob('*'):
            if (path.is_symlink() or getattr(path, 'is_junction', lambda: False)()
                    or not path.resolve().is_relative_to(stage)):
                return
        if stage.resolve().parent == temp_root and not (stage/'backup').exists():
            shutil.rmtree(stage)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'[Update] Previous staging retained: {error}', file=sys.stderr)


def safe_path(root, relative):
    if not isinstance(relative, str):
        raise ValueError('Package path must be a string')
    path = PurePosixPath(relative)
    if not relative or '\\' in relative or ':' in relative or path.is_absolute() or any(p in ('..', '.') for p in relative.split('/')):
        raise ValueError(f'Unsafe package path: {relative}')
    if any(not p or p.endswith((' ', '.')) or PureWindowsPath(p).is_reserved() or re.search(r'[<>"|?*\x00-\x1f\x7f]', p) for p in relative.split('/')):
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
    if not isinstance(manifest, dict) or not isinstance(manifest.get('files'), list) or not isinstance(manifest.get('version'), str):
        raise ValueError('Invalid package manifest: expected version and file list')
    for entry in manifest['files']:
        if (not isinstance(entry, dict) or not isinstance(entry.get('path'), str)
                or type(entry.get('size')) is not int or entry['size'] < 0
                or not isinstance(entry.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', entry['sha256'])):
            raise ValueError('Invalid package file entry: expected path, integer size and SHA256')
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
    for key in seen:
        if any(parent.as_posix() in seen for parent in PurePosixPath(key).parents):
            raise ValueError(f'Conflicting package file paths: {key}')
    if verify:
        actual = {p.relative_to(root).as_posix().casefold() for p in root.rglob('*') if p.is_file()}
        if actual != seen | {'package-manifest.json'}:
            raise ValueError('Release has unlisted or missing files')
        validate_metadata(update_metadata(root), manifest)


def latest_release(repo=None, timeout=10):
    repo = repo or metadata()['repository']
    req = urllib.request.Request(f'https://api.github.com/repos/{repo}/releases/latest', headers={'User-Agent': 'Strata-T8', 'Accept': 'application/vnd.github+json'})
    with urlopen(req, timeout=timeout) as response:
        payload = response.read(4*1024**2+1)
        if len(payload) > 4*1024**2:
            raise ValueError('Oversized release API metadata')
        release = update_json(payload)
    if not isinstance(release, dict):
        raise ValueError('Release API metadata must be an object')
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
        infos = z.infolist()
        if any(not i.filename or not PurePosixPath(i.filename).parts for i in infos):
            raise ValueError('Invalid release ZIP member')
        prefixes = {PurePosixPath(i.filename).parts[0] for i in infos}
        if len(prefixes) != 1:
            raise ValueError('Release must contain one application directory')
        prefix = prefixes.pop()
        safe_path(destination, prefix)
        seen = {}
        total = 0
        members = []
        for info in infos:
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Symlink in release ZIP')
            if info.filename != prefix and not info.filename.startswith(prefix+'/'):
                raise ValueError('Release ZIP member has a noncanonical application root')
            relative = info.filename[len(prefix)+1:]
            if info.is_dir():
                relative = relative[:-1]
            if not relative:
                if not info.is_dir() or info.filename != prefix+'/':
                    raise ValueError('Release application root must be a directory')
                continue
            target = safe_path(destination, relative)
            if relative.casefold() in seen:
                raise ValueError('Duplicate ZIP member')
            seen[relative.casefold()] = info.is_dir()
            total += info.file_size
            if total > 8*1024**3:
                raise ValueError('Release expands beyond 8 GiB')
            members.append((info, target, relative))
        if seen.get('package-manifest.json') is not False:
            raise ValueError('Release ZIP lacks its package manifest')
        # Validate all source and destination topology before creating the first file.
        for _, target, relative in members:
            if any(seen.get(parent.as_posix().casefold()) is False for parent in PurePosixPath(relative).parents):
                raise ValueError(f'Conflicting release ZIP paths: {relative}')
            if target.exists() and target.is_dir() != seen[relative.casefold()]:
                raise ValueError(f'Conflicting release destination: {relative}')
            for parent in target.parents:
                if parent == destination.parent:
                    break
                if parent.exists() and not parent.is_dir():
                    raise ValueError(f'Release destination parent is a file: {relative}')
        for info, target, _ in members:
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)
    manifest = update_json((destination/'PACKAGE-MANIFEST.json').read_text(encoding='utf-8'))
    validate_manifest(destination, manifest)
    return manifest


def check_installation_topology(root, new_entries, old_entries):
    old_names = {e['path'].casefold() for e in old_entries}
    old_directories = {p.as_posix().casefold() for e in old_entries for p in PurePosixPath(e['path']).parents if p.as_posix() != '.'}
    for entry in new_entries:
        relative = entry['path']
        target = safe_path(root, relative)
        if target.is_file() and relative.casefold() not in old_names:
            raise ValueError(f'New release conflicts with user file: {relative}')
        if target.is_dir():
            if relative.casefold() not in old_directories:
                raise ValueError(f'New release conflicts with user directory: {relative}')
            for descendant in target.rglob('*'):
                name = descendant.relative_to(root).as_posix()
                safe_path(root, name)
                allowed = old_directories if descendant.is_dir() else old_names
                if name.casefold() not in allowed:
                    raise ValueError(f'New release conflicts with user file or directory: {name}')
        for parent in PurePosixPath(relative).parents:
            if parent.as_posix() == '.':
                continue
            if safe_path(root, parent.as_posix()).is_file() and parent.as_posix().casefold() not in old_names:
                raise ValueError(f'New release conflicts with user file parent: {parent}')


def publish_control_pair(root, plan_dir, plan):
    """Keep a previous verified plan paired with its executor on publication failure."""
    executor = plan_dir/'apply.ps1'
    previous = executor.read_bytes() if executor.exists() else None
    descriptor, temporary = tempfile.mkstemp(prefix='.apply-', suffix='.ps1', dir=plan_dir)
    os.close(descriptor)
    temporary = Path(temporary)
    replaced = False
    try:
        shutil.copy2(root/'tools/apply_portable_update.ps1', temporary)
        os.replace(temporary, executor)
        replaced = True
        atomic_json(plan_dir/'plan.json', plan)
    except Exception:
        if replaced:
            if previous is None:
                executor.unlink(missing_ok=True)
            else:
                temporary.write_bytes(previous)
                os.replace(temporary, executor)
        raise
    finally:
        temporary.unlink(missing_ok=True)


def prepare(root=ROOT, release=None, edition=None):
    root = Path(root)
    if not root.is_dir():
        raise ValueError('Application root must be a directory')
    current = update_metadata(root)
    plan_dir = root/'.portable-update'
    # Check every control file before mkdir, unlink or copying can follow a junction.
    for path in (plan_dir, plan_dir/'plan.json', plan_dir/'apply.ps1'):
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            raise ValueError('Linked portable update control path')
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Portable update control path leaves the application')
    plan_dir.mkdir(exist_ok=True)
    previous = None
    if (plan_dir/'plan.json').is_file():
        try:
            previous = update_json((plan_dir/'plan.json').read_text(encoding='utf-8'))
        except ValueError:
            pass  # A damaged old descriptor never grants permission to delete a path.
    if (root/'.git').exists():
        raise RuntimeError('Git checkout: use git pull / upstream sync. Release updates apply to extracted packages.')
    if running := running_processes(root):
        raise RuntimeError(f'Exit Strata before updating. Running processes: {running}')
    release = release or latest_release(current['repository'])
    current_edition = current.get('edition', 'Portable-NoModels')
    edition = edition or current_edition
    release_version, current_version = version_key(release['tag_name']), version_key(current['version'])
    if release_version < current_version or (release_version == current_version and edition == current_edition):
        # The wrapper applies plan.json after exit 0; a no-op must not apply an older pending edition.
        (plan_dir/'plan.json').unlink(missing_ok=True)
        discard_owned_pending_stage(root, previous)
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
        token = uuid.uuid4().hex
        (stage/'stage-owner.json').write_text(json.dumps({'root': str(root.resolve()), 'token': token}), encoding='utf-8')
        manifest = extract_release(archive, incoming)
        if manifest.get('edition', 'Portable-NoModels') != edition:
            raise ValueError('Release edition differs from the requested edition')
        if manifest['version'] != release['tag_name'].removeprefix('v') or update_metadata(incoming)['version'] != manifest['version']:
            raise ValueError('Release, manifest and application versions differ')
        old_manifest_bytes = (root/'PACKAGE-MANIFEST.json').read_bytes()
        old = update_json(old_manifest_bytes)
        validate_manifest(root, old, verify=False)
        installed = update_metadata(root)
        validate_metadata(installed, old)
        installed_version = version_key(installed['version'])
        if (version_key(manifest['version']) < installed_version or
                (version_key(manifest['version']) == installed_version and
                 edition == installed.get('edition', 'Portable-NoModels'))):
            raise ValueError('Installed version or edition changed during update preparation')
        # Never overwrite files a user created that were not managed by their old package.
        check_installation_topology(root, manifest['files'], old['files'])
        plan = {'root': str(root.resolve()), 'stage': str(incoming), 'backup': str(stage/'backup'),
                'new': manifest['files'], 'old': old['files'], 'version': manifest['version'],
                'stage_owner': token,
                'installed_manifest_sha256': hashlib.sha256(old_manifest_bytes).hexdigest(),
                'manifest_sha256': hashlib.sha256((incoming/'PACKAGE-MANIFEST.json').read_bytes()).hexdigest()}
        archive.unlink()
        publish_control_pair(root, plan_dir, plan)
        discard_owned_pending_stage(root, previous)
    except Exception:
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
