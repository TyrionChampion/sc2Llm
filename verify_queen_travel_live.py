"""Zero-model native long-distance injection, with an explicit travel deadline."""
import argparse
import json
import time
from pathlib import Path

from absl import flags
from s2clientprotocol import debug_pb2 as debug
from arena.raw_engine import RawArena
from arena.tasks import TaskQueue
from verify_tactical_scenarios_live import Fixture


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',required=True)
    args=parser.parse_args();flags.FLAGS(['queen-travel'])
    folder=Path('logs/queen-travel-'+args.label);folder.mkdir(exist_ok=False)
    report=dict(model_calls=0,debug_fixture=True,fast_build=False,game_speed='slower',
                passed=False,events=[],samples=[])
    arena=queue=None
    try:
        arena=RawArena(render=False,races=['zerg','terran'],game_speed='slower',seed=7)
        fixture=Fixture(arena);home=fixture.worlds[0].my(86)[0]
        east=home.x>88
        destination=(73.5,138.5) if east else (102.5,33.5)
        base=fixture.spawn(86,1,destination)[0]
        queen=fixture.spawn(126,1,fixture.point((home.x-1.5 if east else home.x+1.5,
                                               home.y-9 if east else home.y+9)))[0]
        fixture.values([queen],debug.DebugSetUnitValue.Energy,50)
        queue=TaskQueue(arena,0,lambda _:None,
            lambda kind,**data:report['events'].append(dict(kind=kind,**data)))
        before=fixture.worlds[0];start=before.seconds;wall=time.monotonic();last=-1
        queue.enqueue([dict(skill='inject_larva',args=dict(base='expansion',timeout_s=150))],
                      start,'explicit native travel control')
        task=queue.tasks[-1]
        while fixture.worlds[0].seconds-start<154 and time.monotonic()-wall<390:
            fixture.worlds=arena.step([queue.tick(fixture.worlds[0],set()),[]])
            w=fixture.worlds[0];caster=w.by_tag(queen.tag);target=w.by_tag(base.tag)
            if w.seconds-start>=last+1:
                last=w.seconds-start
                report['samples'].append(dict(game_s=last,state=task.status,
                    queen_pos=caster.pos if caster else None,energy=caster.energy if caster else None,
                    native_orders=list(caster.orders) if caster else [],
                    inject_buff=bool(target and 11 in target.buffs),
                    reserved=queen.tag in queue.protected()))
            if task.status in ('completed','failed'):break
            time.sleep(.02)
        confirmed=[e for e in report['events'] if e['kind']=='inject_larva_confirmed']
        dispatches=sum(e['kind']=='inject_larva_order_issued' for e in report['events'])
        elapsed=fixture.worlds[0].seconds-start
        report.update(task_state=task.status,reason=task.reason,game_s=elapsed,
            engine_action_errors=arena.action_errors,dispatches=dispatches,
            original_ninety_second_deadline_exceeded=elapsed>90,
            actual_energy_and_buff_confirmed=bool(confirmed),
            passed=(task.status=='completed' and elapsed>90 and bool(confirmed)
                    and dispatches==1 and arena.action_errors==0))
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:report['error']=type(error).__name__+': '+str(error)
    finally:
        if queue:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in report.items() if k not in ('events','samples')},ensure_ascii=False),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
