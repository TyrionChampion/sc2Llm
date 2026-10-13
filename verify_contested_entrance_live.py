"""Native explicit movement into a contested entrance; auto rally remains safe. Zero LLM calls."""
import json,math,time
from pathlib import Path
from absl import flags
from arena.raw_engine import RawArena,ATTACK
from arena.commander import Commander
from arena.autonomous_bot import BotPlanner,RequestBudget
from arena.rally import named_rally_point
from verify_tactical_scenarios_live import Fixture
from verify_tactical_controls import CaptureAdapter

def main():
    flags.FLAGS(['contested-entrance']);folder=Path('logs/contested-entrance-control');folder.mkdir(exist_ok=False)
    arena=commander=client=None;report=dict(model_calls=0,debug_fixture=True,fast_build=False,passed=False)
    try:
        arena=RawArena(render=False,races=['zerg','terran'],seed=7,game_speed='slower');f=Fixture(arena)
        home=f.worlds[0].my(86)[0];dx,dy=88-home.x,86-home.y;length=math.hypot(dx,dy)
        base=f.spawn(86,1,f.point((home.x+dx/length*24,home.y+dy/length*24)))[0]
        point,_=named_rally_point(f.grid,arena,0,'expansion_entrance',f.worlds[0])
        f.spawn(48,2,point);troops=f.spawn(105,1,f.point((base.x+2,base.y+2)),4)
        try:
            named_rally_point(f.grid,arena,0,'expansion_entrance',f.worlds[0]);blocked=False
        except ValueError as e:blocked='blocked or threatened' in str(e)
        adapter=CaptureAdapter([dict(skill='order_units',count=1,args=dict(unit='zergling',count=1,
            mode='attack_move',target='expansion_entrance',timeout_s=60,reason='显式增援受压矿口'))],folder/'captured-input.json')
        client=BotPlanner(adapter,RequestBudget(1),'只验收显式调兵接口，不选择战术',1)
        commander=Commander(arena,0,client,opening=False,feedback_dir=folder/'feedback')
        wall=time.monotonic();sent=False;native=False
        while time.monotonic()-wall<12:
            actions=commander.tick(f.worlds[0]);sent|=any(a.action_raw.unit_command.ability_id==ATTACK for a in actions)
            f.worlds=arena.step([actions,[]]);commander.ingest_feedback(f.worlds[0])
            native|=any(u.tag in {t.tag for t in troops} and any(a in (23,3674) for a in u.orders) for u in f.worlds[0].my_units)
            if sent and native:break
            time.sleep(.02)
        report.update(checks=dict(visible_enemy_at_entrance=bool(adapter.payload and adapter.payload['world']['visible_enemy']),
            default_rally_still_rejects_threat=blocked,plan_accepted=commander.decisions==1,
            explicit_attack_action_sent=sent,native_unit_attack_order_observed=native,
            input_within_45000=0<adapter.input_chars<=45000),engine_action_errors=arena.action_errors,
            local_adapter_calls=adapter.calls,point=point)
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
