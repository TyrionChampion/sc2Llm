"""Debug fixture: native ordinary Gateway births with a distant rally and steps.

Zero LLM calls, no fast build. Not a competitive match or counted victory.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import traceback

from absl import flags
from s2clientprotocol import debug_pb2 as debug
from arena.linux_step import LinuxStepArena
from arena.raw_engine import command
from arena.tasks import TaskQueue


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--step-loops',type=int,default=64)
    args=parser.parse_args()
    if not flags.FLAGS.is_parsed():flags.FLAGS(['verify_step_births'])
    folder=Path(tempfile.mkdtemp(prefix='step-births-',dir=Path(__file__).resolve().parent/'logs'))
    report=dict(passed=False,fixture=True,debug_used=True,fast_build=False,model_calls=0,step_loops=args.step_loops)
    a=None
    try:
        a=LinuxStepArena(races=['protoss','terran']);w=a.initial[0];home=a.homes[0]
        def spawn(kind,x,y):
            a.controllers[0].debug([debug.DebugCommand(create_unit=debug.DebugCreateUnit(
                unit_type=kind,owner=1,pos=dict(x=x,y=y),quantity=1))])
        # Fixture resources and completed tech/supply only. Unit training itself
        # retains normal engine cost and duration; no instant build cheat.
        a.controllers[0].debug([debug.DebugCommand(game_state=debug.all_resources)])
        for dx,dy in [(-6,0),(6,0),(0,6),(0,-6)]:spawn(60,home[0]+dx,home[1]+dy)
        spawn(62,home[0]+8,home[1]+4);spawn(72,home[0]-8,home[1]+4)
        for _ in range(4):w=a.step([[],[]],step_loops=8)[0]
        gate=w.my(62)[0]
        report['gateway']=dict(tag=gate.tag,pos=gate.pos,powered=gate.powered)
        assert gate.powered
        enemy=a.enemy_start(0)
        rally=tuple(home[i]+.15*(enemy[i]-home[i]) for i in (0,1))
        a.step([[command(3673,[gate.tag],pos=rally)],[]],step_loops=8)
        events=[];q=TaskQueue(a,0,lambda _:None,lambda k,**kw:events.append(dict(kind=k,**kw)))
        q.enqueue([dict(skill='train_unit',count=8,args={'unit':'stalker'})],w.seconds)
        seen=set();first_seen=[]
        for _ in range(260):
            if a.ended:break
            actions=q.tick(w,set());worlds=a.step([actions,[]],step_loops=args.step_loops);w=worlds[0]
            for u in w.my(74):
                if u.tag not in seen:
                    seen.add(u.tag);first_seen.append(dict(tag=u.tag,pos=u.pos,game_s=w.seconds,distance_to_gate=u.distance(gate)))
            if len(seen)>=8 and (not q.tasks[0].active or not q.tasks[0].program.pending):break
        task=q.tasks[0]
        report.update(actual_native_stalkers=len(seen),first_observations=first_seen,
            task_state=task.status,task_reason=task.reason,workflow=task.program.summary(),
            issued=task.program.issued,born_tags=sorted(task.program.born),bound_tags=sorted(task.program.bound_products),
            pending=task.program.pending,action_errors=a.action_errors)
        report['fixture_engine_ended']=a.ended
        report['passed']=len(seen)==8 and task.status=='completed' and len(task.program.born)==8 and a.action_errors==0
        replay=a.controllers[0].save_replay();(folder/'fixture.SC2Replay').write_bytes(replay)
        report['replay_sha256']=hashlib.sha256(replay).hexdigest()
        (folder/'events.json').write_text(json.dumps(events,ensure_ascii=False,indent=2))
    except Exception:report['error']=traceback.format_exc()
    finally:
        if a:a.close()
        (folder/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('STEP_BIRTH_EVIDENCE',folder,json.dumps(report,ensure_ascii=False),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
