"""Record the actual pinned CPU decoder and redistributed dynamic libraries."""
from __future__ import annotations
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def verify(root=ROOT):
    """Check a staged runtime against fixed wheel members without importing it."""
    root = Path(root)
    sources = json.loads((root/'licenses/video/sources.json').read_text(encoding='utf-8'))
    inventory = json.loads((root/'runtime/VIDEO-DEPENDENCIES.json').read_text(encoding='utf-8'))
    if (inventory.get('av_version') != sources['av_version']
            or inventory.get('ffmpeg_version') != sources['ffmpeg_version']
            or inventory.get('fixed_sources') != sources
            or sorted(inventory.get('dynamic_files', []), key=lambda x:x['path'])
                != sorted(sources['dynamic_files'], key=lambda x:x['path'])):
        raise ValueError('Video runtime inventory differs from the fixed wheel/source identity')
    base = root/'runtime/python/Lib/site-packages'
    files = sources.get('runtime_files')
    if not isinstance(files, list) or not files:
        raise ValueError('Missing fixed video wheel member digests')
    for item in files:
        relative = Path(item['path'])
        path = base/relative
        if (relative.is_absolute() or '..' in relative.parts or not path.is_file()
                or path.is_symlink() or not path.resolve().is_relative_to(base.resolve())):
            raise ValueError('Missing or linked video runtime file: '+item['path'])
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if path.stat().st_size != item['size'] or digest != item['sha256']:
            raise ValueError('Video runtime file differs from the fixed wheel: '+item['path'])
    actual_dlls = sorted(p.name for p in (base/'av.libs').glob('*.dll'))
    if actual_dlls != sorted(x['path'] for x in sources['dynamic_files']):
        raise ValueError('Unexpected or missing video dynamic library')
    for item in sources['license_sources']:
        path = root/'licenses/video'/item['file']
        if (not path.is_file() or path.is_symlink()
                or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']):
            raise ValueError('Video dependency license/source digest differs')
    return inventory


def prepare(root=ROOT):
    import av
    from av._core import library_meta
    root = Path(root)
    sources = json.loads((root/'licenses/video/sources.json').read_text(encoding='utf-8'))
    if importlib.metadata.version('av') != sources['av_version'] or av.ffmpeg_version_info != sources['ffmpeg_version']:
        raise ValueError('Video decoder differs from its fixed dependency sources')
    for item in sources['license_sources']:
        path = root/'licenses/video'/item['file']
        if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('Video dependency license/source digest differs')
    package = Path(av.__file__).resolve().parent
    libraries = package.parent/'av.libs'
    if not libraries.is_dir():
        raise ValueError('The pinned Windows PyAV wheel is missing its dynamic libraries')
    files = []
    for path in sorted(libraries.glob('*.dll')):
        if path.is_symlink() or not path.resolve().is_relative_to(libraries.resolve()):
            raise ValueError('Linked video library is not redistributable')
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        files.append({'path': path.name, 'size': path.stat().st_size, 'sha256': digest})
    if not files:
        raise ValueError('Video dynamic-library inventory is empty')
    result = {'av_version': av.__version__, 'ffmpeg_version': av.ffmpeg_version_info,
              'fixed_sources': sources, 'libraries': library_meta, 'dynamic_files': files}
    destination = root/'runtime/VIDEO-DEPENDENCIES.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    verify(root)
    print(json.dumps({'video_runtime': str(destination), 'av': av.__version__,
                      'ffmpeg': av.ffmpeg_version_info, 'dynamic_libraries': len(files)}),flush=True)
    return result


if __name__ == '__main__':
    prepare(Path(sys.argv[1]) if len(sys.argv)>1 else ROOT)
