"""Native building cancel/refund without an LLM, fast-build or hidden opponent data."""
import argparse,json,time
from pathlib import Path
from absl import flags
from s2clientprotocol import debug_pb2 as debug
from arena.raw_engine import RawArena,command
from arena.tasks import TaskQueue


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--label',required=True)
    parser.add_argument('--race',choices=('protoss','terran','zerg'),default='protoss')
    parser.add_argument('--wait-game-s',type=int,default=0,help='Tick the original macro after construction starts, before cancellation (0..12)')
    parser.add_argument('--count',type=int,default=1,help='Finite building goal size (1..200)')
    args=parser.parse_args();flags.FLAGS(['native-building-cancel'])
    if not 0<=args.wait_game_s<=12 or not 1<=args.count<=200:parser.error('wait-game-s must be 0..12 and count 1..200')
    folder=Path('logs/native-building-cancel-'+args.label);folder.mkdir(exist_ok=False)
    product,product_id,cost,worker_id={'protoss':('pylon',60,100,84),
        'terran':('supply_depot',19,100,45),'zerg':('hatchery',86,300,104)}[args.race]
    result=dict(model_calls=0,debug_resources=True,fast_build=False,race=args.race,product=product,passed=False)
    arena=queue=None;events=[]
    try:
        arena=RawArena(render=False,races=[args.race,'terran'],game_speed='slower')
        arena.controllers[0].debug([debug.DebugCommand(game_state=debug.all_resources)])
        w=arena.step([[],[]])[0]
        queue=TaskQueue(arena,0,lambda _:None,lambda kind,**data:events.append(dict(kind=kind,**data)))
        queue.enqueue([dict(skill='build_structure',count=args.count,args=dict(structure=product,base='expansion' if args.race=='zerg' else 'home'))],w.seconds,'native control')
        original=queue.tasks[-1];baseline={u.tag for u in w.my(product_id)};wall=time.monotonic();building=None
        while time.monotonic()-wall<40:
            w=arena.step([queue.tick(w,set()),[]])[0]
            building=next((u for u in w.my(product_id) if u.tag not in baseline and not u.done),None)
            if building is not None:break
            time.sleep(.02)
        if building is None:raise AssertionError('unfinished native '+product+' not observed')
        until=w.seconds+args.wait_game_s;wall=time.monotonic()
        while w.seconds<until and time.monotonic()-wall<40:
            w=arena.step([queue.tick(w,set()),[]])[0];time.sleep(.02)
        building=w.by_tag(building.tag)
        if w.seconds<until or building is None or building.done:
            raise AssertionError('native construction not still pending after requested wait')
        # Stop fixture miners so raw collected-mineral score separates mining
        # income from the observed cancellation refund. This is not a battle.
        w=arena.step([[command(4,[u.tag for u in w.my(worker_id)])],[]])[0]
        before_min=w.minerals
        before_collected=arena.proto(0).observation.score.score_details.collected_minerals
        queue.enqueue([dict(skill='cancel_native_building',args=dict(task_id=original.id))],w.seconds,'native control')
        cancellation=queue.tasks[-1];wall=time.monotonic()
        while time.monotonic()-wall<40:
            w=arena.step([queue.tick(w,set()),[]])[0]
            if cancellation.status in ('completed','failed'):break
            time.sleep(.02)
        collected=arena.proto(0).observation.score.score_details.collected_minerals-before_collected
        refund=w.minerals-before_min-collected
        result.update(original_state=original.status,cancel_state=cancellation.status,
            requested_count=args.count,issued_count=original.program.issued,wait_game_s=args.wait_game_s,
            building_tag=building.tag,linked_building_gone=w.by_tag(building.tag) is None or w.by_tag(building.tag).type_id!=product_id,
            workers_after_cancel=len(w.my(worker_id)),
            minerals_before=before_min,minerals_after=w.minerals,collected_minerals_during_cancel=collected,
            refund_after_subtracting_mining=refund,
            native_dispatches=sum(e['kind']=='native_building_cancel_order_issued' for e in events),
            engine_action_errors=arena.action_errors,events=events)
        result['passed']=(original.status=='cancelled' and cancellation.status=='completed'
            and original.program.issued==1
            and result['linked_building_gone'] and result['native_dispatches']==1
            and abs(refund-cost*.75)<.5 and arena.action_errors==0)
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:result.update(error=type(error).__name__+': '+str(error),events=events)
    finally:
        if queue:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in result.items() if k!='events'},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
