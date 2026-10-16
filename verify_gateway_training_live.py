"""Zero-model train order control during a real new Gateway's automatic conversion."""
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
    args=parser.parse_args();flags.FLAGS(['gateway-training-control'])
    folder=Path('logs/gateway-training-'+args.label);folder.mkdir(exist_ok=False)
    result=dict(model_calls=0,debug_fixture=True,debug_resources=True,debug_upgrades=True,
        fast_build=False,passed=False,events=[],samples=[],commands=[],
        macro_sha256=hashlib.sha256(Path('arena/macro.py').read_bytes()).hexdigest())
    arena=queue=None
    try:
        arena=RawArena(render=False,races=['protoss','terran'],seed=20261022,game_speed='slower')
        fixture=Fixture(arena);home=fixture.worlds[0].my(59)[0]
        for _ in range(3):fixture.structure_near(60,home)
        fixture.structure_near(72,home)
        arena.controllers[0].debug([debug.DebugCommand(game_state=debug.upgrade),
            debug.DebugCommand(game_state=debug.all_resources)])
        fixture.advance(.25);world=fixture.worlds[0]
        queue=TaskQueue(arena,0,lambda _:None,
            lambda kind,**kw:result['events'].append(dict(kind=kind,**kw)))
        queue.enqueue([dict(skill='build_structure',args=dict(structure='gateway')),
            dict(skill='train_unit',args=dict(unit='zealot'))],world.seconds,'native Gateway transition training control')
        task=queue.tasks[-1];start=world.seconds;wall=time.monotonic();last=None
        while world.seconds-start<130 and time.monotonic()-wall<320:
            actions=queue.tick(world,set())
            for a in actions:
                c=a.action_raw.unit_command
                result['commands'].append(dict(loop=world.game_loop,ability=c.ability_id,tags=list(c.unit_tags)))
            state=dict(minerals=world.minerals,zealots=[u.tag for u in world.my(73)],
                gates=[dict(tag=u.tag,type_id=u.type_id,progress=u.progress,orders=list(u.orders))
                    for u in world.my(62)+world.my(133)],task_state=task.status)
            if state!=last:
                result['samples'].append(dict(game_s=world.seconds-start,**state));last=state
            if task.status in ('completed','failed'):break
            world=arena.step([actions,[]])[0];time.sleep(.02)
        result.update(state=task.status,reason=task.reason,issued=task.program.issued,
            actual_zealots=[u.tag for u in world.my(73) if u.done],engine_action_errors=arena.action_errors,
            unack_transition_reproduced=task.status=='failed' and 'not observed within' in task.reason)
        result['passed']=bool(task.status=='completed' and task.program.issued==1
            and len(result['actual_zealots'])==1 and arena.action_errors==0)
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:result['error']=type(error).__name__+': '+str(error)
    finally:
        if queue:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k not in ('events','samples','commands')},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
