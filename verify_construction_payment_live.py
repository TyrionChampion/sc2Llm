"""Zero-model native control: distinguish a worker approach from construction payment."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from absl import flags
from s2clientprotocol import debug_pb2 as debug

from arena.raw_engine import RawArena
from arena.tasks import TaskQueue
from verify_tactical_scenarios_live import Fixture


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',required=True)
    parser.add_argument('--race',choices=('protoss','terran','zerg'),default='protoss')
    args=parser.parse_args();flags.FLAGS(['construction-payment-control'])
    folder=Path('logs/construction-payment-'+args.label);folder.mkdir(exist_ok=False)
    result=dict(model_calls=0,debug_fixture=True,debug_resources=True,fast_build=False,
        race=args.race,passed=False,samples=[],events=[],
        tasks_sha256=hashlib.sha256(Path('arena/tasks.py').read_bytes()).hexdigest())
    arena=queue=None
    try:
        arena=RawArena(render=False,races=[args.race,'terran'],seed=20261021,game_speed='slower')
        fixture=Fixture(arena);world=fixture.worlds[0]
        worker={'protoss':84,'terran':45,'zerg':104}[args.race]
        product={'protoss':60,'terran':19,'zerg':89}[args.race]
        home=arena.homes[0]
        # Stop mining entirely so a mineral delta has an unambiguous cause.
        arena.controllers[0].debug([
            debug.DebugCommand(kill_unit=debug.DebugKillUnit(tag=[u.tag for u in world.my(worker)])),
            debug.DebugCommand(game_state=debug.all_resources)])
        fixture.advance(.5)
        toward=(88-home[0],86-home[1]);length=(toward[0]**2+toward[1]**2)**.5
        remote=fixture.point((home[0]+toward[0]*34/length,home[1]+toward[1]*34/length))
        actor=fixture.spawn(worker,1,remote)[0];world=fixture.worlds[0]
        queue=TaskQueue(arena,0,lambda _:None,
            lambda kind,**kw:result['events'].append(dict(kind=kind,**kw)))
        goal=(dict(skill='build_structure',args=dict(structure='spawning_pool'))
            if args.race=='zerg' else dict(skill='build_supply'))
        queue.enqueue([goal],world.seconds,'native construction payment control')
        task=queue.tasks[-1];initial=world.minerals;start=world.seconds;wall=time.monotonic()
        last=None
        while world.seconds-start<40 and time.monotonic()-wall<110:
            actions=queue.tick(world,set())
            current=world.by_tag(actor.tag)
            state=dict(minerals=world.minerals,orders=list(current.orders) if current else [],
                building_visible=bool(world.my(product)),
                receipt_seen=any(p.get('seen') for p in task.program.pending),
                reserved=queue.pending_purchase_reservations()['minerals'])
            if state!=last:
                result['samples'].append(dict(game_s=world.seconds-start,**state));last=state
            if world.my(product) or task.status=='failed':break
            world=arena.step([actions,[]])[0];time.sleep(.02)
        result.update(initial_minerals=initial,state=task.status,reason=task.reason,
            engine_action_errors=arena.action_errors,issued=task.program.issued,
            actual_buildings=[dict(tag=u.tag,pos=list(u.pos),progress=u.progress) for u in world.my(product)])
        result['unpaid_acknowledged_approach']=any(s['receipt_seen'] and not s['building_visible']
            and s['orders'] and s['minerals']==initial for s in result['samples'])
        result['unpaid_approach_lost_reservation']=any(s['receipt_seen'] and not s['building_visible']
            and s['orders'] and s['minerals']==initial and s['reserved']==0 for s in result['samples'])
        result['passed']=bool(result['actual_buildings'] and task.program.issued==1 and arena.action_errors==0)
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:result['error']=type(error).__name__+': '+str(error)
    finally:
        if queue:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k not in ('samples','events')},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
