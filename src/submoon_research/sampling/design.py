"""Условные меры на контактно ограниченной области и точные декартовы старты."""
from __future__ import annotations

import math
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from scipy.integrate import quad
from scipy.optimize import brentq
from scipy.stats import qmc


class DomainSpec(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False, frozen=True)
    domain_id: str = Field(min_length=1)
    gm_host: float = Field(gt=0)
    gm_parent: float = Field(gt=0)
    a_host_km: float = Field(gt=0)
    reference_radius_km: float = Field(gt=0)
    submoon_radius_km: float = Field(gt=0)
    eccentricity_max: float = Field(gt=0, le=0.8)
    contact_buffer: float = Field(ge=0, le=0.05)
    reference_plane: Literal['explicit_orthonormal_basis_in_ICRF'] = 'explicit_orthonormal_basis_in_ICRF'
    contact_model: Literal['operational_reference_sphere'] = 'operational_reference_sphere'

    @property
    def hill_km(self):
        return self.a_host_km * (self.gm_host/(3*self.gm_parent))**(1/3)

    @property
    def inner_km(self):
        return (1+self.contact_buffer)*(self.reference_radius_km+self.submoon_radius_km)


MEASURES=('uniform_a_e_cos_i_phases','uniform_log_a','canonical_volume')


def domain_normalization(domain, measure):
    """Интеграл плотности по (a,e); ориентация и фазы нормируются отдельно."""
    if measure not in MEASURES:
        raise ValueError('Неизвестная мера; круговая/архивная сетка имеет отдельную меру')
    low, high=domain.inner_km,domain.hill_km
    emax=min(domain.eccentricity_max,1-low/high)
    if high<=low or emax<=0:
        raise ValueError('Пустая область: выживаемость не определена')

    def width(e):
        lower=low/(1-e)
        if measure==MEASURES[0]:
            return high-lower
        if measure==MEASURES[1]:
            return math.log(high/lower)
        return e*(2/3)*(high**1.5-lower**1.5)
    normal=quad(width,0,emax,epsabs=0,epsrel=1e-12)[0]
    return normal,emax,width


def elements_to_cartesian(gm,a,e,u,mean_anomaly,omega,node,basis):
    """Кеплеровы эллипсы; вырождения не меняют физический смысл декартова старта."""
    basis=np.asarray(basis,dtype=float)
    if not np.isfinite([gm,a,e,u,mean_anomaly,omega,node]).all() or not (gm>0 and a>0 and 0<=e<1 and -1<=u<=1):
        raise ValueError('Недопустимые элементы')
    if basis.shape!=(3,3) or not np.isfinite(basis).all() or not np.allclose(basis.T@basis,np.eye(3),rtol=0,atol=1e-12) or abs(np.linalg.det(basis)-1)>1e-12:
        raise ValueError('Неверный ортонормальный базис')
    m=mean_anomaly%(2*math.pi)
    eccentric_anomaly=brentq(lambda x:x-e*math.sin(x)-m,0,2*math.pi,xtol=1e-14) if m else 0.0
    c,s=math.cos(eccentric_anomaly),math.sin(eccentric_anomaly)
    beta=math.sqrt(1-e*e)
    position=np.array([a*(c-e),a*beta*s,0.0])
    velocity=math.sqrt(gm/a)/(1-e*c)*np.array([-s,beta*c,0.0])
    co,so,cn,sn=math.cos(omega),math.sin(omega),math.cos(node),math.sin(node)
    si=math.sqrt(max(0,1-u*u))
    rotation=np.array([[cn*co-sn*so*u,-cn*so-sn*co*u,sn*si],
        [sn*co+cn*so*u,-sn*so+cn*co*u,-cn*si],[so*si,co*si,u]])
    return np.r_[basis@rotation@position,basis@rotation@velocity]


def generate(domain,measure,*,power,seed,randomization,basis):
    if type(power) is not int or not 0<=power<=16 or type(seed) is not int or seed<0 or type(randomization) is not int or randomization<0:
        raise ValueError('Нужны явные power/seed/randomization')
    basis=np.asarray(basis,dtype=float)
    if basis.shape!=(3,3) or not np.isfinite(basis).all() or not np.allclose(basis.T@basis,np.eye(3),rtol=0,atol=1e-12) or abs(np.linalg.det(basis)-1)>1e-12:
        raise ValueError('Опорная плоскость должна задавать правую ортонормальную систему')
    normal,emax,width=domain_normalization(domain,measure)
    points=qmc.Sobol(d=6,scramble=True,seed=seed).random_base2(power)
    records=[]
    high,low=domain.hill_km,domain.inner_km
    for index,point in enumerate(points):
        target=point[0]*normal
        e=brentq(lambda x:quad(width,0,x,epsabs=0,epsrel=1e-12)[0]-target,0,emax,xtol=1e-14) if target else 0.0
        lower=low/(1-e)
        if measure==MEASURES[0]:
            a=lower+(high-lower)*point[1]
            base=1.0
        elif measure==MEASURES[1]:
            a=lower*(high/lower)**point[1]
            base=1/a
        else:
            a=(lower**1.5+(high**1.5-lower**1.5)*point[1])**(2/3)
            base=math.sqrt(a)*e
        u=2*point[2]-1
        mean,omega,node=point[3:]*2*math.pi
        state=elements_to_cartesian(domain.gm_host,a,e,u,mean,omega,node,basis)
        density=base/(normal*2*(2*math.pi)**3)
        records.append(dict(orbit_id=f'{randomization}-{index}',position=state[:3].tolist(),velocity=state[3:].tolist(),
            elements=dict(a_km=a,e=e,cos_i=u,mean_anomaly=mean,omega=omega,node=node),
            sampling=dict(domain_id=domain.domain_id,measure_id=measure,proposal_density=density,
                target_density=density,weight=1.0,inclusion_probability=1.0,stratum='whole_domain',
                seed=seed,randomization=randomization)))
    return dict(schema_version='0.1',generator_version='conditional-sobol-v1',
        domain=domain.model_dump(),measure_id=measure,normalization_a_e=normal,
        normalization_units='km' if measure==MEASURES[0] else 'dimensionless' if measure==MEASURES[1] else 'km^1.5',
        denominator=len(records),proposed=len(records),accepted=len(records),rejected=0,
        endpoint_policy='closed_physical_domain_sobol_nodes_half_open_unit_cube',
        basis_icrf=basis.tolist(),records=records,physical_contact_verified=False,
        population_interpretation=False)


def reproduction_grid(gm,lower_km,upper_km,basis,*,lower_coefficient,lower_formula_id):
    """Явная самостоятельная 10×13 сетка; архив читается по исходным векторам."""
    if not 0<lower_km<upper_km or not math.isfinite(lower_coefficient) or lower_coefficient<=0 or not lower_formula_id:
        raise ValueError('Нужны явные границы и коэффициент')
    records=[]
    for a in np.linspace(lower_km,upper_km,10):
        for i in np.linspace(0,math.pi,13):
            records.append(elements_to_cartesian(gm,a,0,math.cos(i),0,0,0,np.asarray(basis)).tolist())
    return dict(measure_id='baseline_grid',denominator=130,endpoint_policy='both_endpoints_included',
        lower_km=lower_km,upper_km=upper_km,lower_coefficient=lower_coefficient,
        lower_formula_id=lower_formula_id,records=records,source='own_explicit_grid_not_author_generator')
