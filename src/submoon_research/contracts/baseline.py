"""Закрытая постановка малого W0; произвольный production не принимается."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class BaselineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    schema_version: Literal["0.4"]
    experiment_id: str
    decision_id: Literal["W0-D011"]
    data_kind: Literal["real_nominal_development_baseline"]
    production_allowed: Literal[False]
    population_interpretation: Literal[False]
    model_path: str
    model_manifest: str
    states_path: str
    states_manifest: str
    original_states_path: str
    inputs_sha256: dict[str, str]
    prerequisites: dict[str, str]
    hosts: list[Literal["iapetus", "ganymede", "himalia"]]
    a_count: int = Field(ge=2, le=10)
    inclination_degrees: list[float]
    outer_hill_fraction: float = Field(gt=0, le=1)
    contact_buffer: float = Field(ge=0, le=0.05)
    eccentricity: Literal[0.0]
    periods_per_orbit: float = Field(gt=0, le=10)
    horizon_kind: Literal["per_orbit_initial_periods_not_host_comparison"]
    rtol: float = Field(gt=0, le=1e-11)
    tighter_rtol: float = Field(gt=0, le=1e-13)
    atol_position_km: float = Field(gt=0, le=1e-7)
    atol_velocity_km_s: float = Field(gt=0, le=1e-10)
    max_step_period_fraction: float = Field(gt=0, le=0.05)
    trajectory_position_tolerance_a: float = Field(gt=0, le=1e-6)
    trajectory_velocity_tolerance_na: float = Field(gt=0, le=1e-6)
    energy_relative_tolerance: float = Field(gt=0, le=1e-8)
    event_time_tolerance_period: float = Field(gt=0, le=1e-6)
    checkpoint_fraction: float = Field(gt=0, lt=1)
    ias15_epsilon: float = Field(gt=0, le=1e-12)
    reference_every: int = Field(ge=1, le=12)
    orbit_indices: list[int] | None
    wall_seconds: int = Field(ge=1, le=600)
    output_mib: int = Field(ge=1, le=256)

    @model_validator(mode="after")
    def scientific_contract(self):
        if set(self.prerequisites) != {"V02", "V03_V04"}:
            raise ValueError("Требуются оба численных входных контроля")
        bound_paths = {
            self.model_path,
            self.model_manifest,
            self.states_path,
            self.states_manifest,
            self.original_states_path,
            *self.prerequisites.values(),
        }
        if not bound_paths.issubset(self.inputs_sha256):
            raise ValueError("Все фактически читаемые продукты должны иметь ожидаемый SHA")
        if len(self.hosts) != len(set(self.hosts)) or set(self.hosts) != {
            "iapetus",
            "ganymede",
            "himalia",
        }:
            raise ValueError("Требуются все три пилотных хозяина без дубликатов")
        if (
            self.inclination_degrees != [0.0, 90.0, 180.0]
            or self.tighter_rtol >= self.rtol
            or not self.inputs_sha256
        ):
            raise ValueError("Не определена неизменная сетка/приёмка")
        if self.orbit_indices is not None and (
            len(set(self.orbit_indices)) != len(self.orbit_indices)
            or any(i < 0 for i in self.orbit_indices)
        ):
            raise ValueError("Недопустимый выбор benchmark")
        return self


def validate_prerequisite(name, result):
    """Один passed-маркер или чужой smoke не заменяют входной контроль."""
    scopes = dict(V02="V02_synthetic_two_body_only", V03_V04="V03_CR3BP_and_V04_J2_precession_only")
    required = (
        {
            f"e{e}_{method}_{observable}"
            for e, methods in [
                ("0.0", ["DOP853", "IAS15"]),
                ("0.5", ["DOP853", "IAS15"]),
                ("0.9", ["IAS15"]),
                ("0.99", ["IAS15"]),
            ]
            for method in methods
            for observable in ["position", "velocity", "energy", "angular_momentum"]
        }
        | {f"e{e}_reference_precision" for e in ["0.0", "0.5", "0.9", "0.99"]}
        if name == "V02"
        else {"CR3BP_L4_equilibrium", "CR3BP_L5_equilibrium", "CR3BP_Jacobi"}
        | {
            f"J2_{j2}_{check}"
            for j2 in ["0.0001", "5e-05"]
            for check in ["node_precession", "peri_precession", "energy"]
        }
    )
    checks = result.get("checks", {})
    if (
        name not in scopes
        or result.get("scope") != scopes[name]
        or result.get("status") != "passed"
        or not required.issubset(checks)
        or any(checks[k] is not True for k in required)
    ):
        raise ValueError("Не принят численный вход " + name)
    return result
