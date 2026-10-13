"""Strict evidence scoring for controlled tactical scenarios, never match victories."""
import math

SCENARIOS = ('zerg_three_base_rally', 'terran_high_ground_scan')


def safe_rally(grid, base, target, threats, max_distance=20):
    """Local assembly with a static route outside the specified observed fire zones."""
    if target is None or math.dist(base, target) > max_distance:
        return False
    try:
        route = grid.route(base, target)
    except ValueError:
        return False
    return bool(route) and all(math.dist((x+.5, y+.5), t['pos']) > t['radius']
                               for x, y in route for t in threats)


def evaluate(scenario, prerequisites, evidence):
    """Missing fixture prerequisites are inconclusive; missing behavior is a failure."""
    if scenario not in SCENARIOS:
        raise ValueError('Unknown tactical scenario')
    if not prerequisites or not all(prerequisites.values()):
        return dict(verdict='inconclusive', failed_prerequisites=[
            k for k, v in prerequisites.items() if not v], checks={})
    if evidence.get('model_calls', 0) == 0:
        return dict(verdict='inconclusive', reason='No completed model reply; capability not established', checks={})
    if scenario == 'zerg_three_base_rally':
        checks = {k: bool(evidence.get(k)) for k in (
            'model_changed_third_rally', 'all_three_rallies_safe',
            'rallies_held_for_window', 'third_produced_units',
            'third_units_arrived', 'third_units_survived', 'third_units_avoided_fire_zone')}
    else:
        checks = {k: bool(evidence.get(k)) for k in (
            'model_requested_relevant_scan', 'scan_effect_observed',
            'orbital_energy_spent', 'high_ground_became_visible',
            'own_counterfire_observed', 'enemy_damage_after_scan')}
    checks['no_engine_action_errors'] = evidence.get('engine_action_errors') == 0
    checks['model_reply_observed'] = evidence.get('model_calls', 0) > 0
    return dict(verdict='pass' if all(checks.values()) else 'fail', checks=checks,
                failed_checks=[k for k, v in checks.items() if not v])


def record_scan(evidence, world, opponent, *, high, orbital_tag, energy_before,
                own_tags, enemy_health, previous_cooldowns, cooldowns):
    """Player-local vision/fire plus isolated fixture target health, including instant kills."""
    if not evidence.get('model_requested_relevant_scan'):
        return
    if not any(x[0] == 6 and x[1] == 1 and math.dist((x[2], x[3]), high) < 12
               for x in world.effects):
        return
    evidence['scan_effect_observed'] = True
    caster = world.by_tag(orbital_tag)
    if caster and caster.energy <= energy_before-35:
        evidence['orbital_energy_spent'] = True
    if world.visibility is not None and int(world.visibility[int(high[1]), int(high[0])]) == 2:
        evidence['high_ground_became_visible'] = True
    enemy_tags = set(enemy_health)
    firing = any(u.tag in own_tags and u.distance(high) <= u.attack_range+u.radius+1.5
                 and (u.engaged_target in enemy_tags or cooldowns.get(u.tag, 0) > previous_cooldowns.get(u.tag, 0)+.01)
                 for u in world.my_units)
    if firing:
        evidence['own_counterfire_observed'] = True
    if (any(u.tag in enemy_tags and u.health < enemy_health[u.tag] for u in opponent.my_units)
            or any(tag in world.dead_units for tag in enemy_tags)):
        evidence['enemy_damage_after_scan'] = True
