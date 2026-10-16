"""Zero-model finite two-supply batch; real construction, no fast build."""
import argparse
import json
import time
from pathlib import Path

from absl import flags
from s2clientprotocol import debug_pb2 as debug
from arena.raw_engine import RawArena
from arena.tasks import TaskQueue


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',required=True)
    parser.add_argument('--race',choices=('terran','protoss','zerg'),required=True)
    args=parser.parse_args();flags.FLAGS(['native-building-batch'])
    folder=Path('logs/building-batch-'+args.label);folder.mkdir(exist_ok=False)
    result=dict(model_calls=0,debug_resources=True,fast_build=False,game_speed='slower',
                race=args.race,requested=2,passed=False,events=[],samples=[])
    arena=queue=None
    try:
        arena=RawArena(render=False,races=[args.race,'terran'],game_speed='slower')
        arena.controllers[0].debug([debug.DebugCommand(game_state=debug.all_resources)])
        w=arena.step([[],[]])[0];start=w.seconds;wall=time.monotonic();last=-1
        product={'terran':19,'protoss':60,'zerg':106}[args.race]
        baseline={u.tag for u in w.my(product)}
        queue=TaskQueue(arena,0,lambda _:None,
            lambda kind,**data:result['events'].append(dict(kind=kind,**data)))
        goal=dict(skill='build_supply',count=2)
        queue.enqueue([goal],w.seconds,'native finite batch control');task=queue.tasks[-1]
        replayed=False
        while w.seconds-start<100 and time.monotonic()-wall<260:
            w=arena.step([queue.tick(w,set()),[]])[0]
            if not replayed and task.program.issued:
                queue.enqueue([goal],w.seconds,'native finite batch control')
                replayed=True
                if len(queue.tasks)!=1 or task.count!=2:raise AssertionError('active goal was multiplied')
            if w.seconds-start>=last+1:
                last=w.seconds-start
                result['samples'].append(dict(game_s=last,state=task.status,
                    quantity=queue.production_quantity(task),
                    products=[dict(tag=u.tag,progress=u.progress) for u in w.my(product) if u.tag not in baseline]))
            if task.status in ('completed','failed'):break
            time.sleep(.02)
        new=[u.tag for u in w.my(product) if u.tag not in baseline and u.done]
        result.update(state=task.status,reason=task.reason,game_s=w.seconds-start,
            issued=task.program.issued,observed_new_products=new,duplicate_goal_coalesced=replayed,
            engine_action_errors=arena.action_errors,
            passed=(task.status=='completed' and task.program.issued==2 and len(new)==2
                    and replayed and len(queue.tasks)==1 and arena.action_errors==0))
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:result['error']=type(error).__name__+': '+str(error)
    finally:
        if queue:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k not in ('events','samples')},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
