"""Prepare a verified reversal of a successful portable update, offline."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid


def backup_destination(root,identity):
    if not re.fullmatch('[0-9a-f]{32}',identity):raise ValueError('Invalid backup identity')
    base=Path(os.environ.get('LOCALAPPDATA',tempfile.gettempdir()))/'Strata/portable-backups'
    app=hashlib.sha256(str(Path(root).resolve()).casefold().encode()).hexdigest()[:24]
    path=base/app/identity
    for parent in [path,*path.parents]:
        if parent.is_symlink() or getattr(parent,'is_junction',lambda:False)():raise ValueError('Linked rollback backup path')
    if path.resolve().is_relative_to(Path(root).resolve()):raise ValueError('Backup must be outside the application')
    return path.resolve()


def _receipt(root,path,identity,update_json,validate_manifest,update_metadata,validate_metadata):
    if path.stat().st_size>65536:raise ValueError('Rollback receipt exceeds its budget')
    value=update_json(path.read_bytes())
    if not isinstance(value,dict) or value.get('id')!=identity or value.get('root')!=str(root):raise ValueError('Rollback receipt belongs to another application')
    backup=backup_destination(root,identity)
    if value.get('backup')!=str(backup) or not backup.is_dir():raise ValueError('Rollback backup is unavailable')
    raw=(backup/'PACKAGE-MANIFEST.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=value.get('target_manifest_sha256'):raise ValueError('Rollback manifest checksum failed')
    manifest=update_json(raw)
    validate_manifest(backup,manifest)
    validate_metadata(update_metadata(backup),manifest)
    if manifest['version']!=value.get('from_version'):raise ValueError('Rollback version differs from receipt')
    return value,backup,manifest


def read_receipt(root,identity):
    from portable_update import update_json,validate_manifest,update_metadata,validate_metadata
    if not isinstance(identity,str) or not re.fullmatch('[0-9a-f]{32}',identity):raise ValueError('Invalid rollback selection')
    root=Path(root).resolve();directory=root/'.portable-update/history'
    path=directory/(identity+'.json')
    for item in (root,root/'.portable-update',directory,path):
        if item.is_symlink() or getattr(item,'is_junction',lambda:False)():raise ValueError('Linked rollback control path')
    if not path.resolve().is_relative_to(root):raise ValueError('Rollback receipt leaves the application')
    return _receipt(root,path,identity,update_json,validate_manifest,update_metadata,validate_metadata)


def list_backups(root):
    from portable_update import update_json
    root=Path(root).resolve();directory=root/'.portable-update/history'
    current=hashlib.sha256((root/'PACKAGE-MANIFEST.json').read_bytes()).hexdigest()
    if not directory.is_dir():return {'backups':[]}
    if directory.is_symlink() or getattr(directory,'is_junction',lambda:False)():raise ValueError('Linked rollback directory')
    result=[]
    for path in sorted(directory.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True):
        if not re.fullmatch('[0-9a-f]{32}',path.stem) or path.is_symlink() or path.stat().st_size>65536:continue
        try:
            value=update_json(path.read_bytes())
            if not isinstance(value,dict):continue
            if value.get('id')!=path.stem or value.get('root')!=str(root):continue
            available=value.get('installed_manifest_sha256')==current and backup_destination(root,path.stem).is_dir()
            result.append({'id':path.stem,'from_version':value['from_version'],'to_version':value['to_version'],'available':available})
        except (OSError,ValueError,KeyError,TypeError):continue
    return {'backups':result}


def prepare(root,identity):
    from portable_update import running_processes,update_json,validate_manifest,update_metadata,validate_metadata,check_installation_topology,publish_control_pair
    root=Path(root).resolve()
    if (root/'.git').exists():raise ValueError('Restore applies only to an extracted portable package')
    if running_processes(root):raise RuntimeError('Exit Strata before restoring a backup')
    value,backup,manifest=read_receipt(root,identity)
    raw=(root/'PACKAGE-MANIFEST.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=value.get('installed_manifest_sha256'):raise ValueError('This backup does not reverse the current installation')
    old=update_json(raw);validate_manifest(root,old,verify=False);validate_metadata(update_metadata(root),old)
    check_installation_topology(root,manifest['files'],old['files'])
    # Copy before replacement: a failed restore leaves both current software
    # and the selected successful-update backup available for another attempt.
    stage=Path(tempfile.mkdtemp(prefix='Strata-T8-update-')).resolve()
    incoming=stage/'incoming';incoming.mkdir();token=uuid.uuid4().hex
    try:
        for entry in manifest['files']:
            target=incoming/entry['path'];target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(backup/entry['path'],target)
        shutil.copy2(backup/'PACKAGE-MANIFEST.json',incoming/'PACKAGE-MANIFEST.json')
        validate_manifest(incoming,manifest)
        (stage/'stage-owner.json').write_text(__import__('json').dumps({'root':str(root),'token':token}),encoding='utf-8')
        plan={'root':str(root),'stage':str(incoming),'backup':str(backup_destination(root,token)),
              'new':manifest['files'],'old':old['files'],'version':manifest['version'],'stage_owner':token,
              'manifest_sha256':value['target_manifest_sha256'],'installed_manifest_sha256':hashlib.sha256(raw).hexdigest(),
              'action':'restore','restore_id':identity}
        publish_control_pair(root,root/'.portable-update',plan)
        return plan
    except BaseException:
        if stage.parent==Path(tempfile.gettempdir()).resolve() and stage.name.startswith('Strata-T8-update-') and not stage.is_symlink():
            shutil.rmtree(stage)
        raise
