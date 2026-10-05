"""Build-time vision download: ModelScope, domestic mirror, then official HF."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import urllib.parse
from portable_download import download

ROOT = Path(__file__).resolve().parents[1]


def catalog(root=ROOT):
    return json.loads((root/'vision/catalog.json').read_text(encoding='utf-8'))


def sources(entry):
    domestic = 'https://modelscope.cn/api/v1/models/' + entry['repository'] + '/repo?' + urllib.parse.urlencode(
        {'Revision': entry['modelscope_revision'], 'FilePath': entry['file']})
    suffix = '/' + entry['repository'] + '/resolve/' + entry['official_revision'] + '/' + entry['file']
    return [domestic, 'https://hf-mirror.com'+suffix, 'https://huggingface.co'+suffix]


def verify(root=ROOT):
    entry = catalog(root)
    path = root/'vision/weights'/entry['file']
    if not path.is_file() or path.stat().st_size != entry['size']:
        raise RuntimeError('Bundled vision weight missing or incomplete; use the VisionReady distribution.')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != entry['sha256']:
        raise RuntimeError('Bundled vision weight SHA256 mismatch; reinstall the verified VisionReady distribution.')
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    if not args.verify:
        entry = catalog(args.root)
        for url in sources(entry):
            try:
                print('Vision source: '+urllib.parse.urlparse(url).netloc, flush=True)
                download(url, args.root/'vision/weights'/entry['file'], entry['size'], entry['sha256'], args.workers)
                break
            except Exception as error:
                print(f'Vision source failed: {error}', flush=True)
        else:
            raise RuntimeError('All vision sources failed')
    print(f'Verified vision weight: {verify(args.root)}', flush=True)


if __name__ == '__main__':
    main()
