"""Bounded Go decision + real SC2 tactical scenarios; debug fixtures, not ladder wins.

Run: ./run_sc2.sh .venv/bin/python verify_tactical_scenarios_live.py --scenario all
Use --prepare-only to validate geometry/visibility without any model request.
Every invocation gets a new evidence directory. Original campaign ledgers are read-only.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import tempfile
import time
import traceback

from absl import flags
from s2clientprotocol import debug_pb2 as debug

from arena.autonomous_bot import BotPlanner, RequestBudget, match_time_policy
from arena.commander import Commander
from arena.go_planner import GO_MODEL, GoPlanner
from arena.raw_engine import RawArena, command
from arena.rally import GroundMap
from arena.tactical_scenarios import SCENARIOS, evaluate, safe_rally, record_scan

TAKEOVER_STRATEGY = '''从当前构造的局面接手，依据己方观测自主决定战术。
这是受控中盘布场，engine game_s表示布场时钟，不代表标准开局的进度。
使用现有任务接口并填写完整参数，结果以实际观察为准。'''


def write(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
    tmp.replace(path)


class RecordedBudget(RequestBudget):
    def __init__(self, limit, path):
        super().__init__(limit)
        self.path = path
        write(path, self.report())

    def reserve(self, role):
        super().reserve(role)
        write(self.path, self.report())


class Fixture:
    def __init__(self, arena):
        self.arena = arena
        self.worlds = arena.initial
        self.grid = GroundMap(arena.map_info_by_player[0].start_raw)
        self.history = []
        self.prerequisites = {}
        self.layout = {}

    def advance(self, game_s=.25, actions=None):
        until = self.worlds[0].seconds+game_s
        deadline = time.monotonic()+max(10, game_s*4)
        while self.worlds[0].seconds < until and time.monotonic() < deadline:
            self.worlds = self.arena.step(actions or [[], []])
            actions = None
            time.sleep(.02)
        return self.worlds[0]

    def spawn(self, kind, owner, pos, count=1):
        before = {u.tag for u in self.worlds[owner-1].my_units}
        self.arena.controllers[0].debug([debug.DebugCommand(create_unit=debug.DebugCreateUnit(
            unit_type=kind, owner=owner, quantity=count,
            pos=dict(x=pos[0], y=pos[1])))])
        self.advance()
        units = [u for u in self.worlds[owner-1].my_units if u.tag not in before and u.type_id == kind]
        if len(units) != count:
            raise ValueError('Fixture spawn failed: type=%s owner=%s count=%s observed=%s' %
                             (kind, owner, count, len(units)))
        return units

    def values(self, units, field, value):
        self.arena.controllers[0].debug([debug.DebugCommand(unit_value=debug.DebugSetUnitValue(
            unit_value=field, value=value, unit_tag=u.tag)) for u in units])
        self.advance()

    def point(self, pos):
        return tuple(v+.5 for v in self.grid.nearest(pos))

    def structure_near(self, kind, base):
        """Find an actual legal fixture placement on either spawn orientation."""
        for radius in (6, 8, 10):
            for dx,dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(-1,1),(1,-1),(-1,-1)):
                try:
                    point=self.point((base.x+dx*radius,base.y+dy*radius))
                    return self.spawn(kind,1,point)[0]
                except ValueError as error:
                    if not str(error).startswith(('Fixture spawn failed','no ground cell')):
                        raise
        raise ValueError('No legal fixture structure placement near base')

    def rally(self):
        # Fixed seed 7 Acropolis geometry. Each requested placement is checked in-engine.
        w = self.worlds[0]
        main = w.my(86)[0]
        def oriented(point):
            return point if main.x>88 else (176-point[0],172-point[1])
        natural = self.spawn(86, 1, oriented((144.5, 58.5)))[0]
        third = self.spawn(86, 1, oriented((119.5, 59.5)))[0]
        self.bases = [main, natural, third]
        route = self.grid.route(third.pos, main.pos)
        candidates = [(x+.5, y+.5) for x, y in route
                      if math.dist((x+.5,y+.5), third.pos)>13
                      and math.dist((x+.5,y+.5), main.pos)>13]
        if not candidates:
            raise ValueError('No natural choke on third-to-main route')
        choke = min(candidates, key=lambda p: math.dist(p, natural.pos))
        enemies = self.spawn(48, 2, choke, 6)
        # A friendly aerial observer establishes only player-local enemy sightings.
        self.spawn(106, 1, (choke[0], choke[1]+5))
        self.structure_near(89,third)
        self.spawn(106, 1, (third.x, third.y+8), 3)
        self.spawn(110, 1, (third.x-7, third.y), 6)
        self.arena.controllers[0].debug([debug.DebugCommand(game_state=debug.all_resources)])
        self.threats = [dict(pos=list(u.pos), radius=u.attack_range+u.radius+1)
                        for u in self.worlds[1].my_units if u.tag in {e.tag for e in enemies}]
        # Main/natural start on their safe side; the third deliberately has the reported regression.
        safe = []
        for base in self.bases[:2]:
            choices = [self.point((base.x+dx*6, base.y+dy*6))
                       for dx,dy in ((1,0),(-1,0),(0,1),(0,-1))]
            point = next((p for p in choices if safe_rally(self.grid, base.pos, p, self.threats)), None)
            if point is None:
                raise ValueError('Fixture cannot establish safe main/natural rally')
            safe.append(point)
        targets = safe+[main.pos]
        self.advance(actions=[[command(3673,[b.tag],pos=p) for b,p in zip(self.bases,targets)], []])
        self.advance(1)
        w = self.worlds[0]
        self.prerequisites = dict(three_hatcheries=len(w.my(86))==3,
            terran_pressure_visible=all(any(u.tag==e.tag for u in w.enemy_units) for e in enemies),
            third_to_main_crosses_fire=not safe_rally(self.grid,third.pos,main.pos,self.threats,100),
            main_and_natural_initially_safe=all(self.rallies_safe()[:2]),
            third_initially_unsafe=not self.rallies_safe()[2])
        self.layout = dict(bases=[dict(tag=b.tag,pos=b.pos) for b in self.bases],
                           initial_targets=targets, threat_zones=self.threats)
        self.history = [w]

    def rallies_safe(self):
        result = []
        for base in self.bases:
            actual = self.worlds[0].by_tag(base.tag)
            points = [p[:2] for p in actual.rally_targets if not p[2]] if actual else []
            result.append(bool(points) and all(safe_rally(self.grid,base.pos,p,self.threats) for p in points))
        return result

    def high_ground(self):
        g = self.grid
        pairs = []
        for y in range(24, g.h-24, 2):
            for x in range(24, g.w-24, 2):
                if not g.free(x,y) or math.dist((x,y), self.arena.homes[0])<25:
                    continue
                for dx,dy in ((6,0),(-6,0),(0,6),(0,-6)):
                    a,b=x+dx,y+dy
                    if not g.free(a,b) or int(g.height[b,a])-int(g.height[y,x])<8:
                        continue
                    if not all(g.free(a+i,b+j) and g.free(x+i,y+j)
                               for i,j in ((1,0),(-1,0),(0,1),(0,-1))):
                        continue
                    low,high=(x+.5,y+.5),(a+.5,b+.5)
                    try:
                        length=len(g.route(low,high))
                    except ValueError:
                        continue
                    if length>18:
                        pairs.append((math.dist(low,self.arena.homes[0]),low,high))
        if not pairs:
            raise ValueError('No verifiable cliff fixture')
        _,low,high=min(pairs)
        # One marine creates sustained incoming damage without deleting the entire
        # defender before a bounded Go response. Do not use invulnerability or regen.
        enemies = self.spawn(48, 2, high)
        high = enemies[0].pos
        orbital = self.spawn(132, 1, self.point((self.arena.homes[0][0]-10,self.arena.homes[0][1])))[0]
        self.values([orbital], debug.DebugSetUnitValue.Energy, 100)
        # A real friendly scout provides a last-seen contact, then loses vision.
        scout = self.spawn(45, 1, (high[0]+2,high[1]+2))[0]
        self.history.append(self.worlds[0])
        self.arena.controllers[0].debug([debug.DebugCommand(kill_unit=debug.DebugKillUnit(tag=[scout.tag]))])
        self.advance(2)
        own = self.spawn(32, 1, low, 3)
        low = own[0].pos
        # Explicit fixture enemy hold, not a player/model command.
        self.advance(actions=[[],[command(18,[e.tag for e in enemies])]])
        before = self.worlds[0]
        enemy_before = {u.tag: u.health for u in self.worlds[1].my_units if u.tag in {e.tag for e in enemies}}
        self.history.append(before)
        self.advance(4)
        after = self.worlds[0]
        self.history.append(after)
        self.enemy_tags={u.tag for u in enemies};self.own_tags={u.tag for u in own}
        self.orbital_tag=orbital.tag;self.high=high
        self.enemy_health={u.tag:u.health for u in self.worlds[1].my_units if u.tag in self.enemy_tags}
        self.energy_before=after.by_tag(orbital.tag).energy
        self.prerequisites = dict(higher_terrain=int(g.height[int(high[1]),int(high[0])])>
                                   int(g.height[int(low[1]),int(low[0])])+4,
            all_defenders_on_lower_ground=all(int(g.height[int(u.y),int(u.x)])<
                int(g.height[int(high[1]),int(high[0])])-4 for u in own),
            defenders_survive_setup=all(after.by_tag(u.tag) is not None for u in own),
            isolated_target=all(u.tag in self.enemy_tags or u.distance(low)>25 for u in self.worlds[1].my_units),
            high_ground_hidden=int(after.visibility[int(high[1]),int(high[0])])!=2,
            no_counter_damage_without_vision=len(self.enemy_health)==len(self.enemy_tags)
                and all(health>=enemy_before[tag] for tag,health in self.enemy_health.items()),
            actual_incoming_damage=any(after.by_tag(u.tag) and before.by_tag(u.tag)
                and after.by_tag(u.tag).health < before.by_tag(u.tag).health for u in own),
            orbital_has_scan=after.by_tag(orbital.tag).energy>=50,
            scan_in_range=all(2.5<u.distance(high)<13 for u in own))
        self.layout=dict(low=low,high=high,own_tags=sorted(self.own_tags),enemy_tags=sorted(self.enemy_tags),
            orbital_tag=orbital.tag,terrain_heights=[int(g.height[int(p[1]),int(p[0])]) for p in (low,high)],
            raw_enemy_contact_visible=any(u.tag in self.enemy_tags for u in after.enemy_units),
            enemy_health_before=enemy_before,enemy_health_after=self.enemy_health,
            own_positions=[u.pos for u in after.my_units if u.tag in self.own_tags])


def run_case(name, args, root):
    folder=Path(tempfile.mkdtemp(prefix=name+'-',dir=root)).resolve()
    report=dict(scenario=name,fixture=True,debug_used=True,competitive_win=False,
                model=GO_MODEL,game_speed='slower',realtime=True,max_requests=args.max_requests,
                evidence={},prerequisites={},cleanup_complete=False,status='preparing',
                code_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in [Path(__file__), *sorted(Path('arena').glob('*.py'))]})
    budget=RecordedBudget(args.max_requests,folder/'budget.json')
    arena=commander=client=None
    try:
        arena=RawArena(render=False,races=['zerg' if name==SCENARIOS[0] else 'terran','terran'],
                       seed=7,game_speed='slower')
        f=Fixture(arena)
        (f.rally if name==SCENARIOS[0] else f.high_ground)()
        f.prerequisites['fixture_action_errors_zero']=arena.action_errors==0
        report.update(prerequisites=f.prerequisites,layout=f.layout,
                      engine_version=arena.controllers[0].ping().game_version)
        write(folder/'result.json',report)
        if not all(f.prerequisites.values()) or args.prepare_only:
            report['acceptance']=dict(verdict='inconclusive',reason='prepare_only' if args.prepare_only
                else 'fixture preconditions failed; no model request made')
            return report,folder
        base=GoPlanner(GO_MODEL,folder/'planner')
        report['status']='running'
        client=BotPlanner(base,budget,TAKEOVER_STRATEGY,args.max_requests)
        report['decision_context']=dict(phase='midgame_takeover',strategy=TAKEOVER_STRATEGY,
            model_receives_acceptance_criteria=False,model_receives_oracle_enemy_view=False)
        commander=Commander(arena,0,client,opening=False,interval=25,feedback_dir=folder/'feedback',combat_micro=True)
        for w in f.history:
            commander.ingest_feedback(w)
        e=report['evidence'];start=time.monotonic();last_sample=-1
        stable_start=None;probe_started=False;probe_tags=set();probe_before=set();trace_safe=True
        while time.monotonic()-start<args.wall_seconds and not arena.ended:
            w=f.worlds[0]
            previous_cooldowns={u.tag:u.weapon_cooldown for u in arena.proto(0).observation.raw_data.units
                                if u.alliance==1}
            client.policy=match_time_policy(w.seconds,time.monotonic()-start,1800,args.wall_seconds,'slower')
            client.policy['phase']='midgame_takeover'
            actions=commander.tick(w)
            # Examiner reads commands and observations, never supplies tactical answers to the model.
            if name==SCENARIOS[1]:
                for action in actions:
                    c=action.action_raw.unit_command
                    if c.ability_id==399 and f.orbital_tag in c.unit_tags:
                        p=(c.target_world_space_pos.x,c.target_world_space_pos.y)
                        if math.dist(p,f.high)<12:e['model_requested_relevant_scan']=True
            f.worlds=arena.step([actions,[]]);w=f.worlds[0];commander.ingest_feedback(w)
            if name==SCENARIOS[0]:
                third=f.bases[2]
                explicit=any(t.program and t.program.point and t.program.spec
                    and third.tag in t.program.spec.get('tags',())
                    and math.dist(t.program.point, f.bases[0].pos)>20
                    for t in commander.tasks.rally_orders())
                safe=all(f.rallies_safe())
                e['model_changed_third_rally']=bool(explicit)
                e['all_three_rallies_safe']=safe
                if safe and explicit:
                    if stable_start is None:stable_start=w.seconds
                    if not probe_started:
                        larvae=[u for u in w.my(151) if u.distance(third)<8 and not u.orders]
                        if larvae:
                            probe_started=True;probe_before={u.tag for u in w.my(105)}
                            report['reinforcement_probe']=dict(larva_tag=larvae[0].tag,
                                hatchery_tag=third.tag,source='examiner trains one native larva to verify rally inheritance')
                            f.worlds=arena.step([[command(1343,[larvae[0].tag])],[]])
                    e['rallies_held_for_window']=w.seconds-stable_start>=12
                else:
                    stable_start=None;e['rallies_held_for_window']=False
                if probe_started:
                    probe_tags.update(u.tag for u in w.my(105) if u.tag not in probe_before and u.distance(third)<9)
                    for tag in probe_tags:
                        u=w.by_tag(tag)
                        if u and any(u.distance(t['pos'])<=t['radius'] for t in f.threats):trace_safe=False
                    actual=w.by_tag(third.tag)
                    targets=[p[:2] for p in actual.rally_targets if not p[2]] if actual else []
                    living=[w.by_tag(t) for t in probe_tags]
                    e.update(third_produced_units=len(probe_tags)>=2,
                        third_units_survived=len(living)>=2 and all(living),
                        third_units_arrived=len(living)>=2 and all(u and any(u.distance(p)<4 for p in targets) for u in living),
                        third_units_avoided_fire_zone=bool(probe_tags) and trace_safe)
            else:
                cooldowns={u.tag:u.weapon_cooldown for u in arena.proto(0).observation.raw_data.units if u.alliance==1}
                record_scan(e,w,f.worlds[1],high=f.high,orbital_tag=f.orbital_tag,
                    energy_before=f.energy_before,own_tags=f.own_tags,enemy_health=f.enemy_health,
                    previous_cooldowns=previous_cooldowns,cooldowns=cooldowns)
            e.update(model_calls=client.calls,engine_action_errors=arena.action_errors)
            verdict=evaluate(name,f.prerequisites,e)
            if w.seconds-last_sample>=2:
                last_sample=w.seconds
                with (folder/'observations.jsonl').open('a') as out:
                    out.write(json.dumps(dict(game_s=w.seconds,own=w.summary(),evidence=dict(e),
                        rallies={u.tag:u.rally_targets for u in w.my_units if u.type_id in (86,100,101)},
                        probe_units=[dict(tag=u.tag,pos=u.pos,health=u.health) for u in w.my(105) if u.tag in probe_tags]),ensure_ascii=False)+'\n')
                write(folder/'result.json',dict(report,budget=budget.report(),progress=verdict))
            if verdict['verdict']=='pass' or client.fatal_error:break
            time.sleep(.02)
        report['acceptance']=evaluate(name,f.prerequisites,e)
        if client.fatal_error:report['error']=client.fatal_error
    except Exception as error:
        report.update(error=type(error).__name__+': '+str(error),traceback=traceback.format_exc(),
                      acceptance=dict(verdict='inconclusive',reason='runtime/fixture failure'))
    finally:
        errors=[]
        for label,resource,operation in [('planner',client,'close'),('commander',commander,'stop')]:
            if resource:
                try:getattr(resource,operation)()
                except Exception as error:errors.append(label+': '+str(error))
        if arena:
            try:
                arena.save_replay(folder/'scenario.SC2Replay')
                report['replay']=str(folder/'scenario.SC2Replay')
                report['timing']=arena.clock.report()
            except Exception as error:errors.append('replay: '+str(error))
            try:arena.close()
            except Exception as error:errors.append('engine: '+str(error))
        report.update(budget=budget.report(),cleanup_complete=not errors,cleanup_errors=errors,
            model_attempts=client.attempts if client else 0,model_calls=client.calls if client else 0,
            status='finished')
        if commander:
            report['journal']=str(commander.feedback.path)
            events=[json.loads(line) for line in Path(commander.feedback.path).read_text().splitlines()]
            report['plan_errors']=[r['event'] for r in events if r.get('record')=='feedback_event'
                and r.get('event',{}).get('kind') in ('plan_rejected','model_response_invalid','model_request_failed','plan_expired')]
        if errors:report['acceptance']=dict(verdict='inconclusive',reason='cleanup/replay failure')
        write(folder/'result.json',report)
        print('SCENARIO_RESULT',json.dumps(dict(scenario=name,acceptance=report.get('acceptance'),
            budget=report['budget'],directory=str(folder)),ensure_ascii=False),flush=True)
    return report,folder


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario',choices=['all',*SCENARIOS],default='all')
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--max-requests',type=int,default=3)
    parser.add_argument('--wall-seconds',type=float,default=240)
    args=parser.parse_args()
    if not 1<=args.max_requests<=3 or not 1<=args.wall_seconds<=300:
        parser.error('Scenario ceilings: 3 Go attempts and 300 wall seconds per case')
    flags.FLAGS(['tactical_scenarios'])
    root=Path('logs/tactical-scenarios');root.mkdir(parents=True,exist_ok=True)
    old=list(Path('logs/autonomous-bot').glob('*/campaign.json'))
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in old}
    cases=SCENARIOS if args.scenario=='all' else [args.scenario]
    reports=[run_case(name,args,root) for name in cases]
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in hashes.items()),'Historical campaign changed'
    # Reconstruct a durable additive scenario ledger, including prior failed/preparation runs.
    ledgers=[(p,json.loads(p.read_text())) for p in sorted(root.glob('*/budget.json'))]
    write(root/'ledger.json',dict(attempted=sum(d['attempted'] for _,d in ledgers),
        runs=[dict(path=str(p.parent),**d) for p,d in ledgers],historical_campaign_hashes=hashes))
    if args.prepare_only:
        return 0 if all(all(r['prerequisites'].values()) and r['prerequisites'] for r,_ in reports) else 2
    if any(r.get('error') or r['acceptance']['verdict']=='inconclusive' for r,_ in reports):
        return 2
    return 0 if all(r['acceptance']['verdict']=='pass' for r,_ in reports) else 1


if __name__=='__main__':
    raise SystemExit(main())
