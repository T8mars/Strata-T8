"""Local ASR profile. Checking availability is cheap and never hashes weights."""
from __future__ import annotations
import json
import os
from pathlib import Path
from tools.portable_io import atomic_json

DEFAULTS = {'model_dir':'', 'vad_dir':'', 'quantization':'Q4_K_M', 'device':'cpu',
            'language':'Auto', 'context':'', 'audio_track':0, 'channel':'mean', 'offset_ms':0}
LANGUAGES = ('Auto','Chinese','English','Cantonese','Japanese','Korean','German','French','Russian','Portuguese','Spanish','Italian')
MODELS = {
 'Q4_K_M': ('Confucius4-R2T2-Q4_K_M.gguf',1107404736,'fa3cb46c8c3a66a58812b9098ba6e96a0266d4e8c9b3cf5ba34432fd2f9f6466'),
 'Q8_0': ('Confucius4-R2T2-Q8_0.gguf',1834422208,'151097e43957a19984ea7de66e8144ce69b95039eb31c93da4f58db367e455c3')}
PROJECTOR = ('mmproj-Confucius4-R2T2-Q8_0.gguf',348336544,'8dc2c67e6a0484114928142d098db7ad94ae9f34c78948ef9d37a9678418cb65')


def validate(value):
    if not isinstance(value, dict) or set(value)-set(DEFAULTS):
        raise ValueError('Invalid ASR settings')
    value = {**DEFAULTS, **value}
    for key in ('model_dir','vad_dir','context'):
        if (not isinstance(value[key],str) or len(value[key].encode('utf-8')) > (8192 if key=='context' else 4096)
                or any(ord(c)<32 and (key!='context' or c not in '\n\r\t') for c in value[key])):
            raise ValueError('Invalid ASR '+key)
    if (any(not isinstance(value[key],str) for key in ('quantization','device','language','channel')) or
            value['quantization'] not in MODELS or value['device'] not in ('cpu','cuda') or value['language'] not in LANGUAGES):
        raise ValueError('Invalid ASR model, device or language')
    if type(value['audio_track']) is not int or not 0 <= value['audio_track'] < 32 or value['channel'] not in ('mean','left','right'):
        raise ValueError('Invalid ASR audio track/channel')
    if type(value['offset_ms']) is not int or abs(value['offset_ms']) > 86400000:
        raise ValueError('Invalid ASR manual timestamp offset')
    return value


def load(root):
    path = Path(root)/'asr-settings.json'
    return validate(json.loads(path.read_text(encoding='utf-8-sig'))) if path.is_file() else dict(DEFAULTS)


def save(root, value):
    value = validate(value)
    atomic_json(Path(root)/'asr-settings.json', value)
    return value


def runtime_paths(root, device='cpu'):
    root = Path(root)
    base = root/'runtime/asr'
    python = base/('python/python.exe' if os.name=='nt' else 'python/bin/python3')
    return python, base/('native-'+device)


def resolve_paths(root, settings):
    """Use the application as the base without rewriting the saved profile."""
    resolved = dict(settings)
    root = Path(root).absolute()
    for name in ('model_dir', 'vad_dir'):
        if resolved[name]:
            path = Path(resolved[name]).expanduser()
            resolved[name] = str(path if path.is_absolute() else root/path)
    return resolved


def vad_path(root, settings):
    settings = resolve_paths(root, settings)
    if settings['vad_dir']:
        return Path(settings['vad_dir'])
    return Path(settings['model_dir'])/'FireRedVAD-ONNX' if settings['model_dir'] else Path(root)/'models/FireRedVAD-ONNX'


def availability(root, settings=None):
    settings = resolve_paths(root, settings or load(root))
    python, build = runtime_paths(root, settings['device'])
    directory = Path(settings['model_dir']) if settings['model_dir'] else None
    vad = vad_path(root, settings)
    missing = []
    if not python.is_file(): missing.append('ASR Python runtime')
    if len(list((build/'python').glob('qwen3asr_native*.pyd'))) != 1:
        missing.append('ASR native '+settings['device'])
    for name in ('ggml.dll','ggml-base.dll','llama.dll','mtmd.dll'):
        if not (build/'lib'/name).is_file(): missing.append(name)
    if directory is None:
        missing.append('语音模型目录')
    else:
        for name,size,_ in (MODELS[settings['quantization']],PROJECTOR):
            path = directory/name
            if not path.is_file() or path.stat().st_size != size: missing.append(name)
    for name in ('fireredvad_stream_vad_with_cache.onnx','cmvn.ark'):
        if not (vad/name).is_file(): missing.append(name)
    return {'supported':not missing,'missing':missing,'model':MODELS[settings['quantization']][0],
            'device':settings['device'],'timing_precision':'segment', 'default_enabled':False,
            'expected_device_guard':True}
