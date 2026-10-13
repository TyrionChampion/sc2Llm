"""Player-local spatial facts and explicit, model-dispatched movement/recon missions.

No strategy triggers, global enemy positions, victory inference or automatic search.
"""
from collections import Counter
import math

from arena.raw_engine import MOVE, ATTACK, command, NAMES

PROTOCOL = '''
Read battlefield for map coordinates, own unit locations, current regional visibility and
last observations. match_status=ongoing only means the engine has not ended the match.
An empty currently visible area is not global enemy elimination. Unknown/fogged areas
and historical enemy sightings remain uncertain. Reconsider an old objective when its
current observations no longer justify it; you decide what information to obtain next.
If an objective appears complete while the game continues, distinguish what was actually
verified from what remains unknown; choose your next information request or mission.
You can explicitly dispatch generic missions (outer count=1):
order_units args={unit:selector,count:1..200 or "all",mode:"move|attack_move",
target:named place/site_N/[x,y],timeout_s:1..600,reason:short Chinese intent}.
scout args={unit:selector,count:1..8,targets:[named places/site_N/[x,y],...]
or "expansion_sites",timeout_s:1..600,reason:short Chinese intent}.
scout only runs after YOUR command. With expansion_sites it visits known resource
clusters without an owned base, ordered by distance from the selected squad. With
an explicit list it follows YOUR order. Both use move, report actual arrivals/current
visibility, and stop the mission at visible enemy contact; YOU decide whether to attack,
retreat or continue. Neither starts another search or transfers the main army by itself.
All selectors are listed in battlefield.unit_selectors. Units already owned by custom
skills/defense/other missions remain unavailable; requested unavailable squads wait with a
visible reason until the deadline. Ground movement may use a reachable
approach within 6 tiles when the exact point is blocked; requested/actual points are reported.
Missing points or no reachable local approach fail visibly, never substitute enemy_start.
order_units finishes on observed
arrival (not victory), may be interrupted by combat. Cancel an active scout/order_units
with cancel_unit_order args={task_id:ID}; then dispatch a new task. A known owned mission
that already ended is a reported no-op, with no stop/state rewrite; unknown IDs are rejected. Read active_tasks,
spatial_reports (last 8 terminal missions, independent of feedback pagination) and
spatial mission feedback. Coordinates are allowed for these spatial tasks, scan and rallies;
never invent enemy unit tags. This contract does not change Python/JSON skill target enums.
'''


def selectors():
    from arena.python_skills import KINDS
    return KINDS


def task_schemas(payload):
    from arena.rally import RALLY_NAMES
    def obj(properties, required):
        return dict(type='object', properties=properties, required=required, additionalProperties=False)
    names = sorted(RALLY_NAMES | set(payload.get('rally_landmarks', [])) | {
        a['id'] for a in payload.get('battlefield', {}).get('areas', []) if not a['id'].startswith('region_')})
    target = {'anyOf': [{'type': 'string', 'enum': names},
        {'type': 'array', 'minItems': 2, 'maxItems': 2, 'items': {'type': 'number'}}]}
    common = dict(unit={'type': 'string', 'enum': sorted(selectors())},
        reason={'type': 'string', 'minLength': 1, 'maxLength': 200},
        timeout_s={'type': 'number', 'minimum': 1, 'maximum': 600})
    specs = {
        'order_units': obj(dict(common, count={'anyOf': [{'type': 'integer', 'minimum': 1, 'maximum': 200},
            {'type': 'string', 'enum': ['all']}]}, mode={'type': 'string', 'enum': ['move', 'attack_move']},
            target=target), ['unit', 'count', 'reason', 'mode', 'target']),
        'scout': obj(dict(common, count={'type': 'integer', 'minimum': 1, 'maximum': 8},
            targets={'anyOf': [{'type': 'string', 'enum': ['expansion_sites']},
                {'type': 'array', 'minItems': 1, 'maxItems': 16, 'items': target}]}),
            ['unit', 'count', 'reason', 'targets']),
        'cancel_unit_order': obj({'task_id': {'type': 'integer', 'minimum': 1}}, ['task_id'])}
    return [obj({'skill': {'type': 'string', 'enum': [name]},
        'count': {'type': 'integer', 'enum': [1]}, 'args': args}, ['skill', 'args'])
        for name, args in specs.items()]


def validate_target(value, size):
    if isinstance(value, str):
        from arena.rally import RALLY_NAMES
        if value in RALLY_NAMES or (value.startswith('site_') and value[5:].isdigit()): return value
        raise ValueError('target must be an existing named place/site_N or [x,y]')
    if not isinstance(value, (list, tuple)) or len(value) != 2 or any(
            type(x) not in (int, float) or not math.isfinite(x) for x in value):
        raise ValueError('target requires finite [x,y]')
    if not all(0 <= value[i] < size[i] for i in (0, 1)):
        raise ValueError('target outside map; not clamped or replaced')
    return [float(x) for x in value]


def validate_args(skill, args, size):
    required = {'unit', 'count', 'reason', 'target', 'mode'} if skill == 'order_units' else {'unit', 'count', 'reason', 'targets'}
    if not required <= set(args) or set(args) - required - {'timeout_s'}:
        raise ValueError('required fields: ' + str(sorted(required)) + '; optional timeout_s')
    if not isinstance(args['unit'], str) or args['unit'] not in selectors(): raise ValueError('unknown unit selector')
    count = args['count']
    if not ((skill == 'order_units' and count == 'all') or (type(count) is int and 1 <= count <= (200 if skill == 'order_units' else 8))):
        raise ValueError('invalid mission squad count')
    if not isinstance(args['reason'], str) or not 1 <= len(args['reason'].strip()) <= 200:
        raise ValueError('reason requires 1..200 chars')
    timeout = args.get('timeout_s', 180)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 600:
        raise ValueError('timeout_s requires 1..600 game seconds')
    result = dict(args, timeout_s=timeout)
    if skill == 'order_units':
        if args['mode'] not in ('move', 'attack_move'): raise ValueError('mode must be move or attack_move')
        result['target'] = validate_target(args['target'], size)
    else:
        targets = args['targets']
        if targets != 'expansion_sites':
            if not isinstance(targets, list) or not 1 <= len(targets) <= 16: raise ValueError('targets requires 1..16 points or expansion_sites')
            result['targets'] = [validate_target(v, size) for v in targets]
    return result


def resource_sites(w):
    from arena.macro import expansion_clusters
    memory=getattr(w,'resource_site_memory',())
    return ({name:(sum(p[0] for p in points)/len(points),sum(p[1] for p in points)/len(points))
             for name,points in memory} if memory else
            {name: (sum(u.x for u in group) / len(group), sum(u.y for u in group) / len(group))
             for name, group in expansion_clusters(w)})


def resolve_target(value, w, q):
    if isinstance(value, (list, tuple)): return tuple(value)
    if value.startswith('site_'):
        sites = resource_sites(w)
        if value not in sites: raise ValueError('resource site no longer present in own observation: ' + value)
        return sites[value]
    from arena.rally import named_rally_point
    point, _ = named_rally_point(q._rally_grid(), q.backend, q.index, value, w)
    return point


def area_visibility(w, point, radius=6):
    layer = w.visibility
    if layer is None:
        return dict(state='unavailable', reason='own raw visibility layer absent; no inferred clear area')
    x, y = point; h, width = layer.shape
    x0, x1 = max(0, int(x-radius)), min(width, int(x+radius)+1)
    y0, y1 = max(0, int(y-radius)), min(h, int(y+radius)+1)
    cells = layer[y0:y1, x0:x1]
    if not cells.size: return dict(state='unavailable', reason='point outside visibility layer')
    fraction = float((cells == 2).sum()) / cells.size
    return dict(state='fully_visible' if fraction == 1 else 'partly_visible' if fraction > 0 else 'fog_or_unexplored',
        visible_fraction=round(fraction, 3), previously_seen_fraction=round(float((cells > 0).sum()) / cells.size, 3))


def ground_destination(w, q, units, requested):
    """Bounded physical approach, including occupied/fogged destinations. No new goal."""
    grid=q._rally_grid()
    if grid is None:raise ValueError('ground topology unavailable; no blind dispatch')
    query=getattr(q.backend,'path_distances',None)
    if not callable(query):raise ValueError('ground path query unavailable; no blind dispatch')
    point=tuple(v+.5 for v in grid.nearest(requested))
    def distances(u,points):
        result=query(q.index,u.pos,points)
        if len(result)!=len(points):raise ValueError('path query returned incomplete distances')
        return result
    ground=[u for u in units if not u.flying]
    if all(u.distance(point)<=1 or distances(u,[point])[0]>0 for u in ground):return point
    candidates=set()
    for radius in (2,4,6):
        for dx,dy in ((radius,0),(-radius,0),(0,radius),(0,-radius),
                      (radius,radius),(radius,-radius),(-radius,radius),(-radius,-radius)):
            x,y=int(requested[0]+dx),int(requested[1]+dy)
            candidate=(x+.5,y+.5)
            if grid.free(x,y) and math.dist(candidate,requested)<=6.75:candidates.add(candidate)
    candidates=sorted(candidates,key=lambda p:(math.dist(p,requested),p))
    if not candidates:raise ValueError('ground destination unreachable; no nearby walkable approach')
    possible=set(range(len(candidates)))
    for u in ground:
        ds=distances(u,candidates)
        possible&={i for i,p in enumerate(candidates) if ds[i]>0 or u.distance(p)<=1}
    if not possible:raise ValueError('ground destination unreachable; no local approach for allocated squad')
    return candidates[min(possible)]


class Battlefield:
    def __init__(self, backend, index):
        self.backend, self.index = backend, index
        self.last_seen = {}
        self.points = {}
        self.last_loop = -1
        self.evicted_observations = 0
        self.site_key=None
        self.site_positions={}
        self.previous_health = {}
        self.recent_damage = []
        self.terrain_grid = None
        self.terrain_checked = False

    def update(self, w):
        if w.game_loop <= self.last_loop: return
        self.last_loop = w.game_loop
        for u in w.my_units:
            previous = self.previous_health.get(u.tag)
            if previous and previous[0] == u.type_id and previous[1] > u.health:
                self.recent_damage.append(dict(tag=u.tag, unit=NAMES.get(u.type_id, str(u.type_id)),
                    pos=[round(u.x, 1), round(u.y, 1)], game_s=round(w.seconds, 2),
                    health_lost=round(previous[1]-u.health, 2)))
        self.previous_health = {u.tag: (u.type_id, u.health) for u in w.my_units}
        self.recent_damage = [d for d in self.recent_damage if w.seconds-d['game_s'] <= 30][-16:]
        # Clustering is quadratic in mineral count. Coordinates change only when
        # the actual observed resource set changes; visibility still updates each frame.
        site_key=tuple(sorted((u.tag,u.x,u.y) for u in w.units if u.mineral))
        if site_key!=self.site_key:
            self.site_positions=resource_sites(w)
            self.site_key=site_key
        self.points = dict(self.site_positions)
        self.points.update(home=self.backend.homes[self.index], enemy_start=self.backend.enemy_start(self.index))
        # Bounded map regions: no recommendation or inferred enemy occupancy.
        sx, sy = self.backend.size
        spacing = max(24, math.ceil(max(sx, sy) / 16))
        for x in range(0, int(sx), spacing):
            for y in range(0, int(sy), spacing):
                self.points['region_%d_%d' % (x, y)] = (min(sx-.5, x+spacing/2), min(sy-.5, y+spacing/2))
        for name, point in self.points.items():
            vis = area_visibility(w, point)
            if vis['state'] == 'fully_visible': self.last_seen[name] = w.seconds
        if len(self.last_seen)>1024:
            self.evicted_observations += len(self.last_seen)-1024
            self.last_seen = dict(sorted(self.last_seen.items(),key=lambda item:item[1],reverse=True)[:1024])

    def terrain_public(self, w):
        """Static nearby elevation and own damage; never locate an unseen attacker."""
        if not self.terrain_checked:
            self.terrain_checked = True
            self.terrain_grid = getattr(self.backend, 'grid', None)
            if self.terrain_grid is None:
                infos = getattr(self.backend, 'map_info_by_player', None)
                if infos:
                    try:
                        from arena.rally import GroundMap
                        self.terrain_grid = GroundMap(infos[self.index].start_raw)
                    except (ValueError, AttributeError, TypeError, IndexError):
                        pass
        grid = self.terrain_grid
        facts = dict(available=grid is not None, recent_own_damage=list(self.recent_damage),
                     nearby_higher_ground=[], combat_contacts=[])
        if grid is None:
            return facts
        from arena.tactical_facts import combat_contacts
        facts['combat_contacts']=combat_contacts(w,grid,self.recent_damage)
        # Bounded samples around own combat locations, with no enemy query or recommendation.
        anchors = {}
        for u in w.my_units:
            if not u.flying and not u.is_building and u.ground_dps > 0:
                anchors.setdefault((int(u.x//8), int(u.y//8)), u.pos)
        candidates = {}
        for pos in list(anchors.values())[:16]:
            x, y = map(int, pos)
            if not (0 <= x < grid.w and 0 <= y < grid.h):
                continue
            height = int(grid.height[y, x])
            for radius in (4, 8, 12):
                for dx, dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)):
                    a, b = x+dx*radius, y+dy*radius
                    if not grid.free(a, b) or int(grid.height[b, a]) <= height+4:
                        continue
                    visibility = (int(w.visibility[b, a]) if w.visibility is not None
                                  and b < w.visibility.shape[0] and a < w.visibility.shape[1] else None)
                    candidates.setdefault((a//8, b//8), dict(pos=[a+.5, b+.5],
                        height=int(grid.height[b, a]), own_reference_pos=list(pos),
                        own_reference_height=height, visibility=visibility))
        facts['nearby_higher_ground'] = list(candidates.values())[:12]
        facts['meaning'] = ('Static height samples near own ground combat units; higher is relative, '
            'not proof of an attacker. visibility: 0 unexplored, 1 fog, 2 currently visible, null unavailable. '
            'Health loss does not identify its cause. Samples are incomplete, not full terrain coverage.')
        return facts

    def public(self, w):
        self.update(w)
        groups = {}
        for u in w.my_units:
            if u.is_building: continue
            key = (NAMES.get(u.type_id, str(u.type_id)), int(u.x//12), int(u.y//12))
            row = groups.setdefault(key, dict(unit=key[0], count=0, center=[0, 0], idle=0))
            row['count'] += 1; row['center'][0] += u.x; row['center'][1] += u.y; row['idle'] += not bool(u.orders)
        for row in groups.values(): row['center'] = [round(v/row['count'], 1) for v in row['center']]
        groups = sorted(groups.values(), key=lambda g: (-g['count'], g['unit'], g['center']))
        records = []; regions = []
        for name, point in sorted(self.points.items(), key=lambda p: (p[0].startswith('region_'), p[0])):
            near = [u for u in w.enemy_units if u.distance(point) <= 10]
            if name.startswith('region_'):
                vis=area_visibility(w,point)
                regions.append([round(v,1) for v in point]+[vis.get('visible_fraction'),vis.get('previously_seen_fraction'),self.last_seen.get(name)])
                continue
            records.append(dict(id=name, center=[round(v, 1) for v in point], radius=6,
                enemy_count_radius=10,
                **area_visibility(w, point), last_fully_visible_game_s=self.last_seen.get(name),
                visible_enemy_buildings=sum(u.is_building for u in near),
                visible_enemy_units=sum(not u.is_building for u in near),
                own_units_near=sum(u.distance(point) <= 10 for u in w.my_units)))
        limit = 288
        return dict(match_status='ended' if getattr(self.backend, 'ended', False) else 'ongoing',
            map_size=list(self.backend.size), areas=records[:limit], omitted_areas=max(0,len(records)-limit),
            regions=regions[:256], region_format=['x','y','currently_visible_fraction','ever_seen_fraction','last_fully_visible_game_s'],
            region_radius=6,
            visibility_available=w.visibility is not None,
            terrain_context=self.terrain_public(w),
            evicted_observation_records=self.evicted_observations,
            own_unit_groups=groups[:64], omitted_unit_groups=max(0,len(groups)-64),
            unit_selectors=sorted(selectors()),
            meaning='Static coordinates + OWN observation only. No enemy occupancy behind fog. '
                'Visible coverage is the square radius shown, not the entire base. Empty visible area '
                'does not exclude cloaked units or buildings elsewhere. Model chooses goals; no automatic search.')


class SpatialMission:
    def __init__(self, skill, args):
        self.skill, self.args = skill, dict(args)
        self.tags = set(); self.route = None; self.step = 0; self.sent = -999; self.issued = False
        self.arrivals = []; self.started = None; self.last_pos = None; self.changed_at = None
        self.stop_tags=set();self.stance_pending={}
        self.contacts=[];self.contact_omitted=0;self.allocated_count=0
        self.requested_route=None
        self.engaged_target_health={}

    def close(self): self.stop_tags=set(self.tags); self.tags.clear()
    def summary(self):
        return dict(kind='spatial_mission', skill=self.skill, args=self.args, unit_tags=sorted(self.tags),
            route=[list(p) for p in self.route] if self.route is not None else None,
            requested_route=[list(p) for p in self.requested_route] if self.requested_route is not None else None,
            route_index=self.step, visited=list(self.arrivals), strategic_decision='explicit LLM dispatch only')

    def result(self,t):
        return dict(task_id=t.id,skill=t.skill,state=t.status,reason=t.reason,
            args=self.args,allocated_count=self.allocated_count,arrivals=self.arrivals[-4:],
            omitted_arrivals=max(0,len(self.arrivals)-4),contacts=self.contacts,
            omitted_contacts=self.contact_omitted,
            meaning='Historical OWN observations at recorded times, not current occupancy or victory.')

    def stop_orders(self, w, q, t, out):
        tags = [u.tag for u in w.my_units if u.tag in (self.tags|self.stop_tags) and u.tag not in q.defense_tags]
        if tags: q.issue(t, command(4, tags), out)

    def finish(self, w, q, t, out, state, reason, stop=False):
        if stop: self.stop_orders(w, q, t, out)
        q.event('spatial_mission_report', task_id=t.id, skill=self.skill, state=state, reason=reason, report=self.summary())
        q.update(t, state, reason)

    def tick(self, w, q, t, used, bank, out):
        try: self.advance(w, q, t, used, out)
        except Exception as error:
            import traceback
            squad=[u for u in w.my_units if u.tag in self.tags]
            engaged={u.engaged_target for u in squad if u.engaged_target}
            q.event('spatial_mission_error',task_id=t.id,skill=self.skill,
                error=type(error).__name__+': '+str(error),traceback=traceback.format_exc(),
                observed_squad=[dict(tag=u.tag,pos=list(u.pos),health=u.health,
                    engaged_target=u.engaged_target,orders=list(u.orders),radius=u.radius,
                    attack_range=u.attack_range) for u in squad],
                visible_engaged_targets=[dict(tag=u.tag,unit=NAMES.get(u.type_id,str(u.type_id)),
                    pos=list(u.pos),health=u.health,radius=u.radius) for u in w.enemy_units if u.tag in engaged],
                observation_scope='Current own raw snapshot; engaged target is not proof of damage or killer attribution')
            self.finish(w, q, t, out, 'failed', type(error).__name__+': '+str(error), stop=True)
        return bank

    def advance(self, w, q, t, used, out):
        if w.seconds-t.created > self.args['timeout_s']:
            self.finish(w, q, t, out, 'failed', 'mission game-time deadline; fresh model decision required', stop=True); return
        if self.route is None:
            from arena.python_skills import KINDS
            if self.skill=='order_units' and self.args['count']=='all' and any(
                    u.tag in getattr(q,'reserved_for_cancel',set()) and not u.is_building
                    and u.done and u.type_id in KINDS[self.args['unit']] for u in w.my_units):
                q.update(t,'waiting','awaiting explicit defense release stop before binding all free units')
                return
            targets = self.args.get('targets', [self.args.get('target')])
            if targets == 'expansion_sites':
                from arena.tech_tree import TOWNHALLS
                own_bases = [u for u in w.my_units if u.type_id in TOWNHALLS]
                self.route = [p for p in resource_sites(w).values() if not any(u.distance(p) < 15 for u in own_bases)]
                if not self.route: raise ValueError('no observed resource clusters without owned base')
                if len(self.route)>64:raise ValueError('more than 64 sites; model must supply a bounded explicit route')
            else:
                self.route = [resolve_target(v, w, q) for v in targets]
            candidates = [u for u in w.my_units if not u.is_building and u.type_id in KINDS[self.args['unit']]
                          and u.done and u.tag not in used and u.tag not in q.protected()]
            candidates.sort(key=lambda u: (u.distance(self.route[0]), u.tag))
            required = len(candidates) if self.args['count'] == 'all' else self.args['count']
            if not required or len(candidates) < required:
                self.route = None
                q.update(t, 'waiting', 'requested free squad unavailable; no substitute unit'); return
            chosen = candidates[:required]
            self.tags = {u.tag for u in chosen}; used.update(self.tags)
            self.allocated_count=len(chosen)
            if targets == 'expansion_sites':
                remaining = self.route; self.route = []; here = chosen[0].pos
                while remaining:
                    point = min(remaining, key=lambda p: (math.dist(here,p),p)); remaining.remove(point)
                    self.route.append(point); here = point
            self.started = w.seconds
            self.requested_route=list(self.route)
            q.event('spatial_mission_started', task_id=t.id, skill=self.skill, report=self.summary())
        # A confirmed loss releases only that member. The surviving allocated units
        # keep the explicit mission and their existing engine orders; absence from
        # the observation alone is still handled by the waiting path below.
        self.tags.difference_update(w.dead_units)
        units = [u for u in w.my_units if u.tag in self.tags]
        if not self.tags:
            self.finish(w, q, t, out, 'failed', 'all allocated units confirmed dead; no replacement', stop=False)
            return
        if not units:
            q.update(t, 'waiting', 'allocated units not observed (possibly transported); no replacement'); return
        if len(units)!=len(self.tags):
            q.update(t,'waiting','part of allocated squad not observed; no replacement or partial dispatch');return
        if self.skill == 'scout':
            contacts = [e for e in w.enemy_units if any(u.distance(e) <= 15 for u in units)]
            if contacts:
                self.contacts=[dict(unit=NAMES.get(e.type_id,str(e.type_id)),pos=list(e.pos),game_s=round(w.seconds,2)) for e in contacts[:16]]
                self.contact_omitted=max(0,len(contacts)-16)
                q.event('scout_contact', task_id=t.id, contacts=self.contacts,
                        omitted=self.contact_omitted, handling='mission stops; LLM decides attack/retreat/continue; no strategic takeover')
                self.finish(w,q,t,out,'completed','visible enemy contact reported; awaiting model decision',stop=True); return
        point = self.route[self.step]
        exact_arrival=all(u.distance(point)<=4 for u in units)
        center=tuple(sum(getattr(u,k) for u in units)/len(units) for k in ('x','y'))
        # A move squad can settle around a crowded local destination. Require the
        # whole formation nearby, its center inside the original arrival radius,
        # and every outer member idle. Never extend attack/scout arrival or treat
        # distant, engaged, or still-moving outer members as having arrived.
        settled_formation=(self.skill=='order_units' and self.args.get('mode')=='move'
            and len(units)>1 and math.dist(center,point)<=4
            and all(u.distance(point)<=4 or (not u.orders and not u.engaged_target
                    and u.distance(point)<=6+u.radius) for u in units))
        if (exact_arrival or settled_formation) and len(units) == len(self.tags):
            row = dict(center=list(point), game_s=round(w.seconds,2), **area_visibility(w,point))
            if not exact_arrival:
                row['settled_formation']=dict(observed_center=list(center),units=len(units),
                    max_center_distance=max(u.distance(point) for u in units),
                    scope='Explicit move only; outer members idle within local approach plus own radius')
            self.arrivals.append(row)
            q.event('spatial_arrival', task_id=t.id, skill=self.skill, observation=row)
            self.step += 1; self.issued = False
            if self.step == len(self.route):
                self.finish(w,q,t,out,'completed','observed route arrivals; not victory or proof every area is clear'); return
            point = self.route[self.step]
        if self.issued:
            errors = [(tag,a,code) for tag,a,code in w.errors if tag in self.tags and a in (MOVE,ATTACK)]
            if errors: raise ValueError('engine rejected spatial order: '+str(errors))
            center = tuple(sum(getattr(u,k) for u in units)/len(units) for k in ('x','y'))
            if self.last_pos is None or math.dist(self.last_pos,center) >= 1:
                self.last_pos = center; self.changed_at = w.seconds
            visible = {e.tag: e for e in w.enemy_units}
            current_health = {}
            for u in units:
                target = visible.get(u.engaged_target)
                if (target is None or not u.can_attack(target) or
                    u.distance(target) > u.attack_range + u.radius + target.radius + .1):
                    continue
                current_health[target.tag] = target.health
            if any(tag in self.engaged_target_health and health < self.engaged_target_health[tag]
                   for tag, health in current_health.items()):
                self.changed_at = w.seconds
            self.engaged_target_health = current_health
            if self.changed_at is not None and w.seconds-self.changed_at > 20:
                raise ValueError('no observed movement progress for 20 game seconds; combat/obstruction cause unknown')
            return
        if any(not u.flying for u in units):
            requested=self.requested_route[self.step]
            point = ground_destination(w,q,units,requested); self.route[self.step] = point
            if point != requested:
                q.event('spatial_target_resolved',task_id=t.id,requested=list(requested),actual=list(point),
                    reason='walkable tile/local reachable approach; no strategic target substitution')
        mode = self.args.get('mode','move')
        # Mechanical stance change required by an explicit movement order, not strategy.
        from arena.combat_micro import UNSIEGE
        if q.tech_tree is None:raise ValueError('spatial mission requires engine ability catalogue')
        sieged=[u for u in units if u.type_id==32]
        if sieged:
            available=q.backend.available_abilities(q.index,[u.tag for u in sieged])
            for u in sieged:
                if u.tag in self.stance_pending:
                    if w.seconds-self.stance_pending[u.tag]>8:raise ValueError('tank unsiege not observed in 8 game seconds')
                    continue
                if not q.tech_tree.ability_available(UNSIEGE,available.get(u.tag,set())):raise ValueError('tank cannot currently unsiege')
                if q.issue(t,command(UNSIEGE,[u.tag]),out):self.stance_pending[u.tag]=w.seconds
            q.update(t,'waiting','explicit movement awaits observed tank unsiege');return
        ability=ATTACK if mode=='attack_move' else MOVE
        available=q.backend.available_abilities(q.index,sorted(self.tags))
        if q.tech_tree is None:raise ValueError('spatial mission requires engine ability catalogue')
        if any(not q.tech_tree.ability_available(ability,available.get(tag,set())) for tag in self.tags):
            raise ValueError('requested movement ability unavailable for allocated squad')
        if q.issue(t,command(ability,sorted(self.tags),pos=point),out):
            self.issued = True; self.sent = w.seconds; self.last_pos = None; self.changed_at = w.seconds
            self.engaged_target_health = {e.tag: e.health for e in w.enemy_units
                if any(u.engaged_target == e.tag and u.can_attack(e) and
                       u.distance(e) <= u.attack_range + u.radius + e.radius + .1
                       for u in units)}
            q.update(t,'running','movement dispatched; waiting for actual arrival/contact')
