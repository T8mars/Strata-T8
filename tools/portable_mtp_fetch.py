"""Use ModelScope for the pinned MTP checkpoint after comparing shard SHA-256."""
import json
from pathlib import Path
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mtp_fetch


def main():
    ms_url = 'https://modelscope.cn/api/v1/models/Qwen/Qwen3.8-Flash-Next/repo/files?Revision=master&Recursive=true'
    hf_url = f'https://hf-mirror.com/api/models/Qwen/Qwen3.8-Flash-Next/revision/{mtp_fetch.PINNED_REVISION}?blobs=true'
    files = {}
    official = {}
    try:
        with urllib.request.urlopen(ms_url, timeout=30) as r:
            files = {f['Path']: f for f in json.load(r)['Data']['Files'] if f['Type'] == 'blob'}
        try:
            response = urllib.request.urlopen(hf_url, timeout=30)
        except Exception:
            response = urllib.request.urlopen(hf_url.replace('hf-mirror.com', 'huggingface.co'), timeout=30)
        with response as r:
            official = {f['rfilename']: f.get('lfs', {}) for f in json.load(r)['siblings']}
    except Exception as error:
        print(f'ModelScope checkpoint metadata unavailable: {error}. Using pinned HF sources.', flush=True)
    original_get = mtp_fetch.get
    def domestic_get(url, start=None, end=None):
        name = url.rsplit('/', 1)[-1]
        info = files.get(name)
        candidates = []
        expected = official.get(name, {})
        matched = info and official and (not name.endswith('.safetensors') or (info['Sha256'] == expected.get('sha256') and info['Size'] == expected.get('size')))
        if matched:
            candidates.append('https://modelscope.cn/api/v1/models/Qwen/Qwen3.8-Flash-Next/repo?' + urllib.parse.urlencode({'Revision': info['Revision'], 'FilePath': name}))
        suffix = '/Qwen/Qwen3.8-Flash-Next/resolve/' + mtp_fetch.PINNED_REVISION + '/' + name
        candidates += ['https://hf-mirror.com'+suffix, 'https://huggingface.co'+suffix]
        for candidate in candidates:
            try:
                return original_get(candidate, start, end)
            except Exception as error:
                print(f'MTP source failed: {candidate.split("/")[2]}: {error}', flush=True)
        raise RuntimeError(f'All MTP sources failed: {name}')
    mtp_fetch.get = domestic_get
    mtp_fetch.resolve_repo = lambda: mtp_fetch.REPO
    print('MTP source: ModelScope; checkpoint shard hashes checked against pinned Hugging Face revision', flush=True)
    mtp_fetch.main()


if __name__ == '__main__':
    main()
