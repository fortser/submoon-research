"""W2-P012: selective reset внутренней памяти IAS15 из native snapshot.

Только диагностический эксперимент в памяти отдельного процесса. Не production
fallback, не изменение сил, guard, epsilon, PRS23 или min_dt. Смысл вариантов —
docs/tasks/W2_B_memory_controls_v1.md.
"""
from __future__ import annotations

import ctypes
import math
import time

import numpy as np

from submoon_research.contracts.p2_files import canonical, digest
from submoon_research.diagnostics.ias15_controller import reconstruct_prs23
from submoon_research.diagnostics.ias15_replay import adaptation, guard_reason
from submoon_research.dynamics.engine_compare import ias15_step_limit

BUFFERS = ('at', 'x0', 'v0', 'a0', 'csx', 'csv', 'csa0', 'g', 'b', 'csb', 'e', 'br', 'er')
DP7 = frozenset({'g', 'b', 'csb', 'e', 'br', 'er'})
POLYNOMIAL = ('b', 'e', 'g', 'br', 'er', 'csb')
COMPENSATION = ('csx', 'csv')
VARIANTS = ('M0', 'MP', 'MC', 'MD', 'MA')
MASKS = {
    'M0': dict(buffers=(), dt_last_done=False),
    'MP': dict(buffers=POLYNOMIAL, dt_last_done=False),
    'MC': dict(buffers=COMPENSATION, dt_last_done=False),
    'MD': dict(buffers=(), dt_last_done=True),
    'MA': dict(buffers=POLYNOMIAL+COMPENSATION, dt_last_done=True),
}
SCALARS = ('G', 't', 'dt', 'dt_last_done', 'N', 'N_var', 'N_active', 'testparticle_type',
           'force_is_velocity_dependent', 'gravity', 'softening', 'exact_finish_time', 'steps_done')


def _descriptor(native, name):
    integrator = native.sim.integrator
    fd = integrator.callbacks.field_descriptor_list.field_descriptor_for_name(name)
    if fd is None:
        raise ValueError('Нет field descriptor IAS15: '+name)
    expected = 7*8 if name in DP7 else 8
    if int(fd.element_size) != expected:
        raise ValueError('Не совпал element_size IAS15 для '+name)
    pointer = ctypes.c_void_p.from_address(integrator.state+fd.offset).value
    allocated = ctypes.c_size_t.from_address(integrator.state+fd.offset_N).value
    return pointer, int(allocated), int(fd.element_size)


def buffer_view(native, name):
    """Записываемый view выделенного буфера; NULL и короткий буфер — отказ до записи."""
    if name not in BUFFERS:
        raise ValueError('Неизвестный буфер IAS15: '+name)
    pointer, allocated, element_size = _descriptor(native, name)
    n3 = 3*native.count
    if not pointer:
        raise ValueError('Буфер IAS15 не выделен (NULL): '+name)
    if allocated != n3:
        raise ValueError(f'N_allocated={allocated} не равен 3N={n3} для {name}')
    size = allocated*element_size//8
    return np.ctypeslib.as_array(ctypes.cast(pointer, ctypes.POINTER(ctypes.c_double)), shape=(size,))


def _scalar(value):
    return value if isinstance(value, (str, int, float, bool, type(None))) else str(value)


def memory_state(native, figures=()):
    sim = native.sim
    buffers = {}
    for name in BUFFERS:
        values = buffer_view(native, name).copy()
        buffers[name] = dict(values=values.tolist(), sha256=digest(values.tolist()),
                             nonzero=int(np.count_nonzero(values)),
                             max_abs=float(np.max(np.abs(values))) if values.size else 0.)
    params = []
    for f in figures:
        p = sim.particles[f['index']].params
        params.append(dict(index=f['index'], J2=float(p['J2']), R_eq=float(p['R_eq']),
                           Omega=[float(x) for x in p['Omega']]))
    state = native.absolute_state()
    return dict(buffers=buffers, scalars={k: _scalar(getattr(sim, k, None)) for k in SCALARS},
                adaptation=adaptation(native), absolute_state=state.tolist(),
                absolute_state_sha256=digest(state.tolist()),
                gms=[float(p.m*sim.G) for p in sim.particles[:-1]], figure_params=params)


def _summary(state):
    return dict(buffers={k: {x: v[x] for x in ('sha256', 'nonzero', 'max_abs')}
                         for k, v in state['buffers'].items()},
                scalars=state['scalars'], adaptation=state['adaptation'],
                absolute_state_sha256=state['absolute_state_sha256'])


def apply_mask(native, variant, figures=()):
    """Обнулить только объявленные группы; любое иное отличие — отказ до интеграции."""
    if variant not in MASKS:
        raise ValueError('Неизвестный вариант маски: '+repr(variant))
    mask = MASKS[variant]
    before = memory_state(native, figures)
    for name in mask['buffers']:
        buffer_view(native, name)[:] = 0.
    if mask['dt_last_done']:
        native.sim.dt_last_done = 0.
    after = memory_state(native, figures)
    allowed = set(mask['buffers'])
    for name in BUFFERS:
        changed = canonical(before['buffers'][name]['values']) != canonical(after['buffers'][name]['values'])
        if name in allowed:
            if after['buffers'][name]['nonzero']:
                raise ValueError('Буфер не обнулён: '+name)
        elif changed:
            raise ValueError('Изменён необъявленный буфер: '+name)
    for key in SCALARS:
        if key == 'dt_last_done' and mask['dt_last_done']:
            if after['scalars'][key] != 0.:
                raise ValueError('dt_last_done не обнулён')
        elif before['scalars'][key] != after['scalars'][key]:
            raise ValueError('Изменено необъявленное скалярное поле: '+key)
    for key in ('adaptation', 'absolute_state', 'gms', 'figure_params'):
        if canonical(before[key]) != canonical(after[key]):
            raise ValueError('Изменено защищённое поле: '+key)
    csx = np.asarray(before['buffers']['csx']['values']).reshape(native.count, 3)
    csv = np.asarray(before['buffers']['csv']['values']).reshape(native.count, 3)
    return dict(variant=variant, buffers_zeroed=list(mask['buffers']),
                dt_last_done_zeroed=mask['dt_last_done'],
                before=_summary(before), after=_summary(after),
                before_buffers_values={k: before['buffers'][k]['values'] for k in mask['buffers']},
                before_dt_last_done=before['scalars']['dt_last_done'],
                probe_minus_host_csx_km=(csx[-1]-csx[0]).tolist(),
                probe_minus_host_csv_km_s=(csv[-1]-csv[0]).tolist(),
                untouched=('at/x0/v0/a0 перезаписываются в начале шага; csa0 при gravity=basic '
                           'обнуляется исходником; steps_done сохранён'))


def _relative(state):
    state = np.asarray(state, dtype=float)
    return state-state[:1]


def integrate_window(native, identity, grid, *, wall_seconds=60., max_steps=10000):
    """Шаги native IAS15 до общего target P011; те же поля записей, что у run_physical."""
    if not 0 < wall_seconds <= 60 or not 1 <= max_steps <= 10000:
        raise ValueError('Memory controls limited to 60 s / 10000 attempts')
    start_t = float(native.sim.t)
    if start_t != grid['original_t0']:
        raise ValueError('Snapshot time differs from common grid origin')
    sample_times = np.asarray(grid['absolute'], dtype=float)
    if not np.array_equal(start_t+np.asarray(grid['elapsed']), sample_times):
        raise ValueError('Grid time mapping is not exact')
    target = float(sample_times[-1])
    if target != start_t+grid['target_elapsed']:
        raise ValueError('Target mismatch')
    started, cpu = time.monotonic(), time.process_time()
    final_state = _relative(native.absolute_state())
    samples = [dict(index=0, elapsed=grid['elapsed'][0], time=float(sample_times[0]),
                    relative_state=final_state.tolist(), kind='initial')]
    records, pending, accepted = [], 1, 0
    status, failure, last_valid_time = 'step_budget', None, start_t
    for index in range(max_steps):
        if time.monotonic()-started >= wall_seconds:
            status = 'wall_budget'
            break
        left, proposed = float(native.sim.t), float(native.sim.dt)
        before_state = native.absolute_state()
        maximum = ias15_step_limit(native, identity['period_seconds'], identity['gm_host'],
                                   identity['step_fraction'])
        segment = None
        try:
            segment = native.step(target, maximum)
        except (ArithmeticError, ValueError, RuntimeError) as exc:
            failure = dict(reason=guard_reason(exc, left, float(native.sim.t), target),
                           error=str(exc), left=left, right=float(native.sim.t),
                           elapsed_left=left-start_t, dt_done=float(native.sim.dt_last_done),
                           next_dt=float(native.sim.dt))
            status = 'guard'
        dt = float(native.sim.dt_last_done)
        try:
            a0 = native._array('a0', 3*native.count).reshape(native.count, 3)
            br = native._array('br', 21*native.count).reshape(7, native.count, 3)
            control = reconstruct_prs23(a0, br, dt, float(native.sim.integrator.epsilon))
            limiting = control['limiting_body']
            candidates = [r['candidate_dt'] for r in control['per_body']]
        except (ValueError, RuntimeError) as exc:
            limiting, candidates = None, 'unavailable: '+str(exc)
        after_state = native.absolute_state()
        records.append(dict(index=index, left=left, elapsed_left=left-start_t,
                            right=float(native.sim.t), proposed_dt=proposed,
                            max_step=maximum, dt_done=dt, next_dt=float(native.sim.dt),
                            time_ulp=math.ulp(left), dt_over_time_ulp=dt/math.ulp(left) if left else None,
                            host_probe_radius_km=float(np.linalg.norm(before_state[-1, :3]-before_state[0, :3])),
                            limiting_body=limiting, candidate_dt_per_body=candidates,
                            absolute_state=after_state.tolist(),
                            coefficients_sha256=None if segment is None else digest(segment.coefficients.tolist()),
                            endpoint_residual=getattr(native, 'checked_endpoint_residual', None),
                            failure=failure))
        if failure:
            break
        accepted += 1
        last_valid_time = float(native.sim.t)
        final_state = _relative(after_state)
        while pending < len(sample_times) and sample_times[pending] <= segment.right:
            when = float(sample_times[pending])
            if when < segment.left:
                raise ValueError('Common sample lost; no nearest-step substitution allowed')
            samples.append(dict(index=pending, elapsed=grid['elapsed'][pending], time=when,
                                relative_state=np.asarray(segment(when)).reshape(native.count, 6).tolist(),
                                kind='dense'))
            pending += 1
        if last_valid_time >= target:
            status = 'target_reached'
            break
    dt_values = [r['dt_done'] for r in records]
    return dict(status=status, failure=failure, attempts=len(records), accepted_steps=accepted,
                last_valid_time=last_valid_time, last_valid_elapsed=last_valid_time-start_t,
                target_time=target, target_elapsed=grid['target_elapsed'],
                target_reached=status == 'target_reached',
                wall_seconds=time.monotonic()-started, cpu_seconds=time.process_time()-cpu,
                dt_quantiles_s=np.quantile(dt_values, [0, .1, .5, .9, 1]).tolist() if dt_values else None,
                final_accepted_relative_state=final_state.tolist(), samples=samples, records=records,
                scientific_admission=False, scope='native-step diagnostic only; no event tracker')


STEP_KEYS = ('left', 'right', 'dt_done', 'next_dt', 'absolute_state', 'coefficients_sha256')
FAILURE_KEYS = ('reason', 'error', 'left', 'right', 'dt_done', 'next_dt')


def verify_m0(result, reference, expected_steps=416):
    """M0 обязан побитно повторить C073: принятые шаги и поля отказа."""
    accepted = [r for r in result['records'] if r['failure'] is None]
    ref = reference['records']
    checks = dict(accepted_count=len(accepted) == len(ref) == expected_steps,
                  status_guard=result['status'] == 'guard' and result['failure'] is not None)
    checks['records_exact'] = checks['accepted_count'] and all(
        canonical({k: a[k] for k in STEP_KEYS}) == canonical({k: b[k] for k in STEP_KEYS})
        for a, b in zip(accepted, ref))
    checks['failure_exact'] = result['failure'] is not None and canonical(
        {k: result['failure'][k] for k in FAILURE_KEYS}) == canonical(
        {k: reference['failure'][k] for k in FAILURE_KEYS})
    first = next((i for i, (a, b) in enumerate(zip(accepted, ref))
                  if canonical({k: a[k] for k in STEP_KEYS}) != canonical({k: b[k] for k in STEP_KEYS})),
                 None)
    return dict(checks=checks, passed=all(checks.values()), first_mismatch_step=first)


P00_KEYS = ('left', 'right', 'proposed_dt', 'max_step', 'dt_done', 'next_dt', 'limiting_body',
            'candidate_dt_per_body')


def verify_ma(result, p00):
    """Полнота маски: MA против fresh physical restart P00 (не научная точность)."""
    a, b = result['records'], p00['records']
    first = next((i for i, (x, y) in enumerate(zip(a, b))
                  if canonical({k: x[k] for k in P00_KEYS}) != canonical({k: y[k] for k in P00_KEYS})),
                 None)
    samples_a = {s['index']: s for s in result['samples']}
    samples_b = {s['index']: s for s in p00['samples']}
    checks = dict(status=result['status'] == p00['status'], attempts=len(a) == len(b),
                  records_exact=first is None and len(a) == len(b),
                  samples_exact=set(samples_a) == set(samples_b) and all(
                      canonical(samples_a[i]['relative_state']) == canonical(samples_b[i]['relative_state'])
                      and samples_a[i]['elapsed'] == samples_b[i]['elapsed'] for i in samples_a))
    return dict(checks=checks, passed=all(checks.values()), first_record_mismatch=first,
                interpretation='completeness of mask vs fresh restart only; not scientific accuracy')


def first_divergence(result, baseline):
    keys = ('left', 'dt_done', 'next_dt')
    for i, (x, y) in enumerate(zip(result['records'], baseline['records'])):
        if canonical({k: x[k] for k in keys}) != canonical({k: y[k] for k in keys}):
            return dict(index=i, elapsed_left=x['elapsed_left'], variant=dict((k, x[k]) for k in keys),
                        baseline=dict((k, y[k]) for k in keys))
    return None
