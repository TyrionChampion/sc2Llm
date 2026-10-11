"""Defensive production rallies from own bases and public ground topology.

Only production-building rally targets are changed. Existing squads are never
recalled; worker rallies, warp-in locations and morphing combat units are untouched.

Two paths share this module:
- DefensiveRally: automatic anchor (main ramp -> exposed expansion entrance, or the
  army-tactics assembly point when tactical control is on).
- RallyOrder: an explicit, per-production-building order issued by the commander
  (see arena/tasks.py). A producer owned by a RallyOrder is added to the automatic
  rally's busy set, so the automatic code never overwrites the explicit target.
"""
import heapq
import math
from pysc2.lib.features import Feature
from arena.raw_engine import command
from arena.tech_tree import TOWNHALLS

# Native hatchery UNIT rally is distinct from its worker rally.
PRODUCERS = {21,27,28,62,71,67,86,100,101}
# Warp gates are producers too but their units spawn at a pylon/prism anchor, not a rally.
NO_RALLY_PRODUCERS = {133}

# Last-resort names for the producer types, used only when the backend exposes no type
# metadata. The engine-bound backend supplies authoritative names via types[].name; this
# table keeps the commander able to address buildings instead of silently addressing none.
FALLBACK_PRODUCER_NAMES = {21:'barracks',27:'factory',28:'starport',62:'gateway',
                           67:'stargate',71:'roboticsfacility',86:'hatchery',100:'lair',101:'hive'}

# Names the commander may pass as a rally_units target, resolved by named_rally_point().
RALLY_NAMES = {'home','expansion','rally','enemy_start','nearest_enemy',
               'main_ramp_top','expansion_entrance','main_ground_approach'}


def producer_kind(backend, type_id):
    """Deterministic kind name for a production building type; None if not a producer."""
    if type_id not in PRODUCERS:
        return None
    types = getattr(backend, 'types', None) or {}
    meta = types.get(type_id)
    name = getattr(meta, 'name', None)
    if name:
        return ''.join(ch for ch in name if ch.isalnum()).lower()
    return FALLBACK_PRODUCER_NAMES.get(type_id)


def producer_inventory(backend, world, index):
    """Stable type#N addressing for the player's own production buildings.

    Ordinals are assigned by unit tag order, like site_N naming elsewhere in this
    codebase: they are stable for the life of the building and are never renumbered
    while it lives. Returns a list of records plus a tag->label map.
    """
    mine = [u for u in world.my_units
            if u.type_id in PRODUCERS and u.type_id not in NO_RALLY_PRODUCERS
            and u.done and not u.flying]
    records = []
    labels = {}
    counters = {}
    for unit_ in sorted(mine, key=lambda u: u.tag):
        kind = producer_kind(backend, unit_.type_id)
        if kind is None:
            continue
        counters[kind] = counters.get(kind, 0) + 1
        label = '%s#%d' % (kind, counters[kind])
        labels[unit_.tag] = label
        records.append(dict(label=label, kind=kind, tag=unit_.tag, type_id=unit_.type_id,
                            pos=[round(unit_.x, 1), round(unit_.y, 1)]))
    return records, labels


def parse_producer_selector(value):
    """'hatchery#2' -> ('hatchery', 2). Rejects anything else, explicitly."""
    if not isinstance(value, str) or '#' not in value:
        raise ValueError('producer selector must look like barracks#1, got ' + repr(value))
    kind, _, ordinal = value.partition('#')
    if not kind or not ordinal.isdigit() or int(ordinal) < 1:
        raise ValueError('producer selector must look like barracks#1, got ' + repr(value))
    return kind.lower(), int(ordinal)


class GroundMap:
    def __init__(self, raw):
        self.pathing = Feature.unpack_layer(raw.pathing_grid)
        self.height = Feature.unpack_layer(raw.terrain_height)
        if self.pathing is None or self.height is None or self.pathing.shape != self.height.shape:
            raise ValueError('missing or inconsistent pathing/height grid; rally unavailable')
        self.h,self.w = self.pathing.shape
        self.cache = {}

    def free(self, x, y):
        return 0<=x<self.w and 0<=y<self.h and bool(self.pathing[y,x])

    def nearest(self, pos):
        x,y=map(int,pos)
        candidates=[(a,b) for a in range(max(0,x-8),min(self.w,x+9))
                    for b in range(max(0,y-8),min(self.h,y+9)) if self.free(a,b)]
        if not candidates:
            raise ValueError('no ground cell within 8 tiles of base/start')
        return min(candidates,key=lambda p:math.dist((p[0]+.5,p[1]+.5),pos))

    def route(self, start, end):
        key=(tuple(start),tuple(end))
        if key in self.cache:
            return self.cache[key]
        a,b=self.nearest(start),self.nearest(end)
        frontier=[(math.dist(a,b),0,a)];cost={a:0};parent={}
        while frontier:
            _,g,p=heapq.heappop(frontier)
            if g!=cost[p]:continue
            if p==b:break
            for dx,dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)):
                n=(p[0]+dx,p[1]+dy)
                if not self.free(*n) or dx and dy and (not self.free(p[0]+dx,p[1]) or not self.free(p[0],p[1]+dy)):
                    continue
                ng=g+math.hypot(dx,dy)
                if ng<cost.get(n,float('inf')):
                    cost[n]=ng;parent[n]=p;heapq.heappush(frontier,(ng+math.dist(n,b),ng,n))
        if b not in cost:
            raise ValueError('no static ground route to opponent start; rally unavailable')
        path=[b]
        while path[-1]!=a:path.append(parent[path[-1]])
        path.reverse();self.cache[key]=path
        return path

    def candidates(self, base, enemy, expansion, units):
        path=self.route(base,enemy)
        initial=int(self.height[path[0][1],path[0][0]])
        ramp=next((i for i,p in enumerate(path[:45]) if int(self.height[p[1],p[0]])<initial-4),None)
        if not expansion and ramp is not None:
            desired=max(0,ramp-3);kind='main_ramp_top'
        else:
            desired=next((i for i,p in enumerate(path) if math.dist((p[0]+.5,p[1]+.5),base)>=9),len(path)-1)
            kind='expansion_entrance' if expansion else 'main_ground_approach'
        result=[]
        # Prefer the defended side of the entrance; never advance beyond the anchor.
        for i in range(desired,max(-1,desired-10),-1):
            x,y=path[i];p=(x+.5,y+.5)
            if any((u.is_building or u.mineral or u.geyser) and u.distance(p)<u.radius+1.5 for u in units):continue
            if any(u.alliance==4 and (u.ground_dps>0 or u.air_dps>0) and u.distance(p)<max(9,u.attack_range+3) for u in units):continue
            result.append(p)
        if not result:raise ValueError('defensive entrance blocked or threatened; no rally change issued')
        return kind,result,path[0]


def my_townhalls(world):
    return [u for u in world.my_units if u.type_id in TOWNHALLS and u.done and not u.flying]


def named_rally_point(grid, backend, index, name, world):
    """Resolve one named place to a concrete point, or raise ValueError.

    Single source of truth for the names the commander may pass to rally_units, so no
    fourth copy of the home/rally/enemy_start geometry is introduced. `rally` keeps its
    established meaning (12 tiles from home toward the enemy start) because that name is
    already accepted by morph_unit and every python skill.
    """
    home = backend.homes[index]
    enemy = backend.enemy_start(index)
    if name == 'home':
        return (home[0], home[1]), 'home'
    if name == 'enemy_start':
        return (enemy[0], enemy[1]), 'enemy_start'
    if name == 'rally':
        distance = max(1, math.dist(home, enemy))
        return tuple(home[i] + (enemy[i] - home[i]) * 12 / distance for i in (0, 1)), 'rally'
    if name == 'nearest_enemy':
        if not world.enemy_units:
            raise ValueError('nearest_enemy has no visible target; no fallback')
        return min(world.enemy_units, key=lambda u: u.distance(home)).pos, 'nearest_enemy'
    if name == 'expansion':
        halls = [u for u in my_townhalls(world) if u.distance(home) > 15]
        if not halls:
            raise ValueError('expansion has no completed owned expansion')
        base = min(halls, key=lambda u: u.distance(home))
        return (base.x, base.y), 'expansion'
    if name in ('main_ramp_top', 'expansion_entrance', 'main_ground_approach'):
        if grid is None:
            raise ValueError('no static ground map; named entrance unavailable')
        halls = my_townhalls(world)
        if not halls:
            raise ValueError('no completed owned base to anchor ' + name)
        if name == 'main_ramp_top':
            base = min(halls, key=lambda u: u.distance(home))
            expansion = False
        elif name == 'expansion_entrance':
            outer = [u for u in halls if u.distance(home) > 15]
            if not outer:
                raise ValueError('expansion_entrance has no completed owned expansion')
            base = max(outer, key=lambda u: u.distance(home))
            expansion = True
        else:
            base = min(halls, key=lambda u: u.distance(home))
            expansion = True
        kind, points, _ = grid.candidates(base.pos, enemy, expansion, world.units)
        if kind != name:
            raise ValueError('map topology reports ' + kind + ', not ' + name)
        return points[0], name
    raise ValueError('unknown named rally target ' + repr(name))


class RallyOrder:
    """Explicit per-production-building rally order issued by the commander.

    The target is resolved once from the shared static map (a named place or an explicit
    coordinate pair); every owned producer is then commanded with the engine's
    GeneralRallyUnits ability, and confirmation reads each building's own
    raw.rally_targets. While the order lives its producer tags are handed to
    DefensiveRally's busy set, so the automatic path never overwrites the explicit one.
    """

    def __init__(self, backend, index, event, spec=None, ability=None, build_spec=None, grid=None):
        self.backend = backend
        self.index = index
        self.event = event
        # spec is resolved lazily from a world when build_spec is given: selectors like
        # "barracks#1" can only be bound to real building tags once a snapshot exists.
        self.spec = dict(spec) if spec is not None else None
        self.build_spec = build_spec
        self.grid = grid
        if self.grid is None:
            try:
                infos = getattr(backend, 'map_info_by_player', None)
                if infos is not None:
                    self.grid = GroundMap(infos[index].start_raw)
            except (ValueError, AttributeError):
                self.grid = None
        self.kind = None
        self.point = None
        self.reason = None
        self.expires_at = None
        self.pending = {}
        self.confirmed = {}
        self.failures = {}
        self.created_at = None
        self.ability = ability
        self.capability_unverified = False

    def public(self):
        spec = self.spec or {}
        return dict(kind=self.kind, point=list(self.point) if self.point else None,
                    producers={tag: label for tag, label in spec.get('labels', {}).items()},
                    confirmed_buildings=sorted(self.confirmed), pending_buildings=sorted(self.pending),
                    blocked_buildings=sorted(tag for tag, n in self.failures.items() if n >= 3),
                    expires_at=self.expires_at,
                    status='unavailable' if self.reason else ('active' if self.point else 'resolving'),
                    reason=self.reason,
                    note='explicit order; owned producers are excluded from automatic rally')

    @property
    def tags(self):
        return tuple((self.spec or {}).get('tags', ()))

    def summary(self):
        """Same contract as MacroRun.summary(): compact state for active_tasks."""
        return {'kind': 'rally_order', 'landmark': self.kind, 'point': list(self.point) if self.point else None,
                'producers': sorted((self.spec or {}).get('labels', {}).values()),
                'confirmed': sorted(self.confirmed), 'pending': sorted(self.pending),
                'reason': self.reason}

    def _resolve_target(self, world):
        target = (self.spec or {}).get('target')
        if isinstance(target, (list, tuple)):
            if len(target) != 2 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in target):
                raise ValueError('coordinate target must be [x, y] numbers')
            if self.grid is None:
                raise ValueError('backend has no static map to clamp the coordinate')
            x, y = self.grid.nearest((target[0], target[1]))
            return (x + .5, y + .5), 'coordinate'
        if not isinstance(target, str):
            raise ValueError('target must be a named place or [x, y]')
        return named_rally_point(self.grid, self.backend, self.index, target, world)

    def tick(self, world, q, t, used, bank, out):
        """Program protocol shared with MacroRun/SkillRun: append actions, return the bank."""
        if self.created_at is None:
            self.created_at = world.seconds
        if self.expires_at is not None and world.seconds > self.expires_at:
            if t.status == 'running':
                q.update(t, 'completed', 'rally order expired; automatic rally resumes for these producers')
            return bank
        if self.spec is None:
            if self.build_spec is None:
                self.reason = 'no spec builder supplied'
                self.event('rally_order_unavailable', error=self.reason)
                q.update(t, 'failed', self.reason)
                return bank
            try:
                self.spec = dict(self.build_spec(world))
            except (ValueError, AttributeError) as error:
                self.reason = str(error)
                self.event('rally_order_unavailable', error=self.reason)
                q.update(t, 'failed', self.reason)
                return bank
        if t.status == 'queued':
            q.update(t, 'running', 'explicit rally order active; owned producers excluded from automatic rally')
        if self.reason:
            return bank
        if self.point is None:
            try:
                self.point, self.kind = self._resolve_target(world)
            except (ValueError, AttributeError) as error:
                self.reason = str(error)
                self.event('rally_order_unavailable', error=self.reason, target=self.spec.get('target'))
                return bank
            duration = self.spec.get('duration_s')
            if isinstance(duration, (int, float)) and not isinstance(duration, bool):
                self.expires_at = world.seconds + max(1, int(duration))
            self.kind = self.kind or 'coordinate'
            self.event('rally_order_resolved', landmark=self.kind, point=list(self.point),
                       producers=sorted(self.spec.get('tags', ())), expires_at=self.expires_at)
        for tag, sent in list(self.pending.items()):
            building = world.by_tag(tag)
            if building is None:
                if tag in world.dead_units:
                    self.pending.pop(tag)
                    self.confirmed.pop(tag, None)
                continue
            if any(math.dist(target[:2], self.point) < 1 for target in building.rally_targets):
                self.confirmed[tag] = self.point
                self.pending.pop(tag)
                self.failures.pop(tag, None)
                self.event('rally_order_confirmed', building_tag=tag, point=list(self.point), landmark=self.kind)
            elif world.seconds - sent >= 3:
                self.pending.pop(tag)
                self.failures[tag] = self.failures.get(tag, 0) + 1
                self.event('rally_order_unconfirmed', building_tag=tag, point=list(self.point), landmark=self.kind)
        ability = self.ability
        tree = getattr(self.backend, 'tech_tree', None)
        if ability is None and tree is None:
            self.reason = 'backend exposes no tech_tree to confirm the unit rally ability'
            self.event('rally_order_unavailable', error=self.reason)
            q.update(t, 'failed', self.reason)
            return bank
        if ability is None:
            abilities = [a.ability_id for a in tree.abilities.values()
                         if a.link_name == 'GeneralRallyUnits' and a.available]
            if len(abilities) != 1:
                self.reason = 'unique GeneralRallyUnits ability not supplied by engine'
                self.event('rally_order_unavailable', error=self.reason)
                return bank
            self.ability = ability = abilities[0]
        for tag in self.spec.get('tags', ()):
            building = world.by_tag(tag)
            if building is None:
                continue
            if tag in self.pending:
                continue
            if tag in self.confirmed and math.dist(self.confirmed[tag], self.point) < 1:
                continue
            if self.failures.get(tag, 0) >= 3:
                continue
            available = self.backend.available_abilities(self.index, [tag], ignore_resources=True)
            if tree is None:
                # No ability catalogue: still issue the order (the engine rejects an illegal
                # ability explicitly) and let native confirmation decide, rather than silently
                # skipping the building. Recorded once per order so the gap stays visible.
                if not self.capability_unverified:
                    self.capability_unverified = True
                    self.event('rally_order_ability_unverified',
                               reason='backend exposes no tech_tree; ability availability not pre-checked',
                               ability=ability)
            elif not tree.ability_available(ability, available.get(tag, set())):
                self.failures[tag] = self.failures.get(tag, 0) + 1
                self.event('rally_order_ability_unavailable', building_tag=tag, unit_type=building.type_id)
                continue
            if not q.issue(t, command(ability, [tag], pos=self.point), out):
                continue
            self.pending[tag] = world.seconds
            self.event('rally_order_requested', building_tag=tag, point=list(self.point), landmark=self.kind)
        return bank


class DefensiveRally:
    def __init__(self, backend, index, event):
        self.backend=backend;self.index=index;self.event=event
        self.next_at=0;self.pending={};self.confirmed={};self.failures={};self.state={'state':'initializing'}
        self.grid=None;self.ability=None;self.disabled=None
        try:
            infos=getattr(backend,'map_info_by_player',None)
            if infos is None:raise ValueError('backend has no per-player static map info')
            self.grid=GroundMap(infos[index].start_raw)
            abilities=[a.ability_id for a in backend.tech_tree.abilities.values() if a.link_name=='GeneralRallyUnits' and a.available]
            if len(abilities)!=1:raise ValueError('unique GeneralRallyUnits ability not supplied by engine')
            self.ability=abilities[0]
            if not callable(getattr(backend,'path_distances',None)):raise ValueError('backend lacks ground reachability query')
        except (ValueError,AttributeError) as error:
            self.disabled=str(error);self.state=dict(state='unavailable',reason=self.disabled)
            self.event('defensive_rally_unavailable',**self.state)

    def public(self):
        return dict(self.state,policy='automatic production rally only; existing missions unchanged',
                    pending_buildings=sorted(self.pending),confirmed_buildings=sorted(self.confirmed),
                    blocked_buildings={tag:count for tag,count in self.failures.items() if count>=3},
                    scope='barracks/factory/starport, gateway/robotics/stargate, hatchery/lair/hive unit rallies; no worker rally, warp-in or mobile-unit override')

    def tick(self,w,busy=(),*,tactical_point=None,tactical_control=False):
        if self.disabled:return []
        out=[]
        for tag,p in list(self.pending.items()):
            building=w.by_tag(tag)
            failure=next((e for e in w.errors if e[0]==tag and self.backend.tech_tree.canonical_ability(e[1])==self.backend.tech_tree.canonical_ability(self.ability)),None)
            if building is None:
                if tag in w.dead_units:self.pending.pop(tag);self.confirmed.pop(tag,None)
                continue
            if failure:
                self.event('defensive_rally_rejected',building_tag=tag,error=list(failure))
            elif any(math.dist(target[:2],p['pos'])<1 for target in building.rally_targets):
                self.confirmed[tag]=p['pos'];self.pending.pop(tag);self.failures.pop(tag,None)
                self.event('defensive_rally_confirmed',building_tag=tag,pos=list(p['pos']))
                continue
            elif w.seconds-p['sent']<3:continue
            else:self.event('defensive_rally_unconfirmed',building_tag=tag,pos=list(p['pos']),timeout_game_s=3)
            self.pending.pop(tag);self.failures[tag]=self.failures.get(tag,0)+1
        changed=tactical_control and (self.state.get('pos')!=(list(tactical_point) if tactical_point else None))
        if w.seconds<self.next_at and not changed:return out
        self.next_at=w.seconds+4
        producers=[u for u in w.my_units if u.type_id in PRODUCERS and u.done and not u.flying]
        if not producers:return out
        bases=[u for u in w.my_units if u.type_id in TOWNHALLS and not u.flying and u.progress>=.25]
        if not bases:
            self._state(dict(state='waiting',reason='no grounded owned base'));return out
        home=self.backend.homes[self.index];enemy=self.backend.enemy_start(self.index)
        def score(b):
            threat=sum(e.distance(b)<22 and e.ground_dps>0 for e in w.enemy_units)
            return (bool(threat),threat,b.distance(home)>15,-b.distance(enemy))
        base=max(bases,key=score);expansion=base.distance(home)>15
        try:
            if tactical_control:
                if tactical_point is None:raise ValueError('no safe tactical assembly point; new units are intercepted by army tactics')
                kind='safe_assembly';point=tactical_point
            else:
                kind,candidates,source=self.grid.candidates(base.pos,enemy,expansion,w.units)
                distances=self.backend.path_distances(self.index,(source[0]+.5,source[1]+.5),candidates)
                point=next((p for p,d in zip(candidates,distances) if d>0 or math.dist(p,(source[0]+.5,source[1]+.5))<1),None)
            if point is None:raise ValueError('engine reports entrance unreachable; no rally change issued')
        except ValueError as error:
            self._state(dict(state='waiting',reason=str(error)));return out
        self._state(dict(state='active',landmark=kind,pos=list(point),base_tag=base.tag,
                         evidence='own base + static path/height + engine ground query'))
        eligible=[u for u in producers if u.tag not in busy and u.tag not in self.pending
                  and self.failures.get(u.tag,0)<3 and (u.tag not in self.confirmed or math.dist(self.confirmed[u.tag],point)>2)]
        if tactical_control:
            reachable=[]
            for u in eligible:
                try:
                    distances=self.backend.path_distances(self.index,u.pos,[point])
                    if distances and distances[0]>0:reachable.append(u)
                except Exception as error:
                    self.event('defensive_rally_unavailable',building_tag=u.tag,reason=str(error))
            eligible=reachable
        if eligible:
            available=self.backend.available_abilities(self.index,[u.tag for u in eligible],ignore_resources=True)
            for u in eligible:
                if not self.backend.tech_tree.ability_available(self.ability,available.get(u.tag,set())):
                    self.event('defensive_rally_ability_unavailable',building_tag=u.tag,unit_type=u.type_id)
                    self.failures[u.tag]=self.failures.get(u.tag,0)+1;continue
                out.append(command(self.ability,[u.tag],pos=point))
                self.pending[u.tag]=dict(pos=point,sent=w.seconds)
                self.event('defensive_rally_requested',building_tag=u.tag,pos=list(point),landmark=kind)
        return out

    def _state(self,state):
        if state!=self.state:
            self.state=state;self.event('defensive_rally_anchor',**state)
