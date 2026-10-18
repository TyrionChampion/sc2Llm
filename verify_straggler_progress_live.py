"""Zero-model native fixture: 32 arrived Roaches must wait for one moving Queen."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import traceback


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parent)
    args=parser.parse_args();sys.path.insert(0,str(args.source_root.resolve()))
    from absl import flags
    from s2clientprotocol import debug_pb2 as debug
    from arena.linux_step import LinuxStepArena
    from arena.tasks import TaskQueue
    if not flags.FLAGS.is_parsed():flags.FLAGS(['straggler-control'])
    folder=Path(tempfile.mkdtemp(prefix='straggler-progress-',dir=args.source_root/'logs'))
    report=dict(passed=False,fixture=True,debug_used=True,model_calls=0,
        spatial_sha256=hashlib.sha256((args.source_root/'arena/spatial.py').read_bytes()).hexdigest(),samples=[])
    a=None
    try:
        a=LinuxStepArena(races=['zerg','terran']);w=a.initial[0];home=a.homes[0];enemy=a.enemy_start(0)
        target=tuple(home[i]+.45*(enemy[i]-home[i]) for i in (0,1))
        a.controllers[0].debug([
            debug.DebugCommand(create_unit=debug.DebugCreateUnit(unit_type=110,owner=1,pos=dict(x=target[0],y=target[1]),quantity=32)),
            debug.DebugCommand(create_unit=debug.DebugCreateUnit(unit_type=126,owner=1,pos=dict(x=home[0]+5,y=home[1]+5),quantity=1))])
        for _ in range(4):w=a.step([[],[]],step_loops=8)[0]
        assert len(w.my(110))==32 and len(w.my(126))==1,'fixture units missing'
        events=[];q=TaskQueue(a,0,lambda _:None,lambda k,**kw:events.append(dict(kind=k,**kw)))
        q.enqueue([dict(skill='order_units',args=dict(unit='army',count='all',mode='attack_move',
            target=list(target),timeout_s=150,reason='explicit mixed-speed rendezvous'))],w.seconds)
        start=w.seconds
        for _ in range(55):
            actions=q.tick(w,set());task=q.tasks[0];queen=w.my(126)[0]
            report['samples'].append(dict(game_s=w.seconds,queen_pos=list(queen.pos),queen_orders=list(queen.orders),
                task_state=task.status,changed_at=task.program.changed_at,route=task.program.route,
                roach_center=[sum(u.x for u in w.my(110))/32,sum(u.y for u in w.my(110))/32]))
            if not task.active:break
            w=a.step([actions,[]],step_loops=64)[0]
        report.update(task_state=task.status,task_reason=task.reason,actual_queen_pos=list(queen.pos),
            game_elapsed_s=w.seconds-start,engine_action_errors=a.action_errors,workflow=task.program.summary())
        report['passed']=task.status=='completed' and w.seconds-start>20 and a.action_errors==0
        replay=a.controllers[0].save_replay();(folder/'fixture.SC2Replay').write_bytes(replay)
        report['replay_sha256']=hashlib.sha256(replay).hexdigest()
        (folder/'events.json').write_text(json.dumps(events,ensure_ascii=False,indent=2))
    except Exception:report['error']=traceback.format_exc()
    finally:
        if a:a.close()
        (folder/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('STRAGGLER_PROGRESS_EVIDENCE',folder,'passed',report['passed'],'state',report.get('task_state'),
        'reason',report.get('task_reason'),'error',report.get('error'),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
