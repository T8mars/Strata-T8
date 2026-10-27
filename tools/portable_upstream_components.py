"""Strict Windows component identities for upstream's split release format."""
from __future__ import annotations
import re
import hashlib
import json
from pathlib import Path

from portable_version import upstream_release_key

PROFILES = {
    'engine': ('engine', 'cuda', 'cuda13'),
    'engine-cuda12': ('engine', 'cuda', 'cuda12'),
    'engine-hip': ('engine', 'hip', 'rx9070'),
    'engine-hip-rdna2': ('engine', 'hip', 'rdna2'),
    'engine-hip-rdna3': ('engine', 'hip', 'rdna3'),
    'engine-dsv41': ('dsv41', 'cuda', 'cuda13'),
}
NAME = re.compile(r'strata-(engine|dsv41)-(\d+\.\d+\.\d+)-windows-x86_64-(cuda|hip)-(cuda12|cuda13|rdna2|rdna3|rx9070)\.tar\.gz')


def component_assets(assets):
    """Choose every supported profile exactly once; never guess a backend by substring."""
    found = {}
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get('name'), str):
            continue
        match = NAME.fullmatch(asset.get('name', ''))
        if match is None:
            continue
        component, version, backend, variant = match.groups()
        upstream_release_key(version)
        directory = next((key for key, profile in PROFILES.items() if profile == (component, backend, variant)), None)
        if directory is None:
            continue
        digest = asset.get('digest')
        if directory in found or not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
            raise ValueError('Invalid or duplicate upstream component: '+asset['name'])
        found[directory] = {'name': asset['name'], 'version': version, 'backend': backend,
                            'variant': variant, 'sha256': digest[7:]}
    if set(found) != set(PROFILES):
        raise ValueError('Upstream release is missing Windows components: '+', '.join(sorted(set(PROFILES)-set(found))))
    return found


def validate_components(components, pins):
    if not isinstance(components, dict) or set(components) != set(PROFILES):
        raise ValueError('Invalid upstream Windows component set')
    parsed = component_assets([{'name': item.get('name'), 'digest': 'sha256:'+str(item.get('sha256', ''))}
                               for item in components.values() if isinstance(item, dict)])
    if parsed != components or pins != {item['name']: item['sha256'] for item in parsed.values()}:
        raise ValueError('Upstream component identities or checksums differ')
    return parsed


def extract_verified_component(archive, destination, descriptor):
    """Verify the vendor tree before T8 changes BUILD.json or replaces executables.

    The original signed manifest is retained as provenance, not as a signature
    of our subsequently modified installation. T8's package manifest covers it.
    """
    from strata_control import components, engine_component, signing
    archive, destination = Path(archive), Path(destination)
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != descriptor['sha256']:
            raise ValueError('Upstream component archive checksum mismatch')
    components.safe_extract(archive, destination, fmt='tar.gz')
    manifest = engine_component.read_manifest(destination)
    entrypoint = 'strata-dsv41.exe' if descriptor['name'].startswith('strata-dsv41-') else 'strata.exe'
    expected = {'id': 'strata-engine', 'entrypoint': entrypoint, 'version': descriptor['version'],
                'platform': 'windows-x86_64', 'backend': descriptor['backend']}
    if any(manifest.raw.get(k) != v for k, v in expected.items()):
        raise ValueError('Signed component does not match its pinned identity')
    signing.install_component_verifier(signing.load_trusted_keys(environ={}))
    if engine_component.verify_signature(manifest) != 'verified':
        raise ValueError('Upstream component signature was not verified')
    engine_component.verify(manifest, full=True)
    receipt = {'scope': 'original upstream archive before T8 modifications',
               'archive': descriptor, 'signature_verified': True, 'manifest': manifest.raw}
    (destination/'UPSTREAM-COMPONENT.json').write_text(json.dumps(receipt, indent=2)+'\n', encoding='utf-8')
    (destination/'engine-component.json').unlink()
