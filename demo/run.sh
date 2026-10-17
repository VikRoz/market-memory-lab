#!/bin/sh
# Explicit local judge action. Never executed by prepare/verify/tests.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "${1:-}" != "--confirm-synthetic-rules" ] || [ "$#" -ne 1 ]; then
  printf '%s\n' 'Review README and the synthetic confirmed review method; then use --confirm-synthetic-rules.'
  exit 2
fi
cd "$HERE"
# Verify only the bounded build context, with the standard library, before Docker.
python3 - <<'PY'
import hashlib,json,pathlib,re,sys
if sys.version_info < (3,10): raise SystemExit('host_python_3_10_or_newer_required')
r=pathlib.Path.cwd(); s=json.loads((r/'PREPARED.json').read_text())
assert re.fullmatch(r'mml-judge-[a-f0-9]{12}',s['identity'])
for n,h in s['files'].items():
 p=r/n
 assert not p.is_symlink() and p.resolve().is_relative_to(r.resolve())
 assert hashlib.sha256(p.read_bytes()).hexdigest()==h
actual={p.relative_to(r).as_posix() for p in r.rglob('*') if p.is_file() and p.relative_to(r).parts[0] not in ('state','attempt')}
assert actual==set(s['files'])|{'PREPARED.json'}
PY
# Atomic attempt latch. Any failure consumes this attempt; no resume/new graph.
mkdir attempt
mkdir state state/runtime
unset DOCKER_HOST DOCKER_CONTEXT DOCKER_TLS_VERIFY DOCKER_CERT_PATH
ENDPOINT=$(docker context inspect --format '{{.Endpoints.docker.Host}}')
case "$ENDPOINT" in unix:///*) ;; *) printf '%s\n' 'STOP: a local Unix Docker context is required.'; exit 2 ;; esac
d() { docker --host "$ENDPOINT" "$@"; }
d version --format '{{.Server.Version}}' > attempt/engine-version.txt
NAME=$(cat identity)
# Preparation only: public dependencies; no credentials/config/cache are mounted.
d pull falkordb/falkordb-server:v4.20.6
d pull python:3.12.14-slim-bookworm
d image inspect --format '{{json .RepoDigests}}' falkordb/falkordb-server:v4.20.6 > attempt/db-image-digests.json
d image inspect --format '{{json .RepoDigests}}' python:3.12.14-slim-bookworm > attempt/python-image-digests.json
d build --tag "$NAME-client" .
d image inspect --format '{{.Id}}' "$NAME-client" > attempt/client-image-id.txt
DB_ID=$(d image inspect --format '{{.Id}}' falkordb/falkordb-server:v4.20.6)
CLIENT_ID=$(cat attempt/client-image-id.txt)
# No host ports, browser, outside network, auth, existing DB or Docker socket mount.
d run --detach --name "$NAME-db" --network none --cpus 0.5 --memory 512m --pids-limit 64 \
  --restart no --volume "$NAME-data:/data" \
  --env 'REDIS_ARGS=--bind 127.0.0.1 --dir /data --appendonly yes' \
  --env 'FALKORDB_ARGS=THREAD_COUNT 1' --env OMP_NUM_THREADS=1 \
  --health-cmd 'redis-cli -h 127.0.0.1 ping' --health-interval 1s --health-timeout 2s --health-retries 3 \
  "$DB_ID" > attempt/db-container-id.txt
READY=0
for WAIT in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
  HEALTH=$(d inspect --format '{{.State.Health.Status}}' "$NAME-db")
  case "$HEALTH" in healthy) READY=1; break ;; starting) sleep 1 ;; *) exit 2 ;; esac
done
[ "$READY" = 1 ]
# Shared network namespace provides only loopback to this brand-new DB. Its
# network is 'none': no L2/host port, internet or model backend is reachable.
# All child readers share one persistent bind-mounted lock/journal directory.
if d run --name "$NAME-client" --network "container:$NAME-db" \
  --user "$(id -u):$(id -g)" \
  --cpus 0.5 --memory 256m --memory-swap 256m --pids-limit 64 --restart no \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=32m \
  --mount "type=bind,src=$HERE/state,dst=/state" \
  --mount "type=bind,src=$HERE/state/runtime,dst=/app/local-runtime" \
  --env MML_FALKOR_HOST=127.0.0.1 --env MML_FALKOR_PORT=6379 --env MML_FALKOR_TLS=0 \
  --env MML_RUN_INTEGRATION=0 \
  --env "MML_JUDGE_CONFIRM_FIXTURE_SHA256=$(cat fixture-digest)" \
  "$CLIENT_ID" > state/stdout.json; then
  CLIENT_EXIT=0
else
  CLIENT_EXIT=$?
fi
printf '%s\n' "$CLIENT_EXIT" > attempt/client-exit.txt
# Read-only diagnostics for this exact client also on failure; never another run.
d inspect --format '{{json .State}}' "$NAME-client" > attempt/client-state.json
[ "$CLIENT_EXIT" -eq 0 ]
cat state/summary.json
# No automatic stop/delete/flush/restart/cleanup. Retain this exact attempt.
