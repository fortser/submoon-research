"""Воспроизводимые контрольные примеры аудита W2-T009, не научный ансамбль."""
import argparse
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import importlib.metadata
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
    os.environ[key] = '1'
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'scripts')]
import numpy as np  # noqa: E402
from submoon_research.dynamics import compiled_dop853 as cd  # noqa: E402
from submoon_research.dynamics.engine_compare import NumericMode  # noqa: E402
from submoon_research.dynamics.kepler_reference import pericenter_seed, pericenter_reference  # noqa: E402
from submoon_research.dynamics.nominal import relative_rhs  # noqa: E402
from submoon_research.workflows import w2_comparison as wc  # noqa: E402
import admission_p1_equivalence as p1  # noqa: E402
import admission_p2_horizons as p2  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output-dir', default='results/evidence/W2-quality-controls-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
args = parser.parse_args()
OUT = ROOT/args.output_dir
RUN_ID = OUT.name
OUT.mkdir(parents=True, exist_ok=True)
if (OUT/'probes.json').exists():
    raise FileExistsError('Результат уже существует; укажите новый --output-dir')
results = dict(run_id=RUN_ID, data_kind='synthetic_audit_controls_not_probability_sample',
               utc=datetime.now(timezone.utc).isoformat(), prototype_version=cd.PROTOTYPE_VERSION,
               checks={}, limitations=['Не допуск W2/V07; нет длинного ансамбля и независимого B.'])
for name in ('numpy', 'scipy', 'numba', 'mpmath', 'rebound', 'reboundx'):
    try:
        results.setdefault('versions', {})[name] = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        results.setdefault('versions', {})[name] = None

def save():
    (OUT/'probes.json').write_text(json.dumps(results, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')

# P1: совпавшее событие и единственный общий старт скрывают различную конечную скорость.
case = dict(orbit_id='synthetic', host='synthetic', period_seconds=2*math.pi, a_km=1.)
row = dict(periods=200., run_status='completed', error=None, outcome='host_contact',
           last_valid_time=1., event=dict(event='reference_contact', time=1., body=0),
           samples=[(0., [1.,0.,0.,0.,1.,0.])], final_probe=[.5,0.,0.,0.,1.,0.],
           steps=10, nfev=150, python_event_steps=1, wall=.1)
other = copy.deepcopy(row)
other['last_valid_time'] += 1e-10
other['event']['time'] += 1e-10
other['final_probe'][4] = 10.
results['checks']['p1_start_only_false_trajectory_pass'] = p1.compare(row, other, case)

# P2: диагностическое расхождение не закрывает вопрос его численной причины.
def p2row(variant, end_x):
    return dict(orbit_id='synthetic', variant=variant, run_status='completed', error=None,
                outcome='survived', event=None, last_valid_time=1.,
                samples=[(0., [1.,0.,0.,0.,1.,0.]), (1., [end_x,0.,0.,0.,1.,0.])])
results['checks']['p2_large_divergence_pass'] = p2.evaluate([p2row('D',1.),p2row('T',2.),p2row('B',3.)], [case], 1.)
results['checks']['p2_empty_pass'] = p2.evaluate([], [case], 1.)

# Независимый Кеплер для binary64 старта: режимы действительно используемых допусков.
started = time.monotonic()
def budget():
    if time.monotonic()-started > 120:
        raise TimeoutError('120 s audit budget')

kepler = []
for e in (0., .5, .9, .99):
    initial = np.vstack([np.zeros(6), pericenter_seed(e)])
    times = np.linspace(0,20*math.pi,513)
    reference, meta = pericenter_reference(initial[-1], times)
    for label, mode in [('default', NumericMode()), ('tight', NumericMode.tight())]:
        out = cd.integrate_compiled(initial,[1.],[],[1e-4],times[-1],period=2*math.pi,
                                    mode=mode,times=times,budget=budget)
        actual = np.array([s['state'][-6:] for s in out['samples']])
        item = dict(e=e, mode=label, numeric=asdict(mode), run_status=out['run_status'],
                    production_allowed=out['production_allowed'], outcome=out['physical_outcome'],
                    reported_massive_energy_drift=out['massive_energy_relative_drift'],
                    eccentricity_monitor=out['eccentricity_monitor'], steps=out['steps'])
        if len(actual)==len(reference):
            energy=np.sum(actual[:,3:]**2,axis=1)/2-1/np.linalg.norm(actual[:,:3],axis=1)
            angular=np.linalg.norm(np.cross(actual[:,:3],actual[:,3:]),axis=1)
            item.update(position_a=float(np.linalg.norm(actual[:,:3]-reference[:,:3],axis=1).max()),
                        velocity_na=float(np.linalg.norm(actual[:,3:]-reference[:,3:],axis=1).max()),
                        probe_energy_relative_drift=float(np.max(abs(energy/meta['energy_from_rounded_seed']-1))),
                        probe_angular_relative_drift=float(np.max(abs(angular/meta['angular_momentum_from_rounded_seed']-1))))
        kepler.append(item)
        results['checks']['kepler_default_tight']=kepler
        save()

# Критерии V02/CR3BP/J2 существующего стенда, но непосредственно на новом backend.
original_engine=wc.integrate_engine
wc.integrate_engine=lambda engine,*args,**kwargs: cd.integrate_compiled(*args,**kwargs)
try:
    results['checks']['compiled_analytical_controls']=wc.analytical_controls('compiled_audit',budget)
finally:
    wc.integrate_engine=original_engine
save()

# Несовместимая версия реализации должна менять контракт рестарта.
initial=np.array([[0.,0.,0.,0.,0.,0.],[1.,0.,0.,0.,1.,0.]])
saved=[]
def interrupt(checkpoint):
    saved.append(copy.deepcopy(checkpoint))
    raise TimeoutError('audit interruption')
partial=cd.integrate_compiled(initial,[1.],[],[.01],2.,period=2*math.pi,batch_steps=1,progress=interrupt)
version=cd.PROTOTYPE_VERSION
cd.PROTOTYPE_VERSION=version+1000
try:
    resumed=cd.integrate_compiled(initial,[1.],[],[.01],2.,period=2*math.pi,resume=saved[0])
    results['checks']['cross_version_resume']=dict(old_version=version,new_version=cd.PROTOTYPE_VERSION,
        accepted=resumed['run_status']=='completed',same_fingerprint=partial['checkpoint']['contract_sha256']==resumed['checkpoint']['contract_sha256'],
        explanation='Версия изменена только в памяти аудиторского процесса; файлы движка не менялись.')
finally:
    cd.PROTOTYPE_VERSION=version

# Монитор проверяет конец шага до локализации терминального события.
initial=np.array([[0.,0.,0.,0.,0.,0.],[1.,0.,0.,0.,math.sqrt(1.95),0.]])
out=cd.integrate_compiled(initial,[1.],[],[1.],1.,period=2*math.pi/(.05**1.5))
results['checks']['eccentricity_after_terminal_event']=dict(last_valid_time=out['last_valid_time'],
    event=out['event'],monitor=out['eccentricity_monitor'])

# Сравнительный original backend не поддерживает J2 хозяина (пока исключено моделью L1).
figures=[dict(index=0,j2=.01,radius=.2,pole=[0.,0.,1.])]
try:
    relative_rhs(initial.ravel(),np.array([1.]),figures)
    host_result=dict(raised=False)
except ValueError as exc:
    host_result=dict(raised=True,error=str(exc))
results['checks']['original_host_j2_unsupported']=host_result
results['elapsed_seconds']=time.monotonic()-started
save()
print(json.dumps(dict(run_id=RUN_ID,output=str(OUT/'probes.json'),elapsed_seconds=results['elapsed_seconds'])))
