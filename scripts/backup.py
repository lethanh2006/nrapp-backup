#!/usr/bin/env python3
"""Encrypted NRApp backups. Credentials are read from running containers."""
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path('/opt/nrapp/backup')
STORE = Path('/opt/nrapp-backups')
BACKEND = Path('/opt/nrapp/backend')
DC = '/home/deploy/bin/dc'


def run(args, **kwargs):
    return subprocess.run(args, check=True, timeout=600, **kwargs)


def container(service, required=True):
    result = run([DC, 'ps', '-q', service], capture_output=True, text=True)
    cid = result.stdout.strip()
    if required and not cid:
        raise RuntimeError('Missing running service: ' + service)
    return cid


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def backup():
    settings = json.loads((ROOT / 'config.json').read_text())
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target = STORE / ('nrapp-' + stamp + '.tar.age')
    with tempfile.TemporaryDirectory(prefix='.work-', dir=STORE) as work:
        work = Path(work)
        # Capture credentials directly from the existing service without logging them.
        raw = run(['docker', 'exec', container('user'), 'node', '-e',
                   'console.log(JSON.stringify({uri:process.env.MONGO_URL,db:process.env.MONGO_DB_NAME}))'],
                  capture_output=True, text=True)
        mongo = json.loads(raw.stdout)
        if not mongo.get('uri') or not mongo.get('db'):
            raise RuntimeError('Missing MongoDB URI/database in user service')
        config = work / 'mongo-config.yml'
        config.write_text('uri: ' + json.dumps(mongo['uri']) + '\n')
        config.chmod(0o600)
        # Do not use --oplog on Atlas Free. This is a live dump, not a global snapshot.
        print('BACKUP_STAGE: MongoDB', file=sys.stderr)
        tool_name = 'nrapp-mongodump-' + stamp.lower()
        with (work / 'mongo-tools.log').open('wb') as log:
            try:
                run(['docker', 'run', '--rm', '--name', tool_name,
                     '--user', str(os.getuid()) + ':' + str(os.getgid()),
                     '--network', 'host', '--memory', '256m', '--cpus', '0.75',
                     '-v', str(work) + ':/backup', settings['mongo_image'],
                     'mongodump', '--config=/backup/mongo-config.yml', '--db=' + mongo['db'],
                     '--archive=/backup/mongo.archive.gz', '--gzip', '--numParallelCollections=1'],
                    stdout=log, stderr=log)
            finally:
                subprocess.run(['docker', 'rm', '-f', tool_name], check=False, timeout=30,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        config.unlink()
        run(['gzip', '-t', str(work / 'mongo.archive.gz')])
        redis = container('redis')
        print('BACKUP_STAGE: Redis', file=sys.stderr)
        remote_rdb = '/tmp/nrapp-backup-' + stamp + '.rdb'
        try:
            run(['docker', 'exec', redis, 'redis-cli', '--rdb', remote_rdb],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            run(['docker', 'exec', redis, 'redis-check-rdb', remote_rdb],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            run(['docker', 'cp', redis + ':' + remote_rdb, str(work / 'redis.rdb')],
                stdout=subprocess.DEVNULL)
        finally:
            run(['docker', 'exec', redis, 'rm', '-f', remote_rdb])
        rabbit = container('rabbitmq')
        print('BACKUP_STAGE: RabbitMQ', file=sys.stderr)
        definitions = '/tmp/nrapp-backup-' + stamp + '.json'
        try:
            run(['docker', 'exec', rabbit, 'rabbitmqctl', 'export_definitions', definitions],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            run(['docker', 'cp', rabbit + ':' + definitions, str(work / 'rabbitmq-definitions.json')],
                stdout=subprocess.DEVNULL)
        finally:
            run(['docker', 'exec', rabbit, 'rm', '-f', definitions])
        json.loads((work / 'rabbitmq-definitions.json').read_text())
        postgres = container('payment-postgres', required=False)
        if postgres:
            with (work / 'payment.dump').open('wb') as output:
                run(['docker', 'exec', postgres, 'sh', '-ec',
                     'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc'], stdout=output)
        files = [BACKEND / '.env', BACKEND / 'compose.yaml', BACKEND / 'compose.vps.yaml']
        files += sorted(BACKEND.glob('*/.env'))
        files += sorted((BACKEND / 'docker').rglob('*'))
        with tarfile.open(work / 'backend-config.tar.gz', 'w:gz') as archive:
            for file in files:
                if file.is_file() and not file.is_symlink():
                    archive.add(file, arcname=str(file.relative_to(BACKEND)), recursive=False)
        manifest = {
            'created_utc': stamp, 'mongo_database': mongo['db'],
            'mongo_image': settings['mongo_image'], 'mongo_consistency': 'live-per-collection',
            'postgres': bool(postgres), 'rabbitmq_messages': False,
            'cloudinary_media': False, 'release': (ROOT / 'current/REVISION').read_text().strip(),
        }
        (work / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        payloads = sorted(p for p in work.iterdir() if p.name != 'mongo-tools.log')
        for path in payloads:
            if path.stat().st_size == 0:
                raise RuntimeError('Empty backup component: ' + path.name)
        (work / 'SHA256SUMS').write_text(''.join(checksum(p) + '  ' + p.name + '\n' for p in payloads))
        payloads.append(work / 'SHA256SUMS')
        tarpath = work / 'payload.tar'
        with tarfile.open(tarpath, 'w') as archive:
            for path in payloads:
                archive.add(path, arcname=path.name, recursive=False)
        partial = target.with_suffix('.partial')
        print('BACKUP_STAGE: Encryption', file=sys.stderr)
        try:
            run([str(ROOT / 'bin/age'), '-r', settings['age_recipient'], '-o', str(partial), str(tarpath)])
            partial.chmod(0o600)
            partial.rename(target)
        finally:
            partial.unlink(missing_ok=True)
    digest = target.with_suffix(target.suffix + '.sha256')
    digest.write_text(checksum(target) + '  ' + target.name + '\n')
    digest.chmod(0o600)
    latest = STORE / '.latest.tmp'
    latest.write_text(target.name + '\n')
    latest.replace(STORE / 'LATEST')
    # Prune only our encrypted archives after a new successful backup; keep >=7 copies.
    archives = sorted(STORE.glob('nrapp-*.tar.age'), reverse=True)
    for old in archives[7:]:
        if time.time() - old.stat().st_mtime > 14 * 86400:
            old.unlink()
            old.with_suffix(old.suffix + '.sha256').unlink(missing_ok=True)
    print('BACKUP_OK: ' + target.name, file=sys.stderr)
    return target


def main():
    os.umask(0o077)
    STORE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STORE / '.backup.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        command = sys.argv[1] if len(sys.argv) == 2 else ''
        if command == 'run':
            backup()
        elif command == 'export':
            latest = STORE / 'LATEST'
            name = latest.read_text().strip() if latest.exists() else ''
            if name and (Path(name).name != name or not name.startswith('nrapp-') or not name.endswith('.tar.age')):
                raise RuntimeError('Invalid latest backup name')
            target = STORE / name if name else None
            if target is None or not target.is_file() or time.time() - target.stat().st_mtime > 23 * 3600:
                target = backup()
            expected = target.with_suffix(target.suffix + '.sha256').read_text().split()[0]
            if checksum(target) != expected:
                raise RuntimeError('Backup checksum mismatch')
            # stdout is binary transport only; never print logs here.
            with tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as archive:
                for path in [target, target.with_suffix(target.suffix + '.sha256')]:
                    archive.add(path, arcname=path.name, recursive=False)
        else:
            raise RuntimeError('Usage: backup.py run|export')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Tool output may contain credentials. Show only a safe class of failure.
        print('BACKUP_FAILED: ' + type(error).__name__ + '; inspect VPS configuration.', file=sys.stderr)
        sys.exit(1)
