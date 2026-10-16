"""Zero-model stop-future-production control: paid eggs hatch, no additional purchase."""
import argparse
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
    args=parser.parse_args();flags.FLAGS(['stop-production-control'])
    folder=Path('logs/stop-production-'+args.label);folder.mkdir(exist_ok=False)
    result=dict(model_calls=0,debug_fixture=True,debug_resources=True,fast_build=False,passed=False,events=[])
    arena=queue=None
    try:
        arena=RawArena(render=False,races=['zerg','terran'],seed=20261021,game_speed='slower')
        fixture=Fixture(arena);home=fixture.worlds[0].my(86)[0]
        fixture.structure_near(89,home);fixture.spawn(106,1,(home.x+8,home.y+8),count=2)
        arena.controllers[0].debug([debug.DebugCommand(game_state=debug.all_resources)])
        fixture.advance(.5);world=fixture.worlds[0]
        queue=TaskQueue(arena,0,lambda _:None,
            lambda kind,**kw:result['events'].append(dict(kind=kind,**kw)))
        queue.enqueue([dict(skill='train_unit',count=20,args=dict(unit='zergling'))],world.seconds,'native stop future production control')
        task=queue.tasks[-1];stopped=False;start=world.seconds;wall=time.monotonic()
        while world.seconds-start<35 and time.monotonic()-wall<90:
            if not stopped and any(p.get('seen') for p in task.program.pending):
                queue.enqueue([dict(skill='stop_production',args=dict(task_id=task.id))],world.seconds,'native explicit stop')
                result['quantity_at_stop']=queue.production_quantity(task);stopped=True
            world=arena.step([queue.tick(world,set()),[]])[0];time.sleep(.02)
            if stopped and task.status in ('completed','failed'):break
        result.update(state=task.status,quantity=queue.production_quantity(task),actual_zerglings=len(world.my(105)),
            engine_action_errors=arena.action_errors,
            native_orders_issued=sum(e['kind']=='macro_order_issued' and e.get('product')=='zergling' for e in result['events']))
        result['passed']=bool(stopped and task.status=='completed' and result['actual_zerglings']==2
            and result['native_orders_issued']==1 and result['quantity']['requested']==20
            and result['quantity']['cancelled_unissued']==18 and result['quantity']['observed_completed']==2
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
