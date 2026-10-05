"""Операционные события отделены от физических выводов и статуса вычисления."""
import numpy as np
from scipy.optimize import brentq,minimize_scalar

from submoon_research.contracts.identifiers import ENERGY_EVENT,CONTACT_EVENT


def specific_energy(state,gm_host):
    state=np.asarray(state,dtype=float)
    if state.shape!=(6,) or not np.isfinite(state).all() or gm_host<=0 or np.linalg.norm(state[:3])==0:
        raise ValueError('Неверное состояние для E2')
    return float(np.dot(state[3:],state[3:])/2-gm_host/np.linalg.norm(state[:3]))


def locate_reference_contact(dense_state,t0,t1,radius,*,time_tolerance=1e-10,distance_tolerance=1e-10):
    """Один разрешённый близкий проход; не гарантия для произвольного длинного шага."""
    if not t0<t1 or radius<=0 or time_tolerance<=0 or distance_tolerance<0:
        raise ValueError('Неверный контракт контактного интервала')
    def gap(t):
        state=np.asarray(dense_state(t),dtype=float)
        if state.shape!=(6,) or not np.isfinite(state).all():
            raise ValueError('Неконечное dense-состояние')
        return float(np.linalg.norm(state[:3])-radius)
    if gap(t0)<=0:
        return dict(time=t0,event='reference_contact',physical_surface_verified=False)
    minimum=minimize_scalar(gap,bounds=(t0,t1),method='bounded',options=dict(xatol=time_tolerance))
    end=min((minimum.x,t1),key=gap)
    if gap(end)>distance_tolerance:
        return None
    if gap(end)<0:
        time=brentq(gap,t0,end,xtol=time_tolerance)
        kind='reference_contact'
    else:
        time=end
        kind='reference_tangency_within_tolerance'
    return dict(time=float(time),event=kind,physical_surface_verified=False)


IMPLEMENTATIONS={ENERGY_EVENT:specific_energy,CONTACT_EVENT:locate_reference_contact}
