"""Shared version and release metadata for the T8 distribution."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def upstream_version_key(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)', value):
        raise ValueError(f'Unsupported stable upstream version: {value}')
    return tuple(map(int, value.split('.')))


def upstream_release_key(value):
    """Upstream may publish a Python-only hotfix with the same three-part engine."""
    number = r'(?:0|[1-9][0-9]*)'
    if not isinstance(value, str) or not re.fullmatch(rf'{number}\.{number}\.{number}(?:\.{number})?', value):
        raise ValueError(f'Unsupported stable upstream release: {value}')
    parts = tuple(map(int, value.split('.')))
    return parts if len(parts) == 4 else (*parts, 0)


def validate_source_release(version, tag):
    source = upstream_version_key(version)
    if not isinstance(tag, str) or not tag.startswith('v'):
        raise ValueError('Expected a stable upstream version tag')
    if upstream_release_key(tag[1:])[:3] != source:
        raise ValueError('Upstream release tag differs from CMake source version')


def metadata(root=ROOT):
    return json.loads((root/'meta.json').read_text(encoding='utf-8'))


def source_version(root=ROOT):
    text = (root/'CMakeLists.txt').read_text(encoding='utf-8')
    return re.search(r'project\(strata VERSION ([\d.]+)', text).group(1)


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
