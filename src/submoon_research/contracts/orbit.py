"""Минимальная исполняемая запись орбиты; вычислительная авария не физическая потеря."""
from typing import Literal
import re

from pydantic import BaseModel,ConfigDict,Field,model_validator


class StrictRecord(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,allow_inf_nan=False)


class InitialState(StrictRecord):
    epoch: float
    time_scale: Literal['TDB','dimensionless']
    origin: str=Field(min_length=1)
    frame: str=Field(min_length=1)
    position_unit: Literal['km','dimensionless']
    velocity_unit: Literal['km/s','dimensionless']
    position: list[float]=Field(min_length=3,max_length=3)
    velocity: list[float]=Field(min_length=3,max_length=3)


class SamplingRecord(StrictRecord):
    domain_id: str=Field(min_length=1)
    measure_id: str=Field(min_length=1)
    proposal_density: float=Field(gt=0)
    target_density: float=Field(ge=0)
    weight: float=Field(ge=0)
    inclusion_probability: float=Field(gt=0,le=1)
    stratum: str=Field(min_length=1)
    seed: int=Field(ge=0)
    randomization: int=Field(ge=0)


class Outcome(StrictRecord):
    run_status: Literal['planned','running','completed','failed','cancelled','partial']
    physical_outcome: Literal['survived','host_contact','other_contact','disrupted','permanent_escape','unresolved']|None
    terminal_event: str|None
    event_time: float|None=Field(ge=0)
    last_valid_time: float|None=Field(ge=0)
    uncertainty_status: str=Field(min_length=1)

    @model_validator(mode='after')
    def separate_failure(self):
        if self.run_status in ('failed','cancelled','partial') and self.physical_outcome not in (None,'unresolved'):
            raise ValueError('Вычислительный сбой не определяет физическую потерю')
        if self.run_status=='completed' and self.physical_outcome is None:
            raise ValueError('Завершённый расчёт требует явного исхода')
        if self.event_time is not None and (self.last_valid_time is None or self.event_time>self.last_valid_time):
            raise ValueError('Событие позже последнего валидного состояния')
        if self.physical_outcome in ('host_contact','other_contact','disrupted','permanent_escape') and (self.event_time is None or not self.terminal_event):
            raise ValueError('Физическая потеря требует зарегистрированного события')
        return self


class OrbitRecord(StrictRecord):
    schema_version: Literal['0.2']='0.2'
    run_id: str=Field(min_length=1)
    orbit_id: str=Field(min_length=1)
    host_id: str=Field(min_length=1)
    model_version: str=Field(min_length=1)
    inputs_sha256: dict[str,str]
    initial_state: InitialState
    sampling: SamplingRecord
    physics_config_sha256: str=Field(pattern=r'^[0-9a-f]{64}$')
    scenario_id: str=Field(min_length=1)
    integrator: str=Field(min_length=1)
    numeric_protocol_id: str=Field(min_length=1)
    event_protocol_id: str=Field(min_length=1)
    outcome: Outcome

    @model_validator(mode='after')
    def input_bindings(self):
        if not self.inputs_sha256 or any(not path or not re.fullmatch(r'[0-9a-f]{64}',digest) for path,digest in self.inputs_sha256.items()):
            raise ValueError('Орбита требует SHA входов')
        return self
