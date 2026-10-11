"""Model-ordered base defence. The script executes; it never decides.

Revized 2026-10-11 after the user's point: choosing what to defend, how to split troops or which
base to give up is the strategic decision that makes the game interesting, so it stays with the
model. This module therefore only executes an explicit order:

- the commander names the base, the unit kinds and optionally how many;
- nothing is defended unless ordered -- an unordered base IS the way to give it up;
- a standing order keeps re-issuing its command every tick (one attack-move would idle on
  arrival), holds its units against other tasks, and never re-targets by itself: if its units die
  or the base falls, that is reported and the commander decides again.
"""
from collections import Counter
from arena.raw_engine import ATTACK, MOVE, NAMES, command
from arena.tech_tree import TOWNHALLS, WORKERS

PROTOCOL = '''Base defence is YOUR decision; nothing is defended unless you order it.
defend_base args={base:"home|site_N|[x,y]", units:["marine","tank",...] or "all",
count:1..32 optional (default: every matching free unit near that base), stance:"hold|trade|screen"
optional, timeout_s:30..900 optional, reason:short Chinese intent}.
Every combat unit you own is selectable by any order, wherever it stands: sending a distant squad to
a threatened base is a normal order. The script only refuses units another mission or defence order
already holds. Two simultaneous defences therefore need a deliberate split: give each order its own
kinds and/or count, or the first one to tick takes what it can reach and the other may find nothing
(it reports that visibly). With units:"all" and no count an order takes about twice the number of
visible attackers (at least 4, at most 16), and when several orders are active it also splits what
is available between them; state a count whenever you want a specific share.
One order defends exactly one base: for two simultaneous attacks issue two orders and split kinds
and counts yourself, or order only one base and leave the other unordered to give it up. Unordered
bases are reported as un-defended and their loss is reported; no script diverts troops to them.
A NEW order for the SAME base REPLACES the old one in the same reply (the old one is marked
superseded and its units are inherited), so you can re-split or change stance without cancelling
first. Use a different base to run two defences at once. The order keeps executing until you cancel
it with cancel_task args={task_id} or it times out; if its units die or the base falls it reports
that and waits, it never re-targets. Units held by other
missions or custom skills are never taken; while an order runs, its units are reserved for it.
Read defense_report each decision: per owned base it lists visible attackers, your nearby free
units by kind, distance, and seconds_to_loss/urgency -- the time until that base is destroyed if
nothing changes. When any base is contested/urgent/critical, a defence order for it belongs in THAT
same reply: an economy-only reply while a base is losing health is a decision to lose the base.
seconds_to_loss <= 15 means act now with every combat unit you can reach; <= 45 means reinforce in
this reply. Splitting is a real choice: order several bases, order one and abandon the other, or
order none and trade the base for something else -- but do it deliberately, not by omission.
Relationship with your other dispatches: a standing order keeps its units reserved, so scout /
order_units will NOT take them -- they wait with a visible reason instead of silently picking other
units. If you want to move a unit that a defence order holds, cancel that order first (cancel_task)
in the same reply, then dispatch; explicit missions never double-command a unit that a defence order
already owns, and the defence order never steals units owned by your explicit missions.'''


def task_schemas(payload=None):
    """Task shapes for the commander-ordered defence controls (outer count must be 1)."""
    def obj(properties, required=()):
        return {'type': 'object', 'properties': properties, 'required': list(required),
                'additionalProperties': False}
    names = sorted((payload or {}).get('world', {}).get('own', {}))
    base = {'anyOf': [
        {'type': 'string', 'enum': sorted({'home'} | {(b.get('label') or '') for b in (payload or {}).get('defense_report', [])} - {''})},
        {'type': 'array', 'minItems': 2, 'maxItems': 2, 'items': {'type': 'number'}}]}
    units = {'anyOf': [{'type': 'string', 'enum': ['all']},
                       {'type': 'array', 'minItems': 1, 'maxItems': 16, 'items':
                        {'type': 'string', 'enum': names or ['marine']}}]}
    return [
        obj({'skill': {'type': 'string', 'enum': ['defend_base']},
             'count': {'type': 'integer', 'enum': [1]},
             'args': obj({'base': base, 'units': units,
                          'count': {'type': 'integer', 'minimum': 1, 'maximum': 32},
                          'stance': {'type': 'string', 'enum': ['hold', 'trade', 'screen']},
                          'timeout_s': {'type': 'number', 'minimum': 30, 'maximum': 900},
                          'reason': {'type': 'string', 'minLength': 1, 'maxLength': 200}},
                         ['base', 'units', 'reason'])}, ['skill', 'args']),
        obj({'skill': {'type': 'string', 'enum': ['cancel_task']},
             'count': {'type': 'integer', 'enum': [1]},
             'args': obj({'task_id': {'type': 'integer', 'minimum': 1}}, ['task_id'])},
            ['skill', 'args']),
    ]


def unit_names(type_id, backend=None):
    """All names the commander may legitimately write for this type.

    world.own shows the display name (e.g. 'Marine') while the combat selector vocabulary is
    lower-case ('marine'); the commander may copy either, so both must resolve. Rejecting one of
    them rejects the whole plan.
    """
    name = NAMES.get(type_id, str(type_id))
    return {name, name.lower().replace(' ', '_')}


def unit_name(type_id, backend=None):
    """One naming source for defence: exactly what world.own shows for the same type.

    Snapshot.summary() uses NAMES.get(type_id, str(type_id)); the name table is filled from the
    engine (raw_engine.NAMES.update on tech data) and falls back to the numeric id. Using the same
    rule here means the name the commander reads in defense_report.own_nearby is always a name it
    is allowed to order back.
    """
    return NAMES.get(type_id, str(type_id))


def _townhalls(w):
    return sorted((u for u in w.my_units if u.type_id in TOWNHALLS and not u.flying), key=lambda u: u.tag)


def _site_centers(w):
    from arena.macro import expansion_clusters
    return {name: (sum(u.x for u in group) / len(group), sum(u.y for u in group) / len(group))
            for name, group in expansion_clusters(w)}


def base_labels(w, home):
    """Stable model-visible name per owned town hall: 'home' or the nearest 'site_N'."""
    sites = _site_centers(w)
    labels = {}
    for base in _townhalls(w):
        if base.distance(tuple(home)) <= 15:
            labels[base.tag] = 'home'
            continue
        if not sites:
            labels[base.tag] = 'base_' + str(base.tag)
            continue
        name, _ = min(sites.items(), key=lambda kv: (kv[1][0] - base.x) ** 2 + (kv[1][1] - base.y) ** 2)
        labels[base.tag] = name
    return labels


def base_threats(w, backend, index, radius=26, ordered_tags=()):
    """Per owned base facts the commander needs to choose what to defend or give up.

    Observations only: visible attackers, own nearby free combat units, distance. It makes no
    recommendation and no allocation decision.
    """
    home = tuple(getattr(backend, 'homes', [(0, 0)])[index])
    labels = base_labels(w, home)
    rows = []
    for base in _townhalls(w):
        attackers = [e for e in w.enemy_units
                     if (e.ground_dps > 0 or e.air_dps > 0) and e.distance(base) <= radius]
        incoming_dps = sum(max(e.ground_dps, e.air_dps) for e in attackers)
        # Mechanical arithmetic only (no recommendation): how soon the base is destroyed if nothing
        # changes. The commander uses this to judge urgency; the script never acts on it.
        seconds_to_loss = round(base.health / incoming_dps, 1) if incoming_dps > 0 else None
        own_near = [u for u in w.my_units
                    if not u.is_building and u.type_id not in WORKERS
                    and (u.ground_dps > 0 or u.air_dps > 0) and u.distance(base) <= radius]
        kinds = Counter(NAMES.get(u.type_id, str(u.type_id)) for u in own_near)
        rows.append(dict(
            label=labels.get(base.tag, 'base_' + str(base.tag)), tag=base.tag,
            pos=[round(base.x, 1), round(base.y, 1)],
            health=round(base.health, 1), health_max=base.health_max, progress=round(base.progress, 2),
            visible_attackers=len(attackers),
            seconds_to_loss=seconds_to_loss,
            urgency=('critical' if seconds_to_loss is not None and seconds_to_loss <= 15
                     else 'urgent' if seconds_to_loss is not None and seconds_to_loss <= 45
                     else 'contested' if seconds_to_loss is not None else 'quiet'),
            attacker_kinds=dict(Counter(unit_name(e.type_id, backend) for e in attackers)),
            attacker_dps=round(sum(max(e.ground_dps, e.air_dps) for e in attackers), 1),
            nearest_attacker_distance=round(min((e.distance(base) for e in attackers), default=-1), 1),
            own_nearby=dict(kinds), own_nearby_total=len(own_near),
            defense_order_active=base.tag in set(ordered_tags),
            defended=base.tag in set(ordered_tags),
        ))
    return rows


class DefendBase:
    """One standing defence order for one named base."""

    radius = 26
    holds_defense_reservation = True   # queue uses this to keep cancelled tags busy for one tick

    def __init__(self, args=None, supersedes=None):
        # Same-base replacement: inherit the previous order's units so re-issuing to change the
        # count/stance does not silently swap which units are defending.
        self.supersedes = list(supersedes or [])
        self.inherited_tags = {tag for task in self.supersedes
                               for tag in getattr(getattr(task, 'program', None), 'tags', ())}
        self.backend = None
        self.args = dict(args or {})
        self.base_selector = self.args.get('base')
        self.unit_kinds = self.args.get('units', 'all')
        self.count = self.args.get('count')
        self.stance = self.args.get('stance', 'hold')
        self.timeout_s = float(self.args.get('timeout_s', 300))
        self.reason = str(self.args.get('reason', ''))
        self.tags = set()
        self.stop_tags = set()
        self.base_tag = None
        self.base_label = None
        self.last_orders = {}
        self.issued = 0
        self.started_s = None
        self.state = 'resolving'
        self.note = None
        self.released = True

    # -- queue program protocol -------------------------------------------------
    def close(self):
        # Keep the tags for stop_orders (same contract as SpatialMission): clearing them here
        # would make cancellation unable to release its reservation, locking the units forever.
        self.stop_tags = set(self.tags)

    def stop_orders(self, w, q, t, out):
        # Cancellation runs through pending_stops, not tick: capture the tags first, then hand the
        # reservation back, otherwise a following attack order is blocked by a stale reservation.
        tags = [u.tag for u in w.my_units if u.tag in (self.tags | self.stop_tags)]
        self._release(q, w)
        if tags:
            out.append(command(4, tags))

    def summary(self):
        return dict(kind='defend_base', base=self.base_label or self.base_selector, base_tag=self.base_tag,
                    units=self.unit_kinds, count=self.count, stance=self.stance,
                    unit_tags=sorted(self.tags), state=self.state, issued=self.issued,
                    timeout_s=self.timeout_s, reason=self.reason, note=self.note,
                    meaning='standing order; reports, never re-targets or diverts other troops')

    def _release(self, q, w):
        if self.released and not self.tags:
            return
        q.defense_tags_snapshot = None
        self.released = True
        self.tags = set()
        self.stop_tags = set()
        q.release_defense(w)

    # -- execution --------------------------------------------------------------
    def _resolve_base(self, w, backend, index):
        from arena.rally import named_rally_point
        from arena.spatial import resource_sites
        selector = self.base_selector
        if isinstance(selector, (list, tuple)):
            point = (float(selector[0]), float(selector[1]))
        elif selector == 'home':
            point = tuple(backend.homes[index])
        elif isinstance(selector, str) and selector.startswith('base_') and selector[5:].isdigit():
            hall = next((b for b in _townhalls(w) if b.tag == int(selector[5:])), None)
            if hall is None:
                raise ValueError('that base is no longer owned: ' + selector)
            return hall, (hall.x, hall.y)
        elif isinstance(selector, str) and selector.startswith('site_'):
            sites = resource_sites(w)
            if selector not in sites:
                raise ValueError('resource site no longer present in own observation: ' + selector)
            point = sites[selector]
        else:
            point = named_rally_point(None, backend, index, selector, w)[0]
        halls = _townhalls(w)
        if not halls:
            raise ValueError('no owned town hall remains')
        base = min(halls, key=lambda b: (b.x - point[0]) ** 2 + (b.y - point[1]) ** 2)
        if base.distance(point) > 30:
            raise ValueError('no owned base near requested ' + str(selector))
        return base, point

    def unit_name(self, unit):
        return unit_name(unit.type_id, self.backend)

    def _kind_matches(self, unit):
        if self.unit_kinds == 'all':
            return True
        requested = {str(k).strip().lower() for k in self.unit_kinds}
        return bool(unit_names(unit.type_id, self.backend) & requested) or \
               self.unit_name(unit).lower() in requested

    def _select_units(self, w, base, q, used, threat=0, sole_order=False, others=0):
        # Units held by a DIFFERENT defence order must be respected, but this order's own inherited
        # units (from the order it replaced) must not block it -- otherwise a same-base override
        # would drop its own squad. A per-tick snapshot of the shared reservation lets several
        # orders coexist even though the queue keeps one union set.
        # Units held by other defence orders are excluded explicitly, reading their LIVE tags (a
        # tick-start snapshot is stale: a sibling may have claimed units earlier in this same tick).
        # The queue keeps ONE shared reservation for all defences, so it cannot express
        # "everyone's units except mine"; asking each sibling is exact and order-independent.
        siblings = {tag for other in getattr(self, '_others', ())
                    for tag in (getattr(getattr(other, 'program', None), 'tags', ()) or ())}
        held_before = set(getattr(self, '_held_before', ()))
        # Subtract what this order held last tick: the queue's global reservation includes those
        # tags, and without this an active defence could never re-claim its own units (it would pick
        # different ones every tick, and two orders would alternate ownership).
        protected = ((q.protected() | set(used) | siblings)
                     - set(self.inherited_tags) - held_before)
        # The whole army is selectable: choosing who defends what is the commander's decision, not
        # a script-side quota. What the script must never do is take units another mission owns.
        def usable(unit):
            if unit.tag in self.inherited_tags:
                return True
            if unit.tag in protected:
                return False
            return True

        free = [u for u in w.my_units
                if usable(u) and not u.is_building and u.type_id not in WORKERS
                and (u.ground_dps > 0 or u.air_dps > 0) and self._kind_matches(u)]
        # Keep the units this order already held, so two orders sharing an army do not alternate
        # ownership every tick (each release used to hand its units to the sibling).
        free.sort(key=lambda u: 0 if u.tag in held_before else 1)
        # Keep the units this order already owns first (inherited from the superseded order),
        # then fill up by proximity.
        free.sort(key=lambda u: (0 if u.tag in (held_before | self.inherited_tags) else 1,
                                 u.distance(base)))
        if isinstance(self.count, int):
            return free[:max(1, self.count)]
        # units:"all" without a count: bound it roughly by the threat (2x attackers, min 4, max 16)
        # and say so, instead of silently eating every unit on the map. A bigger commitment must be
        # an explicit count -- that keeps the quota decision with the commander.
        if threat:
            budget = max(4, min(16, 2 * threat))
            if sole_order:
                budget = max(budget, 16)
            # Several orders and no explicit count: share what is available instead of letting
            # whichever ticks first take everything. An explicit count always overrides this.
            elif others:
                budget = min(budget, max(1, len(free) // (others + 1)))
            return free[:budget]
        return free

    def tick(self, w, q, t, used, bank, out):
        self.backend = q.backend
        # First defence order of this tick captures the shared reservation before anyone releases,
        # so a second order still sees the first one's units as held.
        if getattr(q, 'defense_tags_snapshot', None) is None:
            q.defense_tags_snapshot = set(q.defense_tags)
        if self.started_s is None:
            self.started_s = w.seconds
        if t.status == 'queued':
            q.update(t, 'running', 'standing defence order for ' + str(self.base_selector))
        if w.seconds - self.started_s > self.timeout_s:
            self.state = 'expired'
            self.note = 'timeout reached; re-issue if this base still needs defending'
            self._release(q, w)
            q.update(t, 'completed', 'defend_base timeout; units released, this base may now be un-defended')
            return bank
        try:
            base, point = self._resolve_base(w, q.backend, q.index)
        except (ValueError, TypeError, KeyError) as error:
            self.state = 'unresolved'
            self.note = str(error)
            q.update(t, 'waiting', str(error))
            return bank
        self.base_tag = base.tag
        self.base_label = base_labels(w, tuple(q.backend.homes[q.index])).get(base.tag)
        attackers = [e for e in w.enemy_units
                     if (e.ground_dps > 0 or e.air_dps > 0) and e.distance(base) <= self.radius]
        self._held_before = set(self.tags)       # keep own units sticky across ticks
        self._release(q, w)                      # recompute ownership from scratch each tick
        others = [x for x in q.tasks if x.active and x.skill == 'defend_base' and x.id != t.id]
        self._others = others
        chosen = self._select_units(w, base, q, used, threat=len(attackers),
                                    sole_order=not others, others=len(others))
        if not chosen:
            self.state = 'no_free_units'
            self.note = ('no free %s unit for %s: none left, or all matching units are held by other '
                         'missions/another defence order (other missions are never raided)'
                         % (self.unit_kinds, self.base_selector))
            q.update(t, 'waiting', self.note)
            return bank
        self.tags = {u.tag for u in chosen}
        q.take_defense(self.tags, w.seconds)
        if not attackers:
            self.state = 'no_threat'
            self.note = ('no visible attacker within %d of %s; order kept, cancel it to redeploy'
                         % (self.radius, self.base_selector))
            return bank
        self.state = 'defending'
        self.note = None
        for u in chosen:
            target = min(attackers, key=lambda e: u.distance(e))
            ability = ATTACK if self.stance in ('hold', 'trade') else MOVE
            key = (ability, round(target.x), round(target.y))
            if self.last_orders.get(u.tag) == key and u.orders:
                continue
            self.last_orders[u.tag] = key
            out.append(command(ability, [u.tag], pos=target.pos))
            self.issued += 1
        return bank
