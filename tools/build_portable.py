"""Create audited portable editions with explicit main/MTP/vision weight roles."""
from __future__ import annotations
import hashlib
import argparse
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parent))
from portable_version import metadata, source_version, archive_name
from portable_weights import weight_declaration, MODEL_SUFFIXES, allowed_weight
from windows_utf8_manifest import patch_engine

ROOT = Path(__file__).resolve().parents[1]
META = metadata()
NAME = archive_name(META['version']).removesuffix('.zip')
OUT = ROOT/'dist'
TARGET = OUT/NAME


def copy_tree(relative, ignore=None):
    src = ROOT/relative
    shutil.copytree(src, TARGET/relative, dirs_exist_ok=True, ignore=ignore or shutil.ignore_patterns('__pycache__', '*.pyc'))


def main():
    global NAME, TARGET
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--edition', choices=['Portable-NoModels', 'VisionReady-NoMainModel'], default='VisionReady-NoMainModel')
    edition = parser.parse_args().edition
    NAME = archive_name(META['version'], edition).removesuffix('.zip')
    TARGET = OUT/NAME
    weights = weight_declaration(edition)
    if source_version() != META['upstream_version']:
        raise SystemExit('Source and package version mismatch')
    for backend in ['engine', 'engine-hip']:
        if json.loads((ROOT/backend/'BUILD.json').read_text())['version'] != source_version():
            raise SystemExit(f'{backend} does not match the source version')
    if TARGET.resolve().parent != OUT.resolve() or TARGET.name != NAME or TARGET.is_symlink():
        raise SystemExit('Refusing to rebuild outside the exact distribution directory')
    if TARGET.exists():
        shutil.rmtree(TARGET)
    TARGET.mkdir(parents=True)
    for name in ['runtime', 'engine', 'engine-hip', 'serve', 'tools']:
        copy_tree(name, shutil.ignore_patterns('__pycache__', '*.pyc', 'test_*.py', '*_test.py', '*.log', 'stop_local.ps1'))
    # Patch staged copies too: locally cached vendor engines may predate bootstrap.
    for backend in ['engine', 'engine-hip']:
        patch_engine(TARGET/backend)
    copy_tree('data', shutil.ignore_patterns('experimental-speed-projection'))
    copy_tree('vision', shutil.ignore_patterns('weights', '__pycache__'))
    if weights['vision']:
        from prepare_portable_vision import verify
        weight = verify(ROOT)
        (TARGET/'vision/weights').mkdir()
        shutil.copy2(weight, TARGET/'vision/weights'/weight.name)
    for file in ['portable.py', 'setup.py', 'requirements.txt', 'requirements-portable.txt', 'CMakeLists.txt', 'LICENSE', 'README.md', 'README-PORTABLE.zh-CN.md', 'meta.json', 'features.json', 'model-sources.json', 'START-PORTABLE.bat', 'CHECK-ENV.bat', 'IMPORT-MODEL.bat', 'PREPARE-MODEL.bat', 'UPDATE-PORTABLE.bat', 'INSTALL-VISION.bat', 'VERIFY-PACKAGE.bat']:
        shutil.copy2(ROOT/file, TARGET/file)
    (TARGET/'meta.json').write_text(json.dumps({**META, 'edition': edition, 'weights': weights, 'models_included': weights['vision']}, indent=2), encoding='utf-8')
    features = json.loads((ROOT/'features.json').read_text(encoding='utf-8'))
    features['bundled_vision_weights'] = weights['vision']
    (TARGET/'features.json').write_text(json.dumps(features, indent=2), encoding='utf-8')
    shutil.copy2(ROOT/'START-PORTABLE.bat', TARGET/'START-HERE.bat')
    shutil.copy2(ROOT/'UPDATE-PORTABLE.bat', TARGET/'UPDATE.bat')
    for file in ['ROADMAP.MD', 'README-UPSTREAM.md']:
        shutil.copy2(ROOT/file, TARGET/file)
    # Only gguf-py is needed for preparing imported models. Never compile on recipients' PCs.
    copy_tree('third_party/llama.cpp/gguf-py', shutil.ignore_patterns('__pycache__', '*.pyc', 'tests', 'examples'))
    for rel in ['third_party/llama.cpp/LICENSE']:
        shutil.copy2(ROOT/rel, TARGET/rel)
    (TARGET/'docs').mkdir()
    for name in ['INSTALL.md', 'MODELS.md', 'TROUBLESHOOTING.md', 'AMD_HIP.md', 'HOW_IT_WORKS.md', 'DETAILS.md', 'BATCHING.md', 'UPDATING-T8.md', 'COMFYUI-T8.md', 'VALIDATION-COMFYUI-T8.md']:
        shutil.copy2(ROOT/'docs'/name, TARGET/'docs'/name)
    # Preserve third-party licensing; ROCm/wheels already carry their license directories.
    versions = {d.metadata['Name']: d.version for d in importlib.metadata.distributions()}
    save = {'version': META['version'], 'upstream_version': source_version(), 'source_commit': subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
            'python': sys.version, 'edition': edition, 'weights': weights, 'models_included': weights['vision'], 'dependencies': versions,
            'engines': {backend: json.loads((TARGET/f'{backend}/BUILD.json').read_text()) for backend in ['engine', 'engine-hip']},
            'files': []}
    for file in sorted(TARGET.rglob('*')):
        if not file.is_file():
            continue
        rel = file.relative_to(TARGET).as_posix()
        if file.relative_to(TARGET).parts[0].lower() in ['strata-data', 'models', 'packs', 'mtp']:
            raise SystemExit(f'Model data directory forbidden: {rel}')
        with file.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        entry = {'path': rel, 'size': file.stat().st_size, 'sha256': digest}
        if file.suffix.lower() in MODEL_SUFFIXES and not allowed_weight(entry, edition):
            raise SystemExit(f'Model-like file forbidden in distribution: {rel}')
        save['files'].append(entry)
    (TARGET/'PACKAGE-MANIFEST.json').write_text(json.dumps(save, indent=2, ensure_ascii=False), encoding='utf-8')
    from portable_update import validate_manifest
    validate_manifest(TARGET, save, verify=False)
    archive = OUT/(NAME+'.zip')
    print(f'Writing {archive} ({len(save["files"])} files) ...', flush=True)
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
        for file in sorted(TARGET.rglob('*')):
            if file.is_file():
                z.write(file, NAME+'/'+file.relative_to(TARGET).as_posix())
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if archive.stat().st_size >= 2*1024**3:
        raise SystemExit('Release asset exceeds GitHub 2 GiB limit; split this edition by backend before publishing')
    archive.with_name(archive.name+'.sha256').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    with zipfile.ZipFile(archive) as z:
        bad = z.testzip()
        if bad:
            raise SystemExit(f'Archive CRC check failed: {bad}')
    print(json.dumps({'archive': str(archive), 'size': archive.stat().st_size, 'sha256': digest, 'files': len(save['files']), 'weights': weights}), flush=True)


if __name__ == '__main__':
    main()
