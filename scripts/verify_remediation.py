"""Сквозная приёмка исправлений W0; физические ворота остаются отдельными."""
import json
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from submoon_research.execution import RunContext
from submoon_research.workflows.smoke import write_json


def main():
    root=Path(__file__).resolve().parents[1]
    config=dict(experiment_id='W0-remediation-verification-v1',data_kind='implementation_acceptance')
    with RunContext(root,'W0-remediation',config,wall_seconds=600,output_mib=64) as run:
        run.save_code(Path(__file__).resolve())
        env=os.environ.copy()
        env.update(PYTHONIOENCODING='utf-8',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
        commands=[('pytest',['-m','pytest','-q'],0),('ruff',['-m','ruff','check','src','scripts','tests'],0),
            ('validate',['scripts/project.py','validate'],0),('briefs',['scripts/build_stage_briefs.py','--check'],0),
            ('iapetus',['scripts/verify_iapetus_passport.py'],0),('himalia',['scripts/verify_himalia_passport.py'],0),
            ('ganymede',['scripts/verify_ganymede_passport.py'],0),
            ('l1_v02',['scripts/verify_l1_specification.py','--config','configs/experiments/L1_baseline_spec_v0_2.yaml'],0),
            ('l1_v03',['scripts/verify_l1_specification.py'],0),
            ('geometry',['scripts/acquire_geometric_states.py','--replay','runs/W0-states-20261004T104417Z-eacbd97e'],0),
            ('baseline',['scripts/audit_baseline.py','--config','configs/audits/W0_baseline_v3.yaml'],1),
            ('smoke',['scripts/project.py','smoke'],0)]
        results=[]
        checks={}
        for name,args,expected in commands:
            result=run.subprocess([sys.executable,'-X','utf8',*args],env=env,capture_output=True,text=True,encoding='utf-8',timeout=180)
            (run.folder/'logs'/f'{name}.txt').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
            outcome=dict(name=name,exit_code=result.returncode,expected_exit_code=expected)
            if name not in ('pytest','ruff','briefs'):
                try:
                    outcome['result']=json.loads(result.stdout.strip())
                except json.JSONDecodeError:
                    outcome['result']=None
            results.append(outcome)
            checks[name]=result.returncode==expected
            write_json(run.folder/'results/executions.json',results)
            print(name,'passed' if checks[name] else 'failed',flush=True)
        validation=run.validate(checks,scope='W0_architecture_remediation_only',
            limitations=['V02/физические входы/реальный пилот/freeze W1 не закрыты.',
                'baseline exit=1 ожидаем из-за сохранённой неоднородности источника.'])
    print(json.dumps(dict(run_id=run.run_id,validation=validation['status'])))
    return int(validation['status']!='passed')


if __name__=='__main__':
    raise SystemExit(main())
