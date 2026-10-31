"""Bounded console progress for downloads, hashing and file copies."""
from __future__ import annotations
import hashlib
import time


class Progress:
    def __init__(self, label, total=None, *, unit='bytes'):
        self.label, self.total, self.unit = label, total, unit
        self.current = 0
        self.started = time.monotonic()
        self.last = self.started
        self.update(0, force=True)

    def update(self, current, detail='', *, force=False):
        self.current = current
        now = time.monotonic()
        if not force and now-self.last < 2:
            return
        self.last = now
        if self.unit == 'bytes':
            value = f'{current/1048576:.1f} MiB'
            if self.total is not None:
                value += f' / {self.total/1048576:.1f} MiB'
            if current and now > self.started:
                value += f' ({current/1048576/(now-self.started):.2f} MiB/s)'
        else:
            value = str(current) + (f' / {self.total}' if self.total is not None else '') + ' files'
        print(f'[Update] {self.label}: {value}' + (f' | {detail}' if detail else ''), flush=True)

    def advance(self, count, detail=''):
        self.update(self.current+count, detail)


def file_hash(path, progress=None):
    with path.open('rb') as stream:
        if progress is None:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
        digest = hashlib.sha256()
        while block := stream.read(1024*1024):
            digest.update(block)
            progress.advance(len(block), path.name)
        return digest.hexdigest()


def copy_stream(source, destination, progress, detail=''):
    while block := source.read(1024*1024):
        destination.write(block)
        progress.advance(len(block), detail)
