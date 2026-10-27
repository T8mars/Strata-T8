"""Portable Windows launcher. Offline start/configuration never downloads a model."""
from __future__ import annotations
import argparse
import importlib.metadata
import json
import os
from pathlib import Path, PureWindowsPath
import platform
import re
import subprocess
import sys
import threading
import urllib.request

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
import setup as upstream
from tools.portable_io import atomic_json, atomic_bytes

STATE = ROOT / 'portable-settings.json'


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save_json(path, value):
    atomic_json(path, value)


def read_state():
    state = read_json(STATE) if STATE.exists() else {}
    if not isinstance(state, dict):
        raise RuntimeError('Invalid portable-settings.json: expected a JSON object')
    saved = state.get('portable_data_dir')
    if saved is not None and (not isinstance(saved, str) or not saved or any(ord(c) < 32 for c in saved)):
        raise RuntimeError('Invalid portable-settings.json: expected a model folder path')
    return state


def read_config(path):
    return validate_config(read_json(path))


def validate_config(config, *, require_args=False):
    if (not isinstance(config, dict) or ('args' in config and (not isinstance(config['args'], list)
            or not all(isinstance(arg, str) for arg in config['args'])))
            or (require_args and 'args' not in config)
            or (config.get('vision') is not None and not isinstance(config['vision'], dict))
            or ('exe' in config and (not isinstance(config['exe'], str) or not config['exe']))
            or (config.get('cwd') is not None and not isinstance(config['cwd'], str))
            or (config.get('lib_dirs') is not None and (not isinstance(config['lib_dirs'], list)
                or not all(isinstance(path, str) for path in config['lib_dirs'])))
            or ('port' in config and (type(config['port']) is not int or not 1 <= config['port'] <= 65535))
            or any(config.get(key) is not None and not isinstance(config[key], str) for key in ('host', 'api_key'))
            or (config.get('backend') is not None and (not isinstance(config['backend'], str)
                or config['backend'] not in ('cuda', 'hip')))):
        raise RuntimeError('Invalid run configuration: expected an object with engine arguments and vision settings')
    return config


def context_matches(config, context):
    values = []
    args = config.get('args', [])
    for index, arg in enumerate(args):
        if arg == '--max-context':
            values.append(args[index+1] if index+1 < len(args) else '')
        elif arg.startswith('--max-context='):
            values.append(arg.partition('=')[2])
    try:
        return bool(values) and all(int(value) == context for value in values)
    except ValueError:
        return False


def config_path(state):
    name = state.get('portable_config', 'missing.json')
    if not isinstance(name, str) or Path(name).name != name or '/' in name or '\\' in name or ':' in name:
        raise RuntimeError('Invalid portable-settings.json: config must be a file in the application folder')
    if name != 'missing.json' and not re.fullmatch(r'strata-[A-Za-z0-9_-]+\.json', name):
        raise RuntimeError('Invalid portable-settings.json: expected a strata model configuration')
    path = ROOT/name
    if not path.resolve().is_relative_to(ROOT.resolve()) or path.is_symlink():
        raise RuntimeError('Invalid portable-settings.json: linked configuration path')
    return path


def environment_check():
    if os.name == 'nt' and sys.getwindowsversion().build < 18362:
        raise RuntimeError('Windows 10 1903 (build 18362) or later is required for UTF-8 model paths')
    modules = ['numpy', 'jinja2', 'regex', 'yaml', 'tqdm', 'requests', 'PIL', 'psutil', 'av']
    for name in modules:
        try:
            __import__(name)
        except (ImportError, OSError) as error:
            from tools.runtime_diagnostics import import_failure
            raise RuntimeError(import_failure(ROOT, name, error)) from error
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
    upstream._urlopen = deny_download
    urllib.request.urlopen = deny_download
    original_run = upstream.run
    def offline_run(command, *args, **kwargs):
        # setup can fetch MTP in a child Python process when a delivery is corrupt or incompatible.
        # A child process does not inherit the urlopen guard; only explicit model preparation may fetch it.
        if any(str(part).replace('\\', '/').rsplit('/', 1)[-1].lower() == 'mtp_fetch.py' for part in command) and 'fetch' in command:
            raise RuntimeError('Model data is missing or incompatible. Run PREPARE-MODEL.bat explicitly, then import it again.')
        return original_run(command, *args, **kwargs)
    upstream.run = offline_run
    def hip_engine(url_base, gpu, updating=False):
        engine = bundled_hip_engine(gpu)
        upstream.hip_runtime_beside_exe(engine)
        return engine
    upstream.get_prebuilt_hip = hip_engine


def bundled_hip_engine(gpu):
    arch = gpu.get('arch', '')
    directories = ('engine-hip', 'engine-hip-rdna2', 'engine-hip-rdna3')
    for name in directories:
        manifest = ROOT/name/'BUILD.json'
        if manifest.is_file() and arch in read_json(manifest).get('archs', []):
            return ROOT/name
    if not (ROOT/'engine-hip-rdna2').exists():
        return ROOT/'engine-hip'  # legacy portable layout
    raise RuntimeError('No bundled AMD engine supports '+str(arch))


def configured_engine(config, system=None):
    if config.get('backend') == 'hip':
        cards = (system or {}).get('amd') or upstream.amd_gpus()
        index = config.get('gpu', 0)
        if isinstance(index, list): index = index[0] if index else 0
        card = next((g for g in cards if g.get('index') == index), cards[0] if cards else {})
        return bundled_hip_engine(card)
    return ROOT/('engine-cuda12' if upstream.config_toolkit(config) == 12 else 'engine')


def data_path(requested=None):
    if requested:
        return Path(requested).expanduser().resolve()
    if STATE.exists():
        saved = read_state().get('portable_data_dir')
        if saved:
            path = Path(saved)
            path = path if path.is_absolute() else ROOT/path
            if path.exists():
                return path.resolve()
    for path in [ROOT/'Strata-data', ROOT.parent/'Strata-data']:
        if (path/'portable-model.json').is_file():
            return path.resolve()
    return (ROOT/'Strata-data').resolve()


def model_api_id(model):
    name = model.get('model_name')
    if 'model_name' not in model:
        name = f"{upstream.FAMILIES[model['family']]['name']}-{model['model'].lower()}"
    if (not isinstance(name, str) or not name.strip() or name != name.strip() or len(name) > 200
            or any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise RuntimeError('Invalid portable-model.json: expected a nonempty model_name of at most 200 characters')
    return name


def model_delivery(data):
    if (data/'ds4.txt').is_file():
        from portable_deepseek import inspect_pack
        return inspect_pack(data)
    path = data/'portable-model.json'
    if not path.is_file():
        raise RuntimeError('Model delivery missing. Place the separately supplied Strata-data folder beside START-HERE.bat, or use IMPORT-MODEL.bat with its path. See README-PORTABLE.zh-CN.md.')
    model = read_json(path)
    if not isinstance(model, dict):
        raise RuntimeError('Invalid portable-model.json: expected a JSON object')
    family, size = model.get('family'), model.get('model')
    if not isinstance(family, str) or not isinstance(size, str) or family not in upstream.FAMILIES or size not in upstream.MODELS:
        raise RuntimeError('Invalid portable-model.json: unsupported family or model')
    if family not in upstream.MODELS[size].get('families', ('qwen', 'swift')):
        raise RuntimeError('Model size does not belong to the selected family')
    relative = model.get('gguf_dir')
    required = model.get('required_files', [])
    if not isinstance(relative, str) or not relative or not isinstance(required, list) or not all(isinstance(rel, str) and rel for rel in required):
        raise RuntimeError('Invalid portable-model.json: expected gguf_dir and a list of required_files')
    gguf = (data/relative).resolve()
    if not gguf.is_relative_to(data.resolve()) or not gguf.is_dir():
        raise RuntimeError('Invalid or missing model GGUF directory')
    # A published descriptor may outlive a later interrupted copy or preparation.
    # Catalog-backed deliveries retain the producer's exact shard-size contract;
    # legacy descriptors without a catalog still use their required_files list.
    if 'files' in model:
        entries = model['files']
        if not isinstance(entries, list) or not entries:
            raise RuntimeError('Invalid model descriptor shard catalog')
        names = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise RuntimeError('Invalid model descriptor shard catalog entry')
            name, expected = entry.get('file'), entry.get('size')
            if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.gguf', name, re.I)
                    or PureWindowsPath(name).is_reserved() or name.casefold() in names
                    or type(expected) is not int or expected <= 0):
                raise RuntimeError('Invalid model descriptor shard catalog filename or size')
            names.add(name.casefold())
            shard = (gguf/name).resolve()
            if not shard.is_relative_to(data.resolve()) or not shard.is_file() or shard.stat().st_size != expected:
                raise RuntimeError(f'Model delivery is incomplete: missing or incorrectly sized shard: {name}')
    for rel in required:
        path = (data/rel).resolve()
        if not path.is_relative_to(data.resolve()) or not path.is_file() or not path.stat().st_size:
            raise RuntimeError(f'Model delivery is incomplete: {rel}')
    for rel in ['mtp/rt/experts.bin', 'mtp/rt/dense.bin', 'mtp/rt/dense.txt']:
        component = (data/rel).resolve()
        if not component.is_relative_to(data.resolve()) or not component.is_file() or not component.stat().st_size:
            raise RuntimeError(f'Model delivery is incomplete: missing or empty auxiliary MTP layer: {rel}')
    model_api_id(model)
    return model


def fingerprint(data):
    from portable_version import metadata
    meta = metadata(ROOT)
    return {'app': str(ROOT), 'data': str(data), 'gpu': upstream.gpus(), 'amd': upstream.amd_gpus(),
            'ram': round(upstream.ram_gb()), 'cpu': platform.processor(), 'version': meta['version'],
            'edition': meta.get('edition', 'Portable-NoModels')}


def same_machine(previous, current):
    # Program/edition updates are not hardware/model changes and must not rerun setup defaults.
    return isinstance(previous, dict) and {k: v for k, v in previous.items() if k not in ('version', 'edition')} == {k: v for k, v in current.items() if k not in ('version', 'edition')}


def refresh_updated_config(cfg_path, state, current, *, enable_vision=False, vision_tokens=None):
    previous = {path: path.read_bytes() if path.exists() else None for path in (cfg_path, STATE)}
    original_state = dict(state)
    completed = False
    try:
        _refresh_updated_config(cfg_path, state, current, enable_vision=enable_vision, vision_tokens=vision_tokens)
        completed = True
    finally:
        if not completed:
            state.clear()
            state.update(original_state)
            failed = []
            for path, content in previous.items():
                try:
                    if content is None:
                        path.unlink(missing_ok=True)
                    else:
                        atomic_bytes(path, content)
                except OSError:
                    failed.append(path.name)
            if failed:
                raise RuntimeError('Configuration rollback incomplete; could not restore: ' + ', '.join(failed))


def _refresh_updated_config(cfg_path, state, current, *, enable_vision=False, vision_tokens=None):
    config = read_config(cfg_path)
    if config.get('arch') == 'deepseek41':
        engine = ROOT/'engine-dsv41'
        config.update(exe=str(engine/'strata-dsv41.exe'),cwd=str(ROOT),lib_dirs=[str(engine)])
        config.setdefault('fit_max_tokens',False)
        save_json(cfg_path,config)
        state['portable_fingerprint'] = current
        save_json(STATE,state)
        return
    engine = configured_engine(config, current)
    config['exe'] = str(engine/'strata.exe')
    config['cwd'] = str(ROOT)
    config['lib_dirs'] = [str(p) for p in (upstream.hip_lib_dirs(engine) if config.get('backend') == 'hip' else [engine, *upstream.cuda_lib_dirs(upstream.config_toolkit(config))])]
    config.setdefault('fit_max_tokens', False)
    if config.get('vision') and config['vision'].get('bundled'):
        from portable_version import metadata
        if metadata(ROOT).get('edition') == 'Portable-NoModels':
            config.pop('vision')
            config['args'] = [arg for arg in config.get('args', []) if arg not in ('--vision', '--native-video')]
            print('NoModels edition: bundled vision is disabled. Run INSTALL-VISION.bat to restore it.', flush=True)
        else:
            config['vision']['exe'] = str(engine/'strata-vision.exe')
            config['vision']['mmproj'] = str(ROOT/'vision/weights'/read_json(ROOT/'vision/catalog.json')['file'])
    # Apply upstream compatibility migrations, retaining server settings and custom engine arguments.
    config = upstream.upgrade_config(cfg_path, config)
    if enable_vision and not config.get('vision') and config.get('backend') != 'hip':
        model = model_delivery(Path(current['data']))
        if model['family'] == 'qwen':
            config = attach_vision(config, model, automatic_vision_mode(config, model, system=current), vision_tokens)
    save_json(cfg_path, config)
    state['portable_fingerprint'] = current
    save_json(STATE, state)


def automatic_vision_mode(config, model, previous=None, system=None):
    """Apply upstream's small-card recommendation only when no saved vision choice exists."""
    if config.get('backend') == 'hip' or model['family'] != 'qwen':
        return 'no'
    old = (previous or {}).get('vision')
    if old:
        return 'gpu' if old.get('gpu', True) else 'cpu'
    cards = [card for card in (system or {}).get('gpu', []) if isinstance(card, dict)]
    selected = config.get('gpu', 0)
    if isinstance(selected, list):
        selected = selected[0] if selected else 0
    gpu = next((card for card in cards if card.get('index', 0) == selected), cards[0] if cards else {})
    return upstream.flash_vision_encoder('yes', gpu)


def attach_vision(config, model, mode='gpu', tokens=None):
    """Use only the shipped encoder. No setup downloads, absolute paths or keys in templates."""
    from prepare_portable_vision import verify, catalog
    if model['family'] != catalog(ROOT)['family'] or config.get('backend') == 'hip':
        raise RuntimeError('This VisionReady encoder supports the Qwen NVIDIA profile. Windows AMD vision is not verified.')
    weight = verify(ROOT)
    helper = configured_engine(config)/'strata-vision.exe'
    if not helper.is_file():
        raise RuntimeError('Bundled vision helper missing')
    args = config['args']
    if '--native' not in args:
        raise RuntimeError('Vision requires the imported main-model GGUF shard')
    template = read_json(ROOT/'vision/profile-template.json')['vision']
    old = config.get('vision') or {}
    config['vision'] = {**template, **old, 'exe': str(helper), 'mmproj': str(weight),
                        'model': args[args.index('--native')+1], 'gpu': mode == 'gpu', 'bundled': True}
    config['vision']['max_tokens'] = tokens or (old.get('max_tokens') if bool(old.get('gpu')) == (mode == 'gpu') else None) or (768 if mode == 'gpu' else 300)
    if mode == 'cpu':
        config['vision']['threads'] = old.get('threads') or max(1, (os.cpu_count() or 8)//2)
    if '--vision' not in args:
        args.append('--vision')
    if '--vram-reserve-mib' not in args:
        args += ['--vram-reserve-mib', str(upstream.VISION[mode]['reserve_mib'])]
    return config


def set_native_video(config, enabled):
    if enabled is None:
        return config
    if enabled:
        if config.get('backend') == 'hip' or not isinstance(config.get('vision'), dict):
            raise RuntimeError('Native video requires the NVIDIA engine and an enabled vision encoder')
        build = read_json(configured_engine(config)/'BUILD.json')
        if (build.get('capabilities') or {}).get('native_video') is not True:
            raise RuntimeError('This package does not contain the T8 native-video engine; update the package first')
    if isinstance(config.get('vision'), dict):
        config['vision']['native_video'] = bool(enabled)
    if not enabled:
        config['args'] = [arg for arg in config['args'] if arg != '--native-video']
    return config


def configure(data, context=None, backend=None, vision=None, vision_tokens=None, port=None, native_video=None, speed=None):
    model = model_delivery(data)
    if model['family'] == 'deepseek41':
        from portable_deepseek import configure as configure_deepseek
        return configure_deepseek(sys.modules[__name__],data,context,backend,vision,port,native_video,speed)
    if speed is not None:
        raise RuntimeError('Full/Faster is a DeepSeek setting; it does not apply to Qwen')
    tag = upstream.FAMILIES[model['family']]['tag'] + model['model']
    cfg_path = ROOT/f'strata-{tag.lower()}.json'
    script = ROOT/f'run-{tag.lower()}{".bat" if upstream.WIN else ".sh"}'
    if any(path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()) for path in (cfg_path, script)):
        raise RuntimeError('Linked configuration path; use a regular file in the application folder')
    previous = {path: path.read_bytes() if path.exists() else None for path in (cfg_path, STATE, script)}
    script_mode = script.stat().st_mode if script.exists() else None
    old_config = read_config(cfg_path) if cfg_path.exists() else {}
    if not isinstance(old_config, dict):
        raise RuntimeError('Invalid run configuration: expected a JSON object')
    # Validate state before setup can replace either configuration file.
    original_state = read_state()
    args = ['setup.py', '--setup', '--yes', '--no-start', '--family', model['family'], '--model', model['model'],
            '--vision', 'no', '--data-dir', str(data), '--gguf-dir', str(data/model['gguf_dir']),
            '--experimental-speed-projection', 'off']
    if context:
        args += ['--context', str(context)]
    if backend:
        args += ['--backend', backend]
    if port is not None:
        args += ['--port', str(port)]
    sys.argv = args
    def restore():
        failed = []
        for path, content in previous.items():
            try:
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_bytes(path, content)
                    if path == script and script_mode is not None and not upstream.WIN:
                        path.chmod(script_mode)
            except OSError:
                failed.append(path.name)
        if failed:
            raise RuntimeError('Configuration rollback incomplete; could not restore: ' + ', '.join(failed))
    completed = False
    try:
        result = upstream.main()
        if result:
            return result, cfg_path
        if not cfg_path.is_file():
            raise RuntimeError('Setup did not create the run configuration')
        current_fingerprint = fingerprint(data)
        unchanged = previous[cfg_path] is not None and cfg_path.read_bytes() == previous[cfg_path]
        if unchanged:
            if not same_machine(original_state.get('portable_fingerprint'), current_fingerprint):
                raise RuntimeError('Setup did not write a configuration for the selected model and PC')
            if ((context is not None and not context_matches(old_config, context))
                    or (backend is not None and (old_config.get('backend') or 'cuda') != backend)
                    or (port is not None and old_config.get('port', 8080) != port)):
                raise RuntimeError('Setup did not apply the requested configuration settings')
        if cfg_path.is_file():
            generated = validate_config(read_config(cfg_path), require_args=True)
            generated.setdefault('fit_max_tokens', False)
            if 'model_name' in model:
                generated['model_name'] = model_api_id(model)
            for key in ('host', 'api_key', 'port'):
                if key in old_config and (key != 'port' or port is None):
                    generated[key] = old_config[key]
            if vision == 'auto':
                vision = automatic_vision_mode(generated, model, old_config, current_fingerprint)
            if vision and vision != 'no':
                if isinstance(old_config.get('vision'), dict):
                    generated['vision'] = old_config['vision']
                generated = attach_vision(generated, model, vision, vision_tokens)
            save_json(cfg_path, generated)
            # Setup wrote this companion before portable restored the user's port.
            # Keep it in the same transaction and regenerate it from the final config.
            if script.is_file():
                upstream.write_run_script(tag, cfg_path, generated.get('port', 8080),
                                          generated.get('open_browser') is not False)
        # Paths in native_experts.txt are per-model shard names (no machine paths).
        state = {**original_state, **read_state()}
        try:
            state['portable_data_dir'] = os.path.relpath(data, ROOT)
        except ValueError:
            state['portable_data_dir'] = str(data)
        state['portable_config'] = cfg_path.name
        state['portable_fingerprint'] = current_fingerprint
        save_json(STATE, state)
        if native_video is not None:
            generated = set_native_video(read_config(cfg_path), native_video)
            save_json(cfg_path, generated)
        completed = True
    finally:
        if not completed:
            restore()
    print(f'Portable model configured: {cfg_path.name}', flush=True)
    return 0, cfg_path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', nargs='?', default='start', choices=['start', 'configure', 'import', 'check', 'settings'])
    ap.add_argument('--data-dir')
    ap.add_argument('--context', type=int)
    ap.add_argument('--speed', choices=['full', 'faster'], help='DeepSeek only: six experts (full) or five (faster)')
    ap.add_argument('--backend', choices=['cuda', 'hip'])
    ap.add_argument('--port', type=int)
    ap.add_argument('--vision', choices=['no', 'gpu', 'cpu'], help='use the bundled vision encoder (offline)')
    ap.add_argument('--vision-tokens', type=int)
    ap.add_argument('--native-video', action=argparse.BooleanOptionalAction, default=None,
                    help='opt in to NVIDIA native temporal video encoding (--no-native-video disables it)')
    ap.add_argument('--no-browser', action='store_true')
    ap.add_argument('--ui-port', type=int, help='local model-settings page port')
    args = ap.parse_args()
    if args.vision_tokens is not None and args.vision_tokens < 1:
        ap.error('--vision-tokens must be positive')
    if args.port is not None and not 1 <= args.port <= 65535:
        ap.error('--port must be between 1 and 65535')
    if args.context is not None and args.context < 1:
        ap.error('--context must be positive')
    if args.ui_port is not None and not 1 <= args.ui_port <= 65535:
        ap.error('--ui-port must be between 1 and 65535')
    os.chdir(ROOT)
    environment_check()
    if args.action == 'start':
        from portable_update import check_update
        threading.Thread(target=check_update, daemon=True).start()
    # Browser startup must work before a recipient has supplied a model. Import,
    # checks and explicitly headless startup keep their existing offline path.
    if args.action == 'settings' or (args.action == 'start' and not args.no_browser):
        from serve.portable_ui import run
        return run(args)
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
    state = read_state()
    cfg = config_path(state)
    current = fingerprint(data)
    previous = state.get('portable_fingerprint')
    if args.action in ('configure', 'import') or not cfg.is_file() or not same_machine(previous, current) or args.context or args.backend or args.speed:
        vision = args.vision
        if vision is None and (ROOT/'vision/catalog.json').is_file() and (ROOT/'vision/weights'/read_json(ROOT/'vision/catalog.json')['file']).is_file():
            vision = 'auto'
        if args.speed is not None:
            result, cfg = configure(data, args.context, args.backend, vision, args.vision_tokens, args.port,
                                    native_video=args.native_video, speed=args.speed)
        elif args.native_video is None:
            result, cfg = configure(data, args.context, args.backend, vision, args.vision_tokens, args.port)
        else:
            result, cfg = configure(data, args.context, args.backend, vision, args.vision_tokens, args.port,
                                    native_video=args.native_video)
        if result or args.action in ('configure', 'import'):
            return result
    elif previous.get('version') != current['version'] or previous.get('edition') != current.get('edition'):
        enable = (args.vision is None and previous.get('edition') == 'Portable-NoModels'
                  and current.get('edition') == 'VisionReady-NoMainModel')
        if enable:
            refresh_updated_config(cfg, state, current, enable_vision=True, vision_tokens=args.vision_tokens)
        else:
            refresh_updated_config(cfg, state, current)
    config = read_config(cfg)
    if args.vision is not None or args.vision_tokens is not None:
        if args.vision == 'no':
            config.pop('vision', None)
            if config.get('arch') == 'deepseek41':
                from portable_deepseek import set_option
                config['args'] = set_option(config['args'], '--vision', 'off')
            else:
                config['args'] = [arg for arg in config['args'] if arg not in ('--vision', '--native-video')]
        else:
            mode = args.vision or ('gpu' if (config.get('vision') or {}).get('gpu') else 'cpu')
            config = attach_vision(config, model_delivery(data), mode, args.vision_tokens)
    if args.native_video is not None:
        config = set_native_video(config, args.native_video)
    if args.vision is not None or args.vision_tokens is not None or args.native_video is not None:
        save_json(cfg, config)
    from serve import api_access
    if 'portable_api' in read_state():
        config = api_access.apply(config, api_access.load(sys.modules[__name__], config))
        save_json(cfg, config)
    if config.get('host', '127.0.0.1') not in ('127.0.0.1', 'localhost', '::1') and not config.get('api_key'):
        raise RuntimeError('LAN API requires an API Key. Set it in the model settings page first.')
    port = args.port or config.get('port', 8080)
    command = [sys.executable, '-X', 'utf8', '-u', str(ROOT/'serve/server.py'), '--engine', 'strata', '--config', str(cfg), '--port', str(port)]
    if not args.no_browser:
        command.append('--open')
    print(f'Starting Strata: http://127.0.0.1:{port}', flush=True)
    try:
        if 'portable_api' in read_state():
            environment = dict(os.environ)
            environment.pop('STRATA_API_KEY', None)
            return subprocess.call(command, cwd=ROOT, env=environment)
        return subprocess.call(command, cwd=ROOT)
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError, ImportError) as e:
        print(f'\n[Strata] {e}', file=sys.stderr, flush=True)
        raise SystemExit(2)
