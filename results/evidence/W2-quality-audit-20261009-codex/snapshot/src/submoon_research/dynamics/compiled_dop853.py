"""Прототип этапа F (W2-T008): скомпилированный DOP853 для движка A.

Версия прототипа 2: силы и энергия — собственные скалярные ядра без выделения
памяти и BLAS (формулы fast_forces дословно); отсечка событий уточняется
делением шага на подынтервалы прямо в скомпилированном коде.

Статус: экспериментальный режим ``dop853_compiled_proto``; production_allowed=False,
численного допуска нет. Не входит в ENGINES и не используется W1.

Алгоритм — перенос scipy.integrate.DOP853 (коэффициенты, оценка ошибки,
контроль шага SAFETY/MIN_FACTOR/MAX_FACTOR, плотный вывод 7-го порядка) в
Numba: шаги, силы, плотный вывод, энергетическая диагностика и сетка выборок
выполняются без возврата в Python. Начальный шаг выбирается самой
scipy.select_initial_step. Порядок операций отличается от numpy/BLAS, поэтому
траектория совпадает со scipy-путём A только в пределах округления, а не побитно.

События: на каждом шаге скомпилированная консервативная отсечка (контакт со
всеми телами, оба радиуса ухода, срок подтверждения ухода) по тем же
чебышёвским выборкам плотного вывода. Подозрительный шаг возвращается в Python
и обрабатывается прежним точным кодом (polynomial_contact, EscapeTracker) на
том же плотном полиноме (scipy Dop853DenseOutput). Отсечка только ускоряет:
шаг, который прежний код мог бы признать событием, всегда попадает в Python.
"""
from dataclasses import asdict
import math
import time

import numpy as np
from numba import njit
from scipy.integrate._ivp import dop853_coefficients as _dc
from scipy.integrate._ivp.common import select_initial_step
from scipy.integrate._ivp.rk import Dop853DenseOutput

from submoon_research.dynamics import fast_forces
from submoon_research.dynamics.dense_segments import chebyshev_samples, interpolation_basis
from submoon_research.dynamics.engine_compare import NumericMode, fingerprint
from submoon_research.events.dense_contact import polynomial_contact
from submoon_research.events.escape import EscapeTracker

ENGINE = 'dop853_compiled_proto'
PROTOTYPE_VERSION = 3

_A = np.ascontiguousarray(_dc.A[:_dc.N_STAGES, :_dc.N_STAGES])
_B = np.ascontiguousarray(_dc.B)
_E3 = np.ascontiguousarray(_dc.E3)
_E5 = np.ascontiguousarray(_dc.E5)
_D = np.ascontiguousarray(_dc.D)
_AX = np.ascontiguousarray(_dc.A[_dc.N_STAGES + 1:])
_NODES, _INVERSE = interpolation_basis(7)  # те же узлы, что chebyshev_samples для DOP853
_NODES = np.ascontiguousarray(_NODES)
_INVERSE = np.ascontiguousarray(_INVERSE)

SAFETY, MIN_FACTOR, MAX_FACTOR = 0.9, 0.2, 10.0
ERROR_EXPONENT = -1.0/(7 + 1)
# Запас компилированной отсечки: шаг пропускается Python-проверкой, только если
# прежний точный код его заведомо отбросил бы (расхождение округления << запаса).
SCREEN_MARGIN = 1e-8
# Глубина деления шага при уточнении отсечки: до 2**SCREEN_DEPTH подынтервалов.
SCREEN_DEPTH = 5

DONE, CANDIDATE, BATCH_LIMIT, FAILED = 0, 1, 2, 3
# V02: DOP853 не допущен при e >= 0.9 (аналитический контроль). Версия 3 только
# измеряет оскулирующий эксцентриситет пробы относительно хозяина; правило
# выхода из допустимой области будет принято решением по данным P2-1.
ECCENTRICITY_LIMIT = 0.9
FLAG_FIRST, FLAG_CONTACT, FLAG_CROSSING, FLAG_DEADLINE = 1, 2, 4, 8


@njit(cache=True, fastmath=False)
def _ulp(t):
    if t == 0.0:
        return 5e-324
    # 10*ulp(t), как min_step scipy: 10*|nextafter(t, inf) - t| для t > 0.
    exponent = math.frexp(abs(t))[1]
    return math.ldexp(1.0, exponent - 53)


@njit(cache=True, fastmath=False)
def _j2(rx, ry, rz, gm, j2, radius, px, py, pz):
    """fast_forces.j2_vector в скалярах."""
    d2 = rx*rx + ry*ry + rz*rz
    z = rx*px + ry*py + rz*pz
    factor = 1.5 * gm * j2 * radius**2 / (d2**2 * math.sqrt(d2))
    c = 5 * z*z / d2 - 1
    return factor*(c*rx - 2*z*px), factor*(c*ry - 2*z*py), factor*(c*rz - 2*z*pz)


@njit(cache=True, fastmath=False)
def rhs_into(state, gms, figures, out, acc):
    """fast_forces.rhs без временных массивов: результат в out, acc — буфер (count, 3)."""
    count = gms.size
    n = state.size
    for i in range(count):
        acc[i, 0] = 0.0
        acc[i, 1] = 0.0
        acc[i, 2] = 0.0
    for i in range(count):
        xi, yi, zi = state[6*i], state[6*i+1], state[6*i+2]
        for j in range(i):
            dx, dy, dz = state[6*j]-xi, state[6*j+1]-yi, state[6*j+2]-zi
            d2 = dx*dx + dy*dy + dz*dz
            denom = d2*math.sqrt(d2)
            bx, by, bz = dx/denom, dy/denom, dz/denom
            acc[i, 0] += gms[j]*bx
            acc[i, 1] += gms[j]*by
            acc[i, 2] += gms[j]*bz
            acc[j, 0] -= gms[i]*bx
            acc[j, 1] -= gms[i]*by
            acc[j, 2] -= gms[i]*bz
    for f in range(figures.shape[0]):
        i = int(figures[f, 0])
        j2, radius = figures[f, 1], figures[f, 2]
        px, py, pz = figures[f, 3], figures[f, 4], figures[f, 5]
        for j in range(count):
            if j != i:
                fx, fy, fz = _j2(state[6*j]-state[6*i], state[6*j+1]-state[6*i+1],
                                 state[6*j+2]-state[6*i+2], gms[i], j2, radius, px, py, pz)
                acc[j, 0] += fx
                acc[j, 1] += fy
                acc[j, 2] += fz
                ratio = gms[j]/gms[i]
                acc[i, 0] -= ratio*fx
                acc[i, 1] -= ratio*fy
                acc[i, 2] -= ratio*fz
    for k in range(6):
        out[k] = 0.0
    for k in range(1, count):
        for a in range(3):
            out[6*k+a] = state[6*k+3+a]
            out[6*k+3+a] = acc[k, a] - acc[0, a]
    p = n - 6
    rx, ry, rz = state[p], state[p+1], state[p+2]
    for a in range(3):
        out[p+a] = state[p+3+a]
    d2 = rx*rx + ry*ry + rz*rz
    denom = d2*math.sqrt(d2)
    ax, ay, az = -gms[0]*rx/denom, -gms[0]*ry/denom, -gms[0]*rz/denom
    for i in range(1, count):
        mx, my, mz = state[6*i], state[6*i+1], state[6*i+2]
        dx, dy, dz = mx-rx, my-ry, mz-rz
        p2 = mx*mx + my*my + mz*mz
        s2 = dx*dx + dy*dy + dz*dz
        sd, pd = s2*math.sqrt(s2), p2*math.sqrt(p2)
        ax += gms[i]*(dx/sd - mx/pd)
        ay += gms[i]*(dy/sd - my/pd)
        az += gms[i]*(dz/sd - mz/pd)
    for f in range(figures.shape[0]):
        i = int(figures[f, 0])
        j2, radius = figures[f, 1], figures[f, 2]
        px, py, pz = figures[f, 3], figures[f, 4], figures[f, 5]
        mx, my, mz = state[6*i], state[6*i+1], state[6*i+2]
        fx, fy, fz = _j2(rx-mx, ry-my, rz-mz, gms[i], j2, radius, px, py, pz)
        ax += fx
        ay += fy
        az += fz
        if i != 0:
            fx, fy, fz = _j2(-mx, -my, -mz, gms[i], j2, radius, px, py, pz)
            ax -= fx
            ay -= fy
            az -= fz
        else:
            for j in range(1, count):
                fx, fy, fz = _j2(state[6*j], state[6*j+1], state[6*j+2], gms[0], j2, radius,
                                 px, py, pz)
                ratio = gms[j]/gms[0]
                ax += ratio*fx
                ay += ratio*fy
                az += ratio*fz
    out[p+3] = ax
    out[p+4] = ay
    out[p+5] = az


@njit(cache=True, fastmath=False)
def energy_scalar(state, gms, figures):
    """fast_forces.energy без временных массивов."""
    count = gms.size
    cx = 0.0
    cy = 0.0
    cz = 0.0
    total_gm = 0.0
    for i in range(count):
        cx += gms[i]*state[6*i+3]
        cy += gms[i]*state[6*i+4]
        cz += gms[i]*state[6*i+5]
        total_gm += gms[i]
    cx /= total_gm
    cy /= total_gm
    cz /= total_gm
    total = 0.0
    for i in range(count):
        vx, vy, vz = state[6*i+3]-cx, state[6*i+4]-cy, state[6*i+5]-cz
        total += gms[i]*(vx*vx + vy*vy + vz*vz)/2
        for j in range(i):
            dx, dy, dz = state[6*i]-state[6*j], state[6*i+1]-state[6*j+1], state[6*i+2]-state[6*j+2]
            total -= gms[i]*gms[j]/math.sqrt(dx*dx + dy*dy + dz*dz)
    for f in range(figures.shape[0]):
        i = int(figures[f, 0])
        for j in range(count):
            if i != j:
                dx, dy, dz = state[6*j]-state[6*i], state[6*j+1]-state[6*i+1], state[6*j+2]-state[6*i+2]
                radius = math.sqrt(dx*dx + dy*dy + dz*dz)
                cosine = (dx*figures[f, 3] + dy*figures[f, 4] + dz*figures[f, 5])/radius
                total += gms[i]*gms[j]*figures[f, 1]*figures[f, 2]**2*(3*cosine**2-1)/(2*radius**3)
    return total


@njit(cache=True, fastmath=False)
def _stage(K, s, a_row, y, h, gms, figures, point, acc):
    n = y.size
    for i in range(n):
        total = 0.0
        for j in range(s):
            total += K[j, i]*a_row[j]
        point[i] = y[i] + total*h
    rhs_into(point, gms, figures, K[s], acc)


@njit(cache=True, fastmath=False)
def _step(t, y, f, h_abs, t_bound, max_step, rtol, atol, gms, figures, K, y_new, point, acc):
    """Один принятый шаг RungeKutta._step_impl scipy (DOP853). Возвращает
    (успех, t_new, h_abs следующего шага, h, число вычислений силы)."""
    n = y.size
    min_step = 10.0*_ulp(t)
    if h_abs > max_step:
        h_abs = max_step
    elif h_abs < min_step:
        h_abs = min_step
    rejected = False
    nfev = 0
    while True:
        if h_abs < min_step:
            return False, t, h_abs, 0.0, nfev
        t_new = t + h_abs
        if t_new - t_bound > 0:
            t_new = t_bound
        h = t_new - t
        h_abs = abs(h)
        for i in range(n):
            K[0, i] = f[i]
        for s in range(1, 12):
            _stage(K, s, _A[s], y, h, gms, figures, point, acc)
        for i in range(n):
            total = 0.0
            for j in range(12):
                total += K[j, i]*_B[j]
            y_new[i] = y[i] + h*total
        rhs_into(y_new, gms, figures, K[12], acc)
        nfev += 12
        e5 = 0.0
        e3 = 0.0
        for i in range(n):
            scale = atol[i] + max(abs(y[i]), abs(y_new[i]))*rtol
            a5 = 0.0
            a3 = 0.0
            for j in range(13):
                a5 += K[j, i]*_E5[j]
                a3 += K[j, i]*_E3[j]
            a5 /= scale
            a3 /= scale
            e5 += a5*a5
            e3 += a3*a3
        if e5 == 0.0 and e3 == 0.0:
            error = 0.0
        else:
            error = abs(h)*e5/math.sqrt((e5 + 0.01*e3)*n)
        if error < 1.0:
            if error == 0.0:
                factor = MAX_FACTOR
            else:
                factor = min(MAX_FACTOR, SAFETY*error**ERROR_EXPONENT)
            if rejected:
                factor = min(1.0, factor)
            return True, t_new, h_abs*factor, h, nfev
        h_abs *= max(MIN_FACTOR, SAFETY*error**ERROR_EXPONENT)
        rejected = True


@njit(cache=True, fastmath=False)
def _dense(K, F, y_old, y_new, h, gms, figures, point, acc):
    """Коэффициенты Dop853DenseOutput (3 дополнительные стадии)."""
    n = y_old.size
    for s in range(13, 16):
        _stage(K, s, _AX[s - 13], y_old, h, gms, figures, point, acc)
    for i in range(n):
        delta = y_new[i] - y_old[i]
        F[0, i] = delta
        F[1, i] = h*K[0, i] - delta
        F[2, i] = 2*delta - h*(K[12, i] + K[0, i])
    for r in range(4):
        for i in range(n):
            total = 0.0
            for j in range(16):
                total += _D[r, j]*K[j, i]
            F[3 + r, i] = h*total


@njit(cache=True, fastmath=False)
def _dense_eval(F, y_old, x, out):
    n = y_old.size
    for i in range(n):
        out[i] = 0.0
    for k in range(7):
        row = 6 - k
        multiplier = x if k % 2 == 0 else 1.0 - x
        for i in range(n):
            out[i] = (out[i] + F[row, i])*multiplier
    for i in range(n):
        out[i] += y_old[i]


@njit(cache=True, fastmath=False)
def _bounds(F, y_old, x0, x1, probe, body, S, coeff):
    """(центр, огибающая) |r(x)| на [x0, x1] по чебышёвским коэффициентам полинома
    плотного вывода (степень 7, восемь узлов — представление точное)."""
    for k in range(8):
        _dense_eval(F, y_old, x0 + (_NODES[k] + 1.0)*(x1 - x0)/2.0, S[k])
    for r in range(8):
        for axis in range(3):
            total = 0.0
            for k in range(8):
                value = S[k, probe + axis]
                if body >= 0:
                    value -= S[k, body + axis]
                total += _INVERSE[r, k]*value
            coeff[r, axis] = total
    center = math.sqrt(coeff[0, 0]**2 + coeff[0, 1]**2 + coeff[0, 2]**2)
    envelope = 0.0
    for r in range(1, 8):
        envelope += math.sqrt(coeff[r, 0]**2 + coeff[r, 1]**2 + coeff[r, 2]**2)
    return center, envelope


@njit(cache=True, fastmath=False)
def _proven_clear(F, y_old, body, radius, contact, S, coeff, lo, hi, depth):
    """Строго ли доказано делением шага, что события нет.

    contact: |r| > radius на всём шаге; иначе — |r| не пересекает radius.
    Ни один подынтервал не пропускается: при неуспехе на предельной глубине
    возвращается False, и шаг уходит в точную Python-проверку.
    """
    probe = y_old.size - 6
    top = 0
    lo[0] = 0.0
    hi[0] = 1.0
    depth[0] = 0
    while top >= 0:
        x0, x1, d = lo[top], hi[top], depth[top]
        top -= 1
        center, envelope = _bounds(F, y_old, x0, x1, probe, body, S, coeff)
        margin = SCREEN_MARGIN*(center + envelope + 1.0)
        if contact:
            clear = center - envelope > radius + margin
        else:
            clear = center - envelope > radius + margin or center + envelope < radius - margin
        if clear:
            continue
        if d >= SCREEN_DEPTH:
            return False
        middle = 0.5*(x0 + x1)
        top += 1
        lo[top] = x0
        hi[top] = middle
        depth[top] = d + 1
        top += 1
        lo[top] = middle
        hi[top] = x1
        depth[top] = d + 1
    return True


@njit(cache=True, fastmath=False)
def _screen(F, y_old, radii, tolerance, hill_in, hill_out, S, coeff, lo, hi, depth):
    flags = 0
    for b in range(radii.size):
        if not _proven_clear(F, y_old, 6*b, radii[b] + tolerance, True, S, coeff, lo, hi, depth):
            flags |= FLAG_CONTACT
            break
    if hill_in > 0:
        for radius in (hill_in, hill_out):
            if not _proven_clear(F, y_old, -1, radius, False, S, coeff, lo, hi, depth):
                flags |= FLAG_CROSSING
                break
    return flags


@njit(cache=True, fastmath=False)
def _eccentricity_monitor(y, t, mu, emon):
    """Оскулирующий e пробы относительно хозяина (хозяин в начале координат).

    emon: [0] max e связанных состояний, [1] первое время связанного e >= предела
    (-1 — не было), [2] max e любых состояний, [3] число шагов со связанным
    e >= предела при сближении с хозяином (r·v < 0).
    """
    p = y.size - 6
    rx, ry, rz, vx, vy, vz = y[p], y[p+1], y[p+2], y[p+3], y[p+4], y[p+5]
    r = math.sqrt(rx*rx + ry*ry + rz*rz)
    v2 = vx*vx + vy*vy + vz*vz
    rv = rx*vx + ry*vy + rz*vz
    c = v2 - mu/r
    ex, ey, ez = (c*rx - rv*vx)/mu, (c*ry - rv*vy)/mu, (c*rz - rv*vz)/mu
    e = math.sqrt(ex*ex + ey*ey + ez*ez)
    if e > emon[2]:
        emon[2] = e
    if v2/2 - mu/r < 0:
        if e > emon[0]:
            emon[0] = e
        if e >= ECCENTRICITY_LIMIT:
            if emon[1] < 0:
                emon[1] = t
            if rv < 0:
                emon[3] += 1.0


@njit(cache=True, fastmath=False)
def run_batch(t, y, f, h_abs, t_bound, max_step, rtol, atol, gms, figures, radii, tolerance,
              hill_in, hill_out, deadline, energy0, drift, sample_times, sample_index,
              samples_out, max_steps, force_first, K, F, y_old, state, emon):
    """Шаги до события-кандидата, конца горизонта или лимита партии.

    y, f обновляются на месте (состояние интегратора после последнего шага);
    F, y_old — плотный вывод последнего шага; state — его значение в конце шага.
    """
    n = y.size
    y_new = np.empty(n)
    point = np.empty(n)
    acc = np.empty((gms.size, 3))
    S = np.empty((8, n))
    coeff = np.empty((8, 3))
    lo = np.empty(2*SCREEN_DEPTH + 2)
    hi = np.empty(2*SCREEN_DEPTH + 2)
    depth = np.empty(2*SCREEN_DEPTH + 2, dtype=np.int64)
    steps = 0
    nfev = 0
    t_old = t
    h = 0.0
    while steps < max_steps:
        if t >= t_bound:
            return DONE, t, h_abs, steps, nfev, sample_index, drift, 0, t_old, h
        for i in range(n):
            y_old[i] = y[i]
        ok, t_new, h_abs, h, used = _step(t, y, f, h_abs, t_bound, max_step, rtol, atol,
                                          gms, figures, K, y_new, point, acc)
        nfev += used
        if not ok:
            return FAILED, t, h_abs, steps, nfev, sample_index, drift, 0, t_old, h
        for i in range(n):
            if not math.isfinite(y_new[i]):
                return FAILED, t, h_abs, steps, nfev, sample_index, drift, 0, t_old, h
        _dense(K, F, y_old, y_new, h, gms, figures, point, acc)
        nfev += 3
        steps += 1
        t_old = t
        t = t_new
        for i in range(n):
            y[i] = y_new[i]
            f[i] = K[12, i]
        _eccentricity_monitor(y, t, gms[0], emon)
        flags = FLAG_FIRST if (force_first and steps == 1) else 0
        flags |= _screen(F, y_old, radii, tolerance, hill_in, hill_out, S, coeff, lo, hi, depth)
        if deadline <= t:
            flags |= FLAG_DEADLINE
        if flags:
            return CANDIDATE, t, h_abs, steps, nfev, sample_index, drift, flags, t_old, h
        _dense_eval(F, y_old, (t - t_old)/h, state)
        value = energy_scalar(state, gms, figures)
        drift = max(drift, abs(value - energy0)/max(abs(energy0), 1e-300))
        while sample_index < sample_times.size and sample_times[sample_index] <= t:
            _dense_eval(F, y_old, (sample_times[sample_index] - t_old)/h, samples_out[sample_index])
            sample_index += 1
    return BATCH_LIMIT, t, h_abs, steps, nfev, sample_index, drift, 0, t_old, h


def integrate_compiled(initial, gms, figures, radii, horizon, *, period, mode=None, hill_km=None,
                       escape_window=None, times=None, budget=lambda: None, resume=None,
                       progress=None, batch_steps=2048):
    """Тот же контракт результата, что integrate_engine('dop853_jit', ...)."""
    mode = mode or NumericMode()
    initial = np.asarray(initial, dtype=float).reshape(-1, 6)
    gms = np.asarray(gms, dtype=float)
    radii = np.asarray(radii, dtype=float)
    if (initial.shape != (len(gms)+1, 6) or np.any(initial[0] != 0)
            or not np.isfinite(initial).all() or not np.isfinite(gms).all()
            or np.any(gms <= 0) or radii.shape != gms.shape or np.any(radii <= 0)
            or not np.isfinite(radii).all() or not horizon > 0 or not period > 0):
        raise ValueError('Неверные физические входы движка')
    fs = fast_forces.figure_array(figures, len(gms))
    contract = fingerprint(dict(initial=initial.tolist(), gms=gms.tolist(),
        figures=figures, radii=radii.tolist(), horizon=horizon, period=period,
        mode=asdict(mode), engine=ENGINE, hill=hill_km, escape_window=escape_window))
    state, t = initial.ravel().copy(), 0.0
    tracker = EscapeTracker(hill_km, escape_window) if hill_km is not None else None
    if resume:
        if resume.get('terminal_event') is not None:
            raise ValueError('Нельзя продолжать терминальное событие')
        if resume['contract_sha256'] != contract:
            raise ValueError('Checkpoint относится к другому контракту')
        state, t = np.asarray(resume['state'], dtype=float), float(resume['time'])
        if state.shape != initial.ravel().shape or not np.isfinite(state).all() or not 0 <= t < horizon:
            raise ValueError('Некорректное состояние checkpoint')
        tracker = EscapeTracker(**resume['escape']) if resume['escape'] else None
    started, cpu = time.perf_counter(), time.process_time()
    energy0 = energy_scalar(initial.ravel(), gms, fs)
    times = np.asarray(times if times is not None else np.linspace(0, horizon, 65), dtype=float)
    if not np.isfinite(times).all() or np.any(np.diff(times) < 0) or np.any(times < 0) or np.any(times > horizon):
        raise ValueError('Неверная сетка диагностик')
    sample_times = np.ascontiguousarray(times[times >= t])
    samples_out = np.zeros((len(sample_times), state.size))
    sample_index = 0
    while sample_index < len(sample_times) and sample_times[sample_index] == t:
        samples_out[sample_index] = state
        sample_index += 1
    event, error, status = None, None, 'completed'
    steps, nfev, drift, python_steps, batches = 0, 0, 0.0, 0, 0
    emon = np.array([0.0, -1.0, 0.0, 0.0])

    def checkpoint():
        return dict(schema_version='W2-state-1', contract_sha256=contract, engine=ENGINE,
            time=float(t), state=state.tolist(), cache_identity=None,
            escape=tracker.snapshot() if tracker else None, terminal_event=event,
            continuation='physical_state_new_adaptive_history')

    try:
        budget()
        y = state.copy()
        atol = np.tile([mode.atol_position]*3+[mode.atol_velocity]*3, len(y)//6).astype(float)
        max_step = period*mode.step_fraction

        def fun(_, value):
            return fast_forces.rhs(np.asarray(value, dtype=float), gms, fs)

        f = fun(t, y)
        h_abs = float(select_initial_step(fun, t, y, horizon, max_step, f, 1, 7, mode.rtol, atol))
        nfev += 2
        K = np.empty((16, y.size))
        F = np.empty((7, y.size))
        y_old = np.empty(y.size)
        end_state = np.empty(y.size)
        hill_in = float(hill_km) if tracker is not None else -1.0
        hill_out = hill_in*tracker.outer_factor if tracker is not None else -1.0
        force_first = bool(tracker is not None and t == 0.0)
        while t < horizon:
            budget()
            deadline = (tracker.pending_since + tracker.window_seconds
                        if tracker is not None and tracker.pending_since is not None else np.inf)
            code, t_new, h_abs, done, used, sample_index, drift, flags, t_old, h = run_batch(
                t, y, f, h_abs, float(horizon), max_step, float(mode.rtol), atol, gms, fs, radii,
                1e-7, hill_in, hill_out, float(deadline), energy0, drift, sample_times,
                sample_index, samples_out, batch_steps, force_first, K, F, y_old, end_state, emon)
            batches += 1
            steps += done
            nfev += used
            force_first = False
            if code == FAILED:
                raise ArithmeticError('DOP853 не завершил шаг')
            if code == CANDIDATE:
                python_steps += 1
                left, right = float(t_old), float(t_new)
                dense = Dop853DenseOutput(left, right, y_old.copy(), F.copy())
                if tracker is not None:
                    if tracker.last_time > left:
                        raise ValueError('Разрыв истории события')
                    tracker.last_time = left  # шаги без пересечений: прежний advance не менял бы состояние
                step_samples = chebyshev_samples(dense, left, right)
                event = polynomial_contact(dense, left, right, len(gms), radii, samples=step_samples)
                end = event['time'] if event else right
                if tracker is not None and end > left:
                    departure = tracker.advance(dense, left, end,
                                                samples=step_samples if end == right else None)
                    if departure and (event is None or departure['time'] < event['time']):
                        event, end = departure, departure['time']
                selected = np.asarray(dense(end))
                if not np.isfinite(selected).all():
                    raise ArithmeticError('Неконечное принятое состояние')
                state, t = selected.copy(), float(end)
                drift = max(drift, abs(energy_scalar(state, gms, fs)-energy0)/max(abs(energy0), 1e-300))
                while sample_index < len(sample_times) and sample_times[sample_index] <= t:
                    samples_out[sample_index] = np.asarray(dense(sample_times[sample_index]))
                    sample_index += 1
            else:
                if done:
                    state, t = end_state.copy(), float(t_new)
                if tracker is not None and done:
                    tracker.last_time = t
            if progress:
                progress(checkpoint())
            if event:
                break
            if code == DONE:
                break
    except TimeoutError as exc:
        status, error = 'partial', str(exc)
    except (ArithmeticError, ValueError, RuntimeError, MemoryError) as exc:
        status, error = 'failed', str(exc)
    if status != 'completed':
        outcome = 'unresolved'
    elif event:
        outcome = ('operational_escape' if event['event'] == 'operational_escape'
                   else 'host_contact' if event['body_index'] == 0 else 'other_contact')
    else:
        outcome = tracker.outcome() if tracker else 'survived'
    samples = [dict(time=float(sample_times[k]), state=samples_out[k].tolist()) for k in range(sample_index)]
    return dict(engine=ENGINE, run_status=status, physical_outcome=outcome, error=error,
        last_valid_time=float(t), final_state=state.tolist(), event=event, samples=samples,
        checkpoint=checkpoint(), massive_energy_relative_drift=float(drift),
        endpoint_residual=None, steps=steps, nfev=nfev, wall_seconds=time.perf_counter()-started,
        cpu_seconds=time.process_time()-cpu, cache_preparation_wall_seconds=0.0,
        cache_preparation_cpu_seconds=0.0, cache_bytes=0, python_event_steps=python_steps,
        eccentricity_monitor=dict(limit=ECCENTRICITY_LIMIT, max_bound=float(emon[0]),
            first_bound_at_limit_time=None if emon[1] < 0 else float(emon[1]),
            max_any=float(emon[2]), bound_approach_steps_at_limit=int(emon[3]),
            note='диагностика V02; правило выхода из области не применяется'),
        batches=batches, physical_surface_verified=False, permanent_escape_assessed=False,
        production_allowed=False)
