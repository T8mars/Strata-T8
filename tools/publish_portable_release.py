"""Audit the two current portable editions and publish their exact tested commit."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from portable_version import ROOT, archive_name, source_version, version_key
from portable_build_provenance import ENGINE_ASSETS, build_identity, validate_engine_build
from portable_update import update_json, validate_manifest, validate_metadata, safe_path
from portable_weights import weight_declaration
from check_private_paths import is_private_path, private_paths

EDITIONS = ('VisionReady-NoMainModel', 'Portable-NoModels')


def command(root, args, *, check=True):
    result = subprocess.run(args, cwd=root, text=True, encoding='utf-8', capture_output=True)
    if check and result.returncode:
        raise RuntimeError(f'{args[0]} command failed: {result.stderr.strip()}')
    return result


def checkout_identity(root):
    root = Path(root).resolve()
    if private_paths(root):
        raise ValueError('Private planning files must not be tracked or published')
    if command(root, ['git', 'status', '--porcelain']).stdout.strip():
        raise ValueError('Release requires a clean tested checkout')
    command(root, ['git', 'ls-files', '--error-unmatch', '--', 'meta.json', 'CMakeLists.txt', 'RELEASE-NOTES.md'])
    head = command(root, ['git', 'rev-parse', 'HEAD']).stdout.strip()
    if not re.fullmatch('[0-9a-f]{40}', head):
        raise ValueError('Release requires a full tested commit SHA')
    meta = update_json((root/'meta.json').read_text(encoding='utf-8'))
    if not isinstance(meta, dict) or not isinstance(meta.get('repository'), str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', meta['repository']):
        raise ValueError('Invalid release repository metadata')
    key = version_key(meta['version'])
    identity = build_identity(meta, source_version(root))
    if key[:3] != tuple(map(int, identity['engine_version'].split('.'))):
        raise ValueError('Package version differs from its source engine version')
    if identity['upstream_commit'] is None or meta.get('upstream_release_tag') != identity['upstream_release_tag']:
        raise ValueError('Publishing requires a frozen stable upstream release and asset pins')
    if command(root, ['git', 'merge-base', '--is-ancestor', identity['upstream_commit'], head], check=False).returncode:
        raise ValueError('Frozen upstream commit is not part of the tested source history')
    notes = root/'RELEASE-NOTES.md'
    if not notes.is_file() or notes.is_symlink() or getattr(notes, 'is_junction', lambda: False)():
        raise ValueError('Release requires its local tracked release notes')
    return meta, head


def _zip_json(package, name, limit=32*1024**2):
    entry = package.getinfo(name)
    if entry.file_size > limit:
        raise ValueError('Oversized JSON in release package')
    value = update_json(package.read(entry))
    if not isinstance(value, dict):
        raise ValueError('Release JSON must be an object')
    return value


def audit_archive(archive, checksum, meta, head, edition):
    """Hash the archive and every listed member, without extracting vendor files."""
    archive, checksum = Path(archive), Path(checksum)
    for path in (archive, checksum):
        if not path.is_file() or path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            raise ValueError('Release asset is missing or linked')
    if archive.stat().st_size >= 2*1024**3 or checksum.stat().st_size > 512:
        raise ValueError('Release asset exceeds its size limit')
    checksum_bytes = checksum.read_bytes()
    stamp = checksum_bytes.decode('ascii')
    match = re.fullmatch(r'([0-9a-f]{64})  ' + re.escape(archive.name) + r'\r?\n?', stamp)
    if not match:
        raise ValueError('Checksum file does not identify exactly this archive')
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != match[1]:
        raise ValueError('Release ZIP checksum mismatch')
    prefix = archive_name(meta['version'], edition).removesuffix('.zip')
    if archive.name != prefix+'.zip' or checksum.name != archive.name+'.sha256':
        raise ValueError('Release archive filename differs from version or edition')
    with zipfile.ZipFile(archive) as package, tempfile.TemporaryDirectory(prefix='Strata-release-audit-') as temporary:
        virtual = Path(temporary)
        files, seen = {}, set()
        expanded = 0
        for entry in package.infolist():
            name = entry.filename[:-1] if entry.is_dir() else entry.filename
            if not name.startswith(prefix+'/'):
                raise ValueError('Release ZIP has a different application root')
            relative = name[len(prefix)+1:]
            safe_path(virtual, relative)
            if is_private_path(relative):
                raise ValueError('Private roadmap is forbidden in release archives')
            if ((entry.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError('Linked file in release archive')
            key = relative.casefold()
            if key in seen:
                raise ValueError('Duplicate release ZIP member')
            seen.add(key)
            expanded += entry.file_size
            if expanded > 8*1024**3:
                raise ValueError('Release ZIP expands beyond 8 GiB')
            if not entry.is_dir():
                files[relative] = entry
        manifest = _zip_json(package, prefix+'/PACKAGE-MANIFEST.json')
        validate_manifest(virtual, manifest, verify=False)
        expected_identity = build_identity(meta, meta['upstream_version'])
        if (manifest.get('version') != meta['version'] or manifest.get('source_commit') != head
                or manifest.get('edition') != edition or any(manifest.get(k) != v for k, v in expected_identity.items())):
            raise ValueError('Package manifest differs from tested source, upstream release or edition')
        listed = {entry['path']: entry for entry in manifest['files']}
        if set(files) != set(listed) | {'PACKAGE-MANIFEST.json'}:
            raise ValueError('Release ZIP contains unlisted or missing files')
        for relative, declaration in listed.items():
            entry = files[relative]
            if entry.file_size != declaration['size']:
                raise ValueError(f'Release member size differs from manifest: {relative}')
            with package.open(entry) as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            if actual != declaration['sha256']:
                raise ValueError(f'Release member checksum differs from manifest: {relative}')
        application = _zip_json(package, prefix+'/meta.json', 1024**2)
        validate_metadata(application, manifest)
        expected_application = dict(meta, edition=edition, weights=weight_declaration(edition),
                                    models_included=edition == 'VisionReady-NoMainModel')
        if application != expected_application:
            raise ValueError('Packaged metadata differs from tested checkout')
        engines = manifest.get('engines')
        if not isinstance(engines, dict) or set(engines) != set(ENGINE_ASSETS):
            raise ValueError('Package requires both native engine provenance records')
        for directory in ENGINE_ASSETS:
            build = _zip_json(package, prefix+f'/{directory}/BUILD.json', 1024**2)
            validate_engine_build(build, directory, meta, meta['upstream_version'])
            if engines[directory] != build:
                raise ValueError('Engine manifest differs from its packaged BUILD.json')
            for binary, patch_key in (('strata.exe', 'engine_utf8'), ('strata-vision.exe', 'vision_utf8')):
                rel = directory+'/'+binary
                if rel not in listed:
                    if binary == 'strata.exe' or (directory == 'engine' and build.get('vision') == 'gpu'):
                        raise ValueError('Missing required native executable')
                    continue
                patch = build.get('portable_patches', {}).get(patch_key, {})
                if (patch.get('manifest') != 'activeCodePage=UTF-8'
                        or not re.fullmatch('[0-9a-f]{64}', patch.get('sha256_before', ''))
                        or patch.get('sha256_after') != listed[rel]['sha256']):
                    raise ValueError('Native executable differs from its recorded UTF-8 patch')
    return {'name': archive.name, 'sha256': digest, 'checksum_sha256': hashlib.sha256(checksum_bytes).hexdigest(),
            'edition': edition, 'source_commit': head}


def audit_assets(root, meta, head, *, with_digests=False):
    dist = Path(root)/'dist'
    if dist.is_symlink() or getattr(dist, 'is_junction', lambda: False)():
        raise ValueError('Linked release asset directory')
    assets, digests = [], {}
    for edition in EDITIONS:
        archive = dist/archive_name(meta['version'], edition)
        checksum = archive.with_name(archive.name+'.sha256')
        report = audit_archive(archive, checksum, meta, head, edition)
        assets.extend((archive, checksum))
        digests[archive.name] = report['sha256']
        digests[checksum.name] = report['checksum_sha256']
    return (assets, digests) if with_digests else assets


def remote_tag_commit(root, tag):
    ref = 'refs/tags/'+tag
    result = command(root, ['git', 'ls-remote', 'origin', ref, ref+'^{}'], check=False)
    if result.returncode:
        raise RuntimeError('Cannot verify the remote release tag: '+result.stderr.strip())
    refs = {}
    for line in result.stdout.splitlines():
        match = re.fullmatch(r'([0-9a-f]{40})\t(.+)', line)
        if not match or match[2] not in (ref, ref+'^{}') or match[2] in refs:
            raise ValueError('Invalid or ambiguous remote release tag response')
        refs[match[2]] = match[1]
    if ref+'^{}' in refs and ref not in refs:
        raise ValueError('Remote peeled tag has no corresponding tag ref')
    return refs.get(ref+'^{}', refs.get(ref))


def _github_json(root, arguments):
    # --include gives an actual HTTP status. Authentication, rate-limit and
    # transport failures must never masquerade as a nonexistent draft.
    result = command(root, ['gh', 'api', '--include', *arguments], check=False)
    response = result.stdout.replace('\r\n', '\n')
    headers, separator, body = response.partition('\n\n')
    status = re.match(r'HTTP/\S+ ([0-9]{3})(?: |$)', headers)
    if not separator or not status:
        raise RuntimeError('Cannot determine release API HTTP status: '+result.stderr.strip())
    code = int(status[1])
    if code == 404:
        return code, None
    if result.returncode or code != 200:
        raise RuntimeError(f'Release API failed with HTTP {code}: {result.stderr.strip()}')
    payload = update_json(body)
    if not isinstance(payload, dict):
        raise ValueError('Release API JSON must be an object')
    return code, payload


def release_api(root, repository, tag):
    code, release = _github_json(root, [f'repos/{repository}/releases/tags/{tag}'])
    if code == 404:
        # REST's tag endpoint only finds published releases. The official gh
        # client discovers pending drafts through GraphQL and then fetches by ID.
        owner, name = repository.split('/')
        query = ('query($owner:String!,$name:String!,$tag:String!){repository(owner:$owner,name:$name)'
                 '{release(tagName:$tag){databaseId isDraft tagName}}}')
        graphql_code, payload = _github_json(root, ['graphql', '-f', 'query='+query,
                                                   '-f', 'owner='+owner, '-f', 'name='+name, '-f', 'tag='+tag])
        if graphql_code != 200 or payload.get('errors'):
            raise RuntimeError('Draft release lookup failed; not assuming a missing release')
        data = payload.get('data')
        repo = data.get('repository') if isinstance(data, dict) else None
        if not isinstance(repo, dict) or 'release' not in repo:
            raise ValueError('Draft lookup did not confirm the release repository')
        node = repo['release']
        if node is None:
            return None
        if (not isinstance(node, dict) or node.get('tagName') != tag
                or type(node.get('databaseId')) is not int or node['databaseId'] <= 0):
            raise ValueError('Invalid draft release identity')
        code, release = _github_json(root, [f'repos/{repository}/releases/{node["databaseId"]}'])
        if code != 200:
            raise RuntimeError('Draft disappeared during lookup; not creating a replacement')
    if (not isinstance(release, dict) or release.get('tag_name') != tag
            or type(release.get('draft')) is not bool or release.get('prerelease')):
        raise ValueError('Release API identity or stability differs from metadata')
    return release


def _publish_verified_assets(root, meta, head, assets, digests=None):
    repository, tag = meta['repository'], 'v'+meta['version']
    expected_paths = [Path(root)/'dist'/name for edition in EDITIONS for name in
                      (archive_name(meta['version'], edition), archive_name(meta['version'], edition)+'.sha256')]
    if list(map(lambda path: Path(path).resolve(), assets)) != [path.resolve() for path in expected_paths]:
        raise ValueError('Publishing accepts exactly the four current edition assets')
    if digests is None:  # Internal state-machine tests supply already-audited fixture files.
        digests = {}
        for path in assets:
            with Path(path).open('rb') as stream:
                digests[Path(path).name] = hashlib.file_digest(stream, 'sha256').hexdigest()
    def check_assets_unchanged():
        for path in assets:
            path = Path(path)
            if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                raise ValueError('Linked release upload asset')
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != digests.get(path.name):
                    raise ValueError('Audited release asset changed before upload')
    def check_uploaded_assets(release, *, complete=False):
        uploaded = release.get('assets')
        if not isinstance(uploaded, list):
            raise ValueError('Release API did not provide its draft assets')
        names = set()
        for asset in uploaded:
            if not isinstance(asset, dict) or not isinstance(asset.get('name'), str) or asset['name'] in names:
                raise ValueError('Invalid or duplicate draft release assets')
            name = asset['name']
            if name not in digests:
                raise ValueError('Draft contains assets outside the four current edition files')
            names.add(name)
            if complete:
                path = next(Path(path) for path in assets if Path(path).name == name)
                if (asset.get('digest') != 'sha256:'+digests[name] or type(asset.get('size')) is not int
                        or asset['size'] != path.stat().st_size or asset.get('state') != 'uploaded'):
                    raise ValueError('Uploaded release asset did not confirm its audited size and SHA256')
        if complete and names != set(digests):
            raise ValueError('Release upload did not confirm all four edition assets')
    def check_checkout():
        current_meta, current_head = checkout_identity(root)
        if current_meta != meta or current_head != head:
            raise ValueError('Tested checkout changed during release publication')
    def check_tag():
        remote = remote_tag_commit(root, tag)
        if remote is not None and remote != head:
            raise ValueError('Existing remote release tag differs from the tested commit')
        return remote
    check_checkout()
    existing_tag = check_tag()
    release = release_api(root, repository, tag)
    if release is not None and not release['draft']:
        if existing_tag is None:
            raise ValueError('Published release is missing its immutable Git tag')
        return {'published': False, 'unchanged': True, 'tag': tag, 'source_commit': head}
    if release is not None:
        check_uploaded_assets(release)
    if release is None:
        command(root, ['gh', 'release', 'create', tag, '--repo', repository, '--draft', '--target', head,
                       '--title', 'Strata-T8 '+meta['version'], '--notes-file', str(Path(root)/'RELEASE-NOTES.md')])
    # A draft made by a failed earlier run can still name an older SHA/branch.
    # Rebind it explicitly before any upload, including a newly created draft.
    command(root, ['gh', 'release', 'edit', tag, '--repo', repository, '--draft=true', '--target', head])
    release = release_api(root, repository, tag)
    confirmed_tag = check_tag()
    if release is None or not release['draft'] or (confirmed_tag is None and release.get('target_commitish') != head):
        raise ValueError('Draft did not confirm the exact tested commit')
    check_uploaded_assets(release)
    check_checkout()
    check_tag()
    check_assets_unchanged()
    command(root, ['gh', 'release', 'upload', tag, '--repo', repository, *map(str, assets), '--clobber'])
    check_tag()
    confirmed = release_api(root, repository, tag)
    confirmed_tag = check_tag()
    if confirmed is None or not confirmed['draft'] or (confirmed_tag is None and confirmed.get('target_commitish') != head):
        raise ValueError('Draft target changed before publishing')
    check_uploaded_assets(confirmed, complete=True)
    check_checkout()
    command(root, ['gh', 'release', 'edit', tag, '--repo', repository, '--draft=false', '--latest'])
    if check_tag() != head:
        raise ValueError('Published release tag did not confirm the tested commit')
    published = release_api(root, repository, tag)
    if published is None or published['draft']:
        raise ValueError('Release API did not confirm publication')
    check_uploaded_assets(published, complete=True)
    return {'published': True, 'unchanged': False, 'tag': tag, 'source_commit': head}


def publish(root=ROOT, *, verify_only=False):
    meta, head = checkout_identity(root)
    assets, digests = audit_assets(root, meta, head, with_digests=True)
    if verify_only:
        return {'verified': True, 'source_commit': head, 'assets': [path.name for path in assets]}
    return _publish_verified_assets(root, meta, head, assets, digests)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true', help='Audit local assets without GitHub writes')
    args = parser.parse_args()
    print(json.dumps(publish(verify_only=args.verify_only)), flush=True)


if __name__ == '__main__':
    main()
