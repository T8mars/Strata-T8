"""Portable Windows launcher. Offline start/configuration never downloads a model."""
from __future__ import annotations
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import urllib.request

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
import setup as upstream

STATE = ROOT / 'portable-settings.json'


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')


def environment_check():
    modules = ['numpy', 'jinja2', 'regex', 'yaml', 'tqdm', 'requests', 'PIL', 'psutil']
    for name in modules:
        __import__(name)
    for line in upstream.requirement_lines() + upstream.requirement_lines(ROOT/'requirements-portable.txt') + upstream.CUDA_WHEELS:
        from pip._vendor.packaging.requirements import Requirement
        req = Requirement(line)
        if req.marker and not req.marker.evaluate():
            continue
        version = importlib.metadata.version(req.name)
        if version not in req.specifier:
            raise RuntimeError(f'Runtime dependency mismatch: {req.name} {version}, expected {req.specifier}')
    for path in [ROOT/'engine/strata.exe', ROOT/'engine-hip/strata.exe', ROOT/'third_party/llama.cpp/gguf-py/gguf']:
        if not path.exists():
            raise RuntimeError(f'Missing bundled dependency: {path}')
    print(f'Python {platform.python_version()} (bundled), runtime dependencies OK', flush=True)


def isolate_setup():
    # Keep this portable installation independent of the user's other Strata installs.
    upstream.settings_path = lambda: STATE
    upstream.other_installs = lambda settings: []
    upstream.data_folder = lambda requested: (Path(requested).resolve(), [])
    upstream.get_llama_cpp = lambda: ROOT/'third_party/llama.cpp'
    upstream.pip_install = lambda packages, what: print(f'  [ok] bundled {what}')
    def deny_download(*args, **kwargs):
        raise RuntimeError('Offline package: missing model component. Copy the complete Strata-data folder from the model delivery.')
    upstream.download = deny_download
    urllib.request.urlopen = deny_download
    def hip_engine(url_base, gpu, updating=False):
        engine = ROOT/'engine-hip'
        upstream.hip_runtime_beside_exe(engine)
        return engine
    upstream.get_prebuilt_hip = hip_engine


def data_path(requested=None):
    if requested:
        return Path(requested).expanduser().resolve()
    if STATE.exists():
        saved = read_json(STATE).get('portable_data_dir')
        if saved:
            path = Path(saved)
            path = path if path.is_absolute() else ROOT/path
            if path.exists():
                return path.resolve()
    for path in [ROOT/'Strata-data', ROOT.parent/'Strata-data']:
        if (path/'portable-model.json').is_file():
            return path.resolve()
    return (ROOT/'Strata-data').resolve()


def model_delivery(data):
    path = data/'portable-model.json'
    if not path.is_file():
        raise RuntimeError('Model delivery missing. Place the separately supplied Strata-data folder beside START-HERE.bat, or use IMPORT-MODEL.bat with its path. See README-PORTABLE.zh-CN.md.')
    model = read_json(path)
    family, size = model.get('family'), model.get('model')
    if family not in upstream.FAMILIES or size not in upstream.MODELS:
        raise RuntimeError('Invalid portable-model.json: unsupported family or model')
    if family not in upstream.MODELS[size]['families']:
        raise RuntimeError('Model size does not belong to the selected family')
    gguf = (data/model['gguf_dir']).resolve()
    if not gguf.is_relative_to(data.resolve()) or not gguf.is_dir():
        raise RuntimeError('Invalid or missing model GGUF directory')
    for rel in model.get('required_files', []):
        path = (data/rel).resolve()
        if not path.is_relative_to(data.resolve()) or not path.is_file():
            raise RuntimeError(f'Model delivery is incomplete: {rel}')
    for rel in ['mtp/rt/experts.bin', 'mtp/rt/dense.bin', 'mtp/rt/dense.txt']:
        if not (data/rel).is_file():
            raise RuntimeError(f'Model delivery is missing its auxiliary MTP layer: {rel}')
    return model


def fingerprint(data):
    from portable_version import metadata
    return {'app': str(ROOT), 'data': str(data), 'gpu': upstream.gpus(), 'amd': upstream.amd_gpus(),
            'ram': round(upstream.ram_gb()), 'cpu': platform.processor(), 'version': metadata()['version']}


def configure(data, context=None, backend=None):
    model = model_delivery(data)
    tag = upstream.FAMILIES[model['family']]['tag'] + model['model']
    cfg_path = ROOT/f'strata-{tag.lower()}.json'
    args = ['setup.py', '--setup', '--yes', '--no-start', '--family', model['family'], '--model', model['model'],
            '--vision', 'no', '--data-dir', str(data), '--gguf-dir', str(data/model['gguf_dir']),
            '--experimental-speed-projection', 'off']
    if context:
        args += ['--context', str(context)]
    if backend:
        args += ['--backend', backend]
    sys.argv = args
    result = upstream.main()
    if result:
        return result, cfg_path
    # Paths in native_experts.txt are per-model shard names (no machine paths).
    state = read_json(STATE) if STATE.exists() else {}
    try:
        state['portable_data_dir'] = os.path.relpath(data, ROOT)
    except ValueError:
        state['portable_data_dir'] = str(data)
    state['portable_config'] = cfg_path.name
    state['portable_fingerprint'] = fingerprint(data)
    save_json(STATE, state)
    print(f'Portable model configured: {cfg_path.name}', flush=True)
    return 0, cfg_path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', nargs='?', default='start', choices=['start', 'configure', 'import', 'check'])
    ap.add_argument('--data-dir')
    ap.add_argument('--context', type=int)
    ap.add_argument('--backend', choices=['cuda', 'hip'])
    ap.add_argument('--port', type=int)
    ap.add_argument('--no-browser', action='store_true')
    args = ap.parse_args()
    os.chdir(ROOT)
    environment_check()
    if args.action == 'start':
        from portable_update import check_update
        threading.Thread(target=check_update, daemon=True).start()
    isolate_setup()
    if args.action == 'check':
        sys.argv = ['setup.py', '--check', '--data-dir', str(data_path(args.data_dir))]
        return upstream.main()
    if args.action == 'import' and not args.data_dir:
        args.data_dir = input('Path to the separately supplied Strata-data folder: ').strip().strip('"')
        if not args.data_dir:
            raise RuntimeError('No model folder was selected')
    data = data_path(args.data_dir)
    model_delivery(data)
    state = read_json(STATE) if STATE.exists() else {}
    cfg = ROOT/state.get('portable_config', 'missing.json')
    if args.action in ('configure', 'import') or not cfg.is_file() or state.get('portable_fingerprint') != fingerprint(data) or args.context or args.backend:
        result, cfg = configure(data, args.context, args.backend)
        if result or args.action in ('configure', 'import'):
            return result
    config = read_json(cfg)
    port = args.port or config.get('port', 8080)
    command = [sys.executable, '-X', 'utf8', '-u', str(ROOT/'serve/server.py'), '--engine', 'strata', '--config', str(cfg), '--port', str(port)]
    if not args.no_browser:
        command.append('--open')
    print(f'Starting Strata: http://127.0.0.1:{port}', flush=True)
    try:
        return subprocess.call(command, cwd=ROOT)
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError, ImportError) as e:
        print(f'\n[Strata] {e}', file=sys.stderr, flush=True)
        raise SystemExit(2)
