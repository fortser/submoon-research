"""Версионированная постановка W1; научные ворота не следуют из наличия конфига."""
from typing import Literal

from pydantic import Field, model_validator

from submoon_research.contracts.orbit import StrictRecord
from submoon_research.sampling.design import MEASURES

YEAR_SECONDS = 365.25 * 86400
PRIMARY_SECONDS = 10000 * YEAR_SECONDS
MAIN_HOSTS = ['iapetus', 'titan', 'callisto', 'rhea', 'ganymede', 'phoebe']
CONTROL_HOSTS = ['europa', 'dione', 'hyperion', 'himalia']


class W1Design(StrictRecord):
    schema_version: Literal['1.0'] = '1.0'
    design_id: str = Field(min_length=1)
    status: Literal['development', 'ready_for_freeze', 'frozen'] = 'development'
    horizon_seconds: float = PRIMARY_SECONDS
    year_seconds: float = YEAR_SECONDS
    measures: list[str] = list(MEASURES)
    main_hosts: list[str] = MAIN_HOSTS
    control_hosts: list[str] = CONTROL_HOSTS
    development: list[str] = ['iapetus', 'ganymede', 'himalia']
    holdout: list[str] = ['europa', 'dione', 'hyperion']
    scenario_id: Literal['nominal_J2000_TDB_fixed_J2'] = 'nominal_J2000_TDB_fixed_J2'
    eccentricity_max: float = 0.3
    contact_buffer: float = 0.02
    submoon_radius_km: float = 0.1
    randomizations: int = Field(default=8, ge=2)
    power: int = Field(default=8, ge=1, le=16)
    seed_base: int = Field(default=202610051, ge=0)
    effect: float = Field(default=0.03, gt=0, lt=1)
    target_half_width: float = Field(default=0.01, gt=0, lt=1)
    numeric_sensitivity: float = Field(default=0.005, gt=0, lt=1)
    alpha: float = Field(default=0.05, gt=0, lt=1)
    interval_method: Literal['hoeffding'] = 'hoeffding'
    production_allowed: Literal[False] = False

    @model_validator(mode='after')
    def consistent(self):
        if self.horizon_seconds != PRIMARY_SECONDS or self.year_seconds != YEAR_SECONDS:
            raise ValueError('W1 v1 требует общего горизонта 10000 юлианских лет')
        if self.main_hosts != MAIN_HOSTS or self.control_hosts != CONTROL_HOSTS:
            raise ValueError('Изменение групп требует новой версии протокола')
        if self.measures != list(MEASURES):
            raise ValueError('Требуются три объявленные меры')
        if self.development != ['iapetus', 'ganymede', 'himalia'] or self.holdout != ['europa', 'dione', 'hyperion']:
            raise ValueError('Split v1 изменён')
        if (self.eccentricity_max, self.contact_buffer, self.submoon_radius_km) != (0.3, 0.02, 0.1):
            raise ValueError('Изменение области требует новой версии')
        return self

    def seed(self, randomization, *, phase='pilot'):
        if phase not in ('pilot', 'main', 'coverage') or not 0 <= randomization < self.randomizations:
            raise ValueError('Неизвестная фаза или рандомизация')
        # Один seed внутри пары хозяев/мер, независимые потоки между фазами и repeats.
        return self.seed_base + {'pilot': 0, 'main': 1000000, 'coverage': 2000000}[phase] + randomization


class EnsembleRow(StrictRecord):
    host: str
    scenario_id: str
    measure: Literal['uniform_a_e_cos_i_phases', 'uniform_log_a', 'canonical_volume']
    domain_id: str = Field(min_length=1)
    randomization: int = Field(ge=0)
    seed: int = Field(ge=0)
    index: int = Field(ge=0)
    weight: float = Field(gt=0)
    horizon_seconds: float = Field(gt=0)
    last_valid_time: float = Field(ge=0)
    run_status: Literal['completed', 'partial', 'failed', 'cancelled']
    outcome: Literal['survived', 'host_contact', 'other_contact', 'operational_escape', 'unresolved']
    event_time: float | None = Field(default=None, ge=0)

    @model_validator(mode='after')
    def outcome_consistency(self):
        if self.last_valid_time > self.horizon_seconds:
            raise ValueError('Состояние позже горизонта')
        if self.run_status != 'completed' and self.outcome != 'unresolved':
            raise ValueError('Авария не является физической потерей')
        if self.outcome == 'survived' and self.last_valid_time != self.horizon_seconds:
            raise ValueError('Выживание требует полного горизонта')
        if self.outcome in ('host_contact', 'other_contact', 'operational_escape'):
            if self.event_time is None or self.event_time > self.last_valid_time:
                raise ValueError('Потеря требует подтверждённого времени события')
        return self
