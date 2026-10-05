"""Идентификаторы формул; произвольные строки не исполняются."""
RELATIVE_FORCE='relative_point_mass_indirect_v1'
EXTERNAL_FIGURE='external_field_difference_v1'
ENERGY_EVENT='specific_kepler_energy_diagnostic_v1'
CONTACT_EVENT='reference_sphere_contact_v1'

REGISTRY={RELATIVE_FORCE:'GM*((R-r)/|R-r|^3-R/|R|^3)',
    EXTERNAL_FIGURE:'a_fig(submoon)-a_fig(host)',
    ENERGY_EVENT:'v_rel^2/2-GM_host/r',CONTACT_EVENT:'r<=R_reference+R_submoon'}
