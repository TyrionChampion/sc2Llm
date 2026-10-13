"""Native positive controls for the two fixtures. Scripted executor test, ZERO LLM calls."""
import argparse
import json
import math
from pathlib import Path
import tempfile
import time
from absl import flags

from arena.commander import Commander
from arena.raw_engine import RawArena, command
from arena.rally import producer_inventory
from arena.autonomous_bot import BotPlanner, RequestBudget
from arena.context_tables import decode_tables
from arena.tactical_scenarios import SCENARIOS, safe_rally, record_scan
from verify_tactical_scenarios_live import Fixture, write


class CaptureAdapter:
    """Local scripted reply through the real prompt/JSON pipeline; no API client."""
    model='scripted/executor-positive-control'
    timeout=35
    closed=False
    last_metadata=None

    def __init__(self,plans,path):
        self.plans=plans;self.path=path
        self.calls=self.attempts=0
        self.payload=None;self.input_chars=0

    def chat(self,system,user):
        self.attempts+=1;self.calls+=1
        self.payload=decode_tables(json.loads(user))
        self.input_chars=len(system)+len(user)
        write(self.path,dict(mode='local_input_capture_no_model_api',system=system,observation=user))
        return json.dumps(dict(tasks=self.plans,reason='显式脚本执行器阳性对照'),ensure_ascii=False)

    def request_status(self):return dict(state='local_capture',api_calls=0)
    def close(self):self.closed=True


def control(name, root):
    folder=Path(tempfile.mkdtemp(prefix=name+'-',dir=root)).resolve()
    report=dict(scenario=name,mode='scripted_executor_positive_control',model_calls=0,
                fixture=True,debug_used=True,passed=False)
    arena=commander=client=adapter=None
    try:
        arena=RawArena(render=False,races=['zerg' if name==SCENARIOS[0] else 'terran','terran'],game_speed='slower',seed=7)
        f=Fixture(arena);(f.rally if name==SCENARIOS[0] else f.high_ground)()
        report['prerequisites']=f.prerequisites
        if not all(f.prerequisites.values()):raise ValueError('Fixture preconditions failed')
        if name==SCENARIOS[0]:
            _,labels=producer_inventory(arena,f.worlds[0],0)
            plans=[];targets={}
            for b in f.bases:
                choices=[f.point((b.x+dx*6,b.y+dy*6)) for dx,dy in ((1,0),(-1,0),(0,1),(0,-1))]
                target=next(p for p in choices if safe_rally(f.grid,b.pos,p,f.threats))
                targets[b.tag]=target
                plans.append(dict(skill='rally_units',args=dict(producers=[labels[b.tag]],target=list(target),
                    duration_s=180,reason='执行器阳性对照：显式逐建筑集结')))
        else:
            plans=[dict(skill='scan',args=dict(target=list(f.high))),
                   dict(skill='attack_nearest',args={})]
        report['scripted_orders']=plans
        adapter=CaptureAdapter(plans,folder/'captured-model-input.json')
        client=BotPlanner(adapter,RequestBudget(1),'夹具执行器阳性对照；仅使用当前己方观测与现有接口。',1)
        commander=Commander(arena,0,client,opening=False,feedback_dir=folder/'feedback',combat_micro=True)
        for w in f.history:commander.ingest_feedback(w)
        start=time.monotonic();e={};probe=None;tags=set();initial={u.tag for u in f.worlds[0].my(105)}
        stable=None;safe_trace=True
        while time.monotonic()-start<100:
            before={u.tag:u.weapon_cooldown for u in arena.proto(0).observation.raw_data.units if u.alliance==1}
            w=f.worlds[0];actions=commander.tick(w)
            if name==SCENARIOS[1] and any(a.action_raw.unit_command.ability_id==399 for a in actions):
                e['model_requested_relevant_scan']=True  # helper's transport flag; this is explicitly scripted.
            f.worlds=arena.step([actions,[]]);w=f.worlds[0];commander.ingest_feedback(w)
            if name==SCENARIOS[1]:
                after={u.tag:u.weapon_cooldown for u in arena.proto(0).observation.raw_data.units if u.alliance==1}
                record_scan(e,w,f.worlds[1],high=f.high,orbital_tag=f.orbital_tag,energy_before=f.energy_before,
                    own_tags=f.own_tags,enemy_health=f.enemy_health,previous_cooldowns=before,cooldowns=after)
                checks={k:bool(e.get(k)) for k in ('scan_effect_observed','orbital_energy_spent',
                    'high_ground_became_visible','own_counterfire_observed','enemy_damage_after_scan')}
            else:
                correct=all(f.rallies_safe()) and all(any(math.dist(p[:2],targets[b.tag])<1
                    for p in w.by_tag(b.tag).rally_targets if not p[2]) for b in f.bases if w.by_tag(b.tag))
                if correct and stable is None:stable=w.seconds
                if not correct:stable=None
                if correct and probe is None:
                    larvae=[u for u in w.my(151) if u.distance(f.bases[2])<8 and not u.orders]
                    if larvae:
                        probe=larvae[0].tag
                        f.worlds=arena.step([[command(1343,[probe])],[]])
                        report['third_larva_probe']=probe
                if probe is not None:
                    tags.update(u.tag for u in w.my(105) if u.tag not in initial and u.distance(f.bases[2])<9)
                living=[w.by_tag(t) for t in tags]
                safe_trace &= all(not any(u.distance(t['pos'])<=t['radius'] for t in f.threats) for u in living if u)
                checks=dict(all_three_rallies_confirmed=correct,
                    kept_for_12_game_seconds=stable is not None and w.seconds-stable>=12,
                    two_native_third_reinforcements=len(tags)>=2,
                    arrived_and_alive=len(tags)>=2 and all(u and u.distance(targets[f.bases[2].tag])<4 for u in living),
                    route_avoided_fire_zone=safe_trace)
            payload=adapter.payload or {}
            if name==SCENARIOS[0]:
                rows=payload.get('rally_producers',[])
                third=next((r for r in rows if r['tag']==f.bases[2].tag),{})
                checks.update(all_three_rally_targets_in_input=all(any(r['tag']==b.tag and r.get('rally_targets')
                    for r in rows) for b in f.bases),
                    third_route_risk_in_input=any(r.get('observed_fire_zone_overlap')
                        for r in third.get('point_rally_routes',[])))
            else:
                facts=payload.get('battlefield',{}).get('terrain_context',{})
                checks.update(own_damage_in_input=bool(facts.get('recent_own_damage')),
                    height_and_fog_in_input=any(r.get('higher_than_own') and r.get('enemy_position_visibility')!=2
                        for r in facts.get('combat_contacts',[])))
            checks.update(json_plan_accepted=commander.decisions>0,
                          full_model_input_within_limit=0<adapter.input_chars<=45000)
            report['checks']=checks
            if all(checks.values()):break
            time.sleep(.02)
        report['engine_action_errors']=arena.action_errors
        report['captured_model_input']=str(adapter.path)
        report['input_chars']=adapter.input_chars
        report['local_adapter_calls']=adapter.calls
        report['passed']=all(report.get('checks',{}).values()) and bool(report.get('checks')) and arena.action_errors==0
    except Exception as error:
        report['error']=type(error).__name__+': '+str(error)
    finally:
        errors=[]
        if commander:
            try:commander.stop()
            except Exception as error:errors.append(str(error))
        if client:client.close()
        if arena:
            try:arena.save_replay(folder/'control.SC2Replay')
            except Exception as error:errors.append(str(error))
            try:arena.close()
            except Exception as error:errors.append(str(error))
        report['cleanup_complete']=not errors
        report['cleanup_errors']=errors
        report['passed'] &= not errors
        write(folder/'result.json',report)
        print('CONTROL_RESULT',json.dumps(dict(path=str(folder),**report),ensure_ascii=False),flush=True)
    return report['passed']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario',choices=['all',*SCENARIOS],default='all')
    args=parser.parse_args();flags.FLAGS(['tactical_controls'])
    root=Path('logs/tactical-controls');root.mkdir(parents=True,exist_ok=True)
    results=[control(n,root) for n in (SCENARIOS if args.scenario=='all' else [args.scenario])]
    return 0 if all(results) else 1


if __name__=='__main__':raise SystemExit(main())
