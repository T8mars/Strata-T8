"""Version-bound component archives using the portable updater's trust checks.

Full offline packages keep START-HERE.bat and every required runtime. Component
assets are an update transport: unchanged, verified files are reused locally.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import zipfile
try:
    from .portable_update_progress import Progress, file_hash as progress_hash, copy_stream
except ImportError:  # Invoked by the updater as a standalone script.
    from portable_update_progress import Progress, file_hash as progress_hash, copy_stream

GROUPS=('app','python','cuda','hip','vision','asr-cpu')
MAX_ARCHIVE=2*1024**3-1
MAX_INDEX=8*1024**2


def index_name(version,edition):
    from portable_version import version_key
    from portable_weights import weight_declaration
    version_key(version);weight_declaration(edition)
    return f'Components-Strata-T8-{version}-Windows-x64-{edition}.json'


def file_hash(path, progress=None):
    return progress_hash(Path(path), progress)


def group(path):
    value=path.casefold()
    if value.startswith('runtime/asr/'):return 'asr-cpu'
    if value.startswith('runtime/'):return 'python'
    if value.startswith('engine-hip/'):return 'hip'
    if value.startswith(('engine/','engine-cuda12/')):return 'cuda'
    if value.startswith('vision/weights/'):return 'vision'
    return 'app'


def build(package,output):
    """Split an already validated offline package; never scan user directories."""
    from portable_update import update_json,validate_manifest,update_metadata
    package,output=Path(package).resolve(),Path(output).resolve()
    if output==package or output.is_relative_to(package):
        raise ValueError('Component output must be outside the package')
    manifest=update_json((package/'PACKAGE-MANIFEST.json').read_bytes())
    validate_manifest(package,manifest)
    meta=update_metadata(package)
    edition=manifest.get('edition','Portable-NoModels')
    name=index_name(manifest['version'],edition)
    output.mkdir(parents=True,exist_ok=True)
    components=[]
    for identity in GROUPS:
        entries=sorted((e for e in manifest['files'] if group(e['path'])==identity),key=lambda e:e['path'])
        if not entries:continue
        identity_hash=hashlib.sha256(json.dumps(entries,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        asset=f'Strata-T8-component-{identity}-{identity_hash[:24]}.zip'
        target=output/asset
        with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            for entry in entries:
                info=zipfile.ZipInfo(entry['path'],(1980,1,1,0,0,0))
                info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=(stat.S_IFREG|0o644)<<16
                with (package/entry['path']).open('rb') as source, archive.open(info,'w',force_zip64=True) as dest:
                    shutil.copyfileobj(source,dest,4*1024**2)
        size=target.stat().st_size
        if size>MAX_ARCHIVE:raise ValueError(f'{identity} component exceeds the GitHub asset limit')
        components.append({'id':identity,'asset':asset,'size':size,'sha256':file_hash(target),'files':entries})
    index={'contract_version':1,'version':manifest['version'],'edition':edition,'manifest':manifest,
           'compatibility':{k:meta.get(k) for k in ('python_version','engine_version','protocol_version','upstream_commit')},
           'components':components}
    raw=json.dumps(index,ensure_ascii=False,sort_keys=True,indent=2).encode()
    if len(raw)>MAX_INDEX:raise ValueError('Component index exceeds its byte limit')
    (output/name).write_bytes(raw)
    (output/(name+'.sha256')).write_text(hashlib.sha256(raw).hexdigest()+'  '+name+'\n',encoding='ascii')
    return index


def validate(index,root):
    from portable_update import validate_manifest,safe_path
    if not isinstance(index,dict) or type(index.get('contract_version')) is not int or index['contract_version']!=1:
        raise ValueError('Unsupported component index')
    if set(index)!={'contract_version','version','edition','manifest','compatibility','components'}:
        raise ValueError('Invalid component index fields')
    manifest=index['manifest'];validate_manifest(root,manifest,verify=False)
    if manifest['version']!=index['version'] or manifest.get('edition','Portable-NoModels')!=index['edition']:
        raise ValueError('Component version or edition differs from package')
    if (not isinstance(index['components'],list) or not 1<=len(index['components'])<=len(GROUPS)
            or not isinstance(index['compatibility'],dict)):
        raise ValueError('Invalid component list or compatibility pins')
    seen,files=set(),[]
    for component in index['components']:
        if (not isinstance(component,dict) or set(component)!={'id','asset','size','sha256','files'}
                or component['id'] not in GROUPS or component['id'] in seen
                or not isinstance(component['asset'],str)
                or not re.fullmatch(r'Strata-T8-component-[a-z-]+-[0-9a-f]{24}\.zip',component['asset'])
                or type(component['size']) is not int or not 0<component['size']<=MAX_ARCHIVE
                or not isinstance(component['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',component['sha256'])
                or not isinstance(component['files'],list) or not component['files']):
            raise ValueError('Invalid component descriptor')
        seen.add(component['id'])
        for entry in component['files']:
            safe_path(root,entry['path'])
            if group(entry['path'])!=component['id']:raise ValueError('File assigned to the wrong component')
        files.extend(component['files'])
    if sorted(files,key=lambda e:e['path'])!=sorted(manifest['files'],key=lambda e:e['path']):
        raise ValueError('Component inventory differs from package manifest')
    return manifest


def extract(archive,destination,entries):
    from portable_update import safe_path
    expected={e['path']:e for e in entries}
    progress=Progress('Extracting and checking component',sum(e['size'] for e in entries)*2)
    with zipfile.ZipFile(archive) as zipped:
        infos=zipped.infolist()
        if len(infos)!=len(expected) or len({i.filename.casefold() for i in infos})!=len(infos):
            raise ValueError('Component ZIP has duplicate or unlisted members')
        for info in infos:
            path=safe_path(destination,info.filename)
            mode=info.external_attr>>16
            if info.filename not in expected or info.is_dir() or stat.S_IFMT(mode) not in (0,stat.S_IFREG):
                raise ValueError('Unsafe component ZIP member')
            entry=expected[info.filename]
            if info.file_size!=entry['size']:raise ValueError('Component file size differs from index')
            path.parent.mkdir(parents=True,exist_ok=True)
            with zipped.open(info) as source,path.open('xb') as output:
                copy_stream(source,output,progress,path.name)
            if file_hash(path,progress)!=entry['sha256']:raise ValueError('Component file checksum failed')
    progress.update(progress.current,force=True)


def materialize(root,incoming,index,assets,fetch):
    from portable_update import safe_path,validate_manifest,update_metadata
    manifest=validate(index,incoming)
    downloaded=[];reused=[]
    for component in index['components']:
        entries=component['files']
        progress=Progress('Checking installed '+component['id'],sum(e['size'] for e in entries))
        can_reuse=True
        for entry in entries:
            path=safe_path(root,entry['path'])
            if not path.is_file() or path.stat().st_size!=entry['size'] or file_hash(path,progress)!=entry['sha256']:
                can_reuse=False;break
        progress.update(progress.current,'reuse' if can_reuse else 'download required',force=True)
        if can_reuse:
            progress=Progress('Copying verified '+component['id'],sum(e['size'] for e in entries))
            for entry in entries:
                target=safe_path(incoming,entry['path']);target.parent.mkdir(parents=True,exist_ok=True)
                source=safe_path(root,entry['path'])
                with source.open('rb') as src,target.open('xb') as dst:
                    copy_stream(src,dst,progress,target.name)
                shutil.copystat(source,target)
            progress.update(progress.current,force=True)
            reused.append(component['id']);continue
        asset=assets.get(component['asset'])
        if not asset or asset.get('size')!=component['size']:raise ValueError('Release lacks the pinned component asset')
        archive=incoming.parent/component['asset']
        print(f'[Update] Downloading {component["id"]}: {component["size"]/1048576:.1f} MiB',flush=True)
        count=fetch(asset['browser_download_url'],archive,limit=MAX_ARCHIVE)
        progress=Progress('Checking downloaded '+component['id'],component['size'])
        if count!=component['size'] or file_hash(archive,progress)!=component['sha256']:raise ValueError('Component archive checksum or size failed')
        progress.update(progress.current,force=True)
        extract(archive,incoming,entries);archive.unlink();downloaded.append(component['id'])
    (incoming/'PACKAGE-MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
    validate_manifest(incoming,manifest,progress=True)
    actual=update_metadata(incoming)
    if any(actual.get(k)!=v for k,v in index['compatibility'].items()):raise ValueError('Component ABI/version pins differ from application')
    return manifest,{'downloaded':downloaded,'reused':reused}


def from_release(root,incoming,release,edition,fetch):
    from portable_update import update_json
    name=index_name(release['tag_name'].removeprefix('v'),edition)
    assets={a['name']:a for a in release['assets']}
    if name not in assets or name+'.sha256' not in assets:raise ValueError('Incomplete component index assets')
    raw=fetch(assets[name]['browser_download_url'],limit=MAX_INDEX)
    checksum=fetch(assets[name+'.sha256']['browser_download_url'],limit=4096).decode('ascii').strip().split()
    if len(checksum)!=2 or checksum[1].lstrip('*')!=name or checksum[0]!=hashlib.sha256(raw).hexdigest():
        raise ValueError('Component index checksum failed')
    index=update_json(raw)
    if index.get('version')!=release['tag_name'].removeprefix('v') or index.get('edition')!=edition:
        raise ValueError('Component index does not match selected release')
    return materialize(root,incoming,index,assets,fetch)
