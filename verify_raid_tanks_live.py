"""Zero-model native fixture: siege -> raid movement -> siege -> withdrawal.

Debug units/defenders build the scenario; it is not a competitive win.
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
    from arena.raw_engine import command,MOVE,ATTACK
    from arena.tasks import TaskQueue
    from arena.combat_micro import SIEGE
    if not flags.FLAGS.is_parsed():flags.FLAGS(['raid-tank-control'])
    folder=Path(tempfile.mkdtemp(prefix='raid-tanks-',dir=args.source_root/'logs'))
    report=dict(passed=False,fixture=True,debug_used=True,model_calls=0,samples=[],
        source_sha256={n:hashlib.sha256((args.source_root/n).read_bytes()).hexdigest() for n in ['arena/spatial.py','arena/raids.py']})
    a=None
    try:
        a=LinuxStepArena(races=['terran','zerg']);w=a.initial[0];home=a.homes[0];enemy=a.enemy_start(0)
        target=tuple(home[i]+.2*(enemy[i]-home[i]) for i in (0,1));retreat=(home[0]+5,home[1]+5)
        def spawn(kind,owner,pos,quantity=1):
            a.controllers[0].debug([debug.DebugCommand(create_unit=debug.DebugCreateUnit(unit_type=kind,owner=owner,
                pos=dict(x=pos[0],y=pos[1]),quantity=quantity))])
        spawn(48,1,retreat);spawn(32,1,(home[0]+6,home[1]+5))
        for _ in range(4):w=a.step([[],[]],step_loops=8)[0]
        tank=w.my(32)[0].tag;events=[];q=TaskQueue(a,0,lambda _:None,lambda k,**kw:events.append(dict(kind=k,**kw)))
        q.enqueue([dict(skill='raid',args=dict(unit='army',count=2,targets=[list(target)],retreat_target=list(retreat),
            retreat_enemy_count=2,dwell_s=120,timeout_s=180,reason='explicit tank raid and authorized withdrawal'))],w.seconds)
        start=w.seconds;forced_siege=False;spawned_defenders=False;types=[];moves=[]
        for _ in range(200):
            actions=q.tick(w,set());task=q.tasks[0];run=task.program;u=w.by_tag(tank)
            types.append(u.type_id if u else None)
            for action in actions:
                raw=action.action_raw.unit_command
                if raw.ability_id==MOVE and tank in raw.unit_tags:moves.append(dict(game_s=w.seconds,tank_type=u.type_id,target=[raw.target_world_space_pos.x,raw.target_world_space_pos.y]))
            report['samples'].append(dict(game_s=w.seconds,phase=run.phase,task_state=task.status,
                tank_type=u.type_id if u else None,tank_pos=list(u.pos) if u else None))
            if run.phase=='holding' or not task.active:break
            w=a.step([actions,[]],step_loops=16)[0]
            if moves and not forced_siege and w.by_tag(tank).type_id==33 and w.by_tag(tank).distance(run.route[0])<18:
                w=a.step([[command(SIEGE,[tank])],[]],step_loops=8)[0];forced_siege=True
            if forced_siege and not spawned_defenders and w.by_tag(tank).type_id==32:
                tank_pos=w.by_tag(tank).pos
                import math
                distance=max(1,math.dist(home,tank_pos))
                defender_point=tuple(tank_pos[i]+8*(tank_pos[i]-home[i])/distance for i in (0,1))
                spawn(110,2,defender_point,2)
                w=a.step([[],[]],step_loops=8)[0];spawned_defenders=True
            # Old code will otherwise wait until the task's full deadline.
            if not moves and w.seconds-start>15:break
        report.update(task_state=task.status,task_reason=task.reason,phase=run.phase,
            game_elapsed_s=w.seconds-start,forced_siege=forced_siege,defenders_spawned=spawned_defenders,
            move_commands=moves,observed_types=sorted(set(t for t in types if t is not None)),
            withdrawal_reason=run.withdraw_reason,workflow=run.summary(),engine_action_errors=a.action_errors)
        report['passed']=run.phase=='holding' and forced_siege and spawned_defenders and len(moves)>=2 and all(m['tank_type']==33 for m in moves) and a.action_errors==0
        replay=a.controllers[0].save_replay();(folder/'fixture.SC2Replay').write_bytes(replay)
        report['replay_sha256']=hashlib.sha256(replay).hexdigest()
        (folder/'events.json').write_text(json.dumps(events,ensure_ascii=False,indent=2))
    except Exception:report['error']=traceback.format_exc()
    finally:
        if a:a.close()
        (folder/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('RAID_TANK_EVIDENCE',folder,'passed',report['passed'],'phase',report.get('phase'),
        'reason',report.get('task_reason'),'error',report.get('error'),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
