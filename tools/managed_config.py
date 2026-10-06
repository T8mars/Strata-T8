"""Create an isolated ComfyUI service profile using the portable runtime, without downloads."""
import argparse
import copy
from pathlib import Path
import os
import sys
import portable


def fresh_config(data, model, context, output):
    """Capture setup's engine selection without writing the web install's config, state or launch script."""
    setup = portable.upstream
    captured = []
    original = {name: getattr(setup, name) for name in ('write_setup_config', 'write_run_script', 'save_settings')}
    argv = sys.argv
    def capture(path, config, source=None):
        captured.append(copy.deepcopy(config))
    try:
        setup.write_setup_config = capture
        setup.write_run_script = lambda *args: output.with_suffix('.bat')
        setup.save_settings = lambda *args: None
        sys.argv = ['setup.py', '--setup', '--yes', '--no-start', '--family', model['family'], '--model', model['model'],
                    '--context', str(context), '--vision', 'no', '--data-dir', str(data),
                    '--gguf-dir', str(data/model['gguf_dir']), '--experimental-speed-projection', 'off']
        result = setup.main()
        if result:
            raise RuntimeError(f'Managed model configuration failed (exit {result})')
        if len(captured) != 1:
            raise RuntimeError('Managed model configuration did not produce one engine profile')
        return portable.validate_config(captured[0], require_args=True)
    finally:
        sys.argv = argv
        for name, value in original.items(): setattr(setup, name, value)


def single_request_args(argv, context):
    stripped = []
    controlled = ('--batch', '--slots', '--batch-groups', '--max-context')
    index = 0
    while index < len(argv):
        value = argv[index]
        if value in controlled:
            if index+1 >= len(argv) or argv[index+1].startswith('--'):
                raise RuntimeError(f'Missing engine argument value: {value}')
            index += 2
        elif any(value.startswith(flag+'=') for flag in controlled):
            index += 1
        else:
            stripped.append(value)
            index += 1
    return stripped + ['--max-context', str(context)]


def protected_output(output, data):
    root = portable.ROOT.resolve()
    if output == root or output.is_dir() or output == portable.STATE.resolve() or output.is_relative_to(data):
        return True
    if not output.is_relative_to(root):
        return False
    relative = output.relative_to(root)
    name = relative.parts[0].lower()
    if len(relative.parts) > 1 and name in ('engine', 'engine-hip', 'runtime', 'serve', 'tools', 'vision',
                                           'data', 'third_party', 'src', 'include', 'docs', 'tests', 'bench', '.github', '.git'):
        return True
    if len(relative.parts) == 1 and (name.startswith('strata-') or name in
            ('meta.json', 'features.json', 'model-sources.json', 'portable-settings.json', 'package-manifest.json')
            or (output.suffix.lower() != '.json' and output.is_file())):
        return True
    manifest = root/'PACKAGE-MANIFEST.json'
    if manifest.is_file():
        try:
            entries = portable.read_json(manifest).get('files', [])
            if isinstance(entries, list) and any(isinstance(entry, dict) and isinstance(entry.get('path'), str)
                    and (root/entry['path']).resolve() == output for entry in entries):
                return True
        except (OSError, ValueError, AttributeError):
            pass
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--vision', choices=['no', 'gpu', 'cpu'], default='gpu')
    parser.add_argument('--context', type=int, default=32768)
    args = parser.parse_args()
    if not 1024 <= args.context <= 131072:
        parser.error('--context must be between 1024 and 131072')
    data = args.data_dir.expanduser().resolve()
    args.output = args.output.expanduser().resolve()
    if protected_output(args.output, data):
        raise RuntimeError('Managed profile output must be separate from application files, web configuration, settings and model files')
    portable.environment_check()
    portable.isolate_setup()
    model = portable.model_delivery(data)
    tag = portable.upstream.FAMILIES[model['family']]['tag'] + model['model']
    base = portable.ROOT/f'strata-{tag.lower()}.json'
    state = portable.read_state()
    fp = portable.fingerprint(data)
    if not base.is_file() or not portable.same_machine(state.get('portable_fingerprint'), fp):
        config = fresh_config(data, model, args.context, args.output)
    else:
        config = portable.read_config(base)
    backend = config.get('backend', 'cuda')
    config.update(exe=str(portable.ROOT/('engine-hip' if backend == 'hip' else 'engine')/'strata.exe'),
                  cwd=str(portable.ROOT), lazy_load=True, host='127.0.0.1', parallel=1,
                  before_load=None, min_free_vram_mib=0, idle_unload_s=0,
                  log=str(args.output.with_suffix('.engine.log')), api_monitor=False)
    config.pop('api_key', None)
    config['lib_dirs'] = [str(p) for p in (portable.upstream.hip_lib_dirs(Path(config['exe']).parent) if backend == 'hip' else portable.upstream.cuda_lib_dirs())]
    config['args'] = single_request_args(config['args'], args.context)
    if args.vision != 'no':
        portable.attach_vision(config, model, args.vision)
    else:
        config.pop('vision', None)
        config['args'] = [value for value in config['args'] if value != '--vision']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    portable.save_json(args.output, config)
    print(f'Managed profile ready: {args.output}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
