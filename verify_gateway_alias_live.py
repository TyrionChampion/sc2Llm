"""Native purchase confirmation across automatic Gateway -> WarpGate conversion."""
import argparse,json,time
from pathlib import Path
from absl import flags
from s2clientprotocol import debug_pb2 as debug
from arena.raw_engine import RawArena
from arena.tasks import TaskQueue
from verify_tactical_scenarios_live import Fixture


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--label',required=True)
    args=parser.parse_args();flags.FLAGS(['gateway-alias-control'])
    folder=Path('logs/gateway-alias-'+args.label);folder.mkdir(exist_ok=False)
    result=dict(model_calls=0,debug_fixture=True,fast_build=False,passed=False)
    arena=queue=None;events=[]
    try:
        arena=RawArena(render=False,races=['protoss','terran'],game_speed='slower')
        fixture=Fixture(arena);home=fixture.worlds[0].my(59)[0]
        fixture.structure_near(60,home);fixture.structure_near(133,home)
        arena.controllers[0].debug([debug.DebugCommand(game_state=debug.upgrade),
                                  debug.DebugCommand(game_state=debug.all_resources)])
        fixture.advance(.25);w=fixture.worlds[0];baseline={u.tag for u in w.my(62)+w.my(133)}
        queue=TaskQueue(arena,0,lambda _:None,lambda kind,**data:events.append(dict(kind=kind,**data)))
        queue.enqueue([dict(skill='build_structure',args=dict(structure='gateway'))],w.seconds,'native gateway alias control')
        task=queue.tasks[-1];wall=time.monotonic()
        while time.monotonic()-wall<160:
            w=arena.step([queue.tick(w,set()),[]])[0]
            if task.status in ('completed','failed'):break
            time.sleep(.02)
        queue.tick(w,set())
        result.update(state=task.status,reason=task.reason,issued=task.program.issued,
            completed_tags=sorted(task.program.born),baseline_tags=sorted(task.program.baseline or ()),
            actual_new_gates=[dict(tag=u.tag,type_id=u.type_id,progress=u.progress) for u in w.my(62)+w.my(133) if u.tag not in baseline],
            engine_action_errors=arena.action_errors,events=events)
        result['passed']=(task.status=='completed' and task.program.issued==1 and len(task.program.born)==1
            and not baseline&task.program.born and arena.action_errors==0)
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:result.update(error=type(error).__name__+': '+str(error),events=events)
    finally:
        if queue:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in result.items() if k!='events'},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
