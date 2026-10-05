"""Create an isolated ComfyUI service profile using the portable runtime, without downloads."""
import argparse
from pathlib import Path
import os
import sys
import portable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--vision', choices=['no', 'gpu', 'cpu'], default='gpu')
    parser.add_argument('--context', type=int, default=32768)
    args = parser.parse_args()
    portable.environment_check()
    portable.isolate_setup()
    data = args.data_dir.resolve()
    model = portable.model_delivery(data)
    tag = portable.upstream.FAMILIES[model['family']]['tag'] + model['model']
    base = portable.ROOT/f'strata-{tag.lower()}.json'
    state = portable.read_json(portable.STATE) if portable.STATE.exists() else {}
    fp = portable.fingerprint(data)
    if not base.is_file() or not portable.same_machine(state.get('portable_fingerprint'), fp):
        result, base = portable.configure(data, args.context, vision='no')
        if result:
            return result
    config = portable.read_json(base)
    backend = config.get('backend', 'cuda')
    config.update(exe=str(portable.ROOT/('engine-hip' if backend == 'hip' else 'engine')/'strata.exe'),
                  cwd=str(portable.ROOT), lazy_load=True, host='127.0.0.1', parallel=1,
                  before_load=None, min_free_vram_mib=0, idle_unload_s=0,
                  log=str(args.output.with_suffix('.engine.log')), api_monitor=False)
    config.pop('api_key', None)
    config['lib_dirs'] = [str(p) for p in (portable.upstream.hip_lib_dirs(Path(config['exe']).parent) if backend == 'hip' else portable.upstream.cuda_lib_dirs())]
    argv = config['args']
    for flag in ('--batch', '--slots', '--batch-groups'):
        if flag in argv:
            index = argv.index(flag)
            del argv[index:index+2]
    if '--max-context' in argv:
        argv[argv.index('--max-context')+1] = str(args.context)
    if args.vision != 'no':
        portable.attach_vision(config, model, args.vision)
    else:
        config.pop('vision', None)
        config['args'] = [value for value in argv if value != '--vision']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    portable.save_json(args.output, config)
    print(f'Managed profile ready: {args.output}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
