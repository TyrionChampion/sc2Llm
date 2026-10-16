"""Zero-model native gas rejection after an acknowledged worker approach."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from absl import flags
from s2clientprotocol import debug_pb2 as debug

from arena.raw_engine import RawArena,command
from arena.tasks import TaskQueue
from verify_tactical_scenarios_live import Fixture


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',required=True)
    parser.add_argument('--expect-recovery',action='store_true')
    args=parser.parse_args();flags.FLAGS(['gas-approach-rejection-control'])
    folder=Path('logs/gas-approach-'+args.label);folder.mkdir(exist_ok=False)
    result=dict(model_calls=0,debug_fixture=True,debug_resources=True,fast_build=False,
        passed=False,expect_recovery=args.expect_recovery,events=[],commands=[],errors=[],
        macro_sha256=hashlib.sha256(Path('arena/macro.py').read_bytes()).hexdigest())
    arena=queue=None
    try:
        arena=RawArena(render=False,races=['zerg','zerg'],seed=20261021,game_speed='slower')
        fixture=Fixture(arena);world=fixture.worlds[0];home=arena.homes[0]
        geyser=min((u for u in world.units if u.geyser),key=lambda u:u.distance(home))
        enemy=fixture.spawn(104,2,fixture.point((geyser.x+3,geyser.y+3)))[0]
        world=fixture.worlds[0]
        arena.controllers[0].debug([
            debug.DebugCommand(kill_unit=debug.DebugKillUnit(tag=[u.tag for u in world.my(104)])),
            debug.DebugCommand(game_state=debug.all_resources)])
        fixture.advance(.5)
        toward=(88-home[0],86-home[1]);length=(toward[0]**2+toward[1]**2)**.5
        actor=fixture.spawn(104,1,fixture.point((home[0]+toward[0]*34/length,home[1]+toward[1]*34/length)))[0]
        world=fixture.worlds[0]
        queue=TaskQueue(arena,0,lambda _:None,
            lambda kind,**kw:result['events'].append(dict(kind=kind,**kw)))
        queue.enqueue([dict(skill='build_structure',args=dict(structure='extractor'))],world.seconds,'native gas approach rejection control')
        task=queue.tasks[-1];start=world.seconds;wall=time.monotonic();occupied=False
        while world.seconds-start<70 and time.monotonic()-wall<180:
            for error in world.errors:result['errors'].append(dict(game_s=world.seconds,error=list(error)))
            actions=queue.tick(world,set());enemy_actions=[]
            for a in actions:
                c=a.action_raw.unit_command
                result['commands'].append(dict(loop=world.game_loop,ability=c.ability_id,tags=list(c.unit_tags),target=c.target_unit_tag))
            if not occupied and any(p.get('seen') for p in task.program.pending):
                if task.spot!=geyser.pos:raise AssertionError('fixture did not select its intended first geyser')
                result['approach_before_occupancy']=dict(actor=actor.tag,orders=list(world.by_tag(actor.tag).orders),
                    pending_seen=True,pos=list(world.by_tag(actor.tag).pos),target=list(geyser.pos))
                enemy_actions=[command(1154,[enemy.tag],target=geyser.tag)];occupied=True
            if task.status in ('completed','failed'):break
            world=arena.step([actions,enemy_actions])[0];time.sleep(.02)
        result.update(state=task.status,reason=task.reason,issued=task.program.issued,
            actor_after_type=world.by_tag(actor.tag).type_id if world.by_tag(actor.tag) else None,
            own_extractors=[dict(tag=u.tag,pos=list(u.pos),done=u.done) for u in world.my(88)],
            enemy_extractors=[dict(tag=u.tag,pos=list(u.pos)) for u in world.enemy_units if u.type_id==88],
            engine_action_errors=arena.action_errors)
        result['native_rejection_observed']=any(e['error'][0]==actor.tag and e['error'][1]==1154 for e in result['errors'])
        result['passed']=bool(occupied and result['native_rejection_observed'] and
            (task.status=='completed' and len(result['own_extractors'])==1 if args.expect_recovery else task.status=='failed' and not result['own_extractors']))
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:result['error']=type(error).__name__+': '+str(error)
    finally:
        if queue:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k not in ('events','commands')},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
