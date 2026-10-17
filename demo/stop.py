"""Stop only the exact acknowledged local judge DB; retain containers, volumes and state."""
import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from .prepare import verify_prepared

PROJECTION = ('{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},'
              '"network":{{json .HostConfig.NetworkMode}},"ports":{{json .HostConfig.PortBindings}},'
              '"restart":{{json .HostConfig.RestartPolicy.Name}},"mounts":{{json .Mounts}},'
              '"state":{{json .State}}}')
ID = re.compile(r'[a-f0-9]{64}')
IMAGE = re.compile(r'sha256:[a-f0-9]{64}')


def need(value):
    if not value:
        raise ValueError('judge_stop_guard_failed')


def read(path, maximum=65536):
    need(not path.is_symlink() and path.is_file() and path.stat().st_size <= maximum)
    return path.read_text()


def snapshot(root):
    need(root.is_dir() and not root.is_symlink())
    result = {}; total = 0
    for p in sorted(root.rglob('*')):
        need(not p.is_symlink())
        if p.is_dir():
            continue
        need(p.is_file()); total += p.stat().st_size
        need(len(result) < 256 and total <= 16 * 1024 * 1024)
        result[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return result


def save(path, value):
    with path.open('x') as f:
        json.dump(value, f, sort_keys=True); f.write('\n'); f.flush(); os.fsync(f.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def stop(output, confirmed=False):
    need(confirmed)
    root = Path(output).absolute()
    need(not any(p.is_symlink() for p in (root, *root.parents)))
    verify_prepared(root)
    prepared = json.loads(read(root/'PREPARED.json'))
    name = read(root/'identity', 64).strip()
    need(re.fullmatch(r'mml-judge-[a-f0-9]{12}', name) and prepared['identity'] == name)
    attempt = root/'attempt'; need(attempt.is_dir() and not attempt.is_symlink())
    need(not (attempt/'stop-intent.json').exists() and not (attempt/'stop-result.json').exists())
    db = read(attempt/'db-container-id.txt', 80).strip(); need(ID.fullmatch(db))
    client_image = read(attempt/'client-image-id.txt', 80).strip(); need(IMAGE.fullmatch(client_image))
    digests = json.loads(read(attempt/'db-image-digests.json'))
    need(isinstance(digests, list) and len(digests) == 1 and isinstance(digests[0], str)
         and re.fullmatch(r'falkordb/falkordb-server@sha256:[a-f0-9]{64}', digests[0]))
    before = snapshot(root/'state')
    env = {k:v for k,v in os.environ.items()
           if k not in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH')}

    def run(args, timeout=10):
        p = subprocess.run(['docker', *args], env=env, capture_output=True, timeout=timeout)
        need(p.returncode == 0 and len(p.stdout) <= 65536 and len(p.stderr) <= 4096)
        return p.stdout.decode().strip()

    endpoint = run(['context','inspect','--format','{{.Endpoints.docker.Host}}'])
    need(endpoint.startswith('unix:///') and '\n' not in endpoint and len(endpoint) <= 1024)

    def docker(args, timeout=10):
        return run(['--host',endpoint,*args], timeout)

    image = json.loads(docker(['image','inspect','--format',
        '{"id":{{json .Id}},"volumes":{{json (index .Config "Volumes")}}}',digests[0]]))
    need(isinstance(image, dict) and IMAGE.fullmatch(image.get('id','')))
    declared = image.get('volumes') or {}; need(isinstance(declared, dict))

    def inspect(identity):
        return json.loads(docker(['inspect','--format',PROJECTION,identity]))

    def owned_db(value):
        need(value['id'] == db and value['name'] == '/'+name+'-db' and value['image'] == image['id']
             and value['network'] == 'none' and not value['ports'] and value['restart'] == 'no'
             and value['state']['Paused'] is False and value['state']['Restarting'] is False)
        mounts = value['mounts']; need(isinstance(mounts, list))
        data = [m for m in mounts if m['Destination'] == '/data']
        need(len(data) == 1 and data[0]['Type'] == 'volume' and data[0]['Name'] == name+'-data' and data[0]['RW'] is True)
        for m in mounts:
            need(m['Type'] == 'volume' and m['RW'] is True)
            if m['Destination'] != '/data':
                need(m['Destination'] in declared and re.fullmatch(r'[a-f0-9]{64}', m['Name']))
        return sorted((m['Name'],m['Destination'],m['RW']) for m in mounts)

    current = inspect(db); mounts = owned_db(current)
    client = inspect(name+'-client')
    saved = json.loads(read(attempt/'client-state.json'))
    need(ID.fullmatch(client['id']) and client['name'] == '/'+name+'-client' and client['image'] == client_image
         and client['network'] == 'container:'+db and client['state']['Running'] is False
         and client['state']['Paused'] is False and client['state']['Restarting'] is False
         and client['state']['ExitCode'] == saved['ExitCode'] and client['state']['OOMKilled'] == saved['OOMKilled'])
    # Save intent before the only mutation. Never delete/recreate/restart or scan containers.
    save(attempt/'stop-intent.json', {'status':'stop_intent','DB_id':db,'state_sha256':before,
        'volumes':mounts,'was_running':current['state']['Running']})
    if current['state']['Running']:
        need(docker(['stop','--timeout','10',db], 25) == db)
    after = inspect(db); need(owned_db(after) == mounts and after['state']['Running'] is False)
    need(snapshot(root/'state') == before)
    result = {'status':'own_judge_DB_stopped_results_retained','DB_id':db,
              'stop_invocations':int(bool(current['state']['Running'])),
              'running':False,'volumes_retained':True,'state_unchanged':True}
    save(attempt/'stop-result.json', result)
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True)
    p.add_argument('--confirm-stop-own-db', action='store_true')
    a = p.parse_args(argv)
    try:
        print(json.dumps(stop(a.output,a.confirm_stop_own_db), sort_keys=True)); return 0
    except Exception:
        # Never forward Docker stderr, inspection, paths, daemon endpoints or arbitrary errors.
        print('{"status":"judge_stop_refused_or_unconfirmed","automatic_retry":false}'); return 2


if __name__ == '__main__':
    raise SystemExit(main())
