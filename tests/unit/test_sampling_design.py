"""Проверки физических инвариантов и нормировок условных мер."""
import math
import numpy as np
import pytest
from scipy.integrate import quad

from submoon_research.sampling.design import DomainSpec,MEASURES,domain_normalization,generate,elements_to_cartesian,reproduction_grid
from submoon_research.sampling.statistics import randomized_interval,paired_difference,survival_bounds


def domain(**changes):
    values=dict(domain_id='synthetic-control',gm_host=1.0,gm_parent=1000.0,a_host_km=100.0,
        reference_radius_km=0.5,submoon_radius_km=0.1,eccentricity_max=0.3,contact_buffer=0.02)
    return DomainSpec(**(values|changes))


@pytest.mark.parametrize('measure',MEASURES)
def test_conditional_density_normalization_and_orbital_invariants(measure):
    d=domain()
    normal,emax,_=domain_normalization(d,measure)
    integral=quad(lambda e:quad(lambda a:1 if measure==MEASURES[0] else 1/a if measure==MEASURES[1] else math.sqrt(a)*e,
        d.inner_km/(1-e),d.hill_km)[0],0,emax)[0]
    assert abs(integral/normal-1)<1e-10
    sample=generate(d,measure,power=6,seed=42,randomization=0,basis=np.eye(3))
    assert sample['denominator']==64
    assert sample==generate(d,measure,power=6,seed=42,randomization=0,basis=np.eye(3))
    for row in sample['records']:
        a,e=row['elements']['a_km'],row['elements']['e']
        r,v=np.asarray(row['position']),np.asarray(row['velocity'])
        assert a*(1-e)>=d.inner_km*(1-1e-12) and a<=d.hill_km
        assert abs((np.dot(v,v)/2-d.gm_host/np.linalg.norm(r))/(-d.gm_host/(2*a))-1)<1e-11
        assert abs(np.linalg.norm(np.cross(r,v))**2/(d.gm_host*a*(1-e*e))-1)<1e-11


def test_isotropic_axes_degeneracies_and_empty_domain():
    sample=generate(domain(),MEASURES[0],power=8,seed=7,randomization=0,basis=np.eye(3))
    cosines=np.array([r['elements']['cos_i'] for r in sample['records']])
    assert abs(cosines.mean())<0.01
    assert abs((cosines**2).mean()-1/3)<0.01
    a=elements_to_cartesian(1,2,0,1,0.7,0.3,0,np.eye(3))
    b=elements_to_cartesian(1,2,0,1,1.0,0,0,np.eye(3))
    assert np.allclose(a,b,atol=1e-13)
    with pytest.raises(ValueError,match='Пустая'):
        generate(domain(reference_radius_km=100.0),MEASURES[0],power=2,seed=1,randomization=0,basis=np.eye(3))
    with pytest.raises(ValueError):
        generate(domain(),MEASURES[0],power=2,seed=1,randomization=0,basis=np.zeros((3,3)))
    assert reproduction_grid(1,1,2,np.eye(3),lower_coefficient=1.26,lower_formula_id='rounded_rigid_roche')['denominator']==130


def test_paired_error_and_unresolved_denominator():
    left=np.array([0.1,0.2,0.3,0.4])
    result=paired_difference(left,left+0.03)
    assert abs(result['mean']+0.03)<1e-14 and result['se']<1e-14
    assert randomized_interval(left)['randomizations']==4
    bounds=survival_bounds(['survived','physical_loss','unresolved'],[1,2,1])
    assert bounds['lower']==0.25 and bounds['upper']==0.5 and bounds['denominator']==4
