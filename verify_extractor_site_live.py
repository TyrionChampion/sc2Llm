"""Zero-model native query/build control for the fourth duel's rejected geyser."""
import argparse
import hashlib
import json
import math
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
    parser.add_argument('--carrying',action='store_true',help='Require the actual placing Drone to carry harvested minerals first')
    args=parser.parse_args();flags.FLAGS(['extractor-site-control'])
    folder=Path('logs/extractor-site-'+args.label);folder.mkdir(exist_ok=False)
    result=dict(model_calls=0,debug_fixture=True,debug_resources=True,fast_build=False,
        passed=False,events=[],macro_sha256=hashlib.sha256(Path('arena/macro.py').read_bytes()).hexdigest(),
        scope='Controlled native geometry/query/build test; not the exact original mobile-unit/mineral-carrying state')
    arena=queue=None
    try:
        arena=RawArena(render=False,races=['zerg','protoss'],game_speed='slower',seed=20261020)
        home=arena.initial[0].my(86)[0]
        result['fixture_home']=list(home.pos)
        fixture=Fixture(arena)
        expansion=fixture.spawn(86,1,(117.5,60.5))[0]
        actor=fixture.spawn(104,1,(117.5,57.5))[0]
        arena.controllers[0].debug([debug.DebugCommand(game_state=debug.all_resources)])
        fixture.advance(1)
        # Pin the original target without changing the macro's choice rules.
        # The other geyser is occupied explicitly in this controlled fixture.
        others=[u for u in fixture.worlds[0].units if u.geyser and u.distance(expansion)<15
            and math.dist(u.pos,(124.5,63.5))>1]
        for geyser in others:
            builder=fixture.spawn(104,1,(geyser.x+3,geyser.y+3))[0]
            fixture.advance(1)
            fixture.advance(.1,actions=[[command(1154,[builder.tag],target=geyser.tag)],[]])
            until=fixture.worlds[0].seconds+30
            while fixture.worlds[0].seconds<until:
                if any(u.done and u.distance(geyser.pos)<1 for u in fixture.worlds[0].my(88)):break
                fixture.advance(.5)
            if not any(u.done and u.distance(geyser.pos)<1 for u in fixture.worlds[0].my(88)):
                raise AssertionError('setup failed to occupy the other geyser with an actual native Extractor')
        result['fixture_occupied_other_geysers']=[list(u.pos) for u in others]
        fixture.advance(1);world=fixture.worlds[0]
        if args.carrying:
            mineral=min((u for u in world.units if u.mineral),key=lambda u:u.distance(actor))
            fixture.advance(.1,actions=[[command(3666,[actor.tag],target=mineral.tag)],[]])
            until=fixture.worlds[0].seconds+8
            while fixture.worlds[0].seconds<until:
                current=fixture.worlds[0].by_tag(actor.tag)
                if 271 in current.buffs or 272 in current.buffs:break
                fixture.advance(.1)
            world=fixture.worlds[0];current=world.by_tag(actor.tag)
            result['builder_before']=dict(tag=current.tag,pos=list(current.pos),buffs=list(current.buffs))
            if not ({271,272}&set(current.buffs)):raise AssertionError('placing Drone did not actually harvest minerals')
        probes=[]
        for base,worker in ((home,world.my(104)[0]),(expansion,world.by_tag(actor.tag))):
            for geyser in world.units:
                if not geyser.geyser or geyser.distance(base)>15:continue
                point=arena.placement(0,1154,worker.tag,[geyser.pos])
                probes.append(dict(base=list(base.pos),geyser=list(geyser.pos),tag=geyser.tag,
                    accepted=point is not None,evidence=arena.placement_evidence(0,1154)))
        queue=TaskQueue(arena,0,lambda _:None,
            lambda kind,**kw:result['events'].append(dict(kind=kind,**kw)))
        queue.enqueue([dict(skill='build_structure',args=dict(structure='extractor',base='expansion'))],
            world.seconds,'native original expansion gas target control')
        task=queue.tasks[-1];began=world.seconds;wall=time.monotonic()
        while world.seconds-began<50 and time.monotonic()-wall<130:
            fixture.worlds=arena.step([queue.tick(world,set()),[]]);world=fixture.worlds[0];time.sleep(.02)
            if task.status in ('completed','failed'):break
        issued=[e for e in result['events'] if e['kind']=='macro_order_issued']
        result.update(probes=probes,state=task.status,reason=task.reason,orders=issued,
            engine_action_errors=arena.action_errors,
            actual_extractors=[dict(tag=u.tag,pos=list(u.pos),done=u.done) for u in world.my(88)],
            target_matches=bool(len(issued)==1 and math.dist(issued[0]['target_pos'],(124.5,63.5))<1),
            passed=bool(task.status in ('completed','failed') and len(issued)==1
                and math.dist(issued[0]['target_pos'],(124.5,63.5))<1),
            rejection_reproduced=task.status=='failed' and 'CantBuildLocationInvalid' in task.reason)
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:result['error']=type(error).__name__+': '+str(error)
    finally:
        if queue:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k!='events'},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
