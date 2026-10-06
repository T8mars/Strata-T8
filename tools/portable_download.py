"""Resumable range downloader with exact range checks and SHA-256 validation."""
from __future__ import annotations
import argparse
import concurrent.futures
import hashlib
import json
import os
import re
from pathlib import Path
import threading
import time
import urllib.request


def download(url: str, target: Path, size: int, sha256: str, workers=8, chunk_mib=128):
    if type(workers) is not int or workers < 1 or type(chunk_mib) is not int or chunk_mib < 1:
        raise ValueError('workers and chunk size must be positive integers')
    if type(size) is not int or size <= 0 or not isinstance(sha256, str) or not re.fullmatch('[0-9a-f]{64}', sha256):
        raise ValueError('Expected a positive file size and SHA-256')
    target.parent.mkdir(parents=True, exist_ok=True)
    stamp = target.with_name(target.name + '.verified.json')
    if target.exists() and stamp.exists():
        try:
            checked = json.loads(stamp.read_text(encoding='utf-8'))
            if not isinstance(checked, dict): checked = {}
        except (ValueError, OSError):
            checked = {}
        if checked.get('sha256') == sha256 and target.stat().st_size == size and checked.get('mtime_ns') == target.stat().st_mtime_ns:
            print(f'Already verified: {target}', flush=True)
            return
    if target.exists() and target.stat().st_size == size:
        with target.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual == sha256:
            stamp.write_text(json.dumps({'sha256': sha256, 'mtime_ns': target.stat().st_mtime_ns}), encoding='utf-8')
            print(f'Existing file verified: {target}', flush=True)
            return
    partial = target.with_name(target.name + '.part')
    state = target.with_name(target.name + '.ranges.json')
    chunk = chunk_mib * 1024 * 1024
    info = {'url': url, 'size': size, 'sha256': sha256, 'chunk': chunk, 'complete': []}
    if state.exists() and partial.exists():
        try:
            old = json.loads(state.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            old = {}
        count = (size+chunk-1)//chunk
        if (isinstance(old, dict) and all(old.get(k) == info[k] for k in ('size', 'sha256', 'chunk'))
                and partial.stat().st_size == size and isinstance(old.get('complete'), list)
                and all(type(i) is int and 0 <= i < count for i in old['complete'])):
            info = old
    complete = set(info['complete'])
    if not partial.exists() or partial.stat().st_size != size:
        with partial.open('wb') as f:
            f.truncate(size)
    lock = threading.Lock()
    def save():
        info['complete'] = sorted(complete)
        tmp = state.with_name(state.name + '.tmp')
        tmp.write_text(json.dumps(info), encoding='utf-8')
        os.replace(tmp, state)
    save()
    start_time = time.monotonic()
    initial = sum(min(chunk, size-i*chunk) for i in complete)
    def fetch(i):
        first, last = i*chunk, min(size, (i+1)*chunk)-1
        expected = f'bytes {first}-{last}/{size}'
        for attempt in range(6):
            try:
                req = urllib.request.Request(url, headers={'Range': f'bytes={first}-{last}', 'User-Agent': 'Strata-portable/1.0', 'Accept-Encoding': 'identity'})
                with urllib.request.urlopen(req, timeout=90) as response:
                    if response.status != 206 or response.headers.get('Content-Range') != expected:
                        raise ValueError(f'Range mismatch: {response.status} {response.headers.get("Content-Range")} != {expected}')
                    written = 0
                    with partial.open('r+b') as f:
                        f.seek(first)
                        while data := response.read(min(4*1024*1024, last-first+1-written)):
                            f.write(data)
                            written += len(data)
                        f.flush()
                    if written != last-first+1:
                        raise ValueError(f'Short read: {written}')
                with lock:
                    complete.add(i)
                    save()
                return
            except Exception as e:
                print(f'{target.name} range {i}: retry {attempt+1}: {e}', flush=True)
                if attempt == 5:
                    raise
                time.sleep(min(2**attempt, 20))
    count = (size+chunk-1)//chunk
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = {pool.submit(fetch, i) for i in range(count) if i not in complete}
        last_print = 0
        while jobs:
            finished, jobs = concurrent.futures.wait(jobs, timeout=10, return_when=concurrent.futures.FIRST_COMPLETED)
            for job in finished:
                job.result()
            now = time.monotonic()
            if now-last_print >= 15 or not jobs:
                with lock:
                    done = sum(min(chunk, size-i*chunk) for i in complete)
                rate = (done-initial)/max(now-start_time, 1)/1e6
                print(f'{target.name}: {done/1e9:.2f}/{size/1e9:.2f} GB ({100*done/size:.1f}%), {rate:.1f} MB/s', flush=True)
                last_print = now
    print(f'Hashing {target.name} ...', flush=True)
    with partial.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != sha256:
        complete.clear()
        save()
        raise ValueError(f'SHA-256 mismatch for {target}: {digest}')
    os.replace(partial, target)
    stamp.write_text(json.dumps({'sha256': sha256, 'size': size, 'mtime_ns': target.stat().st_mtime_ns, 'source': url}, indent=2), encoding='utf-8')
    state.unlink(missing_ok=True)
    print(f'Verified: {target}', flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('manifest', type=Path)
    ap.add_argument('--workers', type=int, default=8)
    args = ap.parse_args()
    for entry in json.loads(args.manifest.read_text(encoding='utf-8')):
        download(entry['url'], Path(entry['target']), entry['size'], entry['sha256'], args.workers, entry.get('chunk_mib', 128))


if __name__ == '__main__':
    main()
