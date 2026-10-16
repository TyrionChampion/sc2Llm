"""Zero-model native proof that producer selectors retain their actual building identity."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import time

from absl import flags
from s2clientprotocol import debug_pb2 as debug

from arena.battle_plans import capture,revalidate
from arena.raw_engine import RawArena
from arena.rally import producer_inventory
from arena.tasks import TaskQueue
from verify_tactical_scenarios_live import Fixture


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',required=True)
    args=parser.parse_args();flags.FLAGS(['rally-identity-control'])
    folder=Path('logs/rally-identity-'+args.label);folder.mkdir(exist_ok=False)
    result=dict(model_calls=0,debug_fixture=True,debug_kill_fixture_base=True,passed=False,
        source_sha256={name:hashlib.sha256(Path(name).read_bytes()).hexdigest()
            for name in ('arena/rally.py','arena/battle_plans.py')})
    arena=queue=None
    try:
        arena=RawArena(render=False,races=['zerg','terran'],seed=20261021,game_speed='slower')
        fixture=Fixture(arena);home=fixture.worlds[0].my(86)[0]
        second=fixture.spawn(86,1,(117.5,60.5))[0];world=fixture.worlds[0]
        queue=TaskQueue(arena,0,lambda _:None)
        inventory,labels=producer_inventory(arena,world,0)
        result['initial_labels']=labels
        if labels.get(second.tag)!='hatchery#2':raise AssertionError('fixture must publish second Hatchery as #2')
        before=capture(world,queue)
        arena.controllers[0].debug([debug.DebugCommand(kill_unit=debug.DebugKillUnit(tag=[home.tag]))])
        fixture.advance(.5);world=fixture.worlds[0]
        _,labels=producer_inventory(arena,world,0);result['after_death_labels']=labels
        plan=dict(tasks=[dict(skill='rally_units',args=dict(producers=['hatchery#2'],target='home'))])
        result['late_plan_errors']=revalidate(plan,before,world,queue,arena.homes[0],35)
        if result['late_plan_errors']:raise AssertionError('original surviving selector rejected')
        queue.enqueue(plan['tasks'],world.seconds,'native stable producer identity control')
        task=queue.tasks[-1];start=world.seconds;wall=time.monotonic()
        while world.seconds-start<5 and time.monotonic()-wall<15:
            world=arena.step([queue.tick(world,set()),[]])[0];time.sleep(.02)
            if task.program.confirmed or task.status=='failed':break
        source=world.by_tag(second.tag)
        result['confirmed_tag']=second.tag if source and any(math.dist(t[:2],task.program.point)<1 for t in source.rally_targets) else None
        fixture.worlds[0]=world
        new=fixture.spawn(86,1,home.pos)[0];world=fixture.worlds[0]
        _,labels=producer_inventory(arena,world,0);result['after_birth_labels']=labels
        result['stale_dead_selector_errors']=revalidate(dict(tasks=[dict(skill='rally_units',args=dict(producers=['hatchery#1'],target='home'))]),
            before,world,queue,arena.homes[0],35)
        result.update(engine_action_errors=arena.action_errors,
            passed=bool(labels.get(second.tag)=='hatchery#2' and labels.get(new.tag)=='hatchery#3'
                and result['confirmed_tag']==second.tag and result['stale_dead_selector_errors'] and arena.action_errors==0))
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:result['error']=type(error).__name__+': '+str(error)
    finally:
        if queue:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(result,ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
