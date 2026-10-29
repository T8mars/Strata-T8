"""Offline startup diagnosis; no model, credentials, downloads or engine startup."""
from __future__ import annotations
import argparse
import ctypes
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import traceback

MODULES = ('numpy', 'jinja2', 'regex', 'yaml', 'tqdm', 'requests', 'PIL', 'psutil', 'av')
ROOT = Path(__file__).resolve().parents[1]


def machine_resources():
    result = {}
    if os.name == 'nt':
        class Memory(ctypes.Structure):
            _fields_ = [('length',ctypes.c_ulong),('load',ctypes.c_ulong),
                *[(name,ctypes.c_ulonglong) for name in ('total','available','commit_limit','commit_available',
                    'virtual_total','virtual_available','extended')]]
        memory = Memory(); memory.length = ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            result['memory_gib'] = {name:round(getattr(memory,name)/2**30,2)
                for name in ('total','available','commit_limit','commit_available')}
    smi = shutil.which('nvidia-smi')
    if smi:
        try:
            child = subprocess.run([smi,'--query-gpu=index,name,memory.total,memory.free,utilization.gpu,driver_version',
                '--format=csv,noheader,nounits'],capture_output=True,encoding='utf-8',errors='replace',timeout=5)
            result['nvidia_smi'] = {'returncode':child.returncode,'detail':(child.stdout or child.stderr)[-8000:]}
        except (OSError,subprocess.TimeoutExpired) as error:
            result['nvidia_smi'] = {'error':str(error)}
    return result


def selected_configuration(root):
    """Only explicit diagnostic fields; never serialize credentials or arbitrary engine arguments."""
    root = Path(root).resolve()
    result = {}
    meta = root/'meta.json'
    if meta.is_file() and meta.stat().st_size < 1024*1024:
        value = json.loads(meta.read_text('utf-8-sig'))
        result['package'] = {key:value.get(key) for key in ('version','edition','upstream_version')}
    entry = root/'START-HERE.bat'
    result['portable_launcher'] = entry.is_file() and 'portable.py' in entry.read_text('utf-8-sig')
    result['package_manifest'] = (root/'PACKAGE-MANIFEST.json').is_file()
    result['source_checkout'] = (root/'.git').exists()
    state_file = root/'portable-settings.json'
    if state_file.is_file() and state_file.stat().st_size < 1024*1024:
        state = json.loads(state_file.read_text('utf-8-sig'))
        name = state.get('portable_config','')
        config = root/name if isinstance(name,str) else root
        if (isinstance(name,str) and name.startswith('strata-') and config.suffix == '.json'
                and config.resolve().parent == root and not config.is_symlink() and config.is_file()
                and config.stat().st_size < 1024*1024):
            value = json.loads(config.read_text('utf-8-sig'))
            result['selected'] = {key:value.get(key) for key in ('model_name','backend','port')}
            args = value.get('args',[])
            if isinstance(args,list):
                result['selected']['memory_options'] = {}
                for key in ('--max-context','--kv','--resident-budget-gib'):
                    if key in args and args.index(key)+1 < len(args):
                        result['selected']['memory_options'][key] = args[args.index(key)+1]
                result['selected']['memory_flags'] = [key for key in ('--mmap-experts','--resident-experts','--kv-stream') if key in args]
    port = result.get('selected',{}).get('port')
    if type(port) is int and 1 <= port <= 65535:
        connection = HTTPConnection('127.0.0.1',port,timeout=2)
        try:
            connection.request('GET','/health')
            response = connection.getresponse(); data = response.read(16385)
            health = json.loads(data) if len(data) <= 16384 else {}
            result['selected']['health'] = {'http_status':response.status,'server':response.getheader('Server'),
                **{key:health.get(key) for key in ('service','model','status') if isinstance(health,dict) and key in health}}
        except (OSError,ValueError) as error:
            result['selected']['health'] = {'error':str(error)}
        finally:
            connection.close()
    return result


def import_failure(root, name, error):
    label = 'av (PyAV video decoder)' if name == 'av' else name
    detail = f'Runtime import failed: {label}: {error}'
    try:
        report = Path(root)/'logs/runtime-import-error.log'
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(detail+'\n'+platform.platform()+'\n'+''.join(traceback.format_exception(error)), 'utf-8')
        detail += '\nDetails: logs/runtime-import-error.log.'
    except OSError:
        pass
    return detail+'\nRun CHECK-ENV.bat for diagnostics and VERIFY-PACKAGE.bat to check extracted files.'


def video_dependencies(root):
    """Distinguish missing wheel members from unavailable system DLLs; ignore virtual API sets."""
    root = Path(root)
    base = root/'runtime/python/Lib/site-packages'
    missing = []
    source = root/'licenses/video/sources.json'
    if source.is_file():
        for item in json.loads(source.read_text('utf-8-sig')).get('runtime_files', []):
            rel = Path(item['path'])
            if rel.is_absolute() or '..' in rel.parts:
                raise ValueError('Invalid video runtime inventory path')
            if not (base/rel).is_file():
                missing.append(rel.as_posix())
    else:
        missing.append('licenses/video/sources.json (dependency inventory)')
    cores = list((base/'av').glob('_core*.pyd'))
    dlls = list((base/'av.libs').glob('*.dll'))
    if not cores:
        missing.append('av/_core*.pyd')
    if not dlls:
        missing.append('av.libs/*.dll')
    unavailable = []
    if os.name == 'nt':
        sys.path.insert(0, str(ROOT))
        from tools.hip.dll_closure import pe_imports
        known = {p.name.lower() for p in [*cores, *dlls, *(root/'runtime/python').glob('*.dll')]}
        dependencies = {dep.lower() for path in [*cores, *dlls] for dep in pe_imports(path)}
        # Expected wheel DLLs remain package errors even when one is missing.
        if source.is_file():
            known.update(Path(item['path']).name.lower() for item in json.loads(source.read_text('utf-8-sig')).get('runtime_files', []))
        for dep in sorted(dependencies-known):
            if dep.startswith(('api-ms-', 'ext-ms-')):
                continue
            try:
                ctypes.WinDLL(dep, winmode=0x800)  # LOAD_LIBRARY_SEARCH_SYSTEM32, never PATH
            except OSError as error:
                unavailable.append({'name': dep, 'error': str(error)})
    return {'missing_package_files': sorted(set(missing)), 'unavailable_system_libraries': unavailable}


def diagnose(root):
    root = Path(root).resolve()
    python = root/'runtime/python/python.exe'
    if not python.is_file():
        raise ValueError('Select the extracted Strata folder containing runtime/python/python.exe')
    env = os.environ.copy()
    for key in ('PYTHONHOME', 'PYTHONPATH'):
        env.pop(key, None)
    results = []
    for name in MODULES:
        try:
            child = subprocess.run([str(python), '-I', '-B', '-X', 'utf8', '-c',
                'import importlib,sys; importlib.import_module(sys.argv[1]); print("OK")', name],
                cwd=root, env=env, capture_output=True, encoding='utf-8', errors='replace', timeout=30)
            results.append({'module': name, 'returncode': child.returncode,
                            'detail': (child.stderr or child.stdout)[-12000:]})
        except (OSError, subprocess.TimeoutExpired) as error:
            results.append({'module': name, 'returncode': None, 'detail': str(error)})
    try:
        video = video_dependencies(root)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        video = {'error':'Video dependency inventory could not be inspected: '+str(error)}
    result = {'app_root': str(root), 'windows': platform.platform(), 'machine': platform.machine(),
            'modules': results, 'video_dependencies': video, 'resources':machine_resources()}
    try:
        result['installation'] = selected_configuration(root)
    except (OSError,ValueError,TypeError,AttributeError) as error:
        result['installation'] = {'error':str(error)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    result = diagnose(args.root)
    report = args.root/'logs/runtime-diagnostics.json'
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    for item in result['modules']:
        print(f"{item['module']}: {'OK' if item['returncode'] == 0 else item['detail'].strip()}", flush=True)
    print(json.dumps(result['video_dependencies'], ensure_ascii=False, indent=2), flush=True)
    print(f'Diagnostics saved: {report}', flush=True)
    video = result['video_dependencies']
    healthy = all(item['returncode'] == 0 for item in result['modules']) and not any(
        video.get(key) for key in ('error', 'missing_package_files', 'unavailable_system_libraries'))
    return 0 if healthy else 1


if __name__ == '__main__':
    raise SystemExit(main())
