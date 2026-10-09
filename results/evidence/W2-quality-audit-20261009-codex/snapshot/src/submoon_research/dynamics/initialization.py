"""Общие номинальные входы из проверенной реализации W0."""
import numpy as np

SETS = dict(
    iapetus=["iapetus", "sun", "jupiter", "saturn", "titan"],
    ganymede=["ganymede", "sun", "jupiter", "saturn", "io", "europa", "callisto"],
    himalia=["himalia", "sun", "jupiter", "saturn"],
)
PARENTS = dict(iapetus="saturn", ganymede="jupiter", himalia="jupiter")


def physical_inputs(host, model, states):
    names = SETS[host]
    selected = np.array([states["barycentric"][name][0] for name in names])
    relative = selected - selected[0]
    gms = np.array([model["bodies"][name]["gm"]["value"] for name in names])
    figures = []
    for name, figure in model["figures"].items():
        if name in names:
            figures.append(
                dict(
                    index=names.index(name),
                    j2=figure["j2"]["value"],
                    radius=figure["reference_radius"]["value"],
                    pole=figure["pole_icrf"]["value"],
                )
            )
    radii = [
        model["bodies"][name]["contact_radius"]["value"] + model["submoon_radius_km"]
        for name in names
    ]
    parent = relative[names.index(PARENTS[host])]
    central = model["bodies"][PARENTS[host]]["gm"]["value"] + gms[0]
    a_host = 1 / (2 / np.linalg.norm(parent[:3]) - np.dot(parent[3:], parent[3:]) / central)
    if not a_host > 0:
        raise ValueError("Неэллиптическая осцулирующая орбита хозяина")
    first = -parent[:3] / np.linalg.norm(parent[:3])
    normal = np.cross(parent[:3], parent[3:])
    normal /= np.linalg.norm(normal)
    basis = np.column_stack([first, np.cross(normal, first), normal])
    return names, relative, gms, figures, radii, float(a_host), basis

