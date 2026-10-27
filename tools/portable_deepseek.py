"""Local DeepSeek pack discovery and portable configuration; never downloads weights."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re


def small_text(path, limit=32*1024**2):
    if not path.is_file() or not 0 < path.stat().st_size <= limit:
        raise RuntimeError('Missing or oversized DeepSeek metadata: '+path.name)
    return path.read_text(encoding='utf-8-sig')


def inspect_pack(directory):
    """Check every indexed tensor's byte range without reading multi-GB weights."""
    directory = Path(directory).resolve()
    geometry = {}
    for line in small_text(directory/'ds4.txt', 65536).splitlines():
        if line.strip():
            key, value = line.split(None, 1)
            if key in geometry:
                raise RuntimeError('Duplicate DeepSeek geometry field: '+key)
            geometry[key] = value
    expected = {'hidden_size':'5120', 'num_hidden_layers':'40', 'vocab_size':'129280'}
    if any(geometry.get(k) != v for k, v in expected.items()):
        raise RuntimeError('This pack is not DeepSeek-V4.1-Flash-1017')
    base = Path(geometry.get('model_dir', str(directory))).expanduser()
    base = (base if base.is_absolute() else directory/base).resolve()
    files, tensors = {}, set()
    for line in small_text(directory/'tensors.txt').splitlines():
        if line.startswith('file '):
            _, index, name = line.split(None, 2)
            if not index.isdecimal() or int(index) in files or any(ord(c)<32 for c in name):
                raise RuntimeError('Invalid DeepSeek file index')
            path = (base/name).resolve()
            if not path.is_file() or not path.stat().st_size:
                raise RuntimeError('DeepSeek model file is missing: '+str(path))
            files[int(index)] = (path, path.stat().st_size)
        elif line.startswith('t '):
            fields = line.split()
            if len(fields) < 7:
                raise RuntimeError('Invalid DeepSeek tensor record')
            _, name, dtype, index, offset, count, *dims = fields
            if not all(v.isdecimal() for v in [index,offset,count,*dims]):
                raise RuntimeError('Invalid DeepSeek tensor byte range')
            index, offset, count = int(index), int(offset), int(count)
            if (name in tensors or index not in files or not count or min(map(int,dims))<1
                    or offset+count>files[index][1]):
                raise RuntimeError('Truncated or duplicate DeepSeek tensor: '+name)
            tensors.add(name)
        elif line.strip():
            raise RuntimeError('Unknown DeepSeek index record')
    if not files or not tensors or 'embed.weight' not in tensors:
        raise RuntimeError('Incomplete DeepSeek tensor index')
    for name in ('engram.bin', 'tokenizer/vocab.json', 'tokenizer/merges.txt',
                 'tokenizer/token_type.json', 'tokenizer/tokenizer.json', 'tokenizer/chat_template.jinja'):
        path = directory/name
        if not path.is_file() or not path.stat().st_size:
            raise RuntimeError('Missing DeepSeek auxiliary file: '+name)
    return {'family':'deepseek41', 'model':'LOCAL', 'model_name':'deepseek-v4.1-flash-1017',
            'pack_dir':str(directory), 'text_only':True,
            'files':[{'file':str(p), 'size':n} for p,n in files.values()]}


def check_hardware(portable, cfg):
    """Fail before replacing settings when the bundled CUDA engine cannot run."""
    args = cfg.get('args', [])
    index = 0
    for i, arg in enumerate(args):
        if arg in ('--gpu', '--gpus') and i + 1 < len(args): index = int(args[i + 1].split(',')[0])
        elif arg.startswith(('--gpu=', '--gpus=')): index = int(arg.split('=', 1)[1].split(',')[0])
    card = next((g for g in portable.upstream.gpus() if g['index'] == index), None)
    if card is None:
        raise RuntimeError('DeepSeek needs an NVIDIA GPU; no selected NVIDIA card was detected')
    build = portable.read_json(portable.ROOT/'engine-dsv41/BUILD.json')
    arch = int(card['arch']); supported = portable.upstream.arch_numbers(build.get('archs'))
    compatible = any(arch // 10 == base // 10 and arch >= base for base in supported)
    compatible |= bool(supported and build.get('ptx') and arch >= max(supported))
    if not compatible or portable.upstream.driver_major(card) < 580:
        raise RuntimeError('This DeepSeek engine requires a compatible CUDA 13 GPU and NVIDIA driver 580 or later')
    if card.get('vram_gb', 0) < 11.5 or portable.upstream.ram_gb() < 46:
        raise RuntimeError('DeepSeek requires at least 12 GB VRAM and 46 GB system RAM; this native pack also needs fast SSD storage')


def set_option(args, flag, value):
    """Replace a valued DeepSeek flag without discarding a following option."""
    kept = []
    skip = False
    for arg in args:
        if skip:
            skip = False
            if not arg.startswith('--'):
                continue
        if arg == flag:
            skip = True
        elif not arg.startswith(flag+'='):
            kept.append(arg)
    return kept+[flag, str(value)]


def configure(portable, directory, context=None, backend=None, vision=None, port=None, native_video=None, speed=None):
    model = inspect_pack(directory)
    if backend not in (None, 'auto', 'cuda'):
        raise RuntimeError('DeepSeek currently requires the NVIDIA CUDA engine in this portable edition')
    if vision not in (None, 'no', 'auto') or native_video:
        raise RuntimeError('This DeepSeek pack is text-only; select Qwen for images and video')
    if speed not in (None, 'full', 'faster'):
        raise RuntimeError('DeepSeek speed must be full or faster')
    identity = hashlib.sha256(os.path.normcase(str(Path(directory).resolve())).encode('utf-8')).hexdigest()[:12]
    cfg_path = portable.ROOT/f'strata-deepseek41-{identity}.json'
    if cfg_path.is_symlink():
        raise RuntimeError('Linked DeepSeek configuration is not supported')
    engine = portable.ROOT/'engine-dsv41/strata-dsv41.exe'
    if not engine.is_file():
        raise RuntimeError('The DeepSeek engine is missing; install the complete portable environment')
    previous = {p:p.read_bytes() if p.exists() else None for p in (cfg_path, portable.STATE)}
    cfg = portable.read_config(cfg_path) if cfg_path.exists() else {}
    check_hardware(portable, cfg)
    args = list(cfg.get('args', []))
    if context is None:
        context = 8192
        for i, arg in enumerate(args):
            if arg == '--max-context':
                try:
                    context = int(args[i+1])
                except (ValueError, IndexError) as error:
                    raise RuntimeError('Invalid saved DeepSeek context; set --context explicitly') from error
            elif arg.startswith('--max-context='):
                try:
                    context = int(arg.partition('=')[2])
                except ValueError as error:
                    raise RuntimeError('Invalid saved DeepSeek context; set --context explicitly') from error
    # A first selection may inherit Qwen's 32K form value. Keep saved DS choices otherwise.
    if type(context) is not int or context < 512:
        raise RuntimeError('DeepSeek context must be between 512 and 8192')
    context = min(context, 8192)
    def option(flag, value, only_missing=False):
        nonlocal args
        if only_missing and any(a==flag or a.startswith(flag+'=') for a in args):
            return
        args = set_option(args, flag, value)
    option('--pack', model['pack_dir']); option('--max-context', context)
    for flag,value in (('--eos',1),('--experts',6),('--dspark','none')):
        option(flag,value,only_missing=True)
    option('--vision','off')
    if speed is not None:
        option('--experts', 5 if speed == 'faster' else 6)
    cfg.update(exe=str(engine),args=args,cwd=str(portable.ROOT),tokenizer=str(Path(model['pack_dir'])/'tokenizer'),
               arch='deepseek41',backend='cuda',model_name=model['model_name'],lib_dirs=[str(engine.parent)])
    cfg.setdefault('engine_ready_s',3600)
    cfg.setdefault('fit_max_tokens',False)
    cfg['port'] = port if port is not None else cfg.get('port',8080)
    cfg.pop('vision',None)
    state = portable.read_state()
    try:
        portable.save_json(cfg_path,portable.validate_config(cfg,require_args=True))
        state.update(portable_data_dir=model['pack_dir'],portable_config=cfg_path.name,
                     portable_fingerprint=portable.fingerprint(directory))
        portable.save_json(portable.STATE,state)
    except BaseException:
        for path, content in previous.items():
            if content is None: path.unlink(missing_ok=True)
            else: portable.atomic_bytes(path,content)
        raise
    return 0,cfg_path
