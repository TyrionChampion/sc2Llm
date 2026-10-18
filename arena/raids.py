"""Explicit economic raids and single-transport drops, using only own observation.

The model chooses the route, squad, withdrawal point and defender threshold. These
executors never start another raid, buy replacements or choose a new safe point.
"""
import math
from collections import Counter

from arena.raw_engine import MOVE, ATTACK, command, NAMES
from arena.spatial import (SpatialMission, selectors, validate_target, resolve_target,
                           ground_destination, area_visibility)
from arena.tech_tree import WORKERS, TOWNHALLS, GAS_BUILDINGS

SKILLS = {'raid', 'drop_raid'}
HOLD = 3793
TRANSPORTS = {'medivac': (54, 394, 396), 'warp_prism': (81, 911, 913),
              'overlord_transport': (893, 1406, 1408)}
PROTOCOL = '''
Offensive missions are YOUR explicit orders; no raid is dispatched automatically.
raid args={unit:ground combat selector,count:1..32,targets:[named place/site_N/[x,y],...],
retreat_target:named place/site_N/[x,y],retreat_enemy_count:1..200,
dwell_s:1..120,timeout_s:1..600 optional,threat_radius:4..30 optional(default 15),reason:Chinese intent}.
It moves along YOUR ordered route, attacks currently visible workers/town halls/gas
buildings within 12 tiles of each site, and visits the next site after a fully visible
empty local approach or dwell_s. Fog is unknown, not an empty expansion. It never
diverts to the enemy main or allocates extra troops. Choose remote sites using battlefield.
drop_raid uses the same squad/withdrawal fields but replaces targets with target,
requires transport:"medivac|warp_prism|overlord_transport", and optionally accepts
waypoints:[named place/site_N/[x,y],...] for the outbound AIR route. One free empty
flying transport, one exact ground squad, one trip. Capacity is checked using native
cargo sizes; loading and unloading require observed passengers/ground units, not ACKs.
The transport returns to YOUR retreat_target after unloading. No automatic pickups,
repeat drops, extra transports, paid morphs or phasing changes are implied.
For BOTH missions, retreat_enemy_count authorizes MOVE withdrawal when at least that
many currently visible non-worker combat defenders (including armed structures) are
within threat_radius of the squad/transport. Workers alone do not trigger it. Newly
seen defenders are reported; visibility cannot prove they actually returned from elsewhere.
If detected before unloading, the transport withdraws WITH cargo instead of dropping.
The whole surviving squad withdraws to YOUR retreat_target after the route/dwell ends
as well. At that point survivors remain allocated/holding until cancellation or the
original timeout, so the main army does not immediately take them back. No automatic
attack again. No hidden defender knowledge, kill/victory inference or deadline reset.
Read active_tasks.workflow.phase, defender_contacts, cargo and spatial_reports. Cancel
with cancel_unit_order/cancel_task, then explicitly reassign. Protected units are never
stolen from other missions; temporarily unseen/embarked members are never replaced.
Use small zealot/zergling raids at remote expansions or a prepared drop on enemy_start
when appropriate; keep enough army for defense and choose your own retreat coordinates.
'''


def validate_args(skill, args, size):
    required = {'unit', 'count', 'retreat_target', 'retreat_enemy_count', 'dwell_s', 'reason'}
    required |= {'targets'} if skill == 'raid' else {'target', 'transport'}
    optional = {'timeout_s', 'threat_radius'} | ({'waypoints'} if skill == 'drop_raid' else set())
    if not required <= set(args) or set(args) - required - optional:
        raise ValueError('required fields: '+', '.join(sorted(required))+'; optional: '+', '.join(sorted(optional)))
    if not isinstance(args['unit'], str) or args['unit'] not in selectors():
        raise ValueError('unknown ground combat unit selector')
    for name, low, high in [('count', 1, 32), ('retreat_enemy_count', 1, 200)]:
        if type(args[name]) is not int or not low <= args[name] <= high:
            raise ValueError(name+' requires integer '+str(low)+'..'+str(high))
    if not isinstance(args['reason'], str) or not 1 <= len(args['reason'].strip()) <= 200:
        raise ValueError('reason requires 1..200 chars')
    result = dict(args)
    for name, default, low, high in [('timeout_s', 300, 1, 600), ('threat_radius', 15, 4, 30),
                                     ('dwell_s', None, 1, 120)]:
        value = args.get(name, default)
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(name+' requires finite '+str(low)+'..'+str(high))
        result[name] = value
    result['retreat_target'] = validate_target(args['retreat_target'], size)
    if skill == 'raid':
        if not isinstance(args['targets'], list) or not 1 <= len(args['targets']) <= 16:
            raise ValueError('targets requires an explicit ordered list of 1..16 sites')
        result['targets'] = [validate_target(v, size) for v in args['targets']]
    else:
        if not isinstance(args['transport'], str) or args['transport'] not in TRANSPORTS:
            raise ValueError('transport must be medivac, warp_prism or overlord_transport')
        result['target'] = validate_target(args['target'], size)
        route = args.get('waypoints', [])
        if not isinstance(route, list) or len(route) > 16:
            raise ValueError('waypoints requires 0..16 explicit air waypoints')
        result['waypoints'] = [validate_target(v, size) for v in route]
    return result


def task_schemas(target):
    def obj(props, required):
        return dict(type='object', properties=props, required=required, additionalProperties=False)
    common = dict(unit={'type':'string', 'enum':sorted(selectors())},
        count={'type':'integer', 'minimum':1, 'maximum':32}, retreat_target=target,
        retreat_enemy_count={'type':'integer', 'minimum':1, 'maximum':200},
        dwell_s={'type':'number', 'minimum':1, 'maximum':120},
        threat_radius={'type':'number', 'minimum':4, 'maximum':30},
        timeout_s={'type':'number', 'minimum':1, 'maximum':600},
        reason={'type':'string', 'minLength':1, 'maxLength':200})
    required = ['unit', 'count', 'retreat_target', 'retreat_enemy_count', 'dwell_s', 'reason']
    args = {'raid':obj(dict(common, targets={'type':'array', 'minItems':1, 'maxItems':16, 'items':target}), required+['targets']),
        'drop_raid':obj(dict(common, target=target, transport={'type':'string','enum':sorted(TRANSPORTS)},
            waypoints={'type':'array', 'maxItems':16, 'items':target}), required+['target','transport'])}
    return [obj(dict(skill={'type':'string','enum':[name]}, count={'type':'integer','enum':[1]}, args=spec),
                ['skill','args']) for name, spec in args.items()]


def report(w, q):
    """Small actionable inventory; no enemy information beyond current own sight."""
    protected = q.protected()
    return dict(enabled=True, tasks=sorted(SKILLS),
        transports=[dict(tag=u.tag, unit=NAMES.get(u.type_id,str(u.type_id)), pos=list(u.pos),
            selector=next(name for name, spec in TRANSPORTS.items() if spec[0]==u.type_id),
            flying=u.flying, completed=u.done, allocated=u.tag in protected,
            cargo_taken=u.cargo_taken, cargo_max=u.cargo_max, passengers=list(u.passengers))
            for u in w.my_units if u.type_id in {v[0] for v in TRANSPORTS.values()}],
        free_ground_combat=dict(Counter(
            NAMES.get(u.type_id,str(u.type_id)) for u in w.my_units if u.tag not in protected
            and u.done and not u.flying and not u.is_building and not u.hallucination
            and u.type_id not in WORKERS and u.ground_dps>0)),
        rule='Model chooses squad, ordered targets, defender threshold and retreat point. '
             'An available transport/ground count is not proof of research, loadability or route safety. '
             'Only explicit tasks allocate units. Holding survivors stay allocated until cancellation/deadline.')


class RaidMission(SpatialMission):
    def __init__(self, skill, args):
        super().__init__(skill, args)
        self.phase = 'allocate'
        self.squad_tags = set()
        self.transport_tag = None
        self.retreat_point = None
        self.air_route = []; self.air_step = 0
        self.at_site = None
        self.orders = {}
        self.pending_load = None
        self.unload_sent = None
        self.cargo = {}
        self.defender_contacts = []
        self.previous_defenders = set()
        self.withdraw_reason = None
        self.ability_retry_at = {}
        self.ground_points = {}
        self.resolved_ground_destination = None

    def summary(self):
        return dict(super().summary(), phase=self.phase, squad_tags=sorted(self.squad_tags),
            transport_tag=self.transport_tag, retreat_point=self.retreat_point,
            air_route=self.air_route, air_route_index=self.air_step,
            cargo=self.cargo, defender_contacts=self.defender_contacts,
            resolved_ground_destination=self.resolved_ground_destination,
            withdraw_reason=self.withdraw_reason,
            pending_load=self.pending_load, unload_sent_game_s=self.unload_sent)

    def result(self, t):
        return dict(super().result(t), mission=self.summary())

    def transition(self, phase, w, q, t, reason):
        self.phase = phase
        q.event('spatial_mission_report', task_id=t.id, skill=self.skill,
                state='running', reason=reason, report=self.summary())
        q.update(t, 'running', reason)

    def send(self, w, q, t, out, units, ability, point=None, target=None):
        """One command per changed intent; no speculative load/unload repetition."""
        if (ability==MOVE or ability==ATTACK and point is not None) and not self.prepare_ground_movement(w,q,t,out,units):
            return False
        signature = (ability, tuple(point) if point is not None else None, target)
        actors = [u for u in units if self.orders.get(u.tag) != signature]
        if not actors: return True
        if any(w.seconds < self.ability_retry_at.get((u.tag, ability), 0) for u in actors): return False
        available = q.backend.available_abilities(q.index, [u.tag for u in actors])
        missing = [u for u in actors if not q.tech_tree.ability_available(ability, available.get(u.tag, set()))]
        if missing:
            for u in missing: self.ability_retry_at[u.tag, ability] = w.seconds + .5
            q.update(t, 'waiting', 'requested native raid ability unavailable; original deadline retained')
            return False
        if not q.issue(t, command(ability, [u.tag for u in actors], pos=point, target=target), out): return False
        for u in actors: self.orders[u.tag] = signature
        q.update(t, 'running', 'raid native command dispatched; awaiting actual observation')
        return True

    def allocate(self, w, q, t, used):
        self.route = [resolve_target(v, w, q) for v in self.args.get('targets', [self.args.get('target')])]
        self.requested_route = list(self.route)
        self.retreat_point = resolve_target(self.args['retreat_target'], w, q)
        blocked = q.protected() | used
        candidates = [u for u in w.my_units if u.type_id in selectors()[self.args['unit']]
            and u.done and not u.is_building and not u.flying and u.ground_dps > 0
            and u.type_id not in WORKERS and not u.hallucination and u.tag not in blocked]
        anchor = self.route[0]
        transport = None
        if self.skill == 'drop_raid':
            kind = TRANSPORTS[self.args['transport']][0]
            free = [u for u in w.my_units if u.type_id == kind and u.done and u.flying
                    and not u.hallucination and u.tag not in blocked and not u.passengers and not u.cargo_taken]
            if not free:
                q.update(t, 'waiting', 'need one free empty flying '+self.args['transport']+'; no purchase or form change'); return
            transport = min(free, key=lambda u:(u.distance(anchor), u.tag))
            anchor = transport.pos
        candidates.sort(key=lambda u:(u.distance(anchor), u.tag))
        if len(candidates) < self.args['count']:
            q.update(t, 'waiting', 'requested free ground combat squad unavailable; no substitute'); return
        chosen = candidates[:self.args['count']]
        if transport:
            sizes = [q.backend.types[u.type_id].cargo_size for u in chosen]
            if any(s <= 0 for s in sizes) or sum(sizes) > transport.cargo_max:
                raise ValueError('requested squad exceeds observed single-transport cargo capacity or is not loadable')
            self.transport_tag = transport.tag
            self.air_route = [resolve_target(v, w, q) for v in self.args['waypoints']] + self.route
        self.squad_tags = {u.tag for u in chosen}
        self.tags = self.squad_tags | ({transport.tag} if transport else set())
        used.update(self.tags); self.allocated_count = len(chosen); self.started = w.seconds
        self.transition('load' if transport else 'raiding', w, q, t, 'explicit offensive squad allocated; no reinforcements')

    def move(self, w, q, t, out, units, point):
        if not units: return False
        ground = [u for u in units if not u.flying]
        actual = point
        if ground:
            key = (tuple(point), tuple(sorted(u.tag for u in ground)))
            if key not in self.ground_points:
                self.ground_points[key] = ground_destination(w, q, units, point)
                q.event('spatial_target_resolved', task_id=t.id, requested=list(point),
                        actual=list(self.ground_points[key]), reason='explicit raid ground target; bounded local reachable approach only')
            actual = self.ground_points[key]
            self.resolved_ground_destination = dict(requested=list(point), actual=list(actual))
            self.observe_native_paths(w, q, t, units, actual)
        self.send(w, q, t, out, units, MOVE, point=actual)
        return all(u.distance(actual) <= 4 for u in units)

    def withdraw(self, w, q, t, reason):
        if self.phase in ('withdraw', 'holding'): return
        self.withdraw_reason = reason
        self.transition('withdraw', w, q, t, reason+'; retreat target chosen by LLM')

    def advance(self, w, q, t, used, out):
        if w.seconds - t.created >= self.args['timeout_s']:
            self.finish(w, q, t, out, 'failed', 'original raid deadline; no automatic redeployment', stop=True); return
        if self.phase == 'allocate':
            self.allocate(w, q, t, used)
            if self.phase == 'allocate': return
        self.tags.difference_update(w.dead_units)
        self.squad_tags.difference_update(w.dead_units)
        if not self.squad_tags:
            self.finish(w, q, t, out, 'failed', 'all allocated ground units confirmed dead; no replacement', stop=True); return
        units = [u for u in w.my_units if u.tag in self.squad_tags]
        transport = next((u for u in w.my_units if u.tag == self.transport_tag), None)
        if self.transport_tag in w.dead_units:
            if self.phase in ('load', 'fly', 'unload'):
                self.finish(w, q, t, out, 'failed', 'transport confirmed dead; passenger fate not inferred', stop=True); return
            self.transport_tag = None
        if self.transport_tag is not None and transport is None:
            q.update(t, 'waiting', 'allocated transport unobserved; no replacement or assumed death'); return
        passengers = set(transport.passengers) if transport else set()
        self.cargo = dict(passengers=sorted(passengers), space_taken=transport.cargo_taken if transport else None,
                          space_max=transport.cargo_max if transport else None,
                          ground_observed=sorted(u.tag for u in units))
        if passengers - self.squad_tags:
            raise ValueError('unallocated passengers observed; no unloading or takeover of foreign squad')
        if self.squad_tags - ({u.tag for u in units} | passengers):
            q.update(t, 'waiting', 'allocated ground members unobserved, not confirmed embarked/dead; no replacement'); return
        for tag, ability, code in w.errors:
            if tag in self.tags and q.tech_tree.canonical_ability(ability) in {
                    q.tech_tree.canonical_ability(a) for a in (MOVE, ATTACK, HOLD, *TRANSPORTS.get(self.args.get('transport'), (0,0,0))[1:])}:
                raise ValueError('engine rejected raid command: '+str((tag, ability, code)))
        # This is a model-authorized guard over CURRENT visibility, not strategic
        # inference that a defender came from its main army or an unseen position.
        actors = units + ([transport] if transport and self.phase in ('load','fly','unload') else [])
        defenders = [e for e in w.enemy_units if e.type_id not in WORKERS
            and (e.ground_dps > 0 or e.air_dps > 0 or e.type_id == 24)
            and any(e.distance(u) <= self.args['threat_radius'] for u in actors)]
        current = {e.tag for e in defenders}
        if current != self.previous_defenders:
            self.defender_contacts = [dict(tag=e.tag, unit=NAMES.get(e.type_id, str(e.type_id)),
                pos=list(e.pos), game_s=w.seconds, newly_visible_near_squad=e.tag not in self.previous_defenders)
                for e in defenders[:32]]
            self.previous_defenders = current
            q.event('spatial_mission_report', task_id=t.id, skill=self.skill, state='running',
                    reason='current visible defender contacts changed; origin/intent unknown', report=self.summary())
        if len(defenders) >= self.args['retreat_enemy_count']:
            self.withdraw(w, q, t, 'model-authorized visible defender threshold reached')
        if self.phase == 'load':
            if self.pending_load:
                if self.pending_load['tag'] in passengers or self.pending_load['tag'] not in self.squad_tags:
                    self.pending_load = None; self.orders.pop(transport.tag, None)
                elif w.seconds - self.pending_load['game_s'] > 20:
                    raise ValueError('load not confirmed by passenger observation in 20 game seconds; no recast')
                else: return
            waiting = [u for u in units if u.tag not in passengers]
            if waiting:
                chosen = min(waiting, key=lambda u:(u.distance(transport), u.tag))
                if self.send(w, q, t, out, [transport], TRANSPORTS[self.args['transport']][1], target=chosen.tag):
                    self.pending_load = dict(tag=chosen.tag, game_s=w.seconds)
                return
            if not self.squad_tags <= passengers: return
            self.transition('fly', w, q, t, 'every surviving allocated passenger observed inside transport')
        if self.phase == 'fly':
            if not self.squad_tags <= passengers:
                raise ValueError('passengers left transport before requested landing; no speculative flight')
            point = self.air_route[self.air_step]
            if transport.distance(point)>4:
                self.move(w, q, t, out, [transport], point); return
            self.air_step += 1
            if self.air_step < len(self.air_route): return
            self.transition('unload', w, q, t, 'transport arrived at model landing point; cargo still aboard')
        if self.phase == 'unload':
            if not passengers and len(units) == len(self.squad_tags):
                self.transition('raiding', w, q, t, 'all surviving passengers observed on ground; unload confirmed')
            elif self.unload_sent is None:
                if self.send(w, q, t, out, [transport], TRANSPORTS[self.args['transport']][2], point=self.route[0]):
                    self.unload_sent = w.seconds
                return
            elif w.seconds - self.unload_sent > 20:
                raise ValueError('unload not confirmed on ground in 20 game seconds; no repeated unload')
            else: return
        if self.phase == 'raiding':
            if passengers:
                raise ValueError('raid ground squad still embarked; no ground attack inferred')
            if transport: self.move(w, q, t, out, [transport], self.retreat_point)
            point = self.route[self.step]
            if not all(u.distance(point) <= 6 for u in units) and self.at_site is None:
                self.move(w, q, t, out, units, point); return
            if self.at_site is None: self.at_site = w.seconds
            economy = [e for e in w.enemy_units if e.type_id in WORKERS | TOWNHALLS | GAS_BUILDINGS
                       and e.distance(point) <= 12]
            attackable = [e for e in economy if any(u.can_attack(e) for u in units)]
            self.contacts = [dict(tag=e.tag, unit=NAMES.get(e.type_id,str(e.type_id)), pos=list(e.pos),
                                  game_s=w.seconds, attackable=e in attackable) for e in economy[:16]]
            if w.seconds - self.at_site >= self.args['dwell_s'] or (
                    not economy and area_visibility(w, point, 4)['state'] == 'fully_visible'):
                self.arrivals.append(dict(center=list(point), game_s=w.seconds,
                    visible_economy=len(economy), **area_visibility(w, point, 4)))
                self.step += 1; self.at_site = None
                if self.step >= len(self.route):
                    self.withdraw(w, q, t, 'explicit raid route/dwell complete; no kill or clearance claim')
                else:
                    self.move(w, q, t, out, units, self.route[self.step]); return
            elif attackable:
                target = min(attackable, key=lambda e:(e.type_id not in WORKERS,
                    min(u.distance(e) for u in units), e.tag))
                self.send(w, q, t, out, units, ATTACK, target=target.tag)
            else:
                # A last visible worker may have escaped the authorized site or
                # entered fog. Cancel its old targeted order; never chase memory.
                self.move(w, q, t, out, units, point)
                q.update(t, 'waiting', 'visible economic targets not attackable by allocated squad; retaining dwell deadline'
                         if economy else 'site not fully visible and no visible economic target; unknown, retaining dwell deadline')
        if self.phase == 'withdraw':
            ground_here = self.move(w, q, t, out, units, self.retreat_point) if units else True
            air_here = self.move(w, q, t, out, [transport], self.retreat_point) if transport else True
            if ground_here and air_here:
                if units and not self.send(w, q, t, out, units, HOLD): return
                self.transition('holding', w, q, t, 'survivors observed at model retreat point; hold ownership until explicit cancellation/deadline')
