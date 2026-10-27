"""Shared version and release metadata for the T8 distribution."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def upstream_version_key(value):
    """Compare exact native versions, including four-part engine hotfixes."""
    return upstream_release_key(value)


def upstream_release_key(value):
    """Normalize stable three/four-part versions for ordering."""
    number = r'(?:0|[1-9][0-9]*)'
    if not isinstance(value, str) or not re.fullmatch(rf'{number}\.{number}\.{number}(?:\.{number})?', value):
        raise ValueError(f'Unsupported stable upstream release: {value}')
    parts = tuple(map(int, value.split('.')))
    return parts if len(parts) == 4 else (*parts, 0)


def validate_source_release(version, tag, *, product_version=None):
    source = upstream_version_key(version)
    if not isinstance(tag, str) or not tag.startswith('v'):
        raise ValueError('Expected a stable upstream version tag')
    release = upstream_release_key(tag[1:])
    if product_version is not None:
        if upstream_release_key(product_version) != release:
            raise ValueError('Upstream release tag differs from product version')
        return
    if release[:3] != source[:3] or (len(version.split('.')) == 4 and release != source):
        raise ValueError('Upstream release tag differs from CMake source version')


def metadata(root=ROOT):
    return json.loads((root/'meta.json').read_text(encoding='utf-8'))


def source_version(root=ROOT):
    text = (root/'CMakeLists.txt').read_text(encoding='utf-8')
    return re.search(r'project\(strata VERSION ([\d.]+)', text).group(1)


def product_version(root=ROOT):
    """The app/Terminal release can differ from the CMake engine build number."""
    path = root/'strata_cli/__init__.py'
    if not path.exists():
        return source_version(root)
    match = re.search(r'^PRODUCT_VERSION\s*=\s*[\"\']([0-9.]+)[\"\']', path.read_text(encoding='utf-8'), re.M)
    if not match:
        raise ValueError('Missing upstream product version')
    upstream_release_key(match[1])
    return match[1]


def version_key(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)-t8\.(\d+)', value)
    if not match:
        raise ValueError(f'Unsupported release version: {value}')
    return tuple(map(int, match.groups()))


def archive_name(version, edition='Portable-NoModels'):
    version_key(version)
    if edition not in ('Portable-NoModels', 'VisionReady-NoMainModel'):
        raise ValueError(f'Unsupported package edition: {edition}')
    return f'Strata-T8-{version}-Windows-x64-{edition}.zip'
