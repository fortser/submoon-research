#!/bin/bash
set -uo pipefail
export DEBIAN_FRONTEND=noninteractive
STATUS=/workspace/p2_status
fail() { echo "FAILED $1 $(date -u +%FT%TZ)" > $STATUS; exit 1; }
echo "setup $(date -u +%FT%TZ)" > $STATUS
python3 - <<'VERIFY_SOURCES'
import hashlib, json, pathlib
root = pathlib.Path('/workspace/submoon-research-W2-P2-2a-56220810')
m = json.loads((root/'bundle_manifest.json').read_text())
for name, expected in m['files'].items():
    p = root/name
    if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
        raise SystemExit('SOURCE SHA MISMATCH: '+name)
print('All source files verified before installation:', len(m['files']))
VERIFY_SOURCES
[ "$?" -eq 0 ] || fail source-manifest
apt-get update -qq || fail apt-update
apt-get install -y -qq python3 python3-venv python3-dev build-essential git || fail apt-install
cd /workspace/submoon-research-W2-P2-2a-56220810
python3 -m venv .venv || fail venv
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements-w2.txt || fail pip
.venv/bin/python -m pip install --disable-pip-version-check -q --no-deps -e . || fail pip-project
.venv/bin/python -m pip freeze > /workspace/p2_pip_freeze.txt
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMBA_NUM_THREADS=1
export W2_REMOTE_EXECUTION=1 W2_INSTANCE_ID=56220810
rm -f /workspace/p2_stop
echo "gates $(date -u +%FT%TZ)" > $STATUS
.venv/bin/python -c "import rebound, reboundx" || fail reboundx
.venv/bin/python -m pytest -q tests/unit/test_compiled_dop853.py tests/unit/test_admission_evaluators.py tests/unit/test_events_shared_samples.py tests/unit/test_native_ias15_guard.py tests/unit/test_high_e_rule.py tests/unit/test_horizon_independence.py tests/unit/test_p2_2_stand.py tests/unit/test_verify_p2_2_run.py tests/unit/test_baseline_gate_p2_2.py || fail pytest
.venv/bin/python -m ruff check src scripts tests || fail ruff
taskset -pc 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31 $$ >/dev/null || fail affinity
mkdir -p runs/W2-P2-2a-56220810 runs/W2-P2-2a-baseline-56220810
{ date -u +%FT%TZ; lscpu; echo; nproc; cat /sys/fs/cgroup/cpu.max; cat /sys/fs/cgroup/memory.max; grep -E 'MemTotal|MemAvailable' /proc/meminfo; df -h /workspace; python3 -c "import os; print('affinity', sorted(os.sched_getaffinity(0)))"; } > runs/W2-P2-2a-56220810/hardware.txt 2>&1
PHYS=$(python3 - <<'PY'
import os, subprocess
allowed = os.sched_getaffinity(0)
cores = set()
for line in subprocess.check_output(['lscpu', '-p=CPU,CORE,SOCKET']).decode().splitlines():
    if line.startswith('#'):
        continue
    cpu, core, socket = line.split(',')[:3]
    if int(cpu) in allowed:
        cores.add((socket, core))
print(len(cores))
PY
)
QUOTA=$(awk '$1!="max"{printf "%d", $1/$2}' /sys/fs/cgroup/cpu.max 2>/dev/null)
MEMB=$(cat /sys/fs/cgroup/memory.max 2>/dev/null || echo max)
if [ "$MEMB" = "max" ] || [ -z "$MEMB" ]; then MEMB=$(awk '/MemTotal/{printf "%d", $2*1024}' /proc/meminfo); fi
MEMW=$(( MEMB / 483183820 ))
W=64
if [ "$PHYS" -gt 0 ] && [ "$PHYS" -lt "$W" ]; then W=$PHYS; fi
if [ -n "$QUOTA" ] && [ "$QUOTA" -gt 0 ] && [ "$QUOTA" -lt "$W" ]; then W=$QUOTA; fi
if [ "$MEMW" -gt 0 ] && [ "$MEMW" -lt "$W" ]; then W=$MEMW; fi
echo "hardware physical=$PHYS quota=${QUOTA:-none} mem_workers=$MEMW offer_limit=64 workers=$W min_workers=32" >> runs/W2-P2-2a-56220810/hardware.txt
[ "$W" -ge 32 ] || fail hardware
echo "baseline $(date -u +%FT%TZ) workers=$W" > $STATUS
BDL=$(date -u -d '+30 min' +%Y-%m-%dT%H:%M:%SZ)
.venv/bin/python -u scripts/admission_p2_2.py --per-cell 4 --b-per-cell 4 --unit-start 0 --years 1 10 --b-years 1 --workers $W --run-dir runs/W2-P2-2a-baseline-56220810 --deadline-utc $BDL --stop-file /workspace/p2_stop 2>&1 | tee runs/W2-P2-2a-baseline-56220810/stand_stdout.log
.venv/bin/python scripts/verify_p2_2_run.py --run runs/W2-P2-2a-baseline-56220810 --per-cell 4 --b-per-cell 4 --unit-start 0 --years 1 10 --b-years 1 --expect-complete --output runs/W2-P2-2a-baseline-56220810/acceptance.json || fail baseline-accept
.venv/bin/python scripts/baseline_gate_p2_2.py --baseline runs/W2-P2-2a-baseline-56220810 --workers $W --deadline-utc 2026-10-17T00:39:40Z --margin 1.15 --output runs/W2-P2-2a-56220810/baseline_gate.json || fail baseline
echo "running $(date -u +%FT%TZ) workers=$W physical=$PHYS quota=${QUOTA:-none} offer_limit=64 pin=0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31 seed_rows=0 deadline=2026-10-17T00:39:40Z" > $STATUS
.venv/bin/python -u scripts/admission_p2_2.py --per-cell 128 --b-per-cell 16 --unit-start 0 --years 1 10 100 1000 --b-years 100 1000 --workers $W --deadline-utc 2026-10-17T00:39:40Z --stop-file /workspace/p2_stop --run-dir runs/W2-P2-2a-56220810 2>&1 | tee -a runs/W2-P2-2a-56220810/stand_stdout.log
EXIT=${PIPESTATUS[0]}
.venv/bin/python scripts/verify_p2_2_run.py --run runs/W2-P2-2a-56220810 --expect-full --expect-decision-ready --output runs/W2-P2-2a-56220810/acceptance.json > runs/W2-P2-2a-56220810/acceptance_stdout.log 2>&1
ACC=$?
echo "done exit=$EXIT accept=$ACC $(date -u +%FT%TZ)" > $STATUS
