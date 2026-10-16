"""Native causal control: a finished egg receipt cannot claim a newer paid egg.

The fixture deliberately assigns the new receipt an idle larva tag after observing
its real new egg, modelling the native larva redirection observed in the duel.
Purchases and Drone births are real; no model calls or fast-build cheats.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import time

from absl import flags
from s2clientprotocol import debug_pb2 as debug

from arena.raw_engine import RawArena
import arena.tasks as tasks
from verify_tactical_scenarios_live import Fixture


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',required=True)
    parser.add_argument('--baseline-source',type=Path)
    parser.add_argument('--expect-fixed',action='store_true')
    args=parser.parse_args();flags.FLAGS(['larva-receipt-handoff-control'])
    source=args.baseline_source or Path('arena/macro.py')
    if args.baseline_source:
        spec=importlib.util.spec_from_file_location('baseline_larva_receipt',source)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        tasks.MacroRun=module.MacroRun
    folder=Path('logs/larva-receipt-'+args.label);folder.mkdir(exist_ok=False)
    result=dict(model_calls=0,debug_fixture=True,debug_resources=True,fast_build=False,
        controlled_receipt_redirection=True,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),passed=False,events=[])
    arena=queue=None
    try:
        arena=RawArena(render=False,races=['zerg','terran'],seed=20261021,game_speed='slower')
        fixture=Fixture(arena);home=fixture.worlds[0].my(86)[0]
        fixture.spawn(106,1,(home.x+8,home.y+8),count=2)
        arena.controllers[0].debug([debug.DebugCommand(game_state=debug.all_resources)])
        fixture.advance(.5);world=fixture.worlds[0];baseline={u.tag for u in world.my(104)}
        queue=tasks.TaskQueue(arena,0,lambda _:None,
            lambda kind,**kw:result['events'].append(dict(kind=kind,**kw)))
        queue.enqueue([dict(skill='train_worker',count=2)],world.seconds,'native receipt handoff control')
        task=queue.tasks[-1];run=task.program
        world=arena.step([queue.tick(world,set()),[]])[0];run.next_at=float('inf')
        start=world.seconds;wall=time.monotonic()
        old=None
        while world.seconds-start<20 and time.monotonic()-wall<55:
            world=arena.step([queue.tick(world,set()),[]])[0];time.sleep(.02)
            if run.pending and run.pending[0].get('seen'):
                old=run.pending[0]['tags'][0]
                raw=arena.controllers[0].observe().observation.raw_data.units
                if any(u.tag==old and u.orders and u.orders[0].progress>=.72 for u in raw):break
        if old is None or world.by_tag(old) is None:raise AssertionError('first real egg was not observed before hatching')
        run.next_at=0
        actions=queue.tick(world,set())
        if run.issued!=2:raise AssertionError('fixture did not buy exactly its second Drone')
        new_receipt=run.pending[-1];world=arena.step([actions,[]])[0]
        until=world.seconds+2
        new=None
        while world.seconds<until:
            new=next((u for u in world.my(103) if u.tag!=old and 1342 in u.orders),None)
            if new:break
            world=arena.step([[],[]])[0];time.sleep(.02)
        if new is None:raise AssertionError('second real paid egg absent')
        idle=next((u for u in world.my(151)),None)
        if idle is None:raise AssertionError('fixture lacks the alternate requested larva')
        result.update(real_new_egg=new.tag,recorded_requested_larva=idle.tag,old_egg=old)
        new_receipt['tags']=[idle.tag]
        run.tags={tag for p in run.pending for tag in p['tags']}
        until=world.seconds+8
        while world.seconds<until and any(u.tag==old for u in world.my(103)):
            world=arena.step([[],[]])[0];time.sleep(.02)
        if any(u.tag==old for u in world.my(103)):raise AssertionError('old egg did not finish')
        queue.tick(world,set())
        result['pending_after_old_hatch']=[dict(tags=list(p['tags']),seen=p['seen'],sent=p['sent']) for p in run.pending]
        until=world.seconds+18;wall=time.monotonic()
        while world.seconds<until and time.monotonic()-wall<50:
            world=arena.step([queue.tick(world,set()) if task.active else [],[]])[0];time.sleep(.02)
            produced=[u.tag for u in world.my(104) if u.tag not in baseline]
            if len(produced)==2 and not task.active:break
        result.update(state=task.status,reason=task.reason,actual_drones=len([u for u in world.my(104) if u.tag not in baseline]),
            new_receipt_acknowledged=new_receipt['seen'],engine_action_errors=arena.action_errors,
            purchases=sum(e['kind']=='macro_order_issued' and e.get('product')=='drone' for e in result['events']))
        result['passed']=bool(result['actual_drones']==2 and result['purchases']==2 and arena.action_errors==0
            and (task.status=='completed' and new_receipt['seen'] if args.expect_fixed
                 else task.status=='failed' and not new_receipt['seen']))
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:result['error']=type(error).__name__+': '+str(error)
    finally:
        if queue:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k!='events'},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
