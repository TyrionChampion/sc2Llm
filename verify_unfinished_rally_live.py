"""Zero-model native rally to a real unfinished expansion under visible pressure."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import time

from absl import flags
from s2clientprotocol import debug_pb2 as debug

from arena.raw_engine import RawArena
from arena.rally import named_rally_point
from arena.tasks import TaskQueue
from verify_tactical_scenarios_live import Fixture


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',required=True)
    args=parser.parse_args();flags.FLAGS(['unfinished-rally-control'])
    folder=Path('logs/unfinished-rally-'+args.label);folder.mkdir(exist_ok=False)
    result=dict(model_calls=0,debug_fixture=True,debug_resources=True,fast_build=False,
        passed=False,events=[],source_sha256={name:hashlib.sha256(Path(name).read_bytes()).hexdigest()
            for name in ('arena/rally.py','arena/tasks.py')})
    arena=queue=None
    try:
        arena=RawArena(render=False,races=['zerg','terran'],seed=20261021,game_speed='slower')
        fixture=Fixture(arena)
        arena.controllers[0].debug([debug.DebugCommand(game_state=debug.all_resources)])
        fixture.advance(.5);world=fixture.worlds[0];home=arena.homes[0]
        queue=TaskQueue(arena,0,lambda _:None,
            lambda kind,**kw:result['events'].append(dict(kind=kind,**kw)))
        queue.enqueue([dict(skill='expand')],world.seconds,'native expansion rally control')
        start=world.seconds;wall=time.monotonic();outer=None
        while world.seconds-start<110 and time.monotonic()-wall<280:
            world=arena.step([queue.tick(world,set()),[]])[0];time.sleep(.02)
            outer=next((u for u in world.my(86) if u.distance(home)>15 and .3<=u.progress<.9),None)
            if outer:break
            if queue.tasks[0].status=='failed':raise AssertionError(queue.tasks[0].reason)
        if outer is None:raise AssertionError('real expansion did not reach partial construction')
        result['expansion_before']=dict(tag=outer.tag,pos=list(outer.pos),progress=outer.progress)
        point,_=named_rally_point(queue._rally_grid(),arena,0,'expansion_entrance',world,
            allow_construction=True,allow_threatened=True)
        # Pressure is a deliberate debug fixture; no model sees this game.
        fixture.worlds[0]=world
        enemies=fixture.spawn(32,2,point,count=1)
        world=fixture.worlds[0]
        if not any(u.tag==enemies[0].tag for u in world.enemy_units):
            # Own vision is added explicitly, never a hidden live-game view.
            fixture.spawn(104,1,point);world=fixture.worlds[0]
        if not any(u.tag==enemies[0].tag for u in world.enemy_units):raise AssertionError('pressure not visible')
        try:named_rally_point(queue._rally_grid(),arena,0,'expansion_entrance',world,allow_construction=True)
        except ValueError:result['automatic_threat_filter_retained']=True
        else:raise AssertionError('pressure must reject every ordinary safe entrance candidate')
        queue.enqueue([dict(skill='rally_units',args=dict(producers='all',target='expansion_entrance',duration_s=60))],
            world.seconds,'native explicit unfinished/pressured rally control')
        task=queue.tasks[-1];start=world.seconds;wall=time.monotonic()
        while world.seconds-start<12 and time.monotonic()-wall<35:
            world=arena.step([queue.tick(world,set()),[]])[0];time.sleep(.02)
            if task.program.confirmed or task.status=='failed':break
        queue.tick(world,set())
        source=next(u for u in world.my(86) if u.distance(home)<15)
        outer=world.by_tag(outer.tag)
        result.update(state=task.status,reason=task.reason,order=task.program.public(),
            expansion_after=dict(tag=outer.tag,progress=outer.progress) if outer else None,
            raw_home_rallies=[list(t) for t in source.rally_targets],engine_action_errors=arena.action_errors)
        result['passed']=bool(outer and not outer.done and task.program.point
            and task.program.confirmed and result['automatic_threat_filter_retained']
            and any(math.dist(t[:2],task.program.point)<1 for t in source.rally_targets)
            and arena.action_errors==0)
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:result['error']=type(error).__name__+': '+str(error)
    finally:
        if queue:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k!='events'},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
