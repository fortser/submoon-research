"""Монополи и ненормированное J2: единицы определяются входным контрактом."""
import numpy as np

from submoon_research.contracts.identifiers import RELATIVE_FORCE,EXTERNAL_FIGURE


def relative_monopoles(r,gm_host,external_positions,external_gm):
    r=np.asarray(r,dtype=float)
    positions=np.asarray(external_positions,dtype=float)
    gm=np.asarray(external_gm,dtype=float)
    if r.shape!=(3,) or positions.shape!=(len(gm),3) or not np.isfinite(np.r_[r,positions.ravel(),gm,gm_host]).all() or gm_host<=0 or (gm<=0).any():
        raise ValueError('Неверный контракт монопольной силы')
    d=positions-r
    norms=np.linalg.norm(d,axis=1)
    origins=np.linalg.norm(positions,axis=1)
    radius=np.linalg.norm(r)
    if radius==0 or (norms==0).any() or (origins==0).any():
        raise ValueError('Сингулярное состояние; требуется событие контакта')
    return -gm_host*r/radius**3 + np.sum(gm[:,None]*(d/norms[:,None]**3-positions/origins[:,None]**3),axis=0)


def j2_acceleration(r,gm,j2,reference_radius,pole):
    r,pole=np.asarray(r,dtype=float),np.asarray(pole,dtype=float)
    if r.shape!=(3,) or pole.shape!=(3,) or not np.isfinite(np.r_[r,pole,gm,j2,reference_radius]).all() or gm<=0 or reference_radius<=0 or abs(np.linalg.norm(pole)-1)>1e-12:
        raise ValueError('Неверный контракт поля J2/полюса')
    radius=np.linalg.norm(r)
    if radius==0:
        raise ValueError('Сингулярность J2')
    z=np.dot(r,pole)
    return 1.5*gm*j2*reference_radius**2/radius**5*((5*z*z/radius**2-1)*r-2*z*pole)


def external_j2_difference(submoon,host,gm,j2,reference_radius,pole):
    return j2_acceleration(submoon,gm,j2,reference_radius,pole)-j2_acceleration(host,gm,j2,reference_radius,pole)


IMPLEMENTATIONS={RELATIVE_FORCE:relative_monopoles,EXTERNAL_FIGURE:external_j2_difference}
