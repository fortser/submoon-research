"""Независимые эталоны обнаруживают отсутствие косвенного члена и ошибки знака."""
import numpy as np

from submoon_research.forces.gravity import relative_monopoles,j2_acceleration,external_j2_difference
from submoon_research.events.reference import locate_reference_contact,specific_energy


def test_independent_barycentric_force_and_missing_indirect_negative_control():
    host=np.array([2.0,-3.0,0.7])
    r=np.array([0.2,0.1,-0.05])
    external=np.array([[7.,3.,2.],[-6.,-2.,5.]])
    gms=np.array([4.,2.])
    def force(position,bodies,masses):
        out=np.zeros(3)
        for body,mass in zip(bodies,masses):
            delta=body-position
            out+=mass*delta/(np.dot(delta,delta)**1.5)
        return out
    reference=force(host+r,np.vstack([host,external]),np.r_[1.,gms])-force(host,external,gms)
    actual=relative_monopoles(r,1.,external-host,gms)
    assert np.linalg.norm(reference-actual)<1e-12*np.linalg.norm(reference)
    missing=force(host+r,np.vstack([host,external]),np.r_[1.,gms])
    assert np.linalg.norm(missing-reference)>1e-4


def test_j2_independent_potential_gradient_and_external_difference():
    r=np.array([2.,1.,0.5])
    pole=np.array([0.,0.,1.])
    gm,j2,rad=1.,1e-3,0.2
    def potential(x):
        norm=np.sqrt(np.dot(x,x))
        return gm*j2*rad**2/(2*norm**3)*(3*x[2]**2/norm**2-1)
    step=1e-5
    gradient=np.array([(potential(r+np.eye(3)[k]*step)-potential(r-np.eye(3)[k]*step))/(2*step) for k in range(3)])
    assert np.allclose(j2_acceleration(r,gm,j2,rad,pole),-gradient,rtol=1e-8,atol=1e-15)
    assert np.array_equal(external_j2_difference(r,r,gm,j2,rad,pole),np.zeros(3))
    assert np.array_equal(j2_acceleration(r,gm,0.,rad,pole),np.zeros(3))


def test_contact_between_steps_tangency_miss_and_energy_sign():
    def line(y):
        return lambda t:np.array([t-2,y,0,1,0,0],dtype=float)
    hit=locate_reference_contact(line(0),0,4,0.5)
    assert abs(hit['time']-1.5)<1e-9
    touch=locate_reference_contact(line(0.5),0,4,0.5)
    assert touch['event']=='reference_tangency_within_tolerance'
    assert locate_reference_contact(line(0.6),0,4,0.5) is None
    assert specific_energy([1.,0,0,0,1.,0],1.)==-0.5
