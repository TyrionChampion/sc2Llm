"""Native zero-model control: explicit expansion under an unarmed enemy scout.

Debug resources and spawned fixture scout/worker, normal fog and construction time.
Not a competitive match or model decision test.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import traceback


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parent)
    args=parser.parse_args();sys.path.insert(0,str(args.source_root.resolve()))
    from absl import flags
    from dataclasses import replace
    from s2clientprotocol import debug_pb2 as debug
    from arena.linux_step import LinuxStepArena
    from arena.macro import expansion_clusters
    from arena.tasks import TaskQueue
    if not flags.FLAGS.is_parsed():flags.FLAGS(['explicit-expansion-control'])
    folder=Path(tempfile.mkdtemp(prefix='explicit-expansion-',dir=args.source_root/'logs'))
    report=dict(passed=False,fixture=True,debug_used=True,fast_build=False,model_calls=0,
        macro_sha256=hashlib.sha256((args.source_root/'arena/macro.py').read_bytes()).hexdigest())
    a=None
    try:
        a=LinuxStepArena(races=['protoss','zerg']);w=a.initial[0];home=a.homes[0]
        groups=[]
        for name,g in expansion_clusters(w):
            center=(sum(u.x for u in g)/len(g),sum(u.y for u in g)/len(g))
            if math.dist(center,home)>18:groups.append((math.dist(center,home),name,center))
        _,site,center=min(groups)
        a.controllers[0].debug([debug.DebugCommand(game_state=debug.all_resources),
            debug.DebugCommand(create_unit=debug.DebugCreateUnit(unit_type=84,owner=1,pos=dict(x=center[0],y=center[1]),quantity=1)),
            debug.DebugCommand(create_unit=debug.DebugCreateUnit(unit_type=106,owner=2,pos=dict(x=center[0],y=center[1]),quantity=1))])
        for _ in range(4):w=a.step([[],[]],step_loops=8)[0]
        visible=[u for u in w.enemy_units if u.type_id==106 and u.distance(center)<12]
        assert visible,'fixture did not expose the actual enemy Overlord'
        events=[];q=TaskQueue(a,0,lambda _:None,lambda k,**kw:events.append(dict(kind=k,**kw)))
        q.enqueue([dict(skill='expand',args=dict(base=site))],w.seconds);task=q.tasks[0]
        actor=min(w.my(84),key=lambda u:u.distance(center));run=task.program;recipe=run.spec['entry']['recipes'][0]
        # Independent native positive query proves the unarmed flying scout does
        # not make this site unbuildable. Only this query omits the enemy from the
        # candidate heuristic; the real queue below receives the original world.
        points=run._positions(replace(w,units=tuple(u for u in w.units if u.alliance!=4)),q,task,actor,recipe)
        accepted=a.placement(0,recipe['ability'],actor.tag,points)
        report.update(site=site,center=list(center),visible_scout_tags=[u.tag for u in visible],
            positive_native_placement=list(accepted) if accepted else None)
        assert accepted,'native control query did not accept a site'
        start=w.seconds
        for n in range(80):
            actions=q.tick(w,set())
            if n==0 and not actions:break
            w=a.step([actions,[]],step_loops=64)[0]
            if task.status in ('completed','failed'):break
        new=[u for u in w.my(59) if u.distance(center)<18]
        report.update(task_state=task.status,task_reason=task.reason,issued=run.issued,born_tags=sorted(run.born),
            actual_nearby_nexus=[dict(tag=u.tag,pos=list(u.pos),progress=u.progress) for u in new],
            game_elapsed_s=w.seconds-start,engine_action_errors=a.action_errors,workflow=run.summary())
        report['passed']=task.status=='completed' and run.issued==1 and len(run.born)==1 and any(u.done for u in new) and a.action_errors==0
        replay=a.controllers[0].save_replay();(folder/'fixture.SC2Replay').write_bytes(replay)
        report['replay_sha256']=hashlib.sha256(replay).hexdigest()
        (folder/'events.json').write_text(json.dumps(events,ensure_ascii=False,indent=2))
    except Exception:report['error']=traceback.format_exc()
    finally:
        if a:a.close()
        (folder/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('EXPLICIT_EXPANSION_EVIDENCE',folder,json.dumps(report,ensure_ascii=False),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
