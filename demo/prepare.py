"""Prepare a checksummed, source-only isolated demo without executing anything."""
import hashlib
import json
import re
import uuid
from pathlib import Path, PurePosixPath

from .replay import SAVED, verify

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ('Dockerfile', 'run.sh', 'container_main.py', '.dockerignore')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe(root, name):
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or str(p) != name:
        raise ValueError('unsafe_release_path')
    path = root / name
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('unsafe_release_file')
    return path


def release_files():
    manifest = json.loads((ROOT / 'RELEASE_SHA256.json').read_text())
    files = manifest['files']
    if not 1 <= len(files) <= 128:
        raise ValueError('release_file_bound')
    for name, digest in files.items():
        p = safe(ROOT, name)
        if not p.is_file() or p.stat().st_size > 128000 or sha(p) != digest:
            raise ValueError('release_file_changed')
    return files


def prepare(output):
    verify()
    files = release_files()
    selected = [n for n in files if n == 'requirements.lock' or n == 'demo/saved_run.json'
                or (n.split('/')[0] in ('market_memory_lab', 'agent', 'demo') and n.endswith('.py'))]
    for name in TEMPLATES:
        if 'demo/' + name not in files:
            raise ValueError('missing_runtime_template')
    target = Path(output).absolute()
    if any(p.is_symlink() for p in (target, *target.parents)):
        raise ValueError('symlink_output')
    target.mkdir(parents=True, exist_ok=False)
    for name in selected:
        p = target / 'source' / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes((ROOT / name).read_bytes())
    for name in TEMPLATES:
        (target / name).write_bytes((ROOT / 'demo' / name).read_bytes())
    identity = 'mml-judge-' + uuid.uuid4().hex[:12]
    (target / 'identity').write_text(identity + '\n')
    digest = sha(SAVED)
    (target / 'fixture-digest').write_text(digest + '\n')
    state = dict(status='prepared_offline_no_DB', identity=identity,
                 scenario='two_conversation_evidence_memory', model_calls=0, database_calls=0,
                 live_run_performed=False, writer='SAVED ACTUAL MODEL RESPONSES / NO NEW LLM CALLS',
                 db_image_tag='falkordb/falkordb-server:v4.20.6',
                 python_image_tag='python:3.12.14-slim-bookworm',
                 synthetic_confirmation_fixture_sha256=digest,
                 files={p.relative_to(target).as_posix(): sha(p)
                        for p in sorted(target.rglob('*')) if p.is_file()})
    (target / 'PREPARED.json').write_text(json.dumps(state, sort_keys=True) + '\n')
    verify_prepared(target)
    return {k: v for k, v in state.items() if k != 'files'}


def verify_prepared(output):
    root = Path(output).absolute()
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('symlink_output')
    state = json.loads((root / 'PREPARED.json').read_text())
    if not re.fullmatch(r'mml-judge-[a-f0-9]{12}', state['identity']):
        raise ValueError('invalid_identity')
    for name, digest in state['files'].items():
        p = safe(root, name)
        if not p.is_file() or sha(p) != digest:
            raise ValueError('prepared_file_changed')
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()
              and p.relative_to(root).parts[0] not in ('state', 'attempt')}
    if actual != set(state['files']) | {'PREPARED.json'}:
        raise ValueError('unexpected_prepared_file')
    return dict(status='prepared_files_verified_offline', model_calls=0, database_calls=0)
