"""Validate the separate source, release and native-engine build identities."""
from __future__ import annotations
import re
from portable_version import upstream_version_key, validate_source_release

ENGINE_ASSETS = {
    'engine': 'strata-windows-x64.zip',
    'engine-hip': 'strata-windows-x64-hip.zip',
}


def build_identity(meta, source):
    engine = meta.get('engine_version', meta['upstream_version'])
    upstream_version_key(source)
    if source != meta['upstream_version'] or engine != source:
        raise ValueError('Source, distribution and engine versions differ')
    tag = meta.get('upstream_release_tag', 'v' + source)
    validate_source_release(source, tag)
    commit = meta.get('upstream_commit')
    if commit is not None and (not isinstance(commit, str) or not re.fullmatch('[0-9a-f]{40}', commit)):
        raise ValueError('Invalid frozen upstream commit')
    pins = meta.get('engine_assets_sha256', {})
    if not isinstance(pins, dict) or any(name not in ENGINE_ASSETS.values()
            or not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value)
            for name, value in pins.items()):
        raise ValueError('Invalid pinned engine asset checksums')
    if commit is not None and set(pins) != set(ENGINE_ASSETS.values()):
        raise ValueError('Frozen upstream requires both pinned engine assets')
    return {'upstream_version': source, 'engine_version': engine,
            'upstream_release_tag': tag, 'upstream_commit': commit}


def validate_engine_build(build, directory, meta, source):
    identity = build_identity(meta, source)
    if not isinstance(build, dict) or build.get('version') != identity['engine_version']:
        raise ValueError(f'{directory} does not match the source engine version')
    name = ENGINE_ASSETS[directory]
    provenance = build.get('upstream_asset')
    if identity['upstream_commit'] is not None or provenance is not None:
        expected = {'repository': meta['upstream_repository'], 'release_tag': identity['upstream_release_tag'],
                    'name': name, 'upstream_commit': identity['upstream_commit']}
        if not isinstance(provenance, dict) or any(provenance.get(k) != v for k, v in expected.items()):
            raise ValueError(f'{directory} upstream asset provenance differs from package metadata')
        digest = provenance.get('sha256')
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError(f'{directory} has no verified upstream asset digest')
        pin = meta.get('engine_assets_sha256', {}).get(name)
        if pin is not None and pin != digest:
            raise ValueError(f'{directory} upstream asset checksum differs from pinned metadata')
    return build
