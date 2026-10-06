"""Explicitly download/prepare IQ3_S; regular portable startup never downloads models."""
import argparse
import json
import os
from pathlib import Path, PureWindowsPath
import re
import subprocess
import sys
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'tools'))
from portable_download import download
from portable_io import atomic_json
import setup


PACK_FILES = ('native_experts.txt', 'index.txt', 'dense.bin', 'tokenizer/vocab.json', 'tokenizer/chat_template.jinja')
MTP_FILES = ('experts.bin', 'dense.bin', 'dense.txt')


def validate_paths(data, catalog):
    entries = catalog.get('files')
    if not isinstance(entries, list) or not entries:
        raise RuntimeError('Invalid model catalog files')
    names = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError('Invalid model catalog entry')
        name = entry.get('file')
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.gguf', name, re.I)
                or PureWindowsPath(name).is_reserved() or name.casefold() in names):
            raise RuntimeError('Invalid or duplicate model catalog filename')
        if (type(entry.get('size')) is not int or entry['size'] <= 0 or not isinstance(entry.get('sha256'), str)
                or not re.fullmatch('[0-9a-f]{64}', entry['sha256'])):
            raise RuntimeError('Invalid model catalog file size or digest')
        names.add(name.casefold())
    destinations = [data/'portable-model.json', data/'models/IQ3_S', data/'packs/iq3_s', data/'mtp']
    destinations += [data/'models/IQ3_S'/entry['file'] for entry in entries]
    destinations += [data/'packs/iq3_s'/name for name in PACK_FILES]
    destinations += [data/'mtp/rt'/name for name in MTP_FILES]
    if any(not path.resolve().is_relative_to(data) for path in destinations):
        raise RuntimeError('Model preparation path resolves outside the model delivery folder')
    # Check existing temporary files and download sidecars as well as final artifacts.
    pending = [data/'models/IQ3_S', data/'packs/iq3_s', data/'mtp']
    visited = set()
    while pending:
        folder = pending.pop().resolve()
        if folder in visited or not folder.is_dir():
            continue
        visited.add(folder)
        for path in folder.iterdir():
            resolved = path.resolve()
            if not resolved.is_relative_to(data):
                raise RuntimeError('Model preparation path resolves outside the model delivery folder')
            if resolved.is_dir():
                pending.append(resolved)


def sources(catalog, entry):
    relative = 'IQ3_S/' + entry['file']
    domestic = 'https://modelscope.cn/api/v1/models/' + catalog['repository'] + '/repo?' + urllib.parse.urlencode({'Revision': catalog['modelscope_revision'], 'FilePath': relative})
    suffix = '/' + catalog['repository'] + '/resolve/' + catalog['official_revision'] + '/' + relative
    return [domestic, 'https://hf-mirror.com'+suffix, 'https://huggingface.co'+suffix]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ROOT.parent/'Strata-data')
    parser.add_argument('--yes', action='store_true', help='accept the approximately 89 GB download')
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()
    data = args.data_dir.expanduser().resolve()
    catalog = json.loads((ROOT/'model-sources.json').read_text(encoding='utf-8'))
    if setup.HF_REVISIONS[catalog['repository']] != catalog['official_revision']:
        raise RuntimeError('Upstream changed the model checkpoint. Update the verified model catalog before downloading.')
    validate_paths(data, catalog)
    print(f'IQ3_S: 83.62 GB GGUF + approximately 5.22 GB MTP; prepared data needs additional disk space.\nDestination: {data}', flush=True)
    if not args.yes and input('Download missing model components? [y/N] ').strip().lower() != 'y':
        return 0
    gguf = data/'models/IQ3_S'
    for entry in catalog['files']:
        last_error = None
        for url in sources(catalog, entry):
            try:
                print(f'Model source: {urllib.parse.urlparse(url).netloc}', flush=True)
                download(url, gguf/entry['file'], entry['size'], entry['sha256'], workers=args.workers)
                break
            except Exception as error:
                last_error = error
                print(f'Source failed; trying next source: {error}', flush=True)
        else:
            raise RuntimeError(f'All model sources failed: {last_error}')
    env = dict(os.environ, STRATA_GGUF_PY=str(ROOT/'third_party/llama.cpp/gguf-py'))
    def run(tool, *arguments):
        subprocess.run([sys.executable, '-X', 'utf8', str(ROOT/'tools'/tool), *map(str, arguments)], env=env, check=True)
    pack = data/'packs/iq3_s'
    pack_files = PACK_FILES
    if not all((pack/name).is_file() and (pack/name).stat().st_size for name in pack_files):
        run('iq_pack.py', '--gguf', gguf/catalog['files'][0]['file'], '--out', pack)
    mtp = data/'mtp'
    if not all((mtp/'rt'/name).is_file() and (mtp/'rt'/name).stat().st_size for name in MTP_FILES):
        run('portable_mtp_fetch.py', 'fetch', '--out', mtp)
        run('mtp_fetch.py', 'verify', '--out', mtp)
        run('mtp_pack.py', '--src', mtp, '--experts', 'q2_0', '--out', mtp/'mtp-q2_0.gguf')
        run('mtp_rt.py', '--gguf', mtp/'mtp-q2_0.gguf', '--out', mtp/'rt')
    required = ['models/IQ3_S/'+e['file'] for e in catalog['files']] + ['packs/iq3_s/'+name for name in pack_files] + ['mtp/rt/'+name for name in MTP_FILES]
    artifacts = required[len(catalog['files']):]
    validate_paths(data, catalog)
    main_complete = all((gguf/entry['file']).is_file() and (gguf/entry['file']).stat().st_size == entry['size'] for entry in catalog['files'])
    if not main_complete or not all((data/name).is_file() and (data/name).stat().st_size for name in artifacts):
        raise RuntimeError('Prepared model artifacts are incomplete; model descriptor was not published')
    descriptor = {**catalog, 'gguf_dir': 'models/IQ3_S', 'required_files': ['models/IQ3_S/'+e['file'] for e in catalog['files']],
                  'mtp_official_revision': __import__('mtp_fetch').PINNED_REVISION}
    descriptor['required_files'] = required
    atomic_json(data/'portable-model.json', descriptor)
    print(f'Model ready: {data}. Use IMPORT-MODEL.bat to configure this PC.', flush=True)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f'[Model] {error}', file=sys.stderr)
        raise SystemExit(2)
