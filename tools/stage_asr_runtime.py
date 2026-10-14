"""Stage a self-contained optional ASR Python/native runtime, excluding build tools."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]


def verify_pe(path):
    with Path(path).open('rb') as stream:
        header=stream.read(64)
        if len(header)!=64 or header[:2]!=b'MZ':
            raise ValueError('Incomplete native component: '+str(path))
        stream.seek(struct.unpack_from('<I',header,60)[0])
        pe=stream.read(6)
        if pe[:4]!=b'PE\x00\x00' or len(pe)!=6 or struct.unpack_from('<H',pe,4)[0]!=0x8664:
            raise ValueError('Native component is not a complete Windows x64 image: '+str(path))


def verify(target, device='cpu'):
    target = Path(target).resolve()
    value = json.loads((target/('ASR-'+device.upper()+'.json')).read_text(encoding='utf-8'))
    if value.get('device') != device or value.get('python') != '3.12.10' or not value.get('files'):
        raise ValueError('Invalid ASR component inventory')
    seen = set()
    for entry in value['files']:
        path = target/entry['path']
        if entry['path'] in seen or path.is_symlink() or not path.resolve().is_relative_to(target):
            raise ValueError('Unsafe ASR component path')
        seen.add(entry['path'])
        if not path.is_file() or path.stat().st_size != entry['size']:
            raise ValueError('ASR component missing or size mismatch: '+entry['path'])
        with path.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest() != entry['sha256']:
                raise ValueError('ASR component checksum mismatch: '+entry['path'])
        if path.suffix.lower() in ('.dll','.pyd'):
            verify_pe(path)
    actual = {path.relative_to(target).as_posix() for name in ('python','native-'+device)
              for path in (target/name).rglob('*') if path.is_file() and '__pycache__' not in path.parts and path.suffix!='.pyc'}
    if actual != seen:
        raise ValueError('ASR inventory does not cover installed component files')
    return value


def stage(python_dir, build, target, *, device, redist=None, cuda_bin=None, cuda_arch=89):
    python_dir, build, target = map(lambda path:Path(path).resolve(),(python_dir,build,target))
    if python_dir==target or target.is_relative_to(python_dir) or target==Path(target.anchor) or python_dir.is_relative_to(target):
        raise ValueError('ASR target must be separate from its build interpreter')
    destination = target/'python'
    destination.mkdir(parents=True,exist_ok=True)
    exclusions = ('cmake','cmake-','ninja','ninja-','pybind11','pybind11-','pip','pip-','setuptools','setuptools-',
                  'av','av-','av.libs','wheel','wheel-')
    def ignore(directory,names):
        return [name for name in names if name in ('__pycache__','tests','test','datasets') or Path(name).suffix.lower() in ('.onnx','.bin','.gguf','.pt','.pth','.safetensors') or (Path(directory).name=='site-packages' and
                any(name==prefix or prefix.endswith('-') and name.startswith(prefix) for prefix in exclusions))]
    for child in python_dir.iterdir():
        if child.name in ('include','libs','Scripts','tcl','Tools','Doc'):
            continue
        if child.is_dir(): shutil.copytree(child,destination/child.name,dirs_exist_ok=True,ignore=ignore)
        elif child.suffix.lower() in ('.exe','.dll','.zip','.txt','._pth'):
            shutil.copy2(child,destination/child.name)
    native = target/('native-'+device)
    for name in ('lib','backends','python'): (native/name).mkdir(parents=True,exist_ok=True)
    dlls = list((build/'bin/Release').glob('*.dll')) or list((build/'bin').glob('*.dll'))
    pyds = list((build/'python').rglob('*.pyd'))
    if not dlls or len(pyds)!=1:
        raise ValueError('ASR build is incomplete')
    for path in [*dlls,*pyds]:verify_pe(path)
    for dll in dlls:
        group = 'backends' if dll.name.startswith(('ggml-cpu-','ggml-cuda')) else 'lib'
        shutil.copy2(dll,native/group/dll.name)
    shutil.copy2(pyds[0],native/'python'/pyds[0].name)
    if redist:
        for group in ('Microsoft.VC143.CRT','Microsoft.VC143.OpenMP'):
            for dll in (Path(redist)/group).glob('*.dll'): shutil.copy2(dll,native/'lib'/dll.name)
    if cuda_bin and device=='cuda':
        for pattern in ('cudart64*.dll','cublas64*.dll','cublasLt64*.dll'):
            for dll in Path(cuda_bin).glob(pattern): shutil.copy2(dll,native/'lib'/dll.name)
    inventory = []
    for path in sorted(target.rglob('*')):
        if path.is_file() and path.relative_to(target).parts[0] in ('python','native-'+device):
            with path.open('rb') as stream: digest=hashlib.file_digest(stream,'sha256').hexdigest()
            inventory.append({'path':path.relative_to(target).as_posix(),'size':path.stat().st_size,'sha256':digest})
    (target/('ASR-'+device.upper()+'.json')).write_text(json.dumps({'source_commit':'b317076ac8f8aee3e5ecb253b94fa389fed2d346',
        'llama_commit':'ad6c66839af3c5646fba8c6c2e2087a1e4e38948','python':'3.12.10','device':device,
        'cuda_architectures':[cuda_arch] if device=='cuda' else [],
        'adapter_sha256':{path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in (ROOT/'tools/asr-native').glob('*') if path.is_file()},
        'cmake_cache_sha256':hashlib.sha256((build/'CMakeCache.txt').read_bytes()).hexdigest(),
        'files':inventory},indent=2),encoding='utf-8')
    return {'files':len(inventory),'bytes':sum(item['size'] for item in inventory),'device':device}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('python-dir','build','target'): parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--device',choices=('cpu','cuda'),default='cpu')
    parser.add_argument('--redist',type=Path);parser.add_argument('--cuda-bin',type=Path)
    parser.add_argument('--cuda-arch',type=int,default=89)
    args=parser.parse_args()
    print(stage(args.python_dir,args.build,args.target,device=args.device,redist=args.redist,cuda_bin=args.cuda_bin,cuda_arch=args.cuda_arch))
