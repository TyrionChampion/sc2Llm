"""Explicit injection through Commander/JSON/TaskQueue; native effect and larvae, zero LLM calls."""
import argparse,json,time
from pathlib import Path
from absl import flags
from s2clientprotocol import debug_pb2
from arena.raw_engine import RawArena
from arena.commander import Commander
from arena.autonomous_bot import BotPlanner,RequestBudget
from verify_tactical_scenarios_live import Fixture
from verify_tactical_controls import CaptureAdapter

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--label',required=True)
    p.add_argument('--remote',action='store_true',help='Put Queen at another owned base; cast must move her to requested home')
    p.add_argument('--distance',type=float,default=24,help='Remote fixture distance, 16..40 tiles')
    args=p.parse_args()
    if not 16<=args.distance<=40:p.error('fixture distance must be 16..40')
    flags.FLAGS(['queen-injection-control']);folder=Path('logs/queen-injection-'+args.label);folder.mkdir(exist_ok=False)
    arena=commander=client=None;report=dict(model_calls=0,debug_fixture=True,fast_build=False,passed=False)
    try:
        arena=RawArena(render=False,races=['zerg','terran'],seed=7,game_speed='slower');f=Fixture(arena)
        base=f.worlds[0].my(86)[0];queen_base=base
        if args.remote:
            import math
            dx,dy=88-base.x,86-base.y;length=math.hypot(dx,dy)
            for distance in (args.distance,args.distance+6):
                try:
                    queen_base=f.spawn(86,1,f.point((base.x+dx/length*distance,base.y+dy/length*distance)))[0]
                    break
                except ValueError:
                    if distance==args.distance+6:raise
        queen=f.spawn(126,1,f.point((queen_base.x+3,queen_base.y+3)))[0]
        f.values([queen],debug_pb2.DebugSetUnitValue.Energy,50)
        baseline=sum(u.distance(base)<9 for u in f.worlds[0].my(151));start=f.worlds[0].seconds;wall=time.monotonic();buff=False
        report.update(remote_queen=args.remote,initial_queen_position=queen.pos,target_base_position=base.pos)
        adapter=CaptureAdapter([dict(skill='inject_larva',count=1,args=dict(base='home'))],folder/'captured-input.json')
        client=BotPlanner(adapter,RequestBudget(1),'明确注卵命令的执行器阳性对照',1)
        commander=Commander(arena,0,client,opening=False,feedback_dir=folder/'feedback')
        while f.worlds[0].seconds-start<(70 if args.remote else 38) and time.monotonic()-wall<(180 if args.remote else 105):
            actions=commander.tick(f.worlds[0]);f.worlds=arena.step([actions,[]]);w=f.worlds[0];commander.ingest_feedback(w)
            observed=w.by_tag(base.tag);buff|=bool(observed and 11 in observed.buffs)
            if buff and sum(u.distance(base)<9 for u in w.my(151))>=baseline+3:break
            time.sleep(.02)
        tasks=[t for t in commander.tasks.tasks if t.skill=='inject_larva']
        report.update(checks=dict(plan_accepted=commander.decisions==1,task_completed=bool(tasks and tasks[0].status=='completed'),
            own_queen_energy_in_input=any(q['tag']==queen.tag and q['energy']>=25 for q in (adapter.payload or {}).get('zerg_injection',{}).get('queens',[])),
            own_base_buff_in_input=any(b['tag']==base.tag and not b['inject_active'] for b in (adapter.payload or {}).get('zerg_injection',{}).get('bases',[])),
            native_inject_buff_observed=buff,three_additional_larvae_observed=sum(u.distance(base)<9 for u in w.my(151))>=baseline+3),
            initial_larvae=baseline,final_larvae=sum(u.distance(base)<9 for u in w.my(151)),game_s=w.seconds-start,
            local_adapter_calls=adapter.calls,input_chars=adapter.input_chars,engine_action_errors=arena.action_errors)
        report['checks']['input_within_45000']=0<adapter.input_chars<=45000
        report['passed']=all(report['checks'].values()) and arena.action_errors==0
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as e:report['error']=type(e).__name__+': '+str(e)
    finally:
        if commander:commander.stop()
        if client:client.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False),flush=True)
    return 0 if report['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
