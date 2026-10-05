"""Малый синтетический продукт W1: нормировки, точные старты и seeds."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from submoon_research.execution import RunContext
from submoon_research.workflows.smoke import write_json


def main():
    root=Path(__file__).resolve().parents[1]
    config=dict(experiment_id='W1-synthetic-sampling-v1',data_kind='synthetic_analytic_control',
        production_allowed=False,power=6,randomizations=8,base_seed=202610050,
        domain=dict(domain_id='synthetic-conditional-v1',gm_host=1.0,gm_parent=1000.0,
            a_host_km=100.0,reference_radius_km=0.5,submoon_radius_km=0.1,
            eccentricity_max=0.3,contact_buffer=0.02))
    with RunContext(root,'W1-sampling',config,wall_seconds=120,output_mib=16) as run:
        run.save_code(Path(__file__).resolve())
        import numpy as np
        from submoon_research.sampling.design import DomainSpec,MEASURES,generate
        d=DomainSpec(**config['domain'])
        checks={}
        seeds=[]
        for mi,measure in enumerate(MEASURES):
            for k in range(config['randomizations']):
                run.check_budget()
                seed=config['base_seed']+mi*100+k
                seeds.append(seed)
                product=generate(d,measure,power=config['power'],seed=seed,randomization=k,basis=np.eye(3))
                product.update(data_kind='synthetic_analytic_control',synthetic=True,production_allowed=False)
                write_json(run.folder/'initial_conditions'/f'{measure}-{k}.json',product)
                checks[f'{measure}-{k}']=bool(product['denominator']==64 and all(
                    row['elements']['a_km']*(1-row['elements']['e'])>=d.inner_km*(1-1e-12)
                    for row in product['records']))
        checks['independent_seed_ids']=len(seeds)==len(set(seeds))
        validation=run.validate(checks,scope='W1_synthetic_generator_only',
            limitations=['Не реальные хозяева; не пилотная стоимость/дисперсия выживаемости.',
                'V06 покрытия интервалов, H1–H3 и freeze ещё не закрыты.'])
    print(json.dumps(dict(run_id=run.run_id,validation=validation['status'])))
    return int(validation['status']!='passed')


if __name__=='__main__':
    raise SystemExit(main())
