"""Zero-model injection during a real native Lair upgrade, with native-order control."""
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
    parser.add_argument('--compare-native',action='store_true')
    args=parser.parse_args();flags.FLAGS(['morphing-base-injection-control'])
    folder=Path('logs/morphing-inject-'+args.label);folder.mkdir(exist_ok=False)
    result=dict(model_calls=0,debug_fixture=True,debug_resources=True,debug_queen_energy=True,
        fast_build=False,passed=False,compare_native=args.compare_native,events=[],
        queen_sha256=hashlib.sha256(Path('arena/queen.py').read_bytes()).hexdigest())
    arena=queue=None
    try:
        arena=RawArena(render=False,races=['zerg','terran'],seed=20261021,game_speed='slower')
        fixture=Fixture(arena);home=fixture.worlds[0].my(86)[0]
        fixture.structure_near(89,home)
        queen=fixture.spawn(126,1,(home.x+4,home.y+4))[0]
        fixture.values([queen],debug.DebugSetUnitValue.Energy,50)
        arena.controllers[0].debug([debug.DebugCommand(game_state=debug.all_resources)])
        fixture.advance(.5);world=fixture.worlds[0]
        queue=TaskQueue(arena,0,lambda _:None,
            lambda kind,**kw:result['events'].append(dict(kind=kind,**kw)))
        queue.enqueue([dict(skill='build_structure',args=dict(structure='lair',base='home'))],world.seconds,'native real Lair morph control')
        began=world.seconds;wall=time.monotonic()
        while world.seconds-began<5 and time.monotonic()-wall<15:
            world=arena.step([queue.tick(world,set()),[]])[0];time.sleep(.02)
            base=world.by_tag(home.tag)
            if base and 1216 in base.orders:break
        base=world.by_tag(home.tag)
        if 1216 not in base.orders:raise AssertionError('fixture did not observe the actual native Lair upgrade order')
        result['base_before']=dict(tag=base.tag,type_id=base.type_id,progress=base.progress,orders=list(base.orders))
        energy_before=world.by_tag(queen.tag).energy
        queue.enqueue([dict(skill='inject_larva',args=dict(base='home'))],world.seconds,'native morphing-base injection control')
        high=queue.tasks[-1]
        world=arena.step([queue.tick(world,set()),[]])[0]
        result['high_level_initial_state']=high.status;result['high_level_initial_reason']=high.reason
        if args.compare_native and high.status=='waiting':
            queue.enqueue([dict(skill='use_ability',args=dict(caster=queen.tag,
                ability=arena.tech_tree.ability_names[251],target=base.tag))],world.seconds,'native explicit ability control')
        began=world.seconds;wall=time.monotonic()
        while world.seconds-began<8 and time.monotonic()-wall<25:
            world=arena.step([queue.tick(world,set()),[]])[0];time.sleep(.02)
            base=world.by_tag(home.tag);actor=world.by_tag(queen.tag)
            if 11 in base.buffs and actor.energy<energy_before-20:break
        queue.tick(world,set());base=world.by_tag(home.tag);actor=world.by_tag(queen.tag)
        actual=11 in base.buffs and actor.energy<energy_before-20 and 1216 in base.orders
        result.update(high_level_state=high.status,high_level_reason=high.reason,
            base_after=dict(type_id=base.type_id,progress=base.progress,buffs=list(base.buffs),orders=list(base.orders)),
            energy_before=energy_before,energy_after=actor.energy,actual_injection_while_morphing=actual,
            high_level_restriction_reproduced=bool(args.compare_native and actual and high.status=='waiting'),
            engine_action_errors=arena.action_errors)
        result['passed']=bool(actual and arena.action_errors==0 and
            (high.status in ('waiting','completed') if args.compare_native else high.status=='completed'))
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:result['error']=type(error).__name__+': '+str(error)
    finally:
        if queue:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k!='events'},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
