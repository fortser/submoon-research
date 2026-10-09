"""ПРОБНЫЙ движок S: симпалектический фиксированный шаг (drift-kick-drift).

Расщепление Гамильтона пробы: H = H_kepler(вокруг хозяина) + V_pert(внешнее поле).
  drift(dt/2)  — точное кеплеровское движение вокруг хозяина (универсальные переменные);
  kick(dt)     — v += a_pert(r) * dt, где a_pert = probe_acceleration - монополь хозяина;
  drift(dt/2).
Композиция точных потоков => симплектический метод 2-го порядка с фиксированным шагом.
Массивные позиции берутся из кеша (одно обращение на шаг). События хозяина и ухода —
по кубическому Эрмиту пробы, без обращений к кешу.
Разведка, не проверенный production-модуль.
"""
import os, sys, json, math, time
for _n in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMBA_NUM_THREADS'):
    os.environ[_n] = '1'
ROOT='/home/user/workspace/submoon-research'; sys.path.insert(0, ROOT+'/src')
import numpy as np
from numba import njit
from scipy.optimize import brentq
from numpy.polynomial import chebyshev as cheb
from submoon_research.dynamics import fast_forces
from submoon_research.dynamics.dense_segments import chebyshev_samples
from submoon_research.dynamics.engine_compare import integrate_engine, NumericMode
from submoon_research.dynamics.native_ias15 import MassiveCache
from submoon_research.events.escape import EscapeTracker
from submoon_research.sampling.design import elements_to_cartesian
from submoon_research.workflows.w2_comparison import warmup

YEAR=31557600.0; GMSUN=1.32712440041279419e11
CFG=dict(names=['iapetus','sun','jupiter','saturn','titan'],
 gm={'iapetus':120.5151060137642,'sun':GMSUN,'jupiter':1.266865341960128e8,
     'saturn':3.7931206225414617e7,'titan':8978.138207},
 rad={'iapetus':734.3,'sun':695700.0,'jupiter':69911.0,'saturn':58232.0,'titan':2574.73},
 parent='saturn', a_host=3560800.0, planet_orbit=1433500000.0,
 moons={'titan':1221870.0}, other_planets={'jupiter':778570000.0},
 figs=[('jupiter',0.014736,71492.0),('saturn',0.016298,60268.0)])

# ---------- ядра ----------
@njit(cache=True, fastmath=False)
def _C(z):
    if z > 1e-8:
        s = math.sqrt(z); return (1.0-math.cos(s))/z
    if z < -1e-8:
        s = math.sqrt(-z); return (math.cosh(s)-1.0)/(-z)
    return 0.5 - z/24.0 + z*z/720.0

@njit(cache=True, fastmath=False)
def _S(z):
    if z > 1e-8:
        s = math.sqrt(z); return (s-math.sin(s))/(s*s*s)
    if z < -1e-8:
        s = math.sqrt(-z); return (math.sinh(s)-s)/(s*s*s)
    return 1.0/6.0 - z/120.0 + z*z/5040.0

@njit(cache=True, fastmath=False)
def kepler_drift(r0, v0, dt, mu):
    r0m = math.sqrt(r0[0]*r0[0]+r0[1]*r0[1]+r0[2]*r0[2])
    v2 = v0[0]*v0[0]+v0[1]*v0[1]+v0[2]*v0[2]
    alpha = 2.0/r0m - v2/mu
    sq = math.sqrt(mu)
    sigma0 = (r0[0]*v0[0]+r0[1]*v0[1]+r0[2]*v0[2])/sq
    chi = sq*dt*alpha if alpha > 1e-14 else sq*dt/r0m
    for _ in range(80):
        z = alpha*chi*chi
        C = _C(z); S = _S(z)
        F = sigma0*chi*chi*C + (1.0-alpha*r0m)*chi*chi*chi*S + r0m*chi - sq*dt
        dF = sigma0*chi*(1.0-z*S) + (1.0-alpha*r0m)*chi*chi*C + r0m
        d = F/dF
        chi -= d
        if abs(d) <= 1e-15*max(1.0, abs(chi)):
            break
    z = alpha*chi*chi
    C = _C(z); S = _S(z)
    f = 1.0 - chi*chi*C/r0m
    g = dt - chi*chi*chi*S/sq
    r = np.empty(3); v = np.empty(3)
    for k in range(3):
        r[k] = f*r0[k] + g*v0[k]
    rm = math.sqrt(r[0]*r[0]+r[1]*r[1]+r[2]*r[2])
    fdot = sq/(r0m*rm)*chi*(z*S-1.0)
    gdot = 1.0 - chi*chi*C/rm
    for k in range(3):
        v[k] = fdot*r0[k] + gdot*v0[k]
    return r, v

@njit(cache=True, fastmath=False)
def pert_acceleration(r, massive_positions, gms, figures):
    a = fast_forces.probe_acceleration(r, massive_positions, gms, figures)
    d2 = r[0]*r[0]+r[1]*r[1]+r[2]*r[2]
    f = gms[0]/(d2*math.sqrt(d2))
    out = np.empty(3)
    for k in range(3):
        out[k] = a[k] + f*r[k]
    return out

# ---------- события пробы по кубическому Эрмиту ----------
class ProbeHermite:
    """dense(t) в контракте PowerSegment: (n_state, n_times); хозяин в начале координат."""
    def __init__(self, r0, v0, r1, v1, t0, t1, count):
        self.r0=r0; self.v0=v0; self.r1=r1; self.v1=v1
        self.t0=t0; self.t1=t1; self.n_state=(count+1)*6
    def __call__(self, times):
        arr=np.asarray(times, dtype=float); scalar=arr.ndim==0
        ts=np.atleast_1d(arr); h=self.t1-self.t0; s=(ts-self.t0)/h
        h00=2*s**3-3*s**2+1; h10=s**3-2*s**2+s; h01=-2*s**3+3*s**2; h11=s**3-s**2
        d00=6*s**2-6*s; d10=3*s**2-4*s+1; d01=-6*s**2+6*s; d11=3*s**2-2*s
        pos=(h00[:,None]*self.r0 + h10[:,None]*h*self.v0
             + h01[:,None]*self.r1 + h11[:,None]*h*self.v1)
        vel=(d00[:,None]*self.r0/h + d10[:,None]*self.v0
             + d01[:,None]*self.r1/h + d11[:,None]*self.v1)
        out=np.zeros((self.n_state, len(ts)))
        out[-6:-3, :]=pos.T; out[-3:, :]=vel.T
        return out[:, 0] if scalar else out

def host_contact(dense, t0, t1, radius, distance_tolerance=1e-7, time_tolerance=1e-5):
    samples, inverse = chebyshev_samples(dense, t0, t1)
    positions = samples[:, -1, :3]
    coefficients = inverse @ positions
    lower = np.linalg.norm(coefficients[0]) - np.sum(np.linalg.norm(coefficients[1:], axis=1))
    if lower > radius + distance_tolerance:
        return None
    squared = np.zeros(2*len(coefficients)-1)
    for axis in range(3):
        part = cheb.chebmul(coefficients[:, axis], coefficients[:, axis]); squared[:len(part)] += part
    roots = cheb.chebroots(cheb.chebder(squared))
    candidates = sorted([-1.0, 1.0] + [float(z.real) for z in roots if abs(z.imag) < 1e-8 and -1 < z.real < 1])
    def gap(x): return float(np.linalg.norm(cheb.chebval(x, coefficients)) - radius)
    previous = -1.0
    for current in candidates:
        value = gap(current)
        if value <= distance_tolerance:
            if gap(previous) <= 0:
                entry = previous
            elif value < 0:
                entry = brentq(gap, previous, current, xtol=max(5e-16, min(1e-12, 2*time_tolerance/(t1-t0))))
            else:
                entry = current
            return dict(time=float(t0+(entry+1)*(t1-t0)/2), body_index=0,
                        event='reference_contact', distance_residual_km=0.0,
                        physical_surface_verified=False)
        previous = current
    return None

# ---------- цикл движка S ----------
def symplectic_loop(st, gms, figures, radii, horizon, period, hill_km, escape_window,
                    cache, steps_per_orbit, events, sample_times, limit):
    started=time.perf_counter()
    def budget():
        if time.perf_counter()-started>limit: raise TimeoutError('wall limit')
    fs=fast_forces.figure_array(figures, len(gms)); count=len(gms)
    dt=period/steps_per_orbit
    r=np.array(st[-6:-3], float); v=np.array(st[-3:], float)
    mu=float(gms[0]); t=0.0
    tracker=EscapeTracker(hill_km, escape_window) if (events and hill_km is not None) else None
    steps=0; event=None; status='completed'
    probe_hist=[]; time_hist=[]; si=0; samples=[]
    try:
        budget()
        while t < horizon-1e-12:
            budget()
            h=min(dt, horizon-t)
            left=t; r0=r.copy(); v0=v.copy()
            r,v=kepler_drift(r,v,h/2,mu); t+=h/2
            massive=cache(t).reshape(-1,6)
            a=pert_acceleration(r, massive[:,:3], gms, fs)
            v=v+a*h
            r,v=kepler_drift(r,v,h/2,mu); t+=h/2
            steps+=1
            probe_hist.append(np.r_[r,v]); time_hist.append(t)
            if events:
                dense=ProbeHermite(r0,v0,r,v,left,t,count)
                ev=host_contact(dense,left,t,radii[0]); end=t
                if tracker is not None:
                    dep=tracker.advance(dense,left,t)
                    if dep and (ev is None or dep['time']<ev['time']):
                        ev=dep; end=dep['time']
                if ev is not None:
                    event=ev; t=end; break
            while si < len(sample_times) and sample_times[si] <= t:
                samples.append(dict(time=float(sample_times[si]), probe=probe_hist[-1].tolist()))
                si+=1
    except TimeoutError:
        status='partial'
    if status=='completed' and events and event:
        outcome=('operational_escape' if event.get('event')=='operational_escape'
                 else 'host_contact' if event.get('body_index')==0 else 'other_contact')
    elif status=='completed':
        outcome=tracker.outcome() if tracker else 'survived'
    else:
        outcome='unresolved'
    return dict(status=status, outcome=outcome, steps=steps, dt=dt,
                steps_per_orbit=steps_per_orbit, events=bool(events),
                wall_seconds=time.perf_counter()-started, last_valid_time=float(t),
                final_probe=np.r_[r,v].tolist(), samples=samples,
                time_hist=time_hist, probe_hist=probe_hist)

def energy_of(probe, t, cache, gms, fs):
    full=np.vstack([cache(t).reshape(-1,6), np.asarray(probe).reshape(1,6)])
    return float(fast_forces.energy(full.ravel(), gms, fs))

def build(cfg):
    names,gm,rad=cfg['names'],cfg['gm'],cfg['rad']; host,parent=names[0],cfg['parent']
    pos,vel={'sun':np.zeros(3)},{'sun':np.zeros(3)}
    for p,r in cfg['other_planets'].items():
        pos[p]=np.array([r,0.,0.]); vel[p]=np.array([0.,math.sqrt(GMSUN/r),0.])
    pr=cfg['planet_orbit']; pos[parent]=np.array([pr,0.,0.]); vel[parent]=np.array([0.,math.sqrt(GMSUN/pr),0.])
    for m,r in cfg['moons'].items():
        pos[m]=pos[parent]+np.array([r,0.,0.]); vel[m]=vel[parent]+np.array([0.,math.sqrt(gm[parent]/r),0.])
    pos[host]=pos[parent]+np.array([cfg['a_host'],0.,0.]); vel[host]=vel[parent]+np.array([0.,math.sqrt(gm[parent]/cfg['a_host']),0.])
    bary=np.array([np.r_[pos[n],vel[n]] for n in names]); rel=bary-bary[0]
    gms=np.array([gm[n] for n in names]); radii=np.array([rad[n]+0.1 for n in names])
    figs=[dict(index=names.index(b),j2=j,radius=r,pole=[0.,0.,1.]) for b,j,r in cfg['figs']]
    hill=cfg['a_host']*(gms[0]/(3*gm[parent]))**(1/3)
    window=2*math.pi*math.sqrt(cfg['a_host']**3/(gm[parent]+gms[0]))
    return rel,gms,radii,figs,hill,window

def probe_ic(rel, gms, hill, a_frac, e, inc_deg):
    a=a_frac*hill
    p=elements_to_cartesian(gms[0], a, e, math.cos(math.radians(inc_deg)), 0.,0.,0., np.eye(3))
    return np.vstack([rel,p]).ravel(), a, 2*math.pi*math.sqrt(a**3/gms[0])

def main():
    warmup()
    rel,gms,radii,figs,hill,window=build(CFG)
    fs=fast_forces.figure_array(figs,len(gms))
    years=1.0; horizon=years*YEAR; times=np.linspace(0,horizon,65)
    res={'meta':dict(case='iapetus',years=years,bodies=len(gms),hill_km=hill,
                     window_days=window/86400,year_seconds=YEAR)}

    print("="*78); print("СЛУЧАЙ 1: выживающая проба a=0.15RH e=0 i=0"); print("="*78)
    st,a,per=probe_ic(rel,gms,hill,0.15,0.0,0.0)
    print(f"a={a:.0f}km period={per/86400:.3f}d horizon={years}yr",flush=True)
    mode=NumericMode()
    t0=time.perf_counter()
    ra=integrate_engine('dop853_jit',st,gms,figs,radii,horizon,period=per,mode=mode,
        hill_km=hill,escape_window=window,times=times,budget=lambda:None)
    ta=time.perf_counter()-t0
    ref=np.asarray(ra['final_state'][-6:], float)
    print(f"A  dop853_jit  events=on : {ta:8.3f}s steps={ra['steps']:6d} nfev={ra['nfev']:7d} "
          f"{ta/ra['steps']*1e3:6.3f}ms/step out={ra['physical_outcome']}",flush=True)
    res['A_events_on']=dict(s=ta,steps=ra['steps'],nfev=ra['nfev'],out=ra['physical_outcome'])
    # референс высокой точности
    t0=time.perf_counter()
    rt=integrate_engine('dop853_jit',st,gms,figs,radii,horizon,period=per,mode=NumericMode.tight(),
        hill_km=hill,escape_window=window,times=times,budget=lambda:None)
    tt=time.perf_counter()-t0
    ref_tight=np.asarray(rt['final_state'][-6:], float)
    dref=float(np.linalg.norm(ref[:3]-ref_tight[:3])/a)
    print(f"A  dop853_jit  rtol=1e-13 : {tt:8.3f}s steps={rt['steps']:6d} "
          f"|A(1e-11)-A(1e-13)|/a={dref:.3e}",flush=True)
    res['A_tight']=dict(s=tt,steps=rt['steps'])
    res['reference_uncertainty_dpos_a']=dref
    # кеш
    t0=time.perf_counter(); cache=MassiveCache(st,gms,figs,epsilon=mode.cache_epsilon)
    cache.extend(horizon,lambda:None); cb=time.perf_counter()-t0
    print(f"cache build: {cb:.3f}s segs={len(cache.segments)} bytes={cache.bytes} ({cache.bytes/2**20:.1f}MiB)",flush=True)
    res['cache']=dict(build_s=cb,segs=len(cache.segments),bytes=cache.bytes)
    # S: сетка шагов
    print("\n--- S: сходимость по шагу (1 год, выживающая) ---",flush=True)
    print(f"{'step/orbit':>10} {'dt, s':>10} {'dt, d':>8} {'time,s':>9} {'steps':>8} "
          f"{'dpos/a':>10} {'order':>6} {'dE/E':>10} {'out':>10}",flush=True)
    conv=[]; prev=None
    for spo in (4,8,16,32,64,128,256):
        r=symplectic_loop(st,gms,figs,radii,horizon,per,hill,window,cache,spo,False,times,900)
        dpos=float(np.linalg.norm(np.array(r['final_probe'][:3])-ref_tight[:3])/a)
        e0=energy_of(st[-6:],0.0,cache,gms,fs)
        dE=0.0
        for smp in r['samples']:
            dE=max(dE, abs(energy_of(smp['probe'],smp['time'],cache,gms,fs)-e0)/abs(e0))
        order=float('nan') if prev is None else math.log(prev[1]/dpos)/math.log(prev[0]/spo)
        print(f"{spo:10d} {r['dt']:10.1f} {r['dt']/86400:8.4f} {r['wall_seconds']:9.3f} {r['steps']:8d} "
              f"{dpos:10.3e} {order:6.2f} {dE:10.2e} {r['outcome']:>10}",flush=True)
        conv.append(dict(steps_per_orbit=spo,dt=r['dt'],dt_days=r['dt']/86400,wall_s=r['wall_seconds'],
            steps=r['steps'],dpos_rel=dpos,order=order,dE_rel=dE,outcome=r['outcome']))
        prev=(spo,dpos)
    res['convergence']=conv
    # S: события вкл (для честного сравнения с A)
    print("\n--- S: события вкл (хозяин+уход по Эрмиту пробы) ---",flush=True)
    s_ev={}
    for spo in (16,32,64):
        r=symplectic_loop(st,gms,figs,radii,horizon,per,hill,window,cache,spo,True,times,900)
        dpos=float(np.linalg.norm(np.array(r['final_probe'][:3])-ref_tight[:3])/a)
        print(f"  S {spo:3d}/orbit events=on : {r['wall_seconds']:8.3f}s steps={r['steps']:6d} "
              f"{r['wall_seconds']/r['steps']*1e3:6.3f}ms/step dpos/a={dpos:.3e} out={r['outcome']}",flush=True)
        s_ev[spo]=dict(s=r['wall_seconds'],steps=r['steps'],dpos_rel=dpos,outcome=r['outcome'])
    res['S_events_on']=s_ev
    # сводка
    best=min(conv,key=lambda c: abs(c['dpos_rel']-dref) if c['dpos_rel']>dref else c['wall_s'])
    s32=[c for c in conv if c['steps_per_orbit']==32][0]
    print("\n=== СВОДКА (выживающая, 1 год) ===",flush=True)
    print(f"  A  (события вкл) : {ta:8.3f}s  шаг≈{ra['steps']/ (horizon/per):.1f}/орбиту",flush=True)
    print(f"  S  (32/орбиту, события выкл) : {s32['wall_s']:8.3f}s  ускорение {ta/s32['wall_s']:.1f}x  dpos/a={s32['dpos_rel']:.3e}",flush=True)
    if 32 in s_ev:
        print(f"  S  (32/орбиту, события вкл) : {s_ev[32]['s']:8.3f}s  ускорение {ta/s_ev[32]['s']:.1f}x  dpos/a={s_ev[32]['dpos_rel']:.3e}",flush=True)
    # --- случай 2: уход ---
    print("\n"+"="*78); print("СЛУЧАЙ 2: проба на уходе a=0.5RH e=0.3"); print("="*78)
    st2,a2,per2=probe_ic(rel,gms,hill,0.5,0.3,0.0)
    print(f"a={a2:.0f}km period={per2/86400:.3f}d",flush=True)
    t0=time.perf_counter()
    ra2=integrate_engine('dop853_jit',st2,gms,figs,radii,horizon,period=per2,mode=mode,
        hill_km=hill,escape_window=window,times=times,budget=lambda:None)
    ta2=time.perf_counter()-t0
    print(f"A  dop853_jit events=on : {ta2:8.3f}s steps={ra2['steps']:6d} nfev={ra2['nfev']:7d} "
          f"{ta2/ra2['steps']*1e3:6.3f}ms/step out={ra2['physical_outcome']} t_end={ra2['last_valid_time']/86400:.1f}d",flush=True)
    t0=time.perf_counter(); cache2=MassiveCache(st2,gms,figs,epsilon=mode.cache_epsilon)
    cache2.extend(horizon,lambda:None); cb2=time.perf_counter()-t0
    esc={}
    for spo in (16,32,64,128):
        r=symplectic_loop(st2,gms,figs,radii,horizon,per2,hill,window,cache2,spo,True,times,900)
        print(f"S  {spo:3d}/orbit events=on : {r['wall_seconds']:8.3f}s steps={r['steps']:6d} "
              f"{r['wall_seconds']/max(r['steps'],1)*1e3:6.3f}ms/step out={r['outcome']} t_end={r['last_valid_time']/86400:.1f}d",flush=True)
        esc[spo]=dict(s=r['wall_seconds'],steps=r['steps'],outcome=r['outcome'],t_end=r['last_valid_time'])
    res['case2']=dict(A=dict(s=ta2,steps=ra2['steps'],out=ra2['physical_outcome'],t_end=ra2['last_valid_time']),
                      S=esc, cache_build_s=cb2)
    res['summary']=dict(A_s=ta, S32_no_events_s=s32['wall_s'], S32_speedup=ta/s32['wall_s'],
        S32_dpos=s32['dpos_rel'], reference_uncertainty=dref)
    with open(ROOT+'/scratch/symplectic_probe.json','w') as f:
        json.dump({k:v for k,v in res.items() if k not in ()}, f, indent=1, default=str)
    print("\nDONE",flush=True)
main()
