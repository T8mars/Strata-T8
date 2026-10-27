"""Build the opt-in T8 CUDA video engine before packaging; recipients need no compiler.

Keep vendor runtime DLLs and upstream_asset identity. Build both executables and
the K-quant library for the same seven CUDA architectures, then atomically install a new
engine tree. --verify refuses changed native inputs or stale executable hashes.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURES = ('75', '80', '86', '89', '90', '100', '120')
CUDA_TARGETS = tuple(a+'-real' for a in ARCHITECTURES[:-1]) + (ARCHITECTURES[-1],)
LLAMA_DIRS = ('cmake', 'ggml', 'include', 'src', 'tools/mtmd', 'vendor', 'common')
SOURCE_SUFFIXES = {'.cpp', '.c', '.cc', '.h', '.hpp', '.cuh', '.cu', '.cmake', '.txt', '.in', '.inc', '.inl', '.s', '.m', '.mm', '.py', '.rc'}
FAILED_LOG_BYTES = 64 * 1024
FAILED_LOG_LINES = 200


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def llama_pin(root):
    text = (Path(root)/'setup.py').read_text('utf-8')
    match = re.search(r'^LLAMA_CPP_COMMIT\s*=\s*[\"\']([a-f0-9]{40})[\"\']', text, re.M)
    if not match:
        raise ValueError('No fixed llama.cpp commit in setup.py')
    return match[1]


def selected_source(relative):
    p = Path(relative)
    return p.suffix.lower() in SOURCE_SUFFIXES and not any(x in p.parts for x in ('__pycache__', '.git'))


def native_inputs(root):
    root = Path(root).resolve()
    names = {'CMakeLists.txt', 'tools/build_native_portable.py', 'tools/windows_utf8_manifest.py'}
    for directory in ('src', 'include', 'cmake', 'tools/vision', 'third_party/ggml'):
        names.update(p.relative_to(root).as_posix() for p in (root/directory).rglob('*')
                     if p.is_file() and selected_source(p.relative_to(root)))
    entries = []
    for name in sorted(names):
        path = root/name
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('Missing or linked native input: ' + name)
        entries.append({'path': name, 'size': path.stat().st_size, 'sha256': digest(path)})
    return entries


def llama_inputs(root):
    source = Path(root)/'third_party/llama.cpp'
    paths = [source/'CMakeLists.txt']
    for directory in LLAMA_DIRS:
        paths += [p for p in (source/directory).rglob('*') if p.is_file() and selected_source(p.relative_to(source))]
    return [{'path': p.relative_to(source).as_posix(), 'size': p.stat().st_size, 'sha256': digest(p)}
            for p in sorted(set(paths))]


def prepare_llama(root, cache):
    """Resolve pinned source and compare every native build input with its archive."""
    root, cache = Path(root), Path(cache)
    pin = llama_pin(root)
    archive = cache/f'llama-{pin}.zip'
    cache.mkdir(parents=True, exist_ok=True)
    if not archive.is_file():
        url = f'https://github.com/ggml-org/llama.cpp/archive/{pin}.zip'
        temporary = archive.with_suffix('.download')
        req = urllib.request.Request(url, headers={'User-Agent': 'Strata-T8-native-build'})
        try:
            with urllib.request.urlopen(req, timeout=60) as response, temporary.open('wb') as stream:
                shutil.copyfileobj(response, stream)
            os.replace(temporary, archive)
        finally:
            temporary.unlink(missing_ok=True)
    source = root/'third_party/llama.cpp'
    prefix = f'llama.cpp-{pin}/'
    expected = []
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            if info.is_dir() or not info.filename.startswith(prefix):
                continue
            name = info.filename[len(prefix):]
            path = Path(name)
            if path.is_absolute() or '..' in path.parts or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Unsafe pinned source archive member')
            if name != 'CMakeLists.txt' and not any(name.startswith(d+'/') for d in LLAMA_DIRS):
                continue
            if not selected_source(name):
                continue
            data = z.read(info)
            target = source/path
            if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
                raise ValueError('Linked pinned native source: '+name)
            if target.exists():
                if target.read_bytes() != data:
                    raise ValueError('Pinned llama.cpp native source changed: '+name)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            expected.append({'path': path.as_posix(), 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    if not (source/'tools/mtmd/mtmd.cpp').is_file() or not (source/'ggml/CMakeLists.txt').is_file():
        raise ValueError('Pinned archive has no complete vision/CUDA build source')
    actual = llama_inputs(root)
    if sorted(expected, key=lambda x:x['path']) != sorted(actual, key=lambda x:x['path']):
        raise ValueError('Unexpected extra or missing llama.cpp native inputs')
    return {'repository': 'ggml-org/llama.cpp', 'commit': pin, 'archive_sha256': digest(archive),
            'source_tree_sha256': canonical_digest(actual), 'source_file_count': len(actual)}


def source_identity(root, third_party):
    value = {'inputs': native_inputs(root), 'third_party': third_party}
    return {**value, 'sha256': canonical_digest(value)}


def validate_native_build(native, root, engine_directory=None, check_source=True, require_source=False):
    if not isinstance(native, dict) or native.get('schema') != 1 or native.get('capabilities') != {
        'native_video': True, 'geniv': 1, 'encv': 1, 'sve': 1}:
        raise ValueError('Missing native-video build capability/contract')
    if native.get('cuda_architectures') != list(ARCHITECTURES) or native.get('portable') is not True:
        raise ValueError('Native release must contain the full portable CUDA architecture set')
    if native.get('cuda_targets') != list(CUDA_TARGETS) or native.get('fatbinary_compression') != 'size':
        raise ValueError('Native release requires seven SASS architectures and latest-only PTX with size compression')
    if not re.fullmatch('[a-f0-9]{40}', native.get('source_commit', '')):
        raise ValueError('Native build has no frozen source commit')
    identity = native.get('source_identity', {})
    value = {'inputs': identity.get('inputs'), 'third_party': identity.get('third_party')}
    if canonical_digest(value) != identity.get('sha256'):
        raise ValueError('Native source-input manifest checksum failed')
    third = identity.get('third_party', {})
    if third.get('commit') != llama_pin(root) or not re.fullmatch('[a-f0-9]{64}', third.get('source_tree_sha256', '')):
        raise ValueError('Native third-party pin differs from the source')
    if require_source and not (Path(root)/'src/program/generate.cpp').is_file():
        raise ValueError('Publisher requires the complete native source checkout')
    if check_source and (Path(root)/'src/program/generate.cpp').is_file():
        if native_inputs(root) != identity.get('inputs'):
            raise ValueError('Native source changed: rebuild T8 binaries before packaging')
        if (Path(root)/'third_party/llama.cpp/src/llama.cpp').is_file() and canonical_digest(llama_inputs(root)) != third['source_tree_sha256']:
            raise ValueError('Pinned native third-party source changed: rebuild required')
    outputs = native.get('outputs', {})
    cmake_file = Path(root)/'CMakeLists.txt'
    libraries = native.get('libraries', {})
    if not isinstance(libraries, dict) or set(libraries) - {'strata_kq.dll'}:
        raise ValueError('Invalid native library build proof')
    requires_kq = (cmake_file.is_file()
                   and re.search(r'add_library\(strata_kq\s+SHARED', cmake_file.read_text('utf-8')))
    if requires_kq and 'strata_kq.dll' not in libraries:
        raise ValueError('Native build requires a source-matched strata_kq.dll')
    if set(outputs) != {'strata.exe', 'strata-vision.exe'}:
        raise ValueError('Native build must include engine and vision executables')
    for name, item in {**outputs, **libraries}.items():
        if not isinstance(item, dict) or not re.fullmatch('[a-f0-9]{64}', item.get('sha256', '')) or (name.endswith('.exe') and item.get('utf8_manifest') != 'activeCodePage=UTF-8'):
            raise ValueError('Native executable lacks checksum or UTF-8 manifest identity')
        if type(item.get('size')) is not int or item['size'] <= 0:
            raise ValueError('Native executable lacks a positive byte size')
        if item.get('cuda_architectures') != list(ARCHITECTURES):
            raise ValueError('Native executable architecture proof differs')
        ptx = item.get('ptx_architectures')
        if not isinstance(ptx,list) or not ptx or ptx != sorted(set(ptx)) or any(x not in ('120','120a') for x in ptx):
            raise ValueError('Native executable latest-only PTX proof differs')
        if engine_directory is not None:
            artifact = Path(engine_directory)/name
            if artifact.stat().st_size != item['size'] or digest(artifact) != item['sha256']:
                raise ValueError('Native executable checksum failed: '+name)
    return native


def developer_environment(vsdevcmd=None, temp_directory=None):
    env = os.environ.copy()
    if not vsdevcmd and env.get('LIB') and env.get('INCLUDE') and shutil.which('cl', path=env.get('PATH')):
        return env
    if not vsdevcmd:
        vswhere = Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'))/'Microsoft Visual Studio/Installer/vswhere.exe'
        if vswhere.exists():
            install = subprocess.check_output([str(vswhere), '-latest', '-products', '*', '-requires',
                'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath'], text=True).strip()
            if install:
                vsdevcmd = Path(install)/'Common7/Tools/VsDevCmd.bat'
    if not vsdevcmd or not Path(vsdevcmd).is_file():
        raise ValueError('MSVC developer environment required; specify --vsdevcmd or run VsDevCmd first')
    if any(c in str(vsdevcmd) for c in '\r\n"'):
        raise ValueError('Invalid developer command path')
    if temp_directory is not None:
        Path(temp_directory).mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(suffix='.cmd', dir=temp_directory)
    os.close(handle)
    script = Path(name)
    try:
        script.write_text(f'@echo off\ncall "{vsdevcmd}" -arch=x64 -host_arch=x64 >nul\n'
                          'if errorlevel 1 exit /b 1\nset\n', encoding='utf-8')
        result = subprocess.run(['cmd', '/d', '/c', str(script)], capture_output=True, check=True)
    finally:
        script.unlink(missing_ok=True)
    for line in result.stdout.decode('mbcs', errors='replace').splitlines():
        if '=' in line and not line.startswith('='):
            key, value = line.split('=', 1)
            env[key.upper()] = value
    return env


def executable(name, env, extra=()):
    path = shutil.which(name, path=env.get('PATH'))
    if path:
        # Windows which() appends PATHEXT as .EXE. Resolve actual filesystem
        # spelling and use CMake's forward slashes so a cache sees the same
        # compiler rather than deleting itself for nvcc.EXE vs nvcc.exe.
        return Path(path).resolve().as_posix()
    for candidate in extra:
        if Path(candidate).is_file():
            return Path(candidate).resolve().as_posix()
    raise ValueError('Build tool not found: '+name)


def msvc_compiler_record(directories, env):
    """Bind version output to the developer environment and actual CMake tools."""
    selected = executable('cl', env)
    caches = {}
    for kind, directory in directories.items():
        text = (Path(directory)/'CMakeCache.txt').read_text('utf-8')
        tools = {}
        for language in ('C', 'CXX'):
            match = re.search(r'^CMAKE_' + language + r'_COMPILER:[^=\r\n]+=([^\r\n]+)$', text, re.M)
            if not match:
                raise ValueError('CMake compiler identity missing: ' + kind + '/' + language)
            path = Path(match[1]).resolve()
            if path != Path(selected).resolve():
                raise ValueError('CMake compiler differs from developer environment: ' + kind + '/' + language)
            tools[language] = path.as_posix()
        caches[kind] = tools
    # On Windows, env= does not control how a bare executable name is resolved
    # by the parent CreateProcess call. An absolute path prevents another cl
    # on the caller's PATH from supplying misleading provenance.
    result = subprocess.run([selected, '/Bv'], env=env, text=True,
                            capture_output=True, errors='replace')
    return {'msvc': (result.stdout + result.stderr)[:12000],
            'msvc_executable': selected, 'cmake_compilers': caches}


def run(command, env, log):
    print('native build: '+subprocess.list2cmdline(list(map(str, command))), flush=True)
    try:
        with Path(log).open('a', encoding='utf-8') as output:
            subprocess.run(list(map(str, command)), env=env, cwd=Path(log).parent,
                           stdout=output, stderr=subprocess.STDOUT, check=True)
    except (subprocess.CalledProcessError, OSError) as error:
        # Close the child's log before reading it. Read a bounded tail rather
        # than loading a potentially large Ninja log into memory or CI output.
        try:
            with Path(log).open('rb') as stream:
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - FAILED_LOG_BYTES))
                tail = stream.read(FAILED_LOG_BYTES).decode('utf-8', errors='replace')
            lines = tail.splitlines()[-FAILED_LOG_LINES:]
            detail = '\n'.join('| ' + line for line in lines) or '| (log is empty)'
        except OSError as read_error:
            detail = '| Cannot read build log: ' + str(read_error)
        status = getattr(error, 'returncode', 'process could not start')
        message = ('native build failed (exit ' + str(status) + '); log: ' +
                   json.dumps(str(Path(log)), ensure_ascii=True) + '\n' +
                   f'Last {FAILED_LOG_LINES} lines / {FAILED_LOG_BYTES} bytes at most:\n' +
                   detail + '\n')
        # CMake/MSVC paths can contain Unicode while a Windows console uses
        # cp1252. Diagnostics must not hide the original child failure.
        encoding = getattr(sys.stderr, 'encoding', None) or 'utf-8'
        message = message.encode(encoding, errors='backslashreplace').decode(encoding)
        print(message, end='', file=sys.stderr, flush=True)
        raise


def cuda_architecture_proof(exe, cuobjdump, env):
    result = subprocess.check_output([cuobjdump, '--list-elf', str(exe)], env=env, text=True, errors='replace')
    found = set(re.findall(r'sm_([0-9]+)', result))
    if not set(ARCHITECTURES).issubset(found):
        raise ValueError(f'Actual CUDA fatbinary architecture coverage failed: {exe}: {sorted(found)}')
    return list(ARCHITECTURES)


def cuda_ptx_proof(exe, cuobjdump, env):
    result = subprocess.check_output([cuobjdump, '--list-ptx', str(exe)], env=env, text=True, errors='replace')
    found = sorted(set(re.findall(r'sm_([0-9]+a?)\.', result)))
    # Fixed ggml specializes Blackwell's target to 120a; record that exact fact
    # rather than claiming its PTX is generic/forward compatible with all GPUs.
    if not found or any(x not in ('120','120a') for x in found):
        raise ValueError(f'Actual CUDA latest-only PTX coverage failed: {exe}: {found}')
    return found


def install_build(root, destination, artifacts, native, build_root):
    """One directory swap publishes both binaries and their BUILD.json together."""
    root, destination, build_root = Path(root), Path(destination), Path(build_root)
    target = destination/'engine'
    if target.is_symlink() or getattr(target, 'is_junction', lambda:False)() or not target.is_dir():
        raise ValueError('Existing verified vendor engine tree required before native installation')
    build = json.loads((target/'BUILD.json').read_text('utf-8'))
    if not isinstance(build.get('upstream_asset'), dict):
        raise ValueError('Install requires complete upstream_asset provenance from bootstrap')
    from windows_utf8_manifest import patch_executable
    temporary = Path(tempfile.mkdtemp(prefix='.t8-native-stage-', dir=destination))
    previous = temporary/'previous'
    keep_backup = False
    try:
        incoming = temporary/'engine'
        shutil.copytree(target, incoming)
        backups = build_root/'vendor-backup'/canonical_digest(build['upstream_asset'])[:16]
        backups.mkdir(parents=True, exist_ok=True)
        native['vendor_binaries'] = {}
        for name, source in artifacts.items():
            old = incoming/name
            if old.exists():
                backup = backups/name
                if not backup.exists(): shutil.copy2(old, backup)
                native['vendor_binaries'][name] = {'sha256': digest(backup)}
            shutil.copy2(source, old)
            if name.endswith('.exe'):
                patch = patch_executable(old)
                native['outputs'][name].update(sha256=patch['sha256_after'], size=old.stat().st_size,
                                               utf8_manifest=patch['manifest'])
                key = 'engine_utf8' if name == 'strata.exe' else 'vision_utf8'
                build.setdefault('portable_patches', {})[key] = patch
            else:
                native['libraries'][name].update(sha256=digest(old), size=old.stat().st_size)
        build['capabilities'] = {**build.get('capabilities', {}), 'native_video': True, 'geniv': 1, 'encv': 1}
        cuda_series = re.search(r'release ([0-9]+\.[0-9]+)', native.get('compiler', {}).get('cuda', ''))
        if not cuda_series:
            raise ValueError('Native build compiler has no CUDA toolkit series')
        # These fields describe the installed executables. The original vendor
        # release remains fully identified in the untouched upstream_asset.
        from portable_version import source_version
        build.update(version=source_version(root), source='t8_native_build', archs=list(map(int, ARCHITECTURES)),
                     cuda=cuda_series[1], portable=True, vision='gpu')
        build['t8_native_build'] = native
        (incoming/'BUILD.json').write_text(json.dumps(build, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        validate_native_build(native, root, incoming)
        target.rename(previous)
        try:
            incoming.rename(target)
        except BaseException as error:
            try: previous.rename(target)
            except OSError as rollback:
                keep_backup = True
                raise RuntimeError(f'Native install rollback incomplete; vendor retained at {previous}: {rollback}') from error
            raise
    finally:
        if not keep_backup: shutil.rmtree(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--destination', type=Path, help='Root containing bootstrapped engine/; default project root')
    parser.add_argument('--build-root', type=Path)
    parser.add_argument('--vsdevcmd', type=Path)
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args(argv)
    root = args.root.resolve(); destination = (args.destination or root).resolve()
    if args.verify:
        build = json.loads((destination/'engine/BUILD.json').read_text('utf-8'))
        validate_native_build(build.get('t8_native_build'), root, destination/'engine')
        print('T8 native source, architecture and executable identity verified', flush=True)
        return
    if os.name != 'nt' or args.jobs < 1 or args.jobs > 32:
        raise ValueError('Windows x64 and jobs in 1..32 required')
    build_root = (args.build_root or root/'.portable-build/t8-native').resolve()
    if build_root == root or build_root == destination or not build_root.is_relative_to(root):
        raise ValueError('Build directory must be a separate child of the source checkout')
    build_root.mkdir(parents=True, exist_ok=True)
    third_party = prepare_llama(root, root/'.portable-build')
    identity = source_identity(root, third_party)
    temp = build_root/'temp'; temp.mkdir(exist_ok=True)
    env = developer_environment(args.vsdevcmd, temp)
    env.update(TEMP=str(temp), TMP=str(temp), VSLANG='1033')
    vsroot = Path(args.vsdevcmd).parents[2] if args.vsdevcmd else None
    extras = lambda suffix: [vsroot/'Common7/IDE/CommonExtensions/Microsoft/CMake'/suffix] if vsroot else []
    cmake = executable('cmake', env, extras('CMake/bin/cmake.exe'))
    ninja = executable('ninja', env, extras('Ninja/ninja.exe'))
    cuda = Path(env.get('CUDA_PATH', 'C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v13.0'))
    nvcc = executable('nvcc', env, [cuda/'bin/nvcc.exe'])
    supported = subprocess.check_output([nvcc, '--list-gpu-arch'], env=env, text=True)
    if not set(ARCHITECTURES).issubset(set(re.findall(r'compute_([0-9]+)', supported))):
        raise ValueError('CUDA toolkit does not support the required seven release architectures')
    cuda_version = subprocess.check_output([nvcc, '--version'], env=env, text=True)
    if not re.search(r'release 13\.', cuda_version):
        raise ValueError('CUDA 13.x required for this portable native release')
    common = ['-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Release', '-DSTRATA_PORTABLE=ON',
        '-DCMAKE_MAKE_PROGRAM='+ninja, '-DCMAKE_CUDA_COMPILER='+nvcc,
        '-DCMAKE_CUDA_ARCHITECTURES='+';'.join(CUDA_TARGETS),
        '-DCMAKE_CUDA_FLAGS=-D_WINDOWS -Xcompiler=" /EHsc" --compress-mode=size']
    directories = {'engine': build_root/'engine-portable', 'vision': build_root/'vision-portable'}
    engine_flags = ['-DSTRATA_ENABLE_CUDA=ON', '-DSTRATA_BUILD_TESTS=OFF', '-DSTRATA_GGML_DIR='+str(root/'third_party/llama.cpp')]
    vision_flags = ['-DSTRATA_VISION_CUDA=ON', '-DSTRATA_VISION_CONTRACT_TESTS=ON', '-DLLAMA_DIR='+str(root/'third_party/llama.cpp')]
    for kind, source, flags, targets in [('engine', root, engine_flags, ['strata', 'strata_kq']),
                ('vision', root/'tools/vision', vision_flags, ['strata-vision', 'strata-native-vision-test'])]:
        run([cmake, '-S', source, '-B', directories[kind], *common, *flags], env, build_root/f'{kind}-configure.log')
        run([cmake, '--build', directories[kind], '--target', *targets, '-j', args.jobs], env, build_root/f'{kind}-build.log')
    run([cmake, '-E', 'env', str(directories['vision']/'strata-native-vision-test.exe')], env, build_root/'native-contract.log')
    if source_identity(root, third_party) != identity:
        raise ValueError('Native source changed during the build; nothing installed')
    cuobjdump = executable('cuobjdump', env, [Path(nvcc).parent/'cuobjdump.exe'])
    artifacts = {'strata.exe': directories['engine']/'strata.exe',
                 'strata-vision.exe': directories['vision']/'bin/strata-vision.exe',
                 'strata_kq.dll': directories['engine']/'strata_kq.dll'}
    outputs = {name: {'sha256': digest(path), 'size': path.stat().st_size,
        'cuda_architectures': cuda_architecture_proof(path, cuobjdump, env),
        'ptx_architectures':cuda_ptx_proof(path,cuobjdump,env)} for name, path in artifacts.items()}
    compiler = msvc_compiler_record(directories, env)
    source_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, env=env, text=True).strip()
    # Older portable updaters require schema 1 and exactly two executable outputs.
    # Keep that public contract; the full package also hashes every library file.
    native = {'schema': 1, 'source_commit': source_commit, 'source_identity': identity,
        'third_party_pin': third_party['commit'], 'cuda_architectures': list(ARCHITECTURES), 'portable': True,
        'cuda_targets':list(CUDA_TARGETS), 'fatbinary_compression':'size',
        'compiler': {**compiler, 'cuda': cuda_version,
                     'cmake': subprocess.check_output([cmake, '--version'], env=env, text=True).splitlines()[0]},
        'flags': {'common': common, 'engine': engine_flags, 'vision': vision_flags},
        'capabilities': {'native_video': True, 'geniv': 1, 'encv': 1, 'sve': 1},
        'validation': {'cpu_contract': True, 'cuda_fatbinary_architectures': True,
                       'gpu_hardware': 'not established by build; see separate inference acceptance'},
              'outputs': {name: item for name, item in outputs.items() if name.endswith('.exe')},
              'libraries': {name: item for name, item in outputs.items() if name.endswith('.dll')}}
    install_build(root, destination, artifacts, native, build_root)
    (build_root/'NATIVE-BUILD.json').write_text(json.dumps(native, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print('Installed portable T8 native CUDA build; vendor HIP remains without native-video support', flush=True)


if __name__ == '__main__':
    main()
