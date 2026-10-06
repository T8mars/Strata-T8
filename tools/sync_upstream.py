"""Merge upstream release/main while retaining T8 history and stopping on conflicts."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from portable_version import ROOT, source_version, upstream_version_key, upstream_release_key, validate_source_release
from check_private_paths import is_private_path, private_paths


def git(root, *args, check=True):
    return subprocess.run(['git', *args], cwd=root, text=True, encoding='utf-8', capture_output=True, check=check)


def select_ref(source, tag=None):
    if source == 'main':
        return 'refs/heads/main'
    if source != 'release' or not isinstance(tag, str) or not tag.startswith('v'):
        raise ValueError('Expected a stable upstream version tag')
    upstream_release_key(tag[1:])
    return 'refs/tags/' + tag


def operation_path(root, name):
    path = Path(git(root, 'rev-parse', '--git-path', name).stdout.strip())
    return path if path.is_absolute() else root/path


def check_idle_checkout(root):
    # A resolved/empty merge or cherry-pick can have a completely clean porcelain status.
    for name in ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-apply',
                 'rebase-merge', 'sequencer', 'BISECT_LOG'):
        if operation_path(root, name).exists():
            raise RuntimeError(f'Git operation already in progress: {name}; finish it before upstream synchronization')


def ignored_collisions(root, commit):
    """Git's merge strategy may overwrite ignored paths even with the merge option."""
    key = str.casefold if os.name == 'nt' else str
    source_names = git(root, 'ls-tree', '-r', '--name-only', '-z', commit).stdout.split('\0')
    # The documentation mirror is generated after merging, rather than by Git itself.
    source_names += ['README-UPSTREAM.md', 'meta.json']
    targets = [key(name) for name in source_names if name]
    ignored = git(root, 'ls-files', '--others', '--ignored', '--exclude-standard', '-z').stdout.split('\0')
    collisions = {name for name in source_names if name and is_private_path(name)}
    for name in filter(None, ignored):
        local = key(name)
        if any(local == target or local.startswith(target+'/') or target.startswith(local+'/') for target in targets):
            collisions.add(name)
    # Include empty ignored directories that would be replaced by a tracked file.
    directories = git(root, 'ls-files', '--others', '--ignored', '--exclude-standard', '--directory', '-z').stdout.split('\0')
    for name in directories:
        if name.endswith('/'):
            local = key(name.rstrip('/'))
            if any(local == target or local.startswith(target+'/') for target in targets):
                collisions.add(name)
    return sorted(collisions)


def diagnostic_branch(root, commit):
    """Keep a user's existing branch and bind the reported branch to this commit."""
    short = 'codex/upstream-' + commit[:12]
    full = 'codex/upstream-' + commit
    index = 0
    while True:
        branch = short if index == 0 else full if index == 1 else full + '-' + str(index - 1)
        existing = git(root, 'show-ref', '--verify', '--hash', 'refs/heads/'+branch, check=False)
        if existing.returncode:
            git(root, 'branch', branch, commit)
            return branch
        if existing.stdout.strip() == commit:
            return branch
        index += 1


def abort_sync_merge(root, commit):
    """Abort this invocation's single-parent merge, never a replacement operation."""
    if not operation_path(root, 'MERGE_HEAD').exists():
        return
    current = git(root, 'rev-parse', '--verify', 'MERGE_HEAD').stdout.strip()
    if current != commit:
        raise RuntimeError('Concurrent Git operation preserved; merge no longer belongs to this synchronization')
    git(root, 'merge', '--abort')


def sync(root, url, ref, *, release_assets=None):
    root = Path(root)
    report = root/'.portable-build/upstream-conflict.json'
    # The workflow reads this exact descriptor on failure. Never reuse another run's branch.
    if report.is_symlink() or getattr(report, 'is_junction', lambda: False)() or report.parent.is_symlink() or getattr(report.parent, 'is_junction', lambda: False)():
        raise RuntimeError('Linked upstream diagnostic path')
    report.unlink(missing_ok=True)
    check_idle_checkout(root)
    if git(root, 'status', '--porcelain').stdout.strip():
        raise RuntimeError('Working tree must be clean before upstream synchronization')
    if private_paths(root):
        raise RuntimeError('Local planning files must be removed from the Git index before synchronization')
    # FETCH_HEAD is shared with other fetches and linked worktrees. A unique ref
    # keeps this invocation bound to its own fetch without modifying that state.
    fetched = 'refs/strata-t8/upstream-' + uuid.uuid4().hex
    try:
        git(root, 'fetch', '--no-write-fetch-head', '--no-tags', url, ref+':'+fetched)
        commit = git(root, 'rev-parse', fetched+'^{commit}').stdout.strip()
        return sync_fetched(root, commit, ref, release_assets=release_assets)
    finally:
        git(root, 'update-ref', '-d', fetched, check=False)


def history_merge_target(root, commit, meta):
    """Connect rewritten history only at an identical, previously imported tree.

    The bridge is an unreferenced object until the one atomic merge succeeds.
    Refusing an unknown anchor preserves normal conflict/rollback behavior.
    """
    common = git(root, 'merge-base', 'HEAD', commit, check=False)
    if common.returncode == 0:
        return commit
    if common.returncode != 1:
        raise RuntimeError('Cannot determine upstream history relationship')
    previous = meta.get('upstream_commit')
    if (not isinstance(previous, str) or not re.fullmatch(r'[0-9a-f]{40}', previous)
            or git(root, 'merge-base', '--is-ancestor', previous, 'HEAD', check=False).returncode != 0):
        raise RuntimeError('Unrelated upstream history has no verified imported commit; manual review required')
    tree = git(root, 'rev-parse', previous+'^{tree}').stdout.strip()
    ancestors = git(root, 'log', '--format=%H %T', commit).stdout.splitlines()
    if not any(line.split(' ', 1)[1] == tree for line in ancestors):
        raise RuntimeError('Rewritten upstream history has no identical imported tree; manual review required')
    incoming = git(root, 'rev-parse', commit+'^{tree}').stdout.strip()
    return git(root, 'commit-tree', incoming, '-p', previous, '-p', commit,
               '-m', f'Bridge verified upstream history rewrite ({previous[:12]} to {commit[:12]})').stdout.strip()


def release_asset_pins(assets):
    required = {'strata-windows-x64.zip', 'strata-windows-x64-hip.zip'}
    pins = {}
    for item in assets:
        if not isinstance(item, dict) or item.get('name') not in required:
            continue
        name, digest = item['name'], item.get('digest')
        if name in pins or not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
            raise ValueError(f'Invalid or duplicate upstream engine digest: {name}')
        pins[name] = digest[7:]
    if pins.keys() != required:
        raise ValueError('Upstream release is missing CUDA/HIP SHA256 engine assets')
    return pins


def sync_fetched(root, commit, ref, *, release_assets=None):
    meta_path = root/'meta.json'
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    release_tag = ref.removeprefix('refs/tags/') if ref.startswith('refs/tags/') else None
    old_release = meta.get('upstream_stable_release_tag') or meta.get('upstream_release_tag')
    pins = release_asset_pins(release_assets) if release_assets is not None else None
    if release_tag:
        release_key = upstream_release_key(release_tag.removeprefix('v'))
        if old_release and release_key < upstream_release_key(old_release.removeprefix('v')):
            raise ValueError('Upstream release would downgrade the distribution')
        previous = meta.get('upstream_commit')
        if (meta.get('upstream_ref') == 'refs/heads/main' and isinstance(previous, str)
                and previous != commit
                and git(root, 'merge-base', '--is-ancestor', commit, previous, check=False).returncode == 0):
            raise ValueError('Release cannot remove newer upstream main preview commits; use a stable checkout')
        if (meta.get('upstream_release_tag') == release_tag and pins is not None
                and meta.get('engine_assets_sha256') is not None and meta['engine_assets_sha256'] != pins):
            raise ValueError('Published upstream engine assets differ from recorded SHA256 pins')
    if (git(root, 'merge-base', '--is-ancestor', commit, 'HEAD', check=False).returncode == 0
            and (release_tag is None or (meta.get('upstream_release_tag') == release_tag
                 and (pins is None or meta.get('engine_assets_sha256') == pins)))):
        return {'changed': False, 'upstream_commit': commit}
    before = git(root, 'rev-parse', 'HEAD').stdout.strip()
    readme_path = root/'README-UPSTREAM.md'
    readme_before = readme_path.read_bytes() if readme_path.exists() else None
    collisions = ignored_collisions(root, commit)
    merge_target = commit
    if collisions:
        merged = subprocess.CompletedProcess([], 1, '', 'Upstream contains private planning paths or would overwrite ignored local paths: '+', '.join(collisions))
    else:
        try:
            merge_target = history_merge_target(root, commit, meta)
            # Scope the keep driver to this merge; never rewrite a user's shared config.
            merged = git(root, '-c', 'merge.t8-keep.driver=true', 'merge', '--no-ff', '--no-commit', '--no-overwrite-ignore', merge_target, check=False)
        except RuntimeError as error:
            merged = subprocess.CompletedProcess([], 1, '', str(error))
    if merged.returncode:
        conflicts = git(root, 'diff', '--name-only', '--diff-filter=U').stdout.splitlines() + collisions
        # Restore the checkout before producing diagnostics. A disk/write failure
        # or a merged file at the diagnostic directory must not leave our merge active.
        if not collisions:
            abort_sync_merge(root, merge_target)
        report = root/'.portable-build/upstream-conflict.json'
        if report.is_symlink() or getattr(report, 'is_junction', lambda: False)() or report.parent.is_symlink() or getattr(report.parent, 'is_junction', lambda: False)():
            raise RuntimeError('Linked upstream diagnostic path')
        report.parent.mkdir(exist_ok=True)
        branch = diagnostic_branch(root, commit)
        report.write_text(json.dumps({'upstream_commit': commit, 'base_commit': before, 'conflicts': conflicts,
                                      'branch': branch, 'error': merged.stdout + merged.stderr}, indent=2), encoding='utf-8')
        raise RuntimeError(f'Upstream merge stopped; original checkout preserved. Diagnostic branch: {branch}; conflicts: {conflicts}')
    try:
        original = git(root, 'show', commit+':README.md').stdout
        (root/'README-UPSTREAM.md').write_text(original, encoding='utf-8')
        version = source_version(root)
        parsed_version = upstream_version_key(version)
        if release_tag:
            validate_source_release(version, release_tag)
        if parsed_version < upstream_version_key(meta['upstream_version']):
            raise ValueError('Upstream source version would downgrade the distribution')
        meta['revision'] = meta['revision'] + 1 if meta['upstream_version'] == version else 1
        meta['upstream_version'] = version
        meta['version'] = f'{version}-t8.{meta["revision"]}'
        meta['upstream_commit'] = commit
        meta['engine_version'] = version
        meta['upstream_ref'] = ref
        if release_tag:
            meta['upstream_release_tag'] = release_tag
            meta['upstream_stable_release_tag'] = release_tag
        else:
            meta.pop('upstream_release_tag', None)
            if old_release:
                meta['upstream_stable_release_tag'] = old_release
        # Pins belong to a specific published asset set, not merely its base version.
        if release_assets is not None:
            meta['engine_assets_sha256'] = pins
        else:
            meta.pop('engine_assets_sha256', None)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        git(root, 'add', 'README-UPSTREAM.md', 'meta.json')
        git(root, 'commit', '-m', f'Sync upstream {ref} ({commit[:12]})')
        return {'changed': True, 'upstream_commit': commit, 'version': meta['version']}
    except Exception:
        abort_sync_merge(root, merge_target)
        # The merge's only generated files are controlled here.
        git(root, 'restore', '--source='+before, '--staged', '--worktree', 'meta.json')
        git(root, 'restore', '--source='+before, '--staged', '--worktree', 'README-UPSTREAM.md', check=False)
        if readme_before is None:
            readme_path.unlink(missing_ok=True)
        else:
            readme_path.write_bytes(readme_before)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=['release', 'main'], default='release')
    args = parser.parse_args()
    meta = json.loads((ROOT/'meta.json').read_text(encoding='utf-8'))
    tag, assets = None, None
    if args.source == 'release':
        release = json.loads(subprocess.check_output(['gh', 'api', f'repos/{meta["upstream_repository"]}/releases/latest'], text=True, encoding='utf-8'))
        if release.get('draft') or release.get('prerelease'):
            raise ValueError('Expected an upstream stable published release')
        tag, assets = release['tag_name'], release['assets']
        release_asset_pins(assets)
    result = sync(ROOT, f'https://github.com/{meta["upstream_repository"]}.git', select_ref(args.source, tag), release_assets=assets)
    if os_output := __import__('os').environ.get('GITHUB_OUTPUT'):
        with open(os_output, 'a', encoding='utf-8') as stream:
            stream.write(f'changed={str(result["changed"]).lower()}\n')
            stream.write(f'commit={git(ROOT, "rev-parse", "HEAD").stdout.strip()}\n')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
