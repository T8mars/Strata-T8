"""Validate all files shipped in a portable package against its SHA-256 manifest."""
import hashlib
import json
from pathlib import Path
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root/'PACKAGE-MANIFEST.json').read_text(encoding='utf-8'))
    errors = []
    for entry in manifest['files']:
        file = (root/entry['path']).resolve()
        if not file.is_relative_to(root) or not file.is_file():
            errors.append(entry['path'] + ': missing')
            continue
        if file.stat().st_size != entry['size']:
            errors.append(entry['path'] + ': size mismatch')
            continue
        with file.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != entry['sha256']:
            errors.append(entry['path'] + ': SHA-256 mismatch')
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    print(f'OK: {len(manifest["files"])} package files match SHA-256; models included: {manifest["models_included"]}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
