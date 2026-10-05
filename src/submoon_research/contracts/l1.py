"""Строгая схема сохранённой L1; новые варианты требуют новой версии контракта."""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from pydantic import ConfigDict, create_model


@lru_cache
def reference(version='0.2'):
    if version not in ('0.2','0.3'):
        raise ValueError('Неподдерживаемая версия L1')
    return json.loads(Path(__file__).with_name('l1_v'+version.replace('.','_')+'.json').read_text(encoding='utf-8'))


def _annotation(value, path):
    if isinstance(value, dict):
        if path.endswith('inputs_sha256'):
            return dict[str, str]
        return create_model(path.replace('.', '_'), __config__=ConfigDict(extra='forbid', strict=True, allow_inf_nan=False),
            **{key: (_annotation(item, path+'.'+key), ...) for key, item in value.items()})
    if isinstance(value, list):
        return list[_annotation(value[0], path+'_item')] if value else list[str]
    return type(value)


@lru_cache
def schema(version='0.2'):
    return _annotation(reference(version), 'L1')


def validate_spec(spec):
    version=spec.get('schema_version')
    schema(version).model_validate(spec)
    editable = {'inputs_sha256', 'routes.independent_reproduction.states.path',
        'routes.independent_reproduction.states.source_run_id',
        'routes.independent_reproduction.states.replay_run_id'}

    def walk(actual, expected, path=''):
        if path in editable:
            if not actual:
                raise ValueError(f'Обязательный непустой набор: {path}')
            return
        if isinstance(expected, dict):
            for key, value in expected.items():
                walk(actual[key], value, path+'.'+key if path else key)
        elif actual != expected:
            raise ValueError(f'Поле противоречит утверждённой L1 v{version}: {path}')
    walk(spec, reference(version))
    return spec


def validate_states(states, spec):
    required = set(reference_states_fields())
    if set(states) != required:
        raise ValueError('Отсутствующие/лишние поля продукта состояний')
    fixed = dict(schema_version='0.1', experiment_id='W0-geometric-states-v1',
        data_kind='real_geometric_ephemeris_snapshot', synthetic=False, production_allowed=False,
        time_scale='TDB', frame='ICRF', axes_motion='fixed', position_unit='km', velocity_unit='km/s',
        geometric_corrections='NONE', barycentric_origin='solar_system_barycenter', validation_status='passed',
        uncertainty=None,covariance_ref=None,uncertainty_status='ephemeris_covariance_not_supplied')
    if any(type(states[k]) is not type(v) or states[k] != v for k,v in fixed.items()):
        raise ValueError('Не принят тип/версия/статус/координатный контракт состояний')
    route = spec['routes']['independent_reproduction']
    times = states['epoch_jd_tdb']
    if not isinstance(times, list) or len(times) != 2 or any(type(t) not in (int,float) or not math.isfinite(t) for t in times) or not times[0] < times[1]:
        raise ValueError('Неверные эпохи')
    index = route['epoch']['initial_epoch_index']
    if type(states['initial_epoch_index']) is not int or index != states['initial_epoch_index'] or times[index] != route['epoch']['jd_tdb']:
        raise ValueError('Несогласованные индекс и эпоха старта')
    expected_bodies = set().union(*map(set, route['massive_body_sets'].values()))
    if set(states['barycentric']) != expected_bodies or set(states['host_relative']) != set(route['pilot_hosts']):
        raise ValueError('Неверный состав состояний')

    def array(value):
        if not isinstance(value,list) or len(value)!=2 or any(not isinstance(row,list) or len(row)!=6 or any(type(x) not in (int,float) or not math.isfinite(x) for x in row) for row in value):
            raise ValueError('Векторы должны быть конечными массивами 2×6')
        return np.asarray(value, dtype=float)
    ssb = {body:array(value) for body,value in states['barycentric'].items()}
    for host, bodies in route['massive_body_sets'].items():
        if set(states['host_relative'][host]) != set(bodies):
            raise ValueError(f'Неверный относительный состав: {host}')
        for body in bodies:
            delta = array(states['host_relative'][host][body]) - (ssb[body]-ssb[host])
            if np.max(np.linalg.norm(delta[:,:3],axis=1)) > 1e-3 or np.max(np.linalg.norm(delta[:,3:],axis=1)) > 1e-8:
                raise ValueError(f'Относительные состояния не равны разности SSB: {host}/{body}')
    if not isinstance(states['sources'],dict) or not states['sources'] or not expected_bodies <= states['sources'].keys():
        raise ValueError('Отсутствуют источники состояний')
    for body in expected_bodies:
        source=states['sources'][body]
        if not source.get('target',{}).get('ephemeris_source') or source.get('center',{}).get('id') != '0':
            raise ValueError(f'Неверный источник/центр: {body}')
    if states['source_run_id'] != route['states']['source_run_id']:
        raise ValueError('Несогласованный source_run_id')
    return states


def reference_states_fields():
    return ('schema_version','experiment_id','data_kind','synthetic','production_allowed',
        'epoch_jd_tdb','initial_epoch_index','time_scale','frame','axes_motion','position_unit',
        'velocity_unit','geometric_corrections','uncertainty','uncertainty_status','covariance_ref',
        'barycentric_origin','barycentric','host_relative','sources','run_id','source_run_id','validation_status')
