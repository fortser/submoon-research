"""Физический контракт геометрических стартов номинального W0."""

import numpy as np
from submoon_research.catalog.nominal_model import BODY_IDS


HOST_SETS = dict(
    iapetus={"iapetus", "sun", "jupiter", "saturn", "titan"},
    ganymede={"ganymede", "sun", "jupiter", "saturn", "io", "europa", "callisto"},
    himalia={"himalia", "sun", "jupiter", "saturn"},
)


def validate_nominal_states(states):
    required = dict(
        schema_version="0.1",
        data_kind="real_geometric_ephemeris_snapshot",
        synthetic=False,
        production_allowed=False,
        initial_epoch_index=0,
        time_scale="TDB",
        frame="ICRF",
        axes_motion="fixed",
        position_unit="km",
        velocity_unit="km/s",
        geometric_corrections="NONE",
        barycentric_origin="solar_system_barycenter",
        uncertainty=None,
        covariance_ref=None,
    )
    if any(states.get(k) != v or type(states.get(k)) is not type(v) for k, v in required.items()):
        raise ValueError("Не приняты единицы, эпоха или геометрический смысл состояний")
    epochs = np.asarray(states["epoch_jd_tdb"], dtype=float)
    if (
        epochs.shape != (2,)
        or epochs[0] != 2451545.0
        or abs((epochs[1] - epochs[0]) * 86400 - 60) > 1e-4
    ):
        raise ValueError("Ожидаются две объявленные эпохи с календарным шагом TDB 60 s")
    if set(states["barycentric"]) != set(BODY_IDS) or set(states["host_relative"]) != set(
        HOST_SETS
    ):
        raise ValueError("Неполный набор тел или хозяев")
    barycentric = {}
    for name, value in states["barycentric"].items():
        array = np.asarray(value, dtype=float)
        if array.shape != (2, 6) or not np.isfinite(array).all():
            raise ValueError("Неверные векторы " + name)
        if str(states["sources"][name]["target"]["id"]) != str(BODY_IDS[name]):
            raise ValueError("Неверный физический центр " + name)
        barycentric[name] = array
    for host, names in HOST_SETS.items():
        if set(states["host_relative"][host]) != names:
            raise ValueError("Неполный набор относительных стартов")
        for name in names:
            relative = np.asarray(states["host_relative"][host][name], dtype=float)
            if (
                relative.shape != (2, 6)
                or not np.isfinite(relative).all()
                or not np.allclose(
                    relative, barycentric[name] - barycentric[host], rtol=0, atol=1e-12
                )
            ):
                raise ValueError("Несогласованная относительная геометрия " + host + ":" + name)
    if "alignment" in states and (
        states["alignment"]["changed_body"] != "himalia"
        or states["alignment"]["not_joint_ephemeris_fit"] is not True
    ):
        raise ValueError("Не определён статус выравнивания эфемерид")
    return states
