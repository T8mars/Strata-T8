"""Prepare a verified reversal of a successful portable update, offline."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid
try:
    from .portable_update_progress import Progress, copy_stream
except ImportError:
    from portable_update_progress import Progress, copy_stream


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
    validate_manifest(backup,manifest,progress=True)
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
    candidates=[]
    for path in directory.glob('*.json'):
        try:
            if not re.fullmatch('[0-9a-f]{32}',path.stem) or path.is_symlink() or getattr(path,'is_junction',lambda:False)():continue
            candidates.append((path.stat().st_mtime,path))
        except OSError:continue  # An unavailable receipt cannot hide other backups.
    for _,path in sorted(candidates,key=lambda item:item[0],reverse=True):
        try:
            if path.is_symlink() or getattr(path,'is_junction',lambda:False)() or path.stat().st_size>65536:continue
            value=update_json(path.read_bytes())
            if not isinstance(value,dict):continue
            if value.get('id')!=path.stem or value.get('root')!=str(root):continue
            available=value.get('installed_manifest_sha256')==current and backup_destination(root,path.stem).is_dir()
            result.append({'id':path.stem,'from_version':value['from_version'],'to_version':value['to_version'],'available':available})
        except (OSError,ValueError,KeyError,TypeError):continue
    return {'backups':result}


def prepare(root,identity):
    from portable_update import running_processes,update_json,validate_manifest,update_metadata,validate_metadata,check_installation_topology,publish_control_pair,discard_owned_pending_stage
    root=Path(root).resolve()
    if (root/'.git').exists():raise ValueError('Restore applies only to an extracted portable package')
    if running_processes(root):raise RuntimeError('Exit Strata before restoring a backup')
    value,backup,manifest=read_receipt(root,identity)
    plan_dir=root/'.portable-update'
    for path in (plan_dir/'plan.json',plan_dir/'apply.ps1'):
        if path.is_symlink() or getattr(path,'is_junction',lambda:False)():raise ValueError('Linked portable update control path')
    previous=None
    if (plan_dir/'plan.json').is_file():
        try:previous=update_json((plan_dir/'plan.json').read_bytes())
        except ValueError:pass  # A malformed old descriptor cannot authorize cleanup.
    raw=(root/'PACKAGE-MANIFEST.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=value.get('installed_manifest_sha256'):raise ValueError('This backup does not reverse the current installation')
    old=update_json(raw);validate_manifest(root,old,verify=False);validate_metadata(update_metadata(root),old)
    check_installation_topology(root,manifest['files'],old['files'])
    # Copy before replacement: a failed restore leaves both current software
    # and the selected successful-update backup available for another attempt.
    stage=Path(tempfile.mkdtemp(prefix='Strata-T8-update-')).resolve()
    incoming=stage/'incoming';incoming.mkdir();token=uuid.uuid4().hex;published=False
    try:
        progress=Progress('Copying verified rollback files',sum(entry['size'] for entry in manifest['files']))
        for entry in manifest['files']:
            target=incoming/entry['path'];target.parent.mkdir(parents=True,exist_ok=True)
            source=backup/entry['path']
            with source.open('rb') as src,target.open('xb') as dst:
                copy_stream(src,dst,progress,target.name)
            shutil.copystat(source,target)
        progress.update(progress.current,force=True)
        shutil.copy2(backup/'PACKAGE-MANIFEST.json',incoming/'PACKAGE-MANIFEST.json')
        validate_manifest(incoming,manifest,progress=True)
        (stage/'stage-owner.json').write_text(__import__('json').dumps({'root':str(root),'token':token}),encoding='utf-8')
        plan={'root':str(root),'stage':str(incoming),'backup':str(backup_destination(root,token)),
              'new':manifest['files'],'old':old['files'],'version':manifest['version'],'stage_owner':token,
              'manifest_sha256':value['target_manifest_sha256'],'installed_manifest_sha256':hashlib.sha256(raw).hexdigest(),
              'action':'restore','restore_id':identity}
        publish_control_pair(root,plan_dir,plan)
        published=True
        discard_owned_pending_stage(root,previous)
        return plan
    except BaseException:
        if not published and stage.parent==Path(tempfile.gettempdir()).resolve() and stage.name.startswith('Strata-T8-update-') and not stage.is_symlink():
            shutil.rmtree(stage)
        raise
