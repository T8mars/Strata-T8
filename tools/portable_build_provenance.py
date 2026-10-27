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
    split = meta.get('component_schema') == 2
    if (meta.get('component_schema') not in (None, 2) or engine != source
            or (not split and source != meta['upstream_version'])
            or (split and meta.get('source_version') != source)):
        raise ValueError('Source, distribution and engine versions differ')
    tag = meta.get('upstream_release_tag', 'v' + source)
    validate_source_release(source, tag, product_version=meta['upstream_version'] if split else None)
    commit = meta.get('upstream_commit')
    if commit is not None and (not isinstance(commit, str) or not re.fullmatch('[0-9a-f]{40}', commit)):
        raise ValueError('Invalid frozen upstream commit')
    pins = meta.get('engine_assets_sha256', {})
    if split:
        from portable_upstream_components import validate_components
        validate_components(meta.get('engine_components'), pins)
        return {'upstream_version': meta['upstream_version'], 'engine_version': engine,
                'source_version': source, 'component_schema': 2,
                'upstream_release_tag': tag, 'upstream_commit': commit,
                'engine_components': meta['engine_components']}
    if not isinstance(pins, dict) or any(name not in ENGINE_ASSETS.values()
            or not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value)
            for name, value in pins.items()):
        raise ValueError('Invalid pinned engine asset checksums')
    if commit is not None and set(pins) != set(ENGINE_ASSETS.values()):
        raise ValueError('Frozen upstream requires both pinned engine assets')
    return {'upstream_version': source, 'engine_version': engine,
            'upstream_release_tag': tag, 'upstream_commit': commit}


def engine_assets(meta):
    if meta.get('component_schema') == 2:
        from portable_upstream_components import validate_components
        components = validate_components(meta.get('engine_components'), meta.get('engine_assets_sha256'))
        return {directory: item['name'] for directory, item in components.items()}
    return dict(ENGINE_ASSETS)


def validate_engine_build(build, directory, meta, source, *, allow_vendor=True, root=None,
                          engine_directory=None, verify_artifacts=True, require_native_source=False):
    identity = build_identity(meta, source)
    native = build.get('t8_native_build') if isinstance(build, dict) else None
    component = meta.get('engine_components', {}).get(directory)
    expected_version = component['version'] if component and native is None else identity['engine_version']
    if not isinstance(build, dict) or build.get('version') != expected_version:
        raise ValueError(f'{directory} does not match the source engine version')
    name = engine_assets(meta)[directory]
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
    from pathlib import Path
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    native = build.get('t8_native_build')
    if native is not None:
        if directory != 'engine':
            raise ValueError('T8 native video build is available for CUDA only')
        from build_native_portable import validate_native_build
        physical = Path(engine_directory) if engine_directory is not None else root/directory
        validate_native_build(native, root, physical if verify_artifacts else None,
                              require_source=require_native_source)
    if not allow_vendor:
        import json
        features = json.loads((root/'features.json').read_text(encoding='utf-8'))
        if directory == 'engine' and features.get('native_video_cuda') and native is None:
            raise ValueError('Native-video release requires a current T8 self-built CUDA engine')
        if directory == 'engine-hip' and build.get('capabilities', {}).get('native_video'):
            raise ValueError('HIP native-video capability has not been built or validated')
    return build
