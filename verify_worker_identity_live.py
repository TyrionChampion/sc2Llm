"""Native old-worker reappearance versus a newly purchased worker; ZERO models.

Debug-created transport/fixture resources; no fast build and no competitive win.
"""
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
    if not flags.FLAGS.is_parsed():flags.FLAGS(['worker-identity'])
    root=Path(tempfile.mkdtemp(prefix='worker-identity-',dir=Path(__file__).resolve().parent/'logs'))
    report=dict(passed=False,fixture=True,debug_used=True,fast_build=False,model_calls=0)
    a=None
    try:
        a=LinuxStepArena(races=['protoss','terran']);w=a.initial[0]
        old=w.my(84)[0];initial_tags={u.tag for u in w.my(84)}
        q=TaskQueue(a,0,lambda _:None);q.tick(w,set())
        a.controllers[0].debug([debug.DebugCommand(game_state=debug.all_resources),
            debug.DebugCommand(create_unit=debug.DebugCreateUnit(unit_type=81,owner=1,
                pos=dict(x=old.x,y=old.y),quantity=1))])
        for _ in range(3):w=a.step([[],[]],step_loops=8)[0]
        tr=w.my(81)[0]
        w=a.step([[command(911,[tr.tag],target=old.tag)],[]],step_loops=8)[0]
        for _ in range(100):
            if old.tag in w.by_tag(tr.tag).passengers and w.by_tag(old.tag) is None:break
            w=a.step([[],[]],step_loops=8)[0];q.tick(w,set())
        else:raise AssertionError('old Probe did not become observed cargo/leave ground snapshot')
        q.enqueue([dict(skill='train_worker')],w.seconds)
        actions=q.tick(w,set());w=a.step([actions,[]],step_loops=8)[0]
        task=q.tasks[0];sent=w.seconds
        w=a.step([[command(913,[tr.tag],pos=old.pos)],[]],step_loops=8)[0]
        for _ in range(150):
            actions=q.tick(w,set());w=a.step([actions,[]],step_loops=8)[0]
            if not task.active:break
        actual_new=[u.tag for u in w.my(84) if u.tag not in initial_tags]
        report.update(old_worker_tag=old.tag,actual_new_workers=actual_new,born_tags=sorted(task.program.born),
            state=task.status,reason=task.reason,issued=task.program.issued,game_s_after_purchase=w.seconds-sent,
            action_errors=a.action_errors)
        report['passed']=(task.status=='completed' and old.tag not in task.program.born
                          and len(task.program.born)==1 and task.program.born<=set(actual_new) and a.action_errors==0)
        replay=a.controllers[0].save_replay();(root/'fixture.SC2Replay').write_bytes(replay)
        report['replay_sha256']=hashlib.sha256(replay).hexdigest()
    except Exception:report['error']=traceback.format_exc()
    finally:
        if a:a.close()
        (root/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('WORKER_IDENTITY_EVIDENCE',root,json.dumps(report,ensure_ascii=False),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
