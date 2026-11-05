"""W2-T071: независимый потенциал, фактические входы и короткие аналитические контроли.

Не изменяет движки, frozen runs или их результаты. Критерии заранее записаны в
 docs/tasks/W2_T071_scientific_audit_20261031.md. Запускать в Ubuntu-24.04.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import sys
import time
import warnings
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
    os.environ[key] = '1'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import mpmath as mp  # noqa: E402
import numpy as np  # noqa: E402

from submoon_research.dynamics import compiled_dop853 as cd, fast_forces as ff  # noqa: E402
from submoon_research.dynamics.engine_compare import (  # noqa: E402
    NumericMode, ias15_start_state, integrate_engine,
)
from submoon_research.dynamics.native_ias15 import NativeIAS15  # noqa: E402
from submoon_research.dynamics.nominal import relative_rhs  # noqa: E402

RUNS = ['W2-PH3-P0-20261026T113011Z', 'W2-PH3-P1-20261028T091305Z',
        'W2-PH3-perturb-20261028T093615Z']
SETS = {'iapetus': ['iapetus', 'sun', 'jupiter', 'saturn', 'titan'],
        'ganymede': ['ganymede', 'sun', 'jupiter', 'saturn', 'io', 'europa', 'callisto'],
        'himalia': ['himalia', 'sun', 'jupiter', 'saturn']}
MODEL = 'data/processed/L1-nominal-v0_4_1/model.json'
STATES = 'data/processed/W0-geometric-states-aligned-v3/geometric_states.json'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads((ROOT / path).read_text(encoding='utf-8'))


def mpnorm(v):
    return mp.sqrt(sum(x*x for x in v))


def field_potential(d, gm, fig):
    """Удельный потенциал J2; независимый исходный объект для дифференцирования."""
    radius = mpnorm(d)
    cos = sum(d[k]*mp.mpf(fig['pole'][k]) for k in range(3)) / radius
    return gm*mp.mpf(fig['j2'])*mp.mpf(fig['radius'])**2*(3*cos*cos-1)/(2*radius**3)


def independent_force(state, gms, figures):
    """-grad(U)/mu и -grad(phi_probe), затем вычитание ускорения хозяина.

    Формула acceleration production здесь не вызывается и не копируется.
    mu=GM, поэтому U есть G*E и имеет единицы L^5/T^4.
    """
    with mp.workdps(50):
        gm = [mp.mpf(float(x)) for x in gms]
        pos = [[mp.mpf(float(x)) for x in row[:3]] for row in state]
        count = len(gm)

        def potential(coords):
            u = mp.mpf(0)
            for i in range(count):
                for j in range(i):
                    u -= gm[i]*gm[j]/mpnorm([coords[j][k]-coords[i][k] for k in range(3)])
            for f in figures:
                i = f['index']
                for j in range(count):
                    if i != j:
                        u += gm[j]*field_potential([coords[j][k]-coords[i][k] for k in range(3)], gm[i], f)
            return u

        absolute = []
        for i in range(count):
            row = []
            for axis in range(3):
                def shifted(x):
                    coords = [r.copy() for r in pos[:count]]
                    coords[i][axis] = x
                    return potential(coords)
                row.append(-mp.diff(shifted, pos[i][axis])/gm[i])
            absolute.append(row)

        def phi(probe):
            total = -sum(gm[i]/mpnorm([probe[k]-pos[i][k] for k in range(3)]) for i in range(count))
            for f in figures:
                i = f['index']
                total += field_potential([probe[k]-pos[i][k] for k in range(3)], gm[i], f)
            return total

        probe_acc = []
        for axis in range(3):
            def shifted_probe(x):
                p = pos[-1].copy()
                p[axis] = x
                return phi(p)
            probe_acc.append(-mp.diff(shifted_probe, pos[-1][axis]))
        relative = [[float(row[k]-absolute[0][k]) for k in range(3)]
                    for row in [*absolute, probe_acc]]
        return np.array(relative), np.array(absolute, dtype=float), float(potential(pos[:count]))


def relerr(actual, expected):
    a, b = np.asarray(actual), np.asarray(expected)
    if a.ndim == 1:
        return float(np.linalg.norm(a-b)/max(np.linalg.norm(b), 1e-300))
    norms = np.linalg.norm(b, axis=1)
    mask = norms > 0
    return float(np.max(np.linalg.norm(a-b, axis=1)[mask]/norms[mask]))


def compiled_acceleration(state, gm, fs):
    out = np.empty(state.size)
    cd.rhs_into(state.ravel(), gm, fs, out, np.empty((len(gm), 3)))
    return out.reshape(-1, 6)[:, 3:]


def pck(text, name):
    raw = re.search(r'\b'+name+r'\s*=\s*\(([^)]+)\)', text).group(1)
    return np.array([float(s.replace('D', 'E')) for s in raw.replace(',', ' ').split()])


def pole(ra, dec):
    a, d = np.deg2rad([ra, dec])
    return np.array([np.cos(d)*np.cos(a), np.cos(d)*np.sin(a), np.sin(d)])


def primary_inputs(model):
    sat = (ROOT / model['source_paths']['sat']).read_text()
    jov = (ROOT / model['source_paths']['jov']).read_text()
    gmtext = (ROOT / model['source_paths']['gm']).read_text()
    checks = {}
    for name, body in model['bodies'].items():
        idx = body['naif_id']
        if name in ('saturn', 'titan', 'iapetus'):
            value = pck(sat, f'BODY{idx}_GM')[0]
        elif name in ('sun', 'himalia'):
            value = pck(gmtext, f'BODY{idx}_GM')[0]
        else:
            matches = re.findall(r'^\s*\w+\s+'+str(idx)+r'\s+(\S+)\s+\d+\s+\d+\s+SATORBINT', jov, re.M)
            assert len(matches) == 2 and matches[0] == matches[1]
            value = float(matches[0])
        checks[name+'_gm'] = bool(value == body['gm']['value'])
    def constant(name):
        return float(re.search(r'\b'+name+r'\s+([-+0-9.E]+)', jov).group(1))
    checks['jupiter_j2_radius'] = (constant('J502') == model['figures']['jupiter']['j2']['value']
                                  and constant('RADIUS') == model['figures']['jupiter']['reference_radius']['value'])
    checks['saturn_j2_radius'] = (pck(sat, 'BODY699_JCOEF')[1] == model['figures']['saturn']['j2']['value']
                                 and pck(sat, 'BODY699_RADII')[0] == model['figures']['saturn']['reference_radius']['value'])
    ra = constant('ZACPL5') + sum(constant(f'RA_AMPL{k}')*math.sin(math.radians(constant(f'RA_FAZE{k}'))) for k in range(1, 5))
    dec = constant('ZDEPL5') + sum(constant(f'DE_AMPL{k}')*math.cos(math.radians(constant(f'DE_FAZE{k}'))) for k in range(1, 5))
    jpole = pole(ra, dec)
    angles = np.deg2rad(pck(sat, 'BODY6_NUT_PREC_ANGLES')[::2])
    raa, dea = pck(sat, 'BODY699_NUT_PREC_RA'), pck(sat, 'BODY699_NUT_PREC_DEC')
    spole = pole(pck(sat, 'BODY699_POLE_RA')[0]+sum(raa*np.sin(angles[:len(raa)])),
                 pck(sat, 'BODY699_POLE_DEC')[0]+sum(dea*np.cos(angles[:len(dea)])))
    for name, value in [('jupiter', jpole), ('saturn', spole)]:
        checks[name+'_pole'] = bool(np.max(abs(value-model['figures'][name]['pole_icrf']['value'])) < 2e-14)
    return {key: bool(value) for key, value in checks.items()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = ROOT / args.output
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    result = dict(run_id=output.name, utc=datetime.now(timezone.utc).isoformat(),
                  scope='targeted_physics_audit_not_production_admission',
                  platform=platform.platform(), python=platform.python_version(), checks={},
                  versions={p: importlib.metadata.version(p) for p in ('numpy', 'scipy', 'numba', 'mpmath', 'rebound', 'reboundx')})
    snapshots = {}

    def remember(path):
        snapshots[str(path)] = sha(ROOT / path)

    def save():
        result['elapsed_seconds'] = time.monotonic()-started
        (output / 'audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')

    def budget():
        if time.monotonic()-started > 600:
            raise TimeoutError('600 s audit budget')

    model, states = load(MODEL), load(STATES)
    for path in (MODEL, STATES, 'scripts/audit_w2_physics_20261031.py',
                 'docs/tasks/W2_T071_scientific_audit_20261031.md'):
        remember(path)
    result['primary_parameters'] = primary_inputs(model)
    result['checks']['primary_parameters'] = all(result['primary_parameters'].values())
    units, sha_checks, records = {}, {}, []
    for run in RUNS:
        base = 'runs/'+run+'/'
        manifest = load(base+'manifest.json')
        for path, expected in manifest['code_sha256'].items():
            remember(path)
            sha_checks[path] = snapshots[path] == expected
        remember(base+'manifest.json')
        remember(base+'units.json')
        sha_checks[base+'units.json'] = snapshots[base+'units.json'] == manifest['units_file_sha256']
        for unit in load(base+'units.json')['units']:
            if unit['orbit_id'] in units:
                assert units[unit['orbit_id']] == unit
            units[unit['orbit_id']] = unit
        remember(base+'jobs.jsonl')
        records.extend(json.loads(line) for line in (ROOT / (base+'jobs.jsonl')).read_text().splitlines())
    source_checks = {}
    for path in model['source_paths'].values():
        remember(path)
        source_checks[path] = snapshots[path] == model['inputs_sha256'][path]
    result['sha_checks'] = sha_checks
    result['source_sha_checks'] = source_checks
    result['checks']['declared_code_units_and_sources_unchanged'] = all(sha_checks.values()) and all(source_checks.values())
    physical = []
    for oid, u in sorted(units.items()):
        names = SETS[u['host']]
        state = np.asarray(u['state']).reshape(-1, 6)
        original = np.array([states['barycentric'][n][0] for n in names])
        expected_figures = [dict(index=names.index(n), j2=f['j2']['value'], radius=f['reference_radius']['value'],
                                pole=f['pole_icrf']['value']) for n, f in model['figures'].items()]
        parent = names.index('saturn' if u['host'] == 'iapetus' else 'jupiter')
        mu = u['gms'][0]+u['gms'][parent]
        r, v = state[parent, :3], state[parent, 3:]
        a = 1/(2/np.linalg.norm(r)-np.dot(v, v)/mu)
        hill = a*(u['gms'][0]/(3*u['gms'][parent]))**(1/3)
        window = 2*math.pi*math.sqrt(a**3/mu)
        p0 = 2*math.pi*math.sqrt(u['a_km']**3/u['gms'][0])
        pr, pv = state[-1, :3], state[-1, 3:]
        evec = np.cross(pv, np.cross(pr, pv))/u['gms'][0]-pr/np.linalg.norm(pr)
        flags = [u['gms'] == [model['bodies'][n]['gm']['value'] for n in names],
                 u['figures'] == expected_figures,
                 u['radii'] == [model['bodies'][n]['contact_radius']['value']+0.1 for n in names],
                 np.array_equal(state[:-1], original-original[0]),
                 abs(hill/u['hill_km']-1) < 5e-13, abs(window/u['escape_window']-1) < 5e-13,
                 abs(p0/u['period_seconds']-1) < 5e-13, abs(np.linalg.norm(evec)-u['eccentricity']) < 5e-13]
        physical.append(dict(orbit_id=oid, passed=bool(all(flags)), checks=list(map(bool, flags))))
    result['physical_inputs'] = physical
    result['checks']['actual_physical_inputs'] = all(x['passed'] for x in physical)
    result['archived_phase3_scope'] = dict(jobs=len(records), base_orbits=len(units),
        outcomes=dict(Counter(r['outcome'] for r in records)),
        high_e_phases=dict(Counter((r.get('numerical_domain') or {}).get('high_e_phase') or 'none' for r in records)))
    save()

    synthetic = dict(state=[[0., 0, 0, 0, 0, 0], [9., 3., 2., 0.1, 0.2, 0.3],
                            [-5., 11., -2., -0.2, 0.3, 0.1], [0.8, 0.4, 0.2, 0.1, 0.8, -0.2]],
        gms=[1., 8., 3.], figures=[dict(index=1, j2=0.03, radius=1.2, pole=[0.36, 0.48, 0.8]),
                                   dict(index=2, j2=0.02, radius=0.8, pole=[0., 0.6, 0.8])])
    force_rows = []
    for oid, u in [('synthetic_two_oblate_sources', synthetic), *sorted(units.items())]:
        budget()
        state, gm = np.array(u['state']).reshape(-1, 6), np.array(u['gms'])
        fs = ff.figure_array(u['figures'], len(gm))
        expected, absolute, potential = independent_force(state, gm, u['figures'])
        fast = ff.rhs(state.ravel(), gm, fs).reshape(-1, 6)[:, 3:]
        compiled = compiled_acceleration(state, gm, fs)
        original = relative_rhs(state.ravel(), gm, u['figures']).reshape(-1, 6)[:, 3:]
        native = NativeIAS15(ias15_start_state(state, gm), gm, u['figures'], initial_dt=1e-6)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            segment = native.step(1e-6, 1e-6)
        native_acc = segment.coefficients[1].reshape(-1, 6)[:, 3:]/native.sim.dt_last_done
        # Потенциал отдельно: скорости нулевые, чтобы не маскировать знак/двойной счёт.
        zero_v = state.copy()
        zero_v[:, 3:] = 0
        energy_error = abs(ff.energy(zero_v.ravel(), gm, fs)-potential)/abs(potential)
        momentum = np.linalg.norm(np.sum(gm[:, None]*absolute, axis=0))/np.sum(gm*np.linalg.norm(absolute, axis=1))
        errors = dict(fast=relerr(fast, expected), compiled=relerr(compiled, expected),
                      original=relerr(original, expected), native=relerr(native_acc, expected))
        force_rows.append(dict(orbit_id=oid, relative_errors=errors, potential_relative_error=float(energy_error),
                               momentum_relative_residual=float(momentum), warnings=[str(w.message) for w in caught]))
    result['forces'] = force_rows
    result['checks']['force_gradient_all_backends'] = all(max(r['relative_errors'].values()) < 5e-11 and not r['warnings'] for r in force_rows)
    result['checks']['potential_and_reaction'] = all(r['potential_relative_error'] < 5e-13 and r['momentum_relative_residual'] < 5e-13 for r in force_rows)

    # J2 отдельно от монополя: mpmath дифференцирует скалярный P2-потенциал.
    j2_checks = []
    with mp.workdps(50):
        for vector in ([1.3, -.7, .9], [2., 0., 0.], [0., 0., 2.]):
            for axis in ([0., 0., 1.], [.36, .48, .8]):
                f = dict(j2=.03, radius=.8, pole=axis)
                x = list(map(mp.mpf, vector))
                gradient = []
                for k in range(3):
                    def perturbed(z):
                        y = x.copy()
                        y[k] = z
                        return field_potential(y, mp.mpf(2), f)
                    gradient.append(float(-mp.diff(perturbed, x[k])))
                measured = ff.j2_vector(np.array(vector), 2., .03, .8, np.array(axis))
                j2_checks.append(relerr(measured, gradient))
    result['j2_gradient_relative_errors'] = j2_checks
    result['checks']['isolated_j2_sign_normalization'] = max(j2_checks) < 5e-11
    y, gm = np.array(synthetic['state']), np.array(synthetic['gms'])
    fs = ff.figure_array(synthetic['figures'], len(gm))
    acceleration = compiled_acceleration(y, gm, fs)
    q, _ = np.linalg.qr(np.array([[1., 2., 3.], [-2., 1., 1.], [1., 1., -1.]]))
    rotated = np.column_stack([y[:, :3]@q.T, y[:, 3:]@q.T])
    figs = [dict(f, pole=(q@f['pole']).tolist()) for f in synthetic['figures']]
    rotation_error = relerr(compiled_acceleration(rotated, gm, ff.figure_array(figs, len(gm))), acceleration@q.T)
    length, duration = 1000., 60.
    scaled = y*np.array([length]*3+[length/duration]*3)
    figs = [dict(f, radius=f['radius']*length) for f in synthetic['figures']]
    scale_error = relerr(compiled_acceleration(scaled, gm*length**3/duration**2,
                                              ff.figure_array(figs, len(gm))), acceleration*length/duration**2)
    result['symmetries'] = dict(rotation_relative_error=rotation_error, unit_scaling_relative_error=scale_error)
    result['checks']['rotation_and_units'] = max(rotation_error, scale_error) < 5e-11
    save()

    def execute(code, initial, gm, figs, radii, horizon, period, times):
        mode = NumericMode.tight() if code == 'T' else NumericMode()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            if code == 'B':
                out = integrate_engine('ias15_reboundx', initial, gm, figs, radii, horizon,
                                       period=period, mode=mode, times=times, budget=budget)
            else:
                out = cd.integrate_compiled(initial, gm, figs, radii, horizon,
                                            period=period, mode=mode, times=times, budget=budget)
        return out, [str(w.message) for w in caught]

    # Действующие backend, точное L4 в инерциальных фиксированных осях хозяина.
    mu, p0 = .01, 2*math.pi
    initial = np.array([[0., 0, 0, 0, 0, 0], [1., 0, 0, 0, 1., 0],
                        [.5, math.sqrt(3)/2, 0, -math.sqrt(3)/2, .5, 0]])
    grid = np.linspace(0, 10*p0, 513)
    cr3bp = []
    for code in ('T', 'B'):
        out, caught = execute(code, initial, [1-mu, mu], [], [.001, .001], grid[-1], p0, grid)
        actual = np.array([s['state'][-6:] for s in out['samples']])
        t = np.array([s['time'] for s in out['samples']])
        c, s = np.cos(t), np.sin(t)
        expected = np.zeros_like(actual)
        expected[:, 0] = .5*c-math.sqrt(3)/2*s
        expected[:, 1] = .5*s+math.sqrt(3)/2*c
        expected[:, 3], expected[:, 4] = -expected[:, 1], expected[:, 0]
        x, yy = c*actual[:, 0]+s*actual[:, 1], -s*actual[:, 0]+c*actual[:, 1]
        vx = c*actual[:, 3]+s*actual[:, 4]+yy
        vy = -s*actual[:, 3]+c*actual[:, 4]-x
        jacobi = (x-mu)**2+yy**2+2*(1-mu)/np.hypot(x, yy)+2*mu/np.hypot(x-1, yy)-vx**2-vy**2
        cr3bp.append(dict(code=code, status=out['run_status'], outcome=out['physical_outcome'],
                          state_error=float(np.max(np.abs(actual-expected))),
                          jacobi_drift=float(np.max(abs(jacobi/jacobi[0]-1))), warnings=caught))
    result['cr3bp'] = cr3bp
    result['checks']['cr3bp_L4_Jacobi'] = all(r['status'] == 'completed' and r['outcome'] == 'survived'
        and r['state_error'] < 1e-8 and r['jacobi_drift'] < 1e-9 and not r['warnings'] for r in cr3bp)
    save()

    # J2 вековые скорости: независимая формула первого порядка, не точная траектория.
    precession = []
    e, inc, radius = .1, .6, .5
    rp, vp = 1-e, math.sqrt((1+e)/(1-e))
    initial = np.array([[0., 0, 0, 0, 0, 0], [rp, 0, 0, 0, vp*math.cos(inc), vp*math.sin(inc)]])
    grid = np.linspace(0, 80*p0, 4097)
    for j2 in (1e-4, 5e-5):
        for code in ('T', 'B'):
            figs = [dict(index=0, j2=j2, radius=radius, pole=[0., 0., 1.])]
            out, caught = execute(code, initial, [1.], figs, [.01], grid[-1], p0, grid)
            y = np.array([s['state'][-6:] for s in out['samples']])
            t = np.array([s['time'] for s in out['samples']])
            r, v = y[:, :3], y[:, 3:]
            rn = np.linalg.norm(r, axis=1)
            h = np.cross(r, v)
            node = np.column_stack([-h[:, 1], h[:, 0], np.zeros(len(h))])
            ev = np.cross(v, h)-r/rn[:, None]
            omega = np.unwrap(np.arctan2(node[:, 1], node[:, 0]))
            peri = np.unwrap(np.arctan2(np.sum(np.cross(node, ev)*h, axis=1)/np.linalg.norm(h, axis=1), np.sum(node*ev, axis=1)))
            scale = j2*radius**2/(1-e*e)**2
            rates = [-1.5*scale*math.cos(inc), .75*scale*(5*math.cos(inc)**2-1)]
            observed = [np.polyfit(t, omega, 1)[0], np.polyfit(t, peri, 1)[0]]
            energy = np.sum(v*v, axis=1)/2-1/rn+j2*radius**2*(3*(r[:, 2]/rn)**2-1)/(2*rn**3)
            precession.append(dict(code=code, j2=j2, status=out['run_status'], outcome=out['physical_outcome'],
                node_relative_error=float(abs(observed[0]/rates[0]-1)),
                peri_relative_error=float(abs(observed[1]/rates[1]-1)),
                specific_energy_drift=float(np.max(abs(energy/energy[0]-1))),
                axial_angular_momentum_drift=float(np.max(abs(h[:, 2]/h[0, 2]-1))), warnings=caught))
    result['j2_precession'] = precession
    result['checks']['J2_precession_energy_Laxis'] = all(r['status'] == 'completed' and r['outcome'] == 'survived'
        and max(r['node_relative_error'], r['peri_relative_error']) < .01
        and max(r['specific_energy_drift'], r['axial_angular_momentum_drift']) < 1e-9
        and not r['warnings'] for r in precession)
    save()

    contacts = []
    for eccentricity in (.9, .99, .999):
        initial = np.array([[0., 0, 0, 0, 0, 0], [1+eccentricity, 0, 0, 0,
                            math.sqrt((1-eccentricity)/(1+eccentricity)), 0]])
        surface = 1.2*(1-eccentricity)
        with mp.workdps(60):
            ra, va = mp.mpf(float(initial[1, 0])), mp.mpf(float(initial[1, 4]))
            a = 1/(2/ra-va*va)
            ec = ra/a-1
            n = a**mp.mpf('-1.5')
            E = mp.acos((1-mp.mpf(surface)/a)/ec)
            tc = float((mp.pi-E+ec*mp.sin(E))/n)
            period = float(2*mp.pi/n)
            def exact(t):
                # От апоцентра до первого перицентра: монотонный Kepler, bisection.
                lo, hi = mp.pi, 2*mp.pi
                mean = mp.pi+mp.mpf(float(t))*n
                for _ in range(220):
                    mid = (lo+hi)/2
                    if mid-ec*mp.sin(mid) < mean:
                        lo = mid
                    else:
                        hi = mid
                z = (lo+hi)/2
                c, s = mp.cos(z), mp.sin(z)
                b = mp.sqrt(1-ec*ec)
                return [float(-a*(c-ec)), float(-a*b*s), 0.,
                        float(a*n*s/(1-ec*c)), float(-a*n*b*c/(1-ec*c)), 0.]
            grid = np.linspace(0, tc, 65)[:-1]
            reference = np.array([exact(t) for t in grid])
        for code in ('D', 'T', 'B'):
            out, caught = execute(code, initial, [1.], [], [surface], period/2, period, grid)
            actual = np.array([s['state'][-6:] for s in out['samples']])
            err = float(np.max(abs(actual-reference[:len(actual)]))) if len(actual) else None
            timing = abs(out['event']['time']-tc)/period if out['event'] else None
            contacts.append(dict(e=eccentricity, code=code, status=out['run_status'], outcome=out['physical_outcome'],
                contact_time_error_p0=timing, state_error_a_na=err, samples=len(actual), expected_samples=len(grid),
                numerical_domain=out.get('numerical_domain'), warnings=caught,
                analytic_contact_time=tc, numeric_contact_time=out['event']['time'] if out['event'] else None))
    result['high_e_contacts'] = contacts
    result['checks']['declared_terminal_Kepler_controls'] = all(r['status'] == 'completed' and r['outcome'] == 'host_contact'
        and r['contact_time_error_p0'] < 1e-6 and r['state_error_a_na'] < 1e-6
        and r['samples'] == r['expected_samples'] and not r['warnings'] for r in contacts)
    save()

    zero_force = []
    for speed in (0., .1):
        native = NativeIAS15([[0., 0, 0, speed, 0, 0]], [1.], [], initial_dt=.05)
        steps = []
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            for k in range(12):
                native.step(1., .05)
                steps.append(dict(step=k+1, time=native.sim.t,
                                  iterations_max_exceeded=int(native.sim.integrator.iterations_max_exceeded)))
        error = float(np.max(abs(native.absolute_state()[0]-[speed*native.sim.t, 0, 0, speed, 0, 0])))
        zero_force.append(dict(speed=speed, steps=steps, warnings=[str(w.message) for w in caught], ballistic_error=error))
    result['zero_force_warning'] = zero_force
    result['checks']['zero_force_PC_counter_prediction'] = all(
        [s['iterations_max_exceeded'] for s in r['steps']] == list(range(1, 13))
        and len(r['warnings']) == 1 and r['ballistic_error'] < 1e-14 for r in zero_force)
    result['inputs_sha256'] = snapshots
    result['checks']['audited_files_unchanged_at_end'] = all(sha(ROOT/p) == h for p, h in snapshots.items())
    result['limitations'] = [
        'Контроли короткие и адресные; не допуск W1/W3, high-e или полного горизонта.',
        'Ковариации и физические поверхности не восстановлены; внешняя наблюдательная валидация не выполнена.',
        'Сопоставление источника sdist с установленной библиотекой по версии/поведению, не воспроизведённая сборка.',
        'События реальных долгих случаев и статистические выводы требуют отдельной сходимости.',
    ]
    save()
    print(json.dumps(dict(run_id=result['run_id'], checks=result['checks'],
                          elapsed_seconds=result['elapsed_seconds']), ensure_ascii=False, indent=2))
    return int(not all(result['checks'].values()))


if __name__ == '__main__':
    raise SystemExit(main())
