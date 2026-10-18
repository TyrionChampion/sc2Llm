"""Zero-model native control for an owned squad member idling before arrival.

A fixture STOP creates the observed idle condition; this does not claim to
reproduce the exact unexplained cause of the competitive game's idle members.
"""
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
    from arena.raw_engine import command
    from arena.tasks import TaskQueue
    if not flags.FLAGS.is_parsed():flags.FLAGS(['idle-redispatch-control'])
    folder=Path(tempfile.mkdtemp(prefix='spatial-redispatch-',dir=args.source_root/'logs'))
    report=dict(passed=False,fixture=True,debug_used=True,model_calls=0,forced_idle_member=True,
        spatial_sha256=hashlib.sha256((args.source_root/'arena/spatial.py').read_bytes()).hexdigest(),samples=[])
    a=None
    try:
        a=LinuxStepArena(races=['terran','zerg']);w=a.initial[0];home=a.homes[0];enemy=a.enemy_start(0)
        target=tuple(home[i]+.2*(enemy[i]-home[i]) for i in (0,1))
        a.controllers[0].debug([debug.DebugCommand(create_unit=debug.DebugCreateUnit(unit_type=48,owner=1,
            pos=dict(x=home[0]+5,y=home[1]+5),quantity=2))])
        for _ in range(4):w=a.step([[],[]],step_loops=8)[0]
        assert len(w.my(48))==2
        stopped=sorted(u.tag for u in w.my(48))[-1];events=[]
        q=TaskQueue(a,0,lambda _:None,lambda k,**kw:events.append(dict(kind=k,**kw)))
        q.enqueue([dict(skill='order_units',args=dict(unit='marine',count=2,mode='move',
            target=list(target),timeout_s=90,reason='explicit original target'))],w.seconds)
        actions=q.tick(w,set());w=a.step([actions,[]],step_loops=8)[0]
        w=a.step([[command(4,[stopped])],[]],step_loops=8)[0]
        report.update(stopped_tag=stopped,stopped_pos=list(w.by_tag(stopped).pos),requested_target=list(target))
        start=w.seconds
        for _ in range(65):
            actions=q.tick(w,set());task=q.tasks[0]
            report['samples'].append(dict(game_s=w.seconds,task_state=task.status,
                units=[dict(tag=u.tag,pos=list(u.pos),orders=list(u.orders)) for u in w.my(48)]))
            if not task.active:break
            w=a.step([actions,[]],step_loops=32)[0]
        retries=[e for e in events if e['kind']=='spatial_order_redispatched']
        report.update(task_state=task.status,task_reason=task.reason,game_elapsed_s=w.seconds-start,
            retries=retries,workflow=task.program.summary(),engine_action_errors=a.action_errors)
        report['passed']=task.status=='completed' and len(retries)==1 and retries[0]['unit_tags']==[stopped] and a.action_errors==0
        replay=a.controllers[0].save_replay();(folder/'fixture.SC2Replay').write_bytes(replay)
        report['replay_sha256']=hashlib.sha256(replay).hexdigest()
        (folder/'events.json').write_text(json.dumps(events,ensure_ascii=False,indent=2))
    except Exception:report['error']=traceback.format_exc()
    finally:
        if a:a.close()
        (folder/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('SPATIAL_REDISPATCH_EVIDENCE',folder,'passed',report['passed'],'state',report.get('task_state'),
        'reason',report.get('task_reason'),'error',report.get('error'),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
