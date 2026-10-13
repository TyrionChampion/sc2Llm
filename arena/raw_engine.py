"""SC2 4.10 realtime backend. Native tags/world coordinates; no screen clicks.

Only this adapter accesses pysc2's private controllers. Failure is explicit;
there is no fallback to screen actions or non-realtime simulation.
"""
from dataclasses import dataclass, field, replace
import math
import time
from pathlib import Path
from collections import Counter
from copy import deepcopy
from pysc2.env import sc2_env
from pysc2.lib import features
from s2clientprotocol import sc2api_pb2 as pb, query_pb2 as query
from s2clientprotocol import error_pb2

LOOPS_PER_SECOND = 22.4  # pysc2 SC2Env.REALTIME_GAME_LOOP_SECONDS
SELF, ENEMY, NEUTRAL = 1, 4, 3
SCV, MARINE, CC, DEPOT, BARRACKS = 45, 48, 18, 19, 21
TRAIN_SCV, TRAIN_MARINE, BUILD_DEPOT, BUILD_BARRACKS = 524, 560, 319, 321
MOVE, ATTACK, GATHER = 3794, 3674, 3666
NAMES = {SCV:'SCV', MARINE:'Marine', CC:'CommandCenter', DEPOT:'SupplyDepot', BARRACKS:'Barracks',33:'SiegeTank',32:'SiegeTankSieged'}


def command(ability, tags, pos=None, target=None, queued=False):
    a = pb.Action()
    c = a.action_raw.unit_command
    c.ability_id = ability
    c.queue_command = queued
    c.unit_tags.extend(tags)
    if pos is not None:
        c.target_world_space_pos.x, c.target_world_space_pos.y = pos
    if target is not None:
        c.target_unit_tag = target
    return a


def camera(pos):
    a = pb.Action()
    a.action_raw.camera_move.center_world_space.x = pos[0]
    a.action_raw.camera_move.center_world_space.y = pos[1]
    return a


@dataclass(frozen=True)
class Entity:
    tag: int
    type_id: int
    alliance: int
    x: float
    y: float
    health: float
    health_max: float
    progress: float = 1.0
    orders: tuple = ()
    is_building: bool = False
    mineral: bool = False
    on_screen: bool = False
    flying: bool = False
    ground_dps: float = 0.0
    air_dps: float = 0.0
    attack_range: float = 0.0
    buffs: tuple = ()
    order_targets: tuple = ()
    engaged_target: int = 0
    order_positions: tuple = ()
    hallucination: bool = False
    energy: float = 0
    energy_max: float = 0
    gas_building: bool = False
    geyser: bool = False
    addon_tag: int = 0
    powered: bool = True
    assigned_harvesters: int = 0
    ideal_harvesters: int = 0
    mineral_contents: int = 0
    vespene_contents: int = 0
    radius: float = 0
    cargo_taken: int = 0
    cargo_max: int = 0
    passengers: tuple = ()
    rally_targets: tuple = ()

    @property
    def pos(self): return (self.x, self.y)
    @property
    def done(self): return self.progress >= 1
    def distance(self, other):
        pos = other.pos if isinstance(other, Entity) else other
        return math.hypot(self.x-pos[0], self.y-pos[1])
    def can_attack(self, other): return (self.air_dps if other.flying else self.ground_dps) > 0


@dataclass(frozen=True)
class Snapshot:
    game_loop: int
    minerals: int
    supply_used: int
    supply_cap: int
    units: tuple
    camera: tuple
    errors: tuple = ()
    dead_units: tuple = ()
    feedback: tuple = ()
    upgrades: tuple = ()
    vespene: int = 0
    race: str = 'terran'
    upgrade_names: tuple = ()
    effects: tuple = ()  # (effect_id, alliance, x, y, radius), current own observation only
    visibility: object = field(default=None, compare=False, repr=False)  # own raw map_state, 0 hidden/1 fog/2 visible
    resource_site_memory: tuple = ()  # locations already observed by this player, never enemy state

    @property
    def seconds(self): return self.game_loop / LOOPS_PER_SECOND
    @property
    def my_units(self): return tuple(u for u in self.units if u.alliance == SELF)
    @property
    def enemy_units(self): return tuple(u for u in self.units if u.alliance == ENEMY)
    def my(self, type_id): return [u for u in self.my_units if u.type_id == type_id]
    def by_tag(self, tag): return next((u for u in self.units if u.tag == tag), None)
    def summary(self):
        own = Counter(NAMES.get(u.type_id, str(u.type_id)) for u in self.my_units)
        enemy = Counter(NAMES.get(u.type_id, str(u.type_id)) for u in self.enemy_units)
        return {'game_s':round(self.seconds,1), 'minerals':self.minerals,
                'vespene':self.vespene,'race':self.race,
                'supply':[self.supply_used,self.supply_cap], 'own':dict(own),
                'upgrades':list(self.upgrade_names),
                'bases':[dict(type=NAMES.get(u.type_id,str(u.type_id)),tag=u.tag,
                    progress=round(u.progress,2),workers=u.assigned_harvesters,ideal_workers=u.ideal_harvesters,
                    energy=round(u.energy,1),energy_max=u.energy_max,
                    orbital_charges=int(u.energy//50) if u.type_id==132 and u.done and not u.flying else 0)
                    for u in self.my_units if u.type_id in (18,132,130,59,86,100,101)],
                'gas_buildings':[dict(type=NAMES.get(u.type_id,str(u.type_id)),tag=u.tag,
                    progress=round(u.progress,2),workers=u.assigned_harvesters,ideal_workers=u.ideal_harvesters,
                    remaining=u.vespene_contents) for u in self.my_units if u.gas_building],
                'visible_enemy':dict(enemy),'stimpack_researched':15 in self.upgrades}


class GameClock:
    """Exclude launch/reset; distinguish displayed frames from game speed."""
    def __init__(self, loop, expected_loops_per_second=LOOPS_PER_SECOND):
        if expected_loops_per_second not in (9.6,16.0,22.4):raise ValueError('unsupported expected game speed')
        self.expected_loops_per_second=expected_loops_per_second
        self.start = time.monotonic()
        self.loop = loop
        self.frames = 0
        self.last = loop
        self.skipped_loops = 0
    def update(self, loop):
        if loop < self.last: raise RuntimeError('game_loop moved backwards')
        self.frames += 1
        self.skipped_loops += max(0, loop-self.last-1)
        self.last = loop
    def report(self):
        wall = time.monotonic()-self.start
        game = (self.last-self.loop)/LOOPS_PER_SECOND
        ratio=game/wall if wall else 0
        expected_ratio=self.expected_loops_per_second/LOOPS_PER_SECOND
        return {'wall_s':wall, 'game_s':game, 'game_wall_ratio':ratio,
                'expected_game_wall_ratio':expected_ratio,
                'loops_per_wall_second':(self.last-self.loop)/wall if wall else 0,
                'expected_loops_per_second':self.expected_loops_per_second,
                'speed_check':'warming_up' if wall<10 else ('ok' if .9<=ratio/expected_ratio<=1.1 else 'DRIFT'),
                'fps':self.frames/wall if wall else 0, 'observations':self.frames,
                'skipped_observation_loops':self.skipped_loops}


_SPEED_MAP_CLASSES = {}


def headless_map_for_speed(map_name, speed):
    from arena.game_speed import map_for_speed, LOOPS_PER_SECOND as SPEEDS
    if speed not in SPEEDS:raise ValueError('unknown game speed')
    if speed=='faster':return map_name
    from pysc2 import maps
    path=map_for_speed(map_name,speed)
    key=(map_name,speed)
    if key not in _SPEED_MAP_CLASSES:
        _SPEED_MAP_CLASSES[key]=type('Arena'+map_name+speed.title(),(maps.lib.Map,),
                                    dict(directory='Arena',filename=path.stem,players=2))
    return _SPEED_MAP_CLASSES[key]()


class RawArena:
    def __init__(self, map_name='Acropolis', render=True, difficulty=None, seed=7, races=None, bot_race='terran',game_speed='faster'):
        from arena.game_speed import LOOPS_PER_SECOND as SPEEDS
        map_name=headless_map_for_speed(map_name,game_speed)
        self.game_speed=game_speed
        def interface(rgb):
            return features.AgentInterfaceFormat(
                rgb_dimensions=features.Dimensions(screen=(1024,768), minimap=256) if rgb else None,
                use_raw_units=True, send_observation_proto=True,
                action_space=sc2_env.ActionSpace.RAW)
        self.races=list(races or (['terran'] if difficulty else ['terran','terran']))
        if len(self.races)!=(1 if difficulty else 2) or any(r not in ('terran','zerg','protoss') for r in self.races):
            raise ValueError('explicit race per API player required')
        players = [sc2_env.Agent(sc2_env.Race[self.races[0]])]
        if difficulty is None:
            players.append(sc2_env.Agent(sc2_env.Race[self.races[1]]))
            interfaces = [interface(render),interface(False)]
        else:
            players.append(sc2_env.Bot(sc2_env.Race[bot_race],sc2_env.Difficulty[difficulty]))
            interfaces = [interface(render)]
        print('[engine] realtime=True native raw actions; render=%s opponent=%s seed=%s' %
              (render,difficulty or 'agent',seed),flush=True)
        self.env = sc2_env.SC2Env(map_name=map_name, players=players,
            agent_interface_format=interfaces, realtime=True, step_mul=1,
            game_steps_per_episode=0, ensure_available_actions=False,
            score_index=-1, random_seed=seed)
        try:
            if not self.env._realtime: raise RuntimeError('realtime is required')
            self.controllers = self.env._controllers
            infos = [c.game_info() for c in self.controllers]
            self.info = infos[0]
            self.size = (self.info.start_raw.map_size.x,self.info.start_raw.map_size.y)
            data=self.controllers[0].data_raw()
            self.configure_tech_tree(data,'Linux '+str(self.controllers[0].ping().game_version))
            self.last = self.env.reset()
            self.initial = self.snapshots()
            self.homes = [next(u.pos for u in w.my_units if u.type_id in (18,132,130,59,86,100,101)) for w in self.initial]
            self.configure_start_locations(infos)
            self.clock = GameClock(self.initial[0].game_loop,SPEEDS[game_speed])
            self.action_errors = 0
            self._pending_surrender = None
            self._terminal_result = None
            self._surrender_replay = None
            self.surrender_report = None
        except BaseException:
            self.env.close()
            raise
    def __enter__(self): return self
    def __exit__(self,*args): self.env.close()
    def close(self): self.env.close()
    def proto(self,index):
        getter=self.last[index].observation.get('_response_observation')
        if getter is None: raise RuntimeError('raw observation missing; screen fallback prohibited')
        return getter()
    def snapshots(self):
        result=[]
        for i in range(len(self.last)):
            response=self.proto(i); o=response.observation; units=[]
            for u in o.raw_data.units:
                # Enemy snapshots behind fog are not current targets.
                if u.alliance == ENEMY and u.display_type != 1: continue
                if u.unit_type not in self.types:
                    raise RuntimeError('unit metadata missing: %s' % u.unit_type)
                meta=self.types[u.unit_type]
                ground=air=reach=0.0
                for weapon in meta.weapons:
                    dps=weapon.damage*weapon.attacks/max(.1,weapon.speed)
                    if weapon.type in (1,3): ground+=dps
                    if weapon.type in (2,3): air+=dps
                    reach=max(reach,weapon.range)
                units.append(Entity(u.tag,u.unit_type,u.alliance,u.pos.x,u.pos.y,
                    u.health+u.shield,u.health_max+u.shield_max,u.build_progress,
                    tuple(a.ability_id for a in u.orders),8 in meta.attributes,
                    meta.has_minerals,u.is_on_screen,u.is_flying,ground,air,reach,
                    tuple(u.buff_ids),tuple(a.target_unit_tag for a in u.orders),u.engaged_target_tag,
                    tuple((a.target_world_space_pos.x,a.target_world_space_pos.y)
                          if a.HasField('target_world_space_pos') else None for a in u.orders),
                    hallucination=u.is_hallucination,energy=u.energy,energy_max=u.energy_max,gas_building=meta.has_vespene and u.alliance==SELF,
                    geyser=meta.has_vespene and u.alliance==NEUTRAL,addon_tag=u.add_on_tag,powered=u.is_powered,
                    assigned_harvesters=u.assigned_harvesters,ideal_harvesters=u.ideal_harvesters,
                    mineral_contents=u.mineral_contents,vespene_contents=u.vespene_contents,radius=u.radius,
                    cargo_taken=u.cargo_space_taken,cargo_max=u.cargo_space_max,passengers=tuple(p.tag for p in u.passengers),
                    rally_targets=tuple((r.point.x,r.point.y,r.tag) for r in u.rally_targets)))
            p=o.player_common; c=o.raw_data.player.camera
            errors=tuple((e.unit_tag,e.ability_id,e.result) for e in response.action_errors)
            result.append(Snapshot(o.game_loop,p.minerals,p.food_used,p.food_cap,
                                   tuple(units),(c.x,c.y),errors,tuple(o.raw_data.event.dead_units),
                                   upgrades=tuple(o.raw_data.player.upgrade_ids),vespene=p.vespene,race=self.races[i],
                                   effects=tuple((e.effect_id,e.alliance,pos.x,pos.y,e.radius) for e in o.raw_data.effects for pos in e.pos),
                                   visibility=features.Feature.unpack_layer(o.raw_data.map_state.visibility),
                                   upgrade_names=tuple(self.tech_tree.upgrade_names.get(uid,str(uid)) for uid in o.raw_data.player.upgrade_ids)))
        from arena.macro import remember_resource_sites
        if not hasattr(self,'_resource_site_cache'):self._resource_site_cache={}
        for i,w in enumerate(result):
            key=tuple(sorted(u.pos for u in w.units if u.mineral))
            previous=self._resource_site_cache.get(i,(None,()))
            memory=previous[1] if key==previous[0] else remember_resource_sites(w,previous[1])
            self._resource_site_cache[i]=(key,memory)
            result[i]=replace(w,resource_site_memory=memory)
        return result
    def register_action_context(self,index,bindings):
        if not hasattr(self,'_action_bindings'): self._action_bindings={}
        self._action_bindings[index]=bindings
    def attach_action_feedback(self,before,worlds,batches,response_failures=None,sent_at=None):
        from arena.feedback import describe_rejections
        bindings=getattr(self,'_action_bindings',{})
        self._action_bindings={}
        return [replace(w,feedback=describe_rejections(before[i],w,batches[i],bindings.get(i,[]),
                    response_failures[i] if response_failures is not None else (),
                    sent_at[i] if sent_at is not None else None))
                for i,w in enumerate(worlds)]
    def step(self,batches):
        if self.ended: raise RuntimeError('cannot step a finished match; implicit reset prohibited')
        if len(batches)!=len(self.last): raise ValueError('action batch/player count mismatch')
        if self.match_control_pending:
            return self._finish_surrender()
        before=self.snapshots(); sent_at=[time.time()]*len(batches)
        self.last=self.env.step([list(b) or [pb.Action()] for b in batches])
        worlds=self.snapshots(); self.clock.update(worlds[0].game_loop)
        for i,w in enumerate(worlds):
            for tag,ability,code in w.errors:
                self.action_errors+=1
                print('[action-error] P%d tag=%d ability=%d result=%s' %
                      (i+1,tag,ability,error_pb2.ActionResult.Name(code)),flush=True)
        return self.attach_action_feedback(before,worlds,batches,sent_at=sent_at)
    @property
    def ended(self): return getattr(self,'_terminal_result',None) is not None or any(t.last() for t in self.last)
    def result(self):
        if getattr(self,'_terminal_result',None) is not None:
            return self._terminal_result.copy()
        results=list(self.proto(0).player_result)
        return {r.player_id:pb.Result.Name(r.result) for r in results}
    @property
    def match_control_pending(self):
        return getattr(self,'_pending_surrender',None) is not None

    def request_surrender(self,index,reason,request_id=None):
        if type(index) is not int or not 0<=index<len(self.controllers):
            raise ValueError('invalid surrender player')
        if not isinstance(reason,str) or not reason.strip() or len(reason)>500:
            raise ValueError('surrender needs a reason of 1..500 characters')
        if self.ended or self.match_control_pending:
            raise ValueError('match ended or another surrender is pending')
        self._pending_surrender=dict(player=index+1,reason=reason.strip(),request_id=request_id,
            requested_game_loop=self.proto(index).observation.game_loop,state='requested')

    def _finish_surrender(self):
        entry=self._pending_surrender.copy();index=entry['player']-1
        worlds=self.snapshots();self.surrender_report=entry;self._action_bindings={}
        try:
            replay=self.controllers[index].save_replay()
            if not replay:raise RuntimeError('empty pre-surrender replay')
        except Exception as error:
            self._pending_surrender=None
            entry.update(state='failed',stage='save_before_leave',error=str(error),leave_sent=False)
            return [replace(w,feedback=(dict(kind='surrender_failed',**entry),)) for w in worlds]
        self._surrender_replay=replay
        try:
            response=self.controllers[index].leave()
            if response is None:raise RuntimeError('leave response missing')
        except Exception as error:
            entry.update(state='unknown',stage='leave',error=str(error),leave_sent=True)
            raise RuntimeError('surrender leave outcome uncertain: '+str(error)) from error
        expected={index+1:'Defeat',2 if index==0 else 1:'Victory'}
        observed={}
        for other,controller in enumerate(self.controllers):
            if other==index:continue
            response=controller.observe()
            observed={r.player_id:pb.Result.Name(r.result) for r in response.player_result}
            if observed and observed!=expected:
                entry.update(state='result_mismatch',engine_results=observed)
                raise RuntimeError('engine result contradicts concession: '+str(observed))
        self._terminal_result=observed or expected;self._pending_surrender=None
        entry.update(state='completed',leave_acknowledged=True,replay_scope='before_leave',
            result_source='engine_player_result' if observed else 'acknowledged_concession_rule',
            engine_results=observed or None,results=self._terminal_result,replay_bytes=len(replay))
        return [replace(w,feedback=(dict(kind='surrender_completed',**entry),)) for w in worlds]

    def save_replay(self,path):
        replay=self._surrender_replay or self.controllers[0].save_replay()
        if not replay:raise RuntimeError('empty replay')
        Path(path).write_bytes(replay)
    def rgb(self):
        rgb=self.last[0].observation.get('rgb_screen')
        if rgb is None: raise RuntimeError('P1 RGB missing; cannot display match')
        return rgb
    def available_abilities(self,index,tags,ignore_resources=False):
        q=query.RequestQuery(ignore_resource_requirements=ignore_resources)
        for tag in tags:q.abilities.add(unit_tag=tag)
        response=self.controllers[index].query(q)
        if len(response.abilities)!=len(tags):raise RuntimeError('incomplete ability query response')
        return {tag:{a.ability_id for a in result.abilities} for tag,result in zip(tags,response.abilities)}
    def path_distances(self,index,start,targets):
        request=query.RequestQuery()
        for target in targets:
            item=request.pathing.add()
            item.start_pos.x,item.start_pos.y=start
            item.end_pos.x,item.end_pos.y=target
        response=self.controllers[index].query(request)
        if len(response.pathing)!=len(targets):raise RuntimeError('incomplete ground path query response')
        return [p.distance for p in response.pathing]

    def placement(self, index, ability, worker, candidates):
        q=query.RequestQuery()
        for x,y in candidates:
            p=q.placements.add(ability_id=ability,placing_unit_tag=worker)
            p.target_pos.x=x; p.target_pos.y=y
        results=self.controllers[index].query(q).placements
        if len(results)!=len(candidates): raise RuntimeError('incomplete placement query response')
        accepted=next((xy for xy,r in zip(candidates,results) if r.result==1),None)
        failures=Counter();samples=[]
        for xy,r in zip(candidates,results):
            if r.result==1:continue
            try:name=error_pb2.ActionResult.Name(r.result)
            except ValueError:name='UnknownPlacementResult_'+str(r.result)
            failures[name]+=1
            if len(samples)<3:samples.append(dict(pos=list(xy),result=name))
        if not hasattr(self,'_placement_evidence'):self._placement_evidence={}
        self._placement_evidence[(index,ability)]=dict(source='native placement query',
            ability=ability,worker_tag=worker,candidate_count=len(candidates),failure_counts=dict(failures),
            rejection_samples=samples,accepted=list(accepted) if accepted is not None else None)
        return accepted

    def placement_evidence(self,index,ability):
        return deepcopy(getattr(self,'_placement_evidence',{}).get((index,ability)))
    def placement_with_addon(self,index,ability,worker,candidates):
        """Reserve a real 2x2 addon tile along with the production footprint.
        Addon abilities' placement queries use landing semantics in this engine;
        the SupplyDepot placement footprint is 2x2 and was tested against actual addon completion.
        """
        req=query.RequestQuery()
        for x,y in candidates:
            p=req.placements.add(ability_id=ability,placing_unit_tag=worker);p.target_pos.x=x;p.target_pos.y=y
            p=req.placements.add(ability_id=BUILD_DEPOT);p.target_pos.x=x+2.5;p.target_pos.y=y-.5
        rows=self.controllers[index].query(req).placements
        if len(rows)!=len(candidates)*2:raise RuntimeError('incomplete production/addon placement query')
        return next((pos for i,pos in enumerate(candidates) if rows[2*i].result==1 and rows[2*i+1].result==1),None)

    def configure_tech_tree(self,data,version):
        from arena.tech_tree import TechTree
        self.types={u.unit_id:u for u in data.units}
        NAMES.update({u.unit_id:u.name for u in data.units})
        self.tech_tree=TechTree(data,version)
        for item in self.tech_tree.diagnostics:print('[tech-data adjustment]',item,flush=True)
        for item in self.tech_tree.excluded:print('[tech-data unavailable]',item,flush=True)

    def configure_start_locations(self,infos):
        # SC2 supplies opponent-start candidates from EACH player's perspective.
        # Sharing P1's list makes P2 attack its own base on a two-spawn map.
        if len(infos)!=len(self.homes):
            raise RuntimeError('player/game-info count mismatch; cannot resolve enemy starts')
        locations=[]
        for index,info in enumerate(infos):
            size=(info.start_raw.map_size.x,info.start_raw.map_size.y)
            if size!=self.size:
                raise RuntimeError('P%d map size disagrees with match' % (index+1))
            points=tuple((p.x,p.y) for p in info.start_raw.start_locations)
            if any(not all(math.isfinite(v) and 0<=v<=self.size[i]
                           for i,v in enumerate(point)) for point in points):
                raise RuntimeError('P%d invalid map start coordinates' % (index+1))
            locations.append(points)
        self.map_info_by_player=tuple(infos)
        self.start_locations_by_player=tuple(locations)
        self.start_location_report=[]
        for index,home in enumerate(self.homes):
            target=self.enemy_start(index)  # Fail before any model/army dispatch.
            evidence=dict(player=index+1,home=home,
                          candidates=self.start_locations_by_player[index],
                          enemy_start=target,source='own player game_info.start_raw.start_locations')
            self.start_location_report.append(evidence)
            print('[map-target]',evidence,flush=True)

    def enemy_start(self,index):
        if type(index) is not int or not 0<=index<len(self.homes):
            raise ValueError('invalid player index for enemy_start: '+str(index))
        home=self.homes[index]
        candidates=[p for p in self.start_locations_by_player[index] if math.dist(p,home)>1]
        if not candidates:
            raise RuntimeError('P%d has no non-home start location; refusing own-base attack or another player data fallback' % (index+1))
        return max(candidates,key=lambda p:math.dist(p,home))
