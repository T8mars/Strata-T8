"""Build-time vision download: ModelScope, domestic mirror, then official HF."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import urllib.parse
from portable_download import download

ROOT = Path(__file__).resolve().parents[1]


def catalog(root=ROOT):
    entry = json.loads((root/'vision/catalog.json').read_text(encoding='utf-8'))
    validate_entry(entry)
    return entry


def validate_entry(entry):
    if not isinstance(entry, dict):
        raise RuntimeError('Invalid vision catalog: expected an object')
    name = entry.get('file')
    if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.gguf', name, re.I)
            or PureWindowsPath(name).is_reserved()
            or type(entry.get('size')) is not int or entry['size'] <= 0
            or not isinstance(entry.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', entry['sha256'])
            or not isinstance(entry.get('family'), str) or not entry['family']):
        raise RuntimeError('Invalid vision catalog filename, size, digest or family')
    repository = entry.get('repository')
    if (not isinstance(repository, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository)
            or any(part in ('.', '..') for part in repository.split('/'))
            or any(not isinstance(entry.get(key), str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', entry[key])
                   for key in ('official_revision', 'modelscope_revision'))):
        raise RuntimeError('Invalid vision catalog repository or revision')


def weight_path(root, entry, *, downloads=False):
    folder = root/'vision/weights'
    path = folder/entry['file']
    destinations = [folder, path]
    if downloads:
        destinations += [path.with_name(path.name+suffix) for suffix in
                         ('.part', '.ranges.json', '.ranges.json.tmp', '.verified.json')]
    if any(not target.resolve().is_relative_to(root.resolve()) for target in destinations):
        raise RuntimeError('Vision weight or download sidecar path resolves outside the application folder')
    if any(not target.resolve().is_relative_to(folder.resolve()) for target in destinations[1:]):
        raise RuntimeError('Vision weight or download sidecar path resolves outside the weights folder')
    return path


def sources(entry):
    validate_entry(entry)
    domestic = 'https://modelscope.cn/api/v1/models/' + entry['repository'] + '/repo?' + urllib.parse.urlencode(
        {'Revision': entry['modelscope_revision'], 'FilePath': entry['file']})
    suffix = '/' + entry['repository'] + '/resolve/' + entry['official_revision'] + '/' + entry['file']
    return [domestic, 'https://hf-mirror.com'+suffix, 'https://huggingface.co'+suffix]


def verify(root=ROOT):
    entry = catalog(root)
    path = weight_path(root, entry)
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
        path = weight_path(args.root, entry, downloads=True)
        for url in sources(entry):
            try:
                print('Vision source: '+urllib.parse.urlparse(url).netloc, flush=True)
                download(url, path, entry['size'], entry['sha256'], args.workers)
                break
            except Exception as error:
                print(f'Vision source failed: {error}', flush=True)
        else:
            raise RuntimeError('All vision sources failed')
    print(f'Verified vision weight: {verify(args.root)}', flush=True)


if __name__ == '__main__':
    main()
