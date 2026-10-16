"""Zero-model native gas policy control with each allocated worker's actual orders."""
import argparse
import json
from pathlib import Path
import time

from absl import flags
from s2clientprotocol import debug_pb2 as debug

from arena.raw_engine import RawArena
from arena.tasks import TaskQueue


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',required=True)
    parser.add_argument('--race',choices=('zerg','protoss','terran'),default='zerg')
    args=parser.parse_args();flags.FLAGS(['gas-allocation-control'])
    folder=Path('logs/gas-allocation-'+args.label);folder.mkdir(exist_ok=False)
    result=dict(model_calls=0,debug_resources=True,fast_build=False,race=args.race,passed=False,samples=[],events=[])
    arena=queue=None
    try:
        arena=RawArena(render=False,races=[args.race,'terran'],seed=20261021,game_speed='slower')
        arena.controllers[0].debug([debug.DebugCommand(game_state=debug.all_resources)])
        world=arena.step([[],[]])[0]
        queue=TaskQueue(arena,0,lambda _:None,
            lambda kind,**kw:result['events'].append(dict(kind=kind,**kw)))
        structure={'zerg':'extractor','protoss':'assimilator','terran':'refinery'}[args.race]
        queue.enqueue([dict(skill='build_structure',args=dict(structure=structure))],world.seconds,'native gas control')
        start=world.seconds;wall=time.monotonic()
        while world.seconds-start<45 and time.monotonic()-wall<115:
            world=arena.step([queue.tick(world,set()),[]])[0];time.sleep(.02)
            if queue.tasks[0].status in ('completed','failed'):break
        if queue.tasks[0].status!='completed':raise AssertionError('gas construction failed: '+queue.tasks[0].reason)
        queue.enqueue([dict(skill='gather_gas',args=dict(workers_per_refinery=3))],world.seconds,'native gas control')
        task=queue.tasks[-1];start=world.seconds;wall=time.monotonic();gas_before=world.vespene;last=None
        while world.seconds-start<25 and time.monotonic()-wall<65:
            world=arena.step([queue.tick(world,set()),[]])[0];time.sleep(.02)
            rows=[]
            for tag,ref in sorted(queue.gas_assignments.items()):
                u=world.by_tag(tag)
                rows.append(dict(tag=tag,refinery=ref,observed=u is not None,
                    **(dict(type_id=u.type_id,orders=list(u.orders),targets=list(u.order_targets),buffs=list(u.buffs),pos=list(u.pos)) if u else {})))
            state=dict(gas=world.vespene,refineries=[dict(tag=u.tag,assigned=u.assigned_harvesters) for u in world.my_units if u.gas_building],workers=rows)
            if state!=last:
                result['samples'].append(dict(game_s=world.seconds-start,**state));last=state
        result.update(state=task.status,reason=task.reason,gas_increase=world.vespene-gas_before,
            engine_action_errors=arena.action_errors,assignments=queue.gas_assignments,
            observed_full_saturation=any(r['assigned']==3 for s in result['samples'] for r in s['refineries']))
        result['passed']=bool(task.status=='completed' and result['gas_increase']>0 and arena.action_errors==0)
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:result['error']=type(error).__name__+': '+str(error)
    finally:
        if queue:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k not in ('events','samples')},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
