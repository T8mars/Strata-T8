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
from portable_build_provenance import build_identity, validate_engine_build
from portable_weights import weight_declaration, MODEL_SUFFIXES, allowed_weight, allowed_runtime_data
from windows_utf8_manifest import patch_engine

ROOT = Path(__file__).resolve().parents[1]
META = metadata()
NAME = archive_name(META['version']).removesuffix('.zip')
OUT = ROOT/'dist'
TARGET = OUT/NAME

# These files are operational documentation, not local planning or benchmark data.
DOCS = (
    'INSTALL.md', 'AI_SETUP.md', 'MODELS.md', 'TROUBLESHOOTING.md', 'AMD_HIP.md',
    'AMD_HIP_PERFORMANCE.md', 'STRIX_HALO.md', 'OLDER_GPUS.md', 'NVIDIA_V100.md',
    'HOW_IT_WORKS.md', 'DETAILS.md', 'BATCHING.md', 'MULTI_GPU.md', 'SECOND_GPU.md',
    'EXCHANGE_ROTATION.md', 'BATCHED_DMA.md', 'DISJOINT_EXPERT_CACHE.md',
    'KV_PREFETCH.md', 'MESSAGE_BOUNDARY_CACHE.md', 'PROMPT_CACHE_TAIL.md',
    'MCP_SERVER.md', 'UNSLOTH_Q4.md', 'UNSLOTH_Q6.md', 'ORCA.md', 'ORCA_Q4_K_S.md',
    'COMMUNITY_BENCHMARKS.md', 'INTEL.md', 'INTEL_ARC.md',
    'VRAM_ELASTIC.md', 'LLAMA_SWAP.md', 'RESEARCH_RUNS.md',
    'UPDATING-T8.md', 'COMFYUI-T8.md', 'VALIDATION-T8.md', 'VALIDATION-COMFYUI-T8.md', 'AUDIT-20-T8.md',
    'AUDIT-20-ROUND2-T8.md', 'AUDIT-20-ROUND3-T8.md', 'AUDIT-20-ROUND4-T8.md',
    'AUDIT-20-ROUND5-T8.md', 'AUDIT-20-ROUND6-T8.md', 'AUDIT-20-ROUND7-T8.md', 'AUDIT-20-ROUND8-T8.md',
    'VALIDATION-UPSTREAM-0140-T8.md',
    'VALIDATION-UPSTREAM-01402-T8.md',
)


def private_plan(path):
    return any(part.casefold() == 'roadmap.md' for part in Path(path).parts)


def shipping_ignore(original):
    def ignore(directory, names):
        return set(original(directory, names)) | {name for name in names if private_plan(name)}
    return ignore


def copy_tree(relative, ignore=None):
    src = ROOT/relative
    if private_plan(relative):
        raise ValueError('Local roadmap is forbidden in distribution')
    shutil.copytree(src, TARGET/relative, dirs_exist_ok=True,
                    ignore=shipping_ignore(ignore or shutil.ignore_patterns('__pycache__', '*.pyc')))


def copy_source(relative, excluded=()):
    """Ship tracked application files; local profiles and download state stay local."""
    paths = subprocess.check_output(['git', 'ls-files', '-z', '--', relative], cwd=ROOT).decode('utf-8').split('\0')
    for name in paths:
        if not name or private_plan(name):
            continue
        path = Path(name)
        if any(part in excluded or part == '__pycache__' for part in path.parts):
            continue
        if path.name.startswith('test_') or path.name.endswith(('_test.py', '.pyc', '.log')) or path.name == 'stop_local.ps1':
            continue
        source = ROOT/path
        if source.is_symlink() or not source.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError(f'Linked source file: {name}')
        destination = TARGET/path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def package_files():
    """Check the final tree before any manifest or ZIP can contain a local plan."""
    files = sorted(path for path in TARGET.rglob('*') if path.is_file())
    for path in files:
        if private_plan(path.relative_to(TARGET)):
            raise ValueError('Local roadmap is forbidden in distribution')
    return files


def main():
    global NAME, TARGET
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--edition', choices=['Portable-NoModels', 'VisionReady-NoMainModel'], default='VisionReady-NoMainModel')
    edition = parser.parse_args().edition
    NAME = archive_name(META['version'], edition).removesuffix('.zip')
    TARGET = OUT/NAME
    weights = weight_declaration(edition)
    identity = build_identity(META, source_version())
    for backend in ['engine', 'engine-hip']:
        validate_engine_build(json.loads((ROOT/backend/'BUILD.json').read_text(encoding='utf-8')),
                              backend, META, identity['engine_version'])
    if TARGET.resolve().parent != OUT.resolve() or TARGET.name != NAME or TARGET.is_symlink():
        raise SystemExit('Refusing to rebuild outside the exact distribution directory')
    if TARGET.exists():
        shutil.rmtree(TARGET)
    TARGET.mkdir(parents=True)
    for name in ['runtime', 'engine', 'engine-hip']:
        copy_tree(name, shutil.ignore_patterns('__pycache__', '*.pyc', 'test_*.py', '*_test.py', '*.log', 'stop_local.ps1'))
    for name in ['serve', 'tools', 'ref']:
        copy_source(name)
    # Patch staged copies too: locally cached vendor engines may predate bootstrap.
    for backend in ['engine', 'engine-hip']:
        patch_engine(TARGET/backend)
    copy_source('data', ('experimental-speed-projection',))
    copy_source('vision', ('weights',))
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
    for file in ['README-UPSTREAM.md']:
        shutil.copy2(ROOT/file, TARGET/file)
    # Only gguf-py is needed for preparing imported models. Never compile on recipients' PCs.
    copy_tree('third_party/llama.cpp/gguf-py', shutil.ignore_patterns('__pycache__', '*.pyc', 'tests', 'examples'))
    for rel in ['third_party/llama.cpp/LICENSE', 'third_party/llama.cpp/UPSTREAM.json']:
        shutil.copy2(ROOT/rel, TARGET/rel)
    (TARGET/'docs').mkdir()
    for name in DOCS:
        shutil.copy2(ROOT/'docs'/name, TARGET/'docs'/name)
    # Preserve third-party licensing; ROCm/wheels already carry their license directories.
    versions = {d.metadata['Name']: d.version for d in importlib.metadata.distributions()}
    save = {'version': META['version'], **identity, 'source_commit': subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
            'python': sys.version, 'edition': edition, 'weights': weights, 'models_included': weights['vision'], 'dependencies': versions,
            'gguf_source': json.loads((TARGET/'third_party/llama.cpp/UPSTREAM.json').read_text(encoding='utf-8')),
            'engines': {backend: json.loads((TARGET/f'{backend}/BUILD.json').read_text()) for backend in ['engine', 'engine-hip']},
            'files': []}
    for file in package_files():
        rel = file.relative_to(TARGET).as_posix()
        if file.relative_to(TARGET).parts[0].lower() in ['strata-data', 'models', 'packs', 'mtp']:
            raise SystemExit(f'Model data directory forbidden: {rel}')
        with file.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        entry = {'path': rel, 'size': file.stat().st_size, 'sha256': digest}
        if file.suffix.lower() in MODEL_SUFFIXES and not allowed_weight(entry, edition) and not allowed_runtime_data(entry):
            raise SystemExit(f'Model-like file forbidden in distribution: {rel}')
        save['files'].append(entry)
    (TARGET/'PACKAGE-MANIFEST.json').write_text(json.dumps(save, indent=2, ensure_ascii=False), encoding='utf-8')
    from portable_update import validate_manifest
    validate_manifest(TARGET, save, verify=False)
    archive = OUT/(NAME+'.zip')
    print(f'Writing {archive} ({len(save["files"])} files) ...', flush=True)
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
        for file in package_files():
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
