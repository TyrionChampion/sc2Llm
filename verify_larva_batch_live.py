"""Zero-model native Roach plus Zergling purchase with exactly three free supply."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from absl import flags
from s2clientprotocol import debug_pb2 as debug

from arena.raw_engine import RawArena
from arena.tasks import TaskQueue
from verify_tactical_scenarios_live import Fixture


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',required=True)
    args=parser.parse_args();flags.FLAGS(['larva-batch-control'])
    folder=Path('logs/larva-batch-'+args.label);folder.mkdir(exist_ok=False)
    result=dict(model_calls=0,debug_fixture=True,debug_resources=True,fast_build=False,
        passed=False,events=[],samples=[],batches=[],
        macro_sha256=hashlib.sha256(Path('arena/macro.py').read_bytes()).hexdigest())
    arena=queue=None
    try:
        arena=RawArena(render=False,races=['zerg','terran'],game_speed='slower',seed=7)
        fixture=Fixture(arena);home=fixture.worlds[0].my(86)[0]
        fixture.structure_near(89,home);fixture.structure_near(97,home)
        fixture.spawn(106,1,(home.x+3,home.y+3))
        fixture.spawn(110,1,(home.x+4,home.y+4),3)
        fixture.spawn(105,1,(home.x+5,home.y+5),2)
        arena.controllers[0].debug([debug.DebugCommand(game_state=debug.all_resources)])
        fixture.advance(1);world=fixture.worlds[0]
        result['initial_supply']=[world.supply_used,world.supply_cap]
        if world.supply_cap-world.supply_used!=3:raise AssertionError('fixture requires exactly three free supply')
        baseline={kind:{u.tag for u in world.my(kind)} for kind in (110,105)}
        queue=TaskQueue(arena,0,lambda _:None,
            lambda kind,**kw:result['events'].append(dict(kind=kind,**kw)))
        queue.enqueue([dict(skill='train_unit',count=1,args=dict(unit='roach')),
            dict(skill='train_unit',count=2,args=dict(unit='zergling'))],world.seconds,
            'explicit 2+1 supply native larva purchase control')
        began=world.seconds;wall=time.monotonic();next_sample=0
        while world.seconds-began<50 and time.monotonic()-wall<130:
            out=queue.tick(world,set())
            if out:result['batches'].append(dict(loop=world.game_loop,
                orders=[dict(ability=a.action_raw.unit_command.ability_id,
                    tags=list(a.action_raw.unit_command.unit_tags)) for a in out]))
            fixture.worlds=arena.step([out,[]]);world=fixture.worlds[0];time.sleep(.02)
            if world.seconds>=next_sample:
                result['samples'].append(dict(loop=world.game_loop,supply=[world.supply_used,world.supply_cap],
                    eggs=[dict(tag=u.tag,orders=list(u.orders)) for u in world.my(103)]))
                next_sample=world.seconds+.25
            if all(t.status in ('completed','failed') for t in queue.tasks):break
        actual={kind:[u.tag for u in world.my(kind) if u.tag not in baseline[kind]] for kind in baseline}
        result.update(states=[t.status for t in queue.tasks],reasons=[t.reason for t in queue.tasks],
            actual=actual,issued=[t.program.issued for t in queue.tasks],
            engine_action_errors=arena.action_errors,
            passed=all(t.status=='completed' for t in queue.tasks)
                and len(actual[110])==1 and len(actual[105])==2 and arena.action_errors==0)
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:result['error']=type(error).__name__+': '+str(error)
    finally:
        if queue:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k not in ('events','samples')},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
