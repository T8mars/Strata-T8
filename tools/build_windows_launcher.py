"""Build a small static-runtime Windows EXE; the Python environment stays beside it."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parent))
from build_native_portable import developer_environment, executable
from portable_version import version_key

ROOT=Path(__file__).resolve().parents[1]
FILES=('tools/windows_launcher/launcher.cpp','tools/windows_launcher/launcher.manifest')

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def verify(root=ROOT):
    root=Path(root)
    record=json.loads((root/'LAUNCHER-BUILD.json').read_text('utf-8'))
    version=json.loads((root/'meta.json').read_text('utf-8'))['version']
    if (record.get('version')!=version or record.get('schema')!=1 or record.get('static_runtime') is not True
            or record.get('inputs')!={name:sha(root/name) for name in FILES}
            or record.get('builder_sha256')!=sha(root/'tools/build_windows_launcher.py')
            or record.get('sha256')!=sha(root/'Strata-T8.exe')):
        raise ValueError('Windows EXE launcher is missing or stale; rebuild it before packaging')
    return record

def build(root=ROOT,vsdevcmd=None):
    root=Path(root).resolve()
    meta=json.loads((root/'meta.json').read_text('utf-8'));version=meta['version'];numbers=version_key(version)
    work=root/'.portable-build/windows-launcher';work.mkdir(parents=True,exist_ok=True)
    env=developer_environment(vsdevcmd,work)
    compiler=executable('cl',env);rc=executable('rc',env)
    with tempfile.TemporaryDirectory(prefix='build-',dir=work) as temporary:
        directory=Path(temporary)
        resource=directory/'launcher.rc'
        resource.write_text('#include <windows.h>\n1 VERSIONINFO\n FILEVERSION '+','.join(map(str,numbers))+
            '\n PRODUCTVERSION '+','.join(map(str,numbers))+'\n FILEOS VOS_NT_WINDOWS32\n FILETYPE VFT_APP\nBEGIN\n'
            ' BLOCK "StringFileInfo"\n BEGIN\n BLOCK "040904b0"\n BEGIN\n'
            ' VALUE "CompanyName", "T8star"\n VALUE "FileDescription", "Strata-T8 portable launcher | By T8star"\n'
            f' VALUE "FileVersion", "{version}"\n VALUE "ProductVersion", "{version}"\n'
            ' VALUE "ProductName", "Strata-T8"\n VALUE "OriginalFilename", "Strata-T8.exe"\n END\n END\n'
            ' BLOCK "VarFileInfo"\n BEGIN\n VALUE "Translation", 0x0409, 1200\n END\nEND\n','utf-8')
        subprocess.run([rc,'/nologo','/fo',str(directory/'launcher.res'),str(resource)],env=env,cwd=directory,check=True)
        command=[compiler,'/nologo','/std:c++17','/O2','/MT','/utf-8','/W4','/WX','/DUNICODE','/D_UNICODE',
            str(root/FILES[0]),str(directory/'launcher.res'),'/Fe:'+str(directory/'Strata-T8.exe'),'/link',
            '/SUBSYSTEM:CONSOLE','/MACHINE:X64','/DYNAMICBASE','/NXCOMPAT','/MANIFEST:EMBED',
            '/MANIFESTINPUT:'+str(root/FILES[1])]
        subprocess.run(command,env=env,cwd=directory,check=True)
        shutil.copy2(directory/'Strata-T8.exe',root/'Strata-T8.exe')
    record={'schema':1,'version':version,'static_runtime':True,'compiler':compiler,
        'inputs':{name:sha(root/name) for name in FILES},'builder_sha256':sha(root/'tools/build_windows_launcher.py'),
        'sha256':sha(root/'Strata-T8.exe')}
    (root/'LAUNCHER-BUILD.json').write_text(json.dumps(record,indent=2),'utf-8')
    return verify(root)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify',action='store_true');parser.add_argument('--vsdevcmd',type=Path)
    args=parser.parse_args()
    print(json.dumps(verify() if args.verify else build(vsdevcmd=args.vsdevcmd)),flush=True)

if __name__=='__main__':main()
