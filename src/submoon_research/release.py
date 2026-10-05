"""Пакет кода/научных файлов с SHA; восстановление только в новую пустую папку."""
import hashlib
import json
import subprocess
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED

from submoon_research.provenance import project_path
from submoon_research.tracking import utc_now
from submoon_research.workflows.smoke import sha256,write_json

ROOTS=('data/raw','data/processed','data/interim','data/kernels','data/acquisition/astrobridge','references/papers','runs')


def release_files(root):
    result=subprocess.run(['git','ls-files','-z','--cached','--others','--exclude-standard'],cwd=root,
        check=True,capture_output=True,text=True,encoding='utf-8',timeout=30)
    names=set(result.stdout.split('\0'))- {''}
    for folder in ROOTS:
        names.update(p.relative_to(root).as_posix() for p in (root/folder).rglob('*') if p.is_file())
    return sorted(name for name in names if not name.startswith(('scratch/','build/','.git/','.venv/','temp_scripts/'))
        and name!='configs/paths.local.yaml'
        and (not Path(name).name.startswith('.env') or Path(name).name=='.env.example')
        and Path(name).suffix not in ('.key','.pem','.pyc') and (root/name).is_file())


def build_release(root,destination):
    root=Path(root).resolve()
    destination=Path(destination)
    if destination.exists():
        raise ValueError('Не перезаписывать выпуск')
    names=release_files(root)
    entries=[]
    destination.parent.mkdir(parents=True,exist_ok=True)
    with ZipFile(destination,'x',compression=ZIP_DEFLATED,compresslevel=6) as archive:
        for name in names:
            data=project_path(root,name).read_bytes()
            archive.writestr(name,data)
            entries.append(dict(path=name,sha256=hashlib.sha256(data).hexdigest(),bytes=len(data)))
        manifest=dict(schema_version='0.1',created_utc=utc_now(),files=entries,
            scope='workspace_release_with_registered_scientific_originals_and_runs',
            excludes=['environment','secrets','local_paths','git_object_store','scratch'],
            backup_status='local_package_only_until_independent_copy_restore_verified')
        archive.writestr('release.manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2,allow_nan=False))
    write_json(destination.with_suffix('.receipt.json'),dict(path=destination.name,sha256=sha256(destination),
        bytes=destination.stat().st_size,files=len(entries),uncompressed_bytes=sum(e['bytes'] for e in entries)))
    return manifest


def restore_release(package,destination):
    package,destination=Path(package),Path(destination).resolve()
    if destination.exists():
        raise ValueError('Восстановление разрешено только в новую пустую папку')
    with ZipFile(package) as archive:
        manifest=json.loads(archive.read('release.manifest.json'))
        names=[item['path'] for item in manifest['files']]
        if len(names)!=len(set(names)) or set(archive.namelist())!=set(names)|{'release.manifest.json'}:
            raise ValueError('Дубликаты/лишние члены выпуска')
        # Проверить каждый путь до первой записи; ZIP extractall не используется.
        for name in names:
            if Path(name).is_absolute() or ':' in name or '\\' in name:
                raise ValueError('Небезопасный путь выпуска')
            project_path(destination,name)
        destination.mkdir(parents=True,exist_ok=False)
        for item in manifest['files']:
            data=archive.read(item['path'])
            if len(data)!=item['bytes'] or hashlib.sha256(data).hexdigest()!=item['sha256']:
                raise ValueError(f"Повреждён выпуск: {item['path']}")
            target=project_path(destination,item['path'])
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(data)
        checks={item['path']:sha256(project_path(destination,item['path']))==item['sha256'] for item in manifest['files']}
    write_json(destination/'restore.validation.json',dict(status='passed' if all(checks.values()) else 'failed',
        package_sha256=sha256(package),checked_files=len(checks),utc=utc_now()))
    return dict(status='passed',checked_files=len(checks),package_sha256=sha256(package))
