"""Paired Linux SC2 army trials; debug setup, normal fog, no automatic Zerg spells.

Controls and calibration never call a model. Model trials are a separate phase
and receive no control outcomes or scenario-specific tactical hints.
"""
import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import tempfile
import time
import traceback

from absl import flags
from s2clientprotocol import debug_pb2 as debug
from arena.linux_step import LinuxStepArena
from arena.raw_engine import ATTACK, MOVE, command
from arena.rally import GroundMap
from arena.lifecycle import write_json_atomic
from go_duel_series import source_snapshot

ROOT=Path(__file__).resolve().parent
GROUND_TYPES={0:{105,107},1:{48,32,33}}

@dataclass(frozen=True)
class ArmyConfig:
    marines:int=24
    tanks:int=4
    medivacs:int=2
    zerglings:int=32
    hydralisks:int=12
    vipers:int=2
    energy:float=200
    seed:int=7
    map_name:str='Flat128'
    variant:str='compact'


def rows_positions(count,x,y_step=.9,columns=4,x_step=.9):
    rows=math.ceil(count/columns)
    return [(x+(i%columns)*x_step,(i//columns-(rows-1)/2)*y_step) for i in range(count)]


def layout(config):
    tanks=[(10, (i-(config.tanks-1)/2)*2.1) for i in range(config.tanks)]
    return {0:{105:rows_positions(config.zerglings,-6,1,8,-.8),
               107:rows_positions(config.hydralisks,-11,1.5,3,-1.3),
               499:[(-.5,-2.1),(-.5,2.1)]},
            1:{48:rows_positions(config.marines,7,1,4,.8),32:tanks,
               54:[(13,-2.5),(13,2.5)]}}


def payload_unit(u,tree):
    return dict(unit=tree.names.get(u.type_id,str(u.type_id)),type_id=u.type_id,
                pos=[round(u.x,4),round(u.y,4)],health=u.health,health_max=u.health_max,
                energy=round(u.energy,4),buffs=list(u.buffs),orders=list(u.orders))


class Trial:
    def __init__(self,config,folder):
        self.config=config;self.folder=Path(folder);self.folder.mkdir()
        self.arena=None;self.worlds=None;self.tags=[set(),set()];self.action_log=[]
        self.report=dict(config=asdict(config),debug_fixture=True,normal_fog=True,realtime=False,
                         automatic_zerg_micro=False,status='starting',snapshots=[],actions=[],outcome='Unknown',
                         model_calls=0,engine_action_errors=0,cleanup_complete=False)

    def setup(self):
        self.arena=LinuxStepArena(races=['zerg','terran'],seed=self.config.seed,map_name=self.config.map_name)
        self.worlds=self.arena.initial
        self.report['engine']=self.arena.version
        # Retain the remote original town halls so the map's elimination rule
        # cannot end a local army test. No workers or background production.
        kill=[u.tag for w in self.worlds for u in w.my_units if not u.is_building]
        self.arena.controllers[0].debug([debug.DebugCommand(kill_unit=debug.DebugKillUnit(tag=kill))])
        self.worlds=self.arena.step([[],[]])
        grid=GroundMap(self.arena.map_info_by_player[0].start_raw)
        homes=[u.pos for w in self.worlds for u in w.my_units if u.is_building]
        candidates=[]
        for y in range(11,grid.h-11):
            for x in range(17,grid.w-17):
                if min(math.dist((x+.5,y+.5),p) for p in homes)<32:continue
                patch=grid.pathing[y-9:y+10,x-16:x+17]
                heights=grid.height[y-9:y+10,x-16:x+17]
                if patch.all() and (heights==grid.height[y,x]).all():
                    candidates.append((math.dist((x,y),(grid.w/2,grid.h/2)),x,y))
        if not candidates:raise AssertionError('No unobstructed same-height army fixture area')
        _,x,y=min(candidates);self.anchor=(x+.5,y+.5)
        before=[{u.tag for u in w.my_units} for w in self.worlds]
        commands=[]
        for side,types in layout(self.config).items():
            for kind,points in types.items():
                for dx,dy in points:
                    commands.append(debug.DebugCommand(create_unit=debug.DebugCreateUnit(
                        unit_type=kind,owner=side+1,quantity=1,pos=dict(x=self.anchor[0]+dx,y=self.anchor[1]+dy))))
        self.arena.controllers[0].debug(commands)
        self.worlds=self.arena.step([[],[]])
        for side,w in enumerate(self.worlds):self.tags[side]={u.tag for u in w.my_units if u.tag not in before[side]}
        expected=[self.config.zerglings+self.config.hydralisks+self.config.vipers,
                  self.config.marines+self.config.tanks+self.config.medivacs]
        if list(map(len,self.tags))!=expected:raise AssertionError('Fixture unit counts differ from configuration')
        values=[]
        for side,w in enumerate(self.worlds):
            for u in w.my_units:
                if u.tag in self.tags[side] and u.type_id in (499,54):
                    values.append(debug.DebugCommand(unit_value=debug.DebugSetUnitValue(
                        unit_value=debug.DebugSetUnitValue.Energy,value=self.config.energy,unit_tag=u.tag)))
        self.arena.controllers[0].debug(values)
        self.worlds=self.arena.step([[],[]])
        self.start=self.worlds[0].seconds
        self.report['anchor']=self.anchor
        self.report['started_game_s']=self.start
        initial=[[payload_unit(u,self.arena.tech_tree) for u in w.my_units if u.tag in self.tags[s]]
                 for s,w in enumerate(self.worlds)]
        for units in initial:units.sort(key=lambda u:(u['type_id'],u['pos']))
        self.report['initial_units']=initial
        self.report['fixture_sha256']=hashlib.sha256(json.dumps(initial,sort_keys=True).encode()).hexdigest()
        if any(u['health']!=u['health_max'] for units in initial for u in units):
            raise AssertionError('Units took damage during setup, before tested commands')
        self.report['zerg_initial_visible_enemy']=[payload_unit(u,self.arena.tech_tree) for u in self.worlds[0].enemy_units]
        if not any(u.type_id==32 for u in self.worlds[0].enemy_units):
            raise AssertionError('Tank threat not visible in the normal Zerg observation')
        self.vip=[u.tag for u in self.worlds[0].my_units if u.tag in self.tags[0] and u.type_id==499]
        self.cloud_points=[(self.anchor[0]+10,self.anchor[1]-2.1),(self.anchor[0]+10,self.anchor[1]+2.1)]
        self.initial_ground=[{u.tag for u in w.my_units if u.tag in self.tags[s] and u.type_id in GROUND_TYPES[s]}
                             for s,w in enumerate(self.worlds)]
        self.snapshot();self.save()

    def save(self):write_json_atomic(self.folder/'result.json',self.report)

    def snapshot(self):
        cooldowns=[{u.tag:u.weapon_cooldown for u in obs.observation.raw_data.units} for obs in self.arena.last]
        self.report['snapshots'].append(dict(game_s=self.worlds[0].seconds-self.start,
            players=[dict(ground=sum(u.tag in self.initial_ground[s] for u in w.my_units),
                units=[dict(tag=u.tag,weapon_cooldown=cooldowns[s].get(u.tag),engaged_target=u.engaged_target,**payload_unit(u,self.arena.tech_tree)) for u in w.my_units if u.tag in self.tags[s]])
                for s,w in enumerate(self.worlds)],
            effects=[list(e) for e in self.worlds[0].effects]))

    def outcome(self):
        counts=[sum(u.tag in self.initial_ground[s] for u in w.my_units) for s,w in enumerate(self.worlds)]
        return 'Draw' if counts==[0,0] else 'TerranVictory' if counts[0]==0 else 'ZergVictory' if counts[1]==0 else None

    def step(self,batches,contexts=None,advance=True):
        for side,actions in enumerate(batches):
            for a in actions:
                c=a.action_raw.unit_command
                if c.unit_tags:self.action_log.append(dict(side=side,game_s=self.worlds[0].seconds-self.start,
                    ability=c.ability_id,tags=list(c.unit_tags),target=c.target_unit_tag if c.HasField('target_unit_tag') else None,
                    pos=[c.target_world_space_pos.x,c.target_world_space_pos.y] if c.HasField('target_world_space_pos') else None))
            if contexts is not None:self.arena.register_action_context(side,[(a.SerializeToString(),ctx) for a,ctx in zip(actions,contexts[side])])
        self.worlds=self.arena.step(batches,advance=advance,step_loops=8)
        self.report['engine_action_errors']=self.arena.action_errors

    def control(self,kind,max_game_s=100):
        self.report['controller']=kind;self.report['status']='running'
        # Same ordinary attack-move orders in both paired arms. Vipers cannot
        # auto-cast; only the positive control below issues explicit cloud API orders.
        z=[u.tag for u in self.worlds[0].my_units if u.tag in self.tags[0] and u.type_id in (105,107,499)]
        t=[u.tag for u in self.worlds[1].my_units if u.tag in self.tags[1] and u.type_id!=32]
        opening=[[command(ATTACK,z,pos=(self.anchor[0]+13,self.anchor[1]))],
                 [command(ATTACK,t,pos=(self.anchor[0]-10,self.anchor[1]))]]
        cast_times={tag:[] for tag in self.vip};last_snapshot=-1;wall=time.monotonic()
        while self.worlds[0].seconds-self.start<max_game_s and time.monotonic()-wall<180:
            elapsed=self.worlds[0].seconds-self.start
            batches=opening if elapsed==0 else [[],[]]
            if kind=='explicit_spell_control':
                available=self.arena.available_abilities(0,self.vip)
                for tag,point in zip(self.vip,self.cloud_points):
                    u=self.worlds[0].by_tag(tag)
                    if u is None:continue
                    # Fixed, disclosed oracle reference; never used in the model
                    # arm. At most two casts/caster, no movement or hidden targets.
                    if len(cast_times[tag])<2 and (not cast_times[tag] or elapsed-cast_times[tag][-1]>=5.5):
                        if self.arena.tech_tree.ability_available(2063,available.get(tag,set())):
                            batches[0].append(command(2063,[tag],pos=point));cast_times[tag].append(elapsed)
            self.step(batches)
            if self.worlds[0].seconds-self.start-last_snapshot>=.5:
                self.snapshot();last_snapshot=self.worlds[0].seconds-self.start
            outcome=self.outcome()
            if outcome:self.report['outcome']=outcome;break
        self.report['cast_times']=cast_times
        self.report['elapsed_game_s']=self.worlds[0].seconds-self.start
        self.report['elapsed_wall_s']=time.monotonic()-wall
        self.snapshot()
        self.report['cloud_effect_observed']=any(any(e[0]==10 for e in snap['effects']) for snap in self.report['snapshots'])
        self.report['blinded_tank_tags']=sorted({u['tag'] for snap in self.report['snapshots'] for u in snap['players'][1]['units'] if u['type_id']==32 and 83 in u['buffs']})

    def close(self):
        if self.arena:
            try:
                replay=self.folder/'trial.SC2Replay';self.arena.save_replay(replay)
                self.report.update(replay=str(replay),replay_sha256=hashlib.sha256(replay.read_bytes()).hexdigest())
            finally:self.arena.close()
        self.report.update(status='finished',actions=self.action_log,cleanup_complete=self.arena is None or self.arena.closed)
        self.save()


def run_control(config,folder,kind):
    trial=Trial(config,folder)
    try:trial.setup();trial.control(kind)
    except Exception as error:trial.report.update(error=type(error).__name__+': '+str(error),traceback=traceback.format_exc())
    finally:trial.close()
    return trial.report



class BudgetMailbox:
    """Exact requested hosted model, connected through the existing file mailbox.

    The agent sees only current.json. No control reports, labels or oracle source
    are included. This is explicitly distinct from subscription CLI inference.
    """
    compact_battle=True
    timeout=240
    soft_timeout=90
    model='gpt-6.1-terra'
    prepared_strategy='使用当前已有部队自主判断战术并击败敌方部队。不生产新单位、不扩张；只使用正常己方观测。'
    def __init__(self,folder,budget,limit,model='gpt-6.1-terra'):
        if model not in ('gpt-6.1-terra','gpt-6-luna'):
            raise ValueError('Explicit supported model identity required; no fallback')
        self.model=model
        from arena.external_planner import ExternalPlanner
        self.mail=ExternalPlanner(folder,timeout=self.timeout)
        self.budget=budget;self.limit=limit
    @property
    def attempts(self):return self.mail.sequence
    @property
    def calls(self):return self.mail.calls
    def ready_for_request(self):return self.attempts<self.limit
    def request_status(self):return self.mail.request_status()
    def chat(self,system,user):
        self.budget.reserve('hosted_model_battle_decision')
        answer=self.mail.chat(system,user)
        plan=json.loads(answer)
        allowed={'use_ability','order_units','cancel_unit_order','cancel_task','no_op','attack_nearest','move_out'}
        if any(t.get('skill') not in allowed for t in plan.get('tasks',[])):
            raise ValueError('Local army trial allows only the published combat/ability/movement/cancel tasks; no production')
        return answer
    def close(self):self.mail.close()


def run_model(config,folder,mailbox,expected_signature,request_limit=12,model='gpt-6.1-terra'):
    from arena.commander import Commander
    from go_duel_series import DurableBudget
    trial=Trial(config,folder);commander=None;client=None
    budget=DurableBudget(Path(folder)/'budget.json',request_limit)
    try:
        trial.setup()
        if trial.report['fixture_sha256']!=expected_signature:
            raise AssertionError('Model initial state differs from paired controls; model was not contacted')
        client=BudgetMailbox(mailbox,budget,request_limit,model=model)
        commander=Commander(trial.arena,0,client,opening=False,interval=3,max_plan_age=30,
                            combat_micro=False,feedback_dir=Path(folder)/'feedback')
        commander.system+='\nControlled local army trial: use existing forces only; no production or economy. Choose your own combat plan. Both players pause while you decide. Only use published combat, movement, ability and cancel tasks.'
        trial.report.update(controller='hosted_model_mailbox',requested_model=client.model,model_identity_source='explicit platform agent model selection',
            model_returned_identity=None,mailbox=str(mailbox),status='running',combat_micro=False,
            no_parent_history=True,no_control_outcomes_in_prompt=True)
        trial.save()
        t=[u.tag for u in trial.worlds[1].my_units if u.tag in trial.tags[1] and u.type_id!=32]
        first=True;last_snapshot=-1;last_save=time.monotonic();wall=time.monotonic()
        while trial.worlds[0].seconds-trial.start<75 and time.monotonic()-wall<1800:
            w=trial.worlds[0];actions=commander.tick(w)
            terran=[command(ATTACK,t,pos=(trial.anchor[0]-10,trial.anchor[1]))] if first else []
            first=False
            trial.step([actions,terran],advance=not commander.thinking)
            commander.ingest_feedback(trial.worlds[0])
            elapsed=trial.worlds[0].seconds-trial.start
            if elapsed-last_snapshot>=.5:
                trial.snapshot();last_snapshot=elapsed
            outcome=trial.outcome()
            if outcome:trial.report['outcome']=outcome;break
            if commander.client_fatal:
                trial.report['model_error']=commander.client_fatal;break
            if time.monotonic()-last_save>=5:
                trial.report.update(elapsed_game_s=elapsed,model_calls=client.calls,model_attempts=client.attempts,
                                    commander=commander.stats(),budget=budget.report())
                trial.save();last_save=time.monotonic()
        trial.report.update(elapsed_game_s=trial.worlds[0].seconds-trial.start,elapsed_wall_s=time.monotonic()-wall,
                            model_calls=client.calls,model_attempts=client.attempts,commander=commander.stats(),
                            budget=budget.report(),timing=trial.arena.timing())
        trial.snapshot()
    except Exception as error:trial.report.update(error=type(error).__name__+': '+str(error),traceback=traceback.format_exc())
    finally:
        if client:client.close()
        if commander:commander.stop()
        trial.close()
    return trial.report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root',type=Path,required=True)
    parser.add_argument('--phase',choices=['controls','model'],default='controls')
    parser.add_argument('--model',choices=['gpt-6.1-terra','gpt-6-luna'],default='gpt-6.1-terra')
    parser.add_argument('--mailbox',type=Path)
    parser.add_argument('--control-report',type=Path)
    parser.add_argument('--marines',type=int,default=24)
    parser.add_argument('--hydralisks',type=int,default=12)
    parser.add_argument('--zerglings',type=int,default=32)
    parser.add_argument('--seed',type=int,default=7)
    args=parser.parse_args()
    if not flags.FLAGS.is_parsed():flags.FLAGS(['army-trial'])
    config=ArmyConfig(marines=args.marines,hydralisks=args.hydralisks,zerglings=args.zerglings,seed=args.seed)
    folder=Path(tempfile.mkdtemp(prefix='calibration-' if args.phase=='controls' else 'model-',dir=args.output_root)).resolve()
    sources=source_snapshot(folder)
    src=Path(__file__);sources[src.name]=hashlib.sha256(src.read_bytes()).hexdigest();(folder/'source'/src.name).write_bytes(src.read_bytes())
    history={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.glob('logs/**/budget.json')}
    report=dict(config=asdict(config),controls=[],history_sha256=history,source_sha256=sources)
    if args.phase=='model':
        if args.mailbox is None or args.control_report is None:parser.error('model phase requires mailbox and passed paired control report')
        control=json.loads(args.control_report.read_text())
        if not control.get('passed') or control['config']!=asdict(config):raise ValueError('Passed controls of this exact configuration required')
        result=run_model(config,folder/'engagement',args.mailbox,control['controls'][0]['fixture_sha256'],model=args.model)
        report.update(model_result=str(folder/'engagement/result.json'),requested_model=args.model,outcome=result['outcome'],control_report=str(args.control_report),
            identical_initial_state=result.get('fixture_sha256')==control['controls'][0]['fixture_sha256'],
            passed=result['outcome']=='ZergVictory' and not result.get('error') and result['engine_action_errors']==0
                   and result.get('fixture_sha256')==control['controls'][0]['fixture_sha256'])
    else:
        for name in ['attack_move_only','explicit_spell_control']:
            result=run_control(config,folder/name,name)
            report['controls'].append(dict(mode=name,path=str(folder/name/'result.json'),outcome=result['outcome'],
                error=result.get('error'),fixture_sha256=result.get('fixture_sha256'),engine_action_errors=result['engine_action_errors'],
                cloud_effect_observed=result.get('cloud_effect_observed',False),blinded_tank_tags=result.get('blinded_tank_tags',[])))
            print('CONTROL',json.dumps(report['controls'][-1]),flush=True)
        report['identical_initial_state']=len({c['fixture_sha256'] for c in report['controls']})==1 and report['controls'][0]['fixture_sha256'] is not None
        report['passed']=(report['identical_initial_state'] and [c['outcome'] for c in report['controls']]==['TerranVictory','ZergVictory']
                           and not any(c['error'] or c['engine_action_errors'] for c in report['controls'])
                           and not report['controls'][0]['cloud_effect_observed'] and report['controls'][1]['cloud_effect_observed']
                           and len(report['controls'][1]['blinded_tank_tags'])>=2)
    report['historical_unchanged']=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in history.items())
    report['source_unchanged']=all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in sources.items())
    write_json_atomic(folder/'result.json',report)
    print('CONTROL_REPORT',folder,json.dumps({k:report[k] for k in ['passed','identical_initial_state','historical_unchanged','source_unchanged']}),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
