#!/bin/bash
# W2-T008: локальное Linux-окружение (WSL) для движков B/C с IAS15+REBOUNDx.
# Повторяет установку удалённого стека (scripts/vast_w2.py): системный python3,
# venv, закреплённые версии из requirements-w2.txt. Окружение создаётся в
# домашнем каталоге Linux (~/venvs/submoon-w2), а не в папке проекта на T:,
# чтобы не смешивать его с Windows-.venv и не замедлять импорты через /mnt/t.
# Повторный запуск безопасен: существующее окружение дополняется.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV="$HOME/venvs/submoon-w2"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$ROOT/scratch/claude/W2-T008_wsl_setup_$STAMP.log"
mkdir -p "$ROOT/scratch/claude"
exec > >(tee -a "$LOG") 2>&1

echo "== W2-T008 WSL setup $STAMP; project: $ROOT; venv: $VENV"
echo "== системные пакеты"
# Устанавливаются заранее из setup_WSL_env.bat через `wsl -u root` (без пароля).
for cmd in python3 gcc; do
    command -v "$cmd" >/dev/null || { echo "Нет $cmd: запустите setup_WSL_env.bat"; exit 1; }
done
python3 -c 'import ensurepip, sysconfig, os; assert os.path.exists(sysconfig.get_paths()["include"]+"/Python.h")' \
    || { echo "Нет python3-venv/python3-dev: запустите setup_WSL_env.bat"; exit 1; }
echo "== venv"
mkdir -p "$(dirname "$VENV")"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --disable-pip-version-check -q --upgrade pip
"$VENV/bin/python" -m pip install --disable-pip-version-check -r "$ROOT/requirements-w2.txt"
echo "== проверка"
"$VENV/bin/python" - <<'PY'
import platform, sys
print('python', sys.version.split()[0], platform.platform())
import numpy, scipy, numba, rebound, reboundx
for m in (numpy, scipy, numba, rebound, reboundx):
    print(f'{m.__name__:10} {getattr(m, "__version__", "?")}')
sim = rebound.Simulation(); sim.add(m=1.0); sim.add(m=0.0, a=1.0)
rebx = reboundx.Extras(sim); rebx.add_force(rebx.load_force('gravitational_harmonics'))
sim.integrator = 'ias15'; sim.integrate(1.0)
print('IAS15+REBOUNDx: OK')
PY
"$VENV/bin/python" -m pip freeze > "$ROOT/scratch/claude/W2-T008_wsl_freeze_$STAMP.txt"
echo "== готово; лог: scratch/claude/$(basename "$LOG")"
