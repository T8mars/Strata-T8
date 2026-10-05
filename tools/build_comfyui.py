"""Build a small, model-free custom-node archive with a hash manifest."""
import hashlib
import json
from pathlib import Path
import zipfile
from portable_version import metadata

ROOT = Path(__file__).resolve().parents[1]


def main():
    meta = metadata()
    source = ROOT/'comfyui-strata-t8'
    files = [p for p in source.rglob('*') if p.is_file() and not any(part in ('.local', '__pycache__') for part in p.relative_to(source).parts) and p.suffix != '.pyc']
    if any(p.suffix.lower() in ('.gguf', '.safetensors', '.pt', '.pth', '.ckpt', '.onnx', '.log') for p in files):
        raise RuntimeError('Weights and local files must not enter the node archive')
    manifest = {'version': meta['version'], 'protocol': 1, 'runtime_min_version': meta['version'], 'files': []}
    contents = {p.relative_to(source).as_posix(): p.read_bytes() for p in files}
    contents['LICENSE'] = (ROOT/'LICENSE').read_bytes()
    contents['version.json'] = json.dumps({k: v for k, v in manifest.items() if k != 'files'}, indent=2).encode()
    for path, data in sorted(contents.items()):
        manifest['files'].append({'path': path, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    contents['NODE-MANIFEST.json'] = json.dumps(manifest, indent=2).encode()
    archive = ROOT/'dist'/f'ComfyUI-Strata-T8-{meta["version"]}.zip'
    archive.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        for path, data in sorted(contents.items()):
            target.writestr('comfyui-strata-t8/'+path, data)
    with zipfile.ZipFile(archive) as check:
        if check.testzip():
            raise RuntimeError('Node archive CRC failure')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name+'.sha256').write_text(f'{digest}  {archive.name}\n')
    print(json.dumps({'archive': str(archive), 'size': archive.stat().st_size, 'sha256': digest}))


if __name__ == '__main__':
    main()
