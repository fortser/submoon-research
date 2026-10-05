"""Ошибка по независимым рандомизациям и парные разности; аварии не исключаются."""
import numpy as np
from scipy.stats import t


def randomized_interval(estimates,confidence=0.95):
    values=np.asarray(estimates,dtype=float)
    if values.ndim!=1 or len(values)<2 or not np.isfinite(values).all() or not 0<confidence<1:
        raise ValueError('Нужны ≥2 конечные независимые оценки и допустимый confidence')
    mean=float(values.mean())
    se=float(values.std(ddof=1)/np.sqrt(len(values)))
    half=float(t.ppf((1+confidence)/2,len(values)-1)*se)
    return dict(mean=mean,se=se,interval=[mean-half,mean+half],randomizations=len(values),
        method='approximate_student_t_across_independent_randomizations',coverage_verified=False)


def paired_difference(left,right,confidence=0.95):
    a,b=np.asarray(left,dtype=float),np.asarray(right,dtype=float)
    if a.shape!=b.shape:
        raise ValueError('Несовместимые парные рандомизации')
    result=randomized_interval(a-b,confidence)
    result['paired_covariance']=float(np.cov(a,b,ddof=1)[0,1])
    return result


def survival_bounds(outcomes,weights):
    """Нерешённые исходы дают границы; они остаются в исходном знаменателе."""
    weights=np.asarray(weights,dtype=float)
    if len(outcomes)!=len(weights) or not np.isfinite(weights).all() or (weights<0).any() or weights.sum()<=0:
        raise ValueError('Неверные веса/знаменатель')
    if any(x not in ('survived','physical_loss','unresolved') for x in outcomes):
        raise ValueError('Неподдерживаемый физический исход')
    kept=sum(w for x,w in zip(outcomes,weights) if x=='survived')
    unknown=sum(w for x,w in zip(outcomes,weights) if x=='unresolved')
    denominator=float(weights.sum())
    return dict(lower=float(kept/denominator),upper=float((kept+unknown)/denominator),
        denominator=denominator,unresolved_weight=float(unknown),self_normalized_weights=True)
