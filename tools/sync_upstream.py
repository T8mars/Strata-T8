"""Merge upstream release/main while retaining T8 history and stopping on conflicts."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from portable_version import ROOT, source_version


def git(root, *args, check=True):
    return subprocess.run(['git', *args], cwd=root, text=True, encoding='utf-8', capture_output=True, check=check)


def select_ref(source, tag=None):
    if source == 'main':
        return 'refs/heads/main'
    if source != 'release' or not tag or not tag.startswith('v') or any(c not in 'v0123456789.' for c in tag):
        raise ValueError('Expected a stable upstream version tag')
    return 'refs/tags/' + tag


def sync(root, url, ref):
    if git(root, 'status', '--porcelain').stdout.strip():
        raise RuntimeError('Working tree must be clean before upstream synchronization')
    git(root, 'config', 'merge.t8-keep.driver', 'true')
    git(root, 'fetch', '--no-tags', url, ref)
    commit = git(root, 'rev-parse', 'FETCH_HEAD').stdout.strip()
    if git(root, 'merge-base', '--is-ancestor', commit, 'HEAD', check=False).returncode == 0:
        return {'changed': False, 'upstream_commit': commit}
    before = git(root, 'rev-parse', 'HEAD').stdout.strip()
    readme_path = root/'README-UPSTREAM.md'
    readme_before = readme_path.read_bytes() if readme_path.exists() else None
    merged = git(root, 'merge', '--no-ff', '--no-commit', commit, check=False)
    if merged.returncode:
        conflicts = git(root, 'diff', '--name-only', '--diff-filter=U').stdout.splitlines()
        report = root/'.portable-build/upstream-conflict.json'
        report.parent.mkdir(exist_ok=True)
        branch = 'codex/upstream-' + commit[:12]
        report.write_text(json.dumps({'upstream_commit': commit, 'base_commit': before, 'conflicts': conflicts,
                                      'branch': branch, 'error': merged.stdout + merged.stderr}, indent=2), encoding='utf-8')
        git(root, 'merge', '--abort')
        if git(root, 'show-ref', '--verify', 'refs/heads/'+branch, check=False).returncode:
            git(root, 'branch', branch, commit)
        raise RuntimeError(f'Upstream merge stopped; original checkout preserved. Diagnostic branch: {branch}; conflicts: {conflicts}')
    try:
        original = git(root, 'show', commit+':README.md').stdout
        (root/'README-UPSTREAM.md').write_text(original, encoding='utf-8')
        meta_path = root/'meta.json'
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        version = source_version(root)
        meta['revision'] = meta['revision'] + 1 if meta['upstream_version'] == version else 1
        meta['upstream_version'] = version
        meta['version'] = f'{version}-t8.{meta["revision"]}'
        meta['upstream_commit'] = commit
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        git(root, 'add', 'README-UPSTREAM.md', 'meta.json')
        git(root, 'commit', '-m', f'Sync upstream {ref} ({commit[:12]})')
        return {'changed': True, 'upstream_commit': commit, 'version': meta['version']}
    except Exception:
        git(root, 'merge', '--abort', check=False)
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
    tag = None
    if args.source == 'release':
        tag = subprocess.check_output(['gh', 'api', f'repos/{meta["upstream_repository"]}/releases/latest', '--jq', '.tag_name'], text=True).strip()
    result = sync(ROOT, f'https://github.com/{meta["upstream_repository"]}.git', select_ref(args.source, tag))
    if os_output := __import__('os').environ.get('GITHUB_OUTPUT'):
        with open(os_output, 'a', encoding='utf-8') as stream:
            stream.write(f'changed={str(result["changed"]).lower()}\n')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
