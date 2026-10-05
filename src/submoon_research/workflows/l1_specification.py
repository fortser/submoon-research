"""Локальная проверка рабочей постановки L1; не разрешение интеграций."""
from decimal import Decimal

from submoon_research.catalog.ephemeris_versions import compare_himalia_versions


def _verify_legacy_contract(spec, states, passports, old_comments, new_comments):
    comparison = compare_himalia_versions(old_comments, new_comments)
    route = spec['routes']['independent_reproduction']
    archive = spec['routes']['archive_literal']
    physics, events, integration = route['physics'], route['events'], route['integration']
    epoch = route['epoch']['jd_tdb']
    new_blocks = comparison['new']['blocks']
    checks = {
        'spec_identity': spec['experiment_id'] == 'L1-baseline-spec-v0.2'
            and spec['supersedes'] == 'L1-baseline-spec-v0.1' and spec['decision_id'] == 'W0-D008',
        'no_runtime_authorized': spec['status'] == 'working_specification_not_executable'
            and not spec['production_allowed'] and not spec['integration_allowed']
            and all(not spec['gates'][key] for key in ('W0', 'W1', 'W2', 'production')),
        'archive_unknowns_preserved': archive['epoch']['time_scale'] is None
            and archive['frame']['global_frame_name'] is None
            and archive['units']['gravitational_constant_au3_solar_day2'] is None
            and archive['status'] == 'blocked_exact_reproduction',
        'archive_anomalies_preserved': archive['ganymede_nonmodal_policy'] == 'preserve_all_130_original_members',
        'independent_units': route['units']['length'] == 'km'
            and route['units']['time'] == 's' and route['units']['velocity'] == 'km/s'
            and route['units']['gm'] == 'km3/s2',
        'epoch_axes_geometry': epoch == states['epoch_jd_tdb'][states['initial_epoch_index']] == 2451545.0
            and route['epoch']['time_scale'] == states['time_scale'] == 'TDB'
            and route['frame']['axes'] == states['frame'] == 'ICRF'
            and route['frame']['geometric_corrections'] == states['geometric_corrections'] == 'NONE',
        'initial_conditions_not_forcing': route['states']['use'] == 'initial_conditions_only'
            and not route['states']['ephemeris_forcing']
            and route['epoch']['second_epoch_role'] == 'coordinate_check_only_not_time_dependent_forcing',
        'mixed_solutions_visible': route['states']['himalia_source'] == 'jup347_merged_DE442'
            and route['states']['planetary_center_source'] == 'DE441'
            and route['states']['compatibility'] == 'explicit_initial_snapshot_not_joint_fitted_solution'
            and 'W0-I007' in spec['unresolved'],
        'himalia_gm_exact_in_both_solutions': comparison['gm_exact_equal']
            and Decimal(str(passports['himalia']['parameters']['gm']['value'])) == Decimal(comparison['new']['gm']),
        'new_solution_covers_epoch': any(b['start_jed'] <= epoch <= b['end_jed'] for b in new_blocks),
        'new_solution_scoped_twice': len(new_blocks) == 2
            and comparison['new']['planetary_ephemeris'] == 'DE-0442/LE-0442',
        'no_dynamics_inferred_from_equal_gm': comparison['dynamic_compatibility'] == 'not_demonstrated'
            and comparison['state_delta'] is None,
        'passport_parameters_not_overridden': all(route['hosts'][host]['gm'] == p['parameters']['gm']
            and route['hosts'][host]['reference_radius'] == p['parameters']['radius']
            and not route['hosts'][host]['joint_parameter_sampling'] for host, p in passports.items()),
        'host_sets_available_in_snapshot': all(set(bodies) <= set(states['barycentric'])
            for bodies in route['massive_body_sets'].values()),
        'massless_limit_labeled': physics['submoon'] == 'one_massless_test_particle_per_orbit'
            and physics['submoon_backreaction'] is False
            and physics['correspondence'] == 'massless_limit_of_article_not_verified_finite_mass_archive',
        'self_consistent_massive_dynamics': physics['massive_bodies'] == 'self_consistent_integration_of_selected_set'
            and physics['relative_external_force'] == 'GM_j*((R_j-r)/abs(R_j-r)^3-R_j/abs(R_j)^3)'
            and physics['figure_external_force'] == 'field_at_submoon_minus_field_at_host',
        'external_effective_constants_not_adopted': all(p['gm'] is None and p['source_id'] is None
            and p['uncertainty'] is None for p in route['external_parameters'].values()),
        'J2_not_fabricated': physics['harmonic_basis'] == 'unnormalized_Legendre_P2'
            and all(p['j2'] is None and p['reference_radius_km'] is None and p['pole_icrf'] is None
                for p in physics['planet_figure_parameters'].values()),
        'reference_contact_role': events['reference_contact']['status']
            == 'operational_reference_surface_not_verified_physical_contact',
        'energy_is_operational_only': events['article_energy_crossing']['physical_permanent_escape'] is False
            and events['hill_or_roche_crossing'] == 'diagnostic_only'
            and events['permanent_escape']['window'] is None,
        'numerical_failure_separate': events['numerical_failure'] == 'incomplete_computation_not_physical_loss',
        'numeric_mode_and_pilot_pending': all(integration[k] is None
            for k in ('integrator', 'tolerances', 'accepted_numeric_mode', 'development_horizon_seconds', 'year_days'))
            and not integration['checkpoint_verified'],
        'measure_not_population': not route['initial_measure']['independent_population_fraction']
            and route['initial_measure']['inclination_measure'] == 'uniform_in_i_not_cos_i'
            and route['initial_measure']['frozen_initial_states'] is None,
    }
    return checks, comparison


def verify_l1_contract(spec, states, passports, old_comments, new_comments):
    from copy import deepcopy
    from submoon_research.contracts.l1 import validate_spec, validate_states
    checks, comparison = {}, None
    try:
        legacy=deepcopy(spec)
        if spec.get('schema_version')=='0.3':
            legacy.update(experiment_id='L1-baseline-spec-v0.2',supersedes='L1-baseline-spec-v0.1',decision_id='W0-D008')
        checks, comparison = _verify_legacy_contract(legacy, states, passports, old_comments, new_comments)
    except (KeyError, TypeError, IndexError, ValueError, AttributeError):
        checks["legacy_contract_structure"] = False
    for name, verify, args in (("strict_spec_schema", validate_spec, (spec,)),
                               ("scientific_states", validate_states, (states, spec))):
        try:
            verify(*args)
            checks[name] = True
        except (ValueError, KeyError, TypeError, IndexError):
            checks[name] = False
    return checks, comparison
