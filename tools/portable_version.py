"""Shared version and release metadata for the T8 distribution."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


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


def archive_name(version):
    version_key(version)
    return f'Strata-T8-{version}-Windows-x64-Portable-NoModels.zip'
