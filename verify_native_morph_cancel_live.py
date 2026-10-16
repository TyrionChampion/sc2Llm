"""Zero-model paid building-morph cancellation after observed native progress."""
import argparse,json,time
from pathlib import Path
from absl import flags
from s2clientprotocol import debug_pb2 as debug
from arena.raw_engine import RawArena
from arena.tasks import TaskQueue
from verify_tactical_scenarios_live import Fixture

CASES={
    'lair':(0,86,(89,)),
    'hive':(0,100,(100,94)),
    'orbital_command':(1,18,(21,)),
    'planetary_fortress':(1,18,(22,)),
    'greater_spire':(0,92,(92,101)),
}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--label',required=True)
    p.add_argument('--products',nargs='+',choices=tuple(CASES),default=['lair','orbital_command'])
    args=p.parse_args();flags.FLAGS(['native-morph-cancel'])
    folder=Path('logs/native-morph-cancel-'+args.label);folder.mkdir(exist_ok=False)
    result=dict(model_calls=0,debug_fixture=True,fast_build=False,passed=False,cases=[],events=[])
    arena=None;queues=[]
    try:
        arena=RawArena(render=False,races=['zerg','terran'],game_speed='slower',seed=7)
        f=Fixture(arena);arena.controllers[0].debug([debug.DebugCommand(game_state=debug.all_resources)])
        for product in dict.fromkeys(args.products):
            index,_,prerequisites=CASES[product]
            home=f.worlds[index].my(86 if index==0 else 18)[0]
            for prerequisite in prerequisites:
                if f.worlds[index].my(prerequisite):continue
                if index==0:f.structure_near(prerequisite,home)
                else:f.spawn(prerequisite,index+1,f.point((home.x+8,home.y+4)))
        f.advance(.5)
        queues=[TaskQueue(arena,i,lambda _:None,
            lambda kind,player=i+1,**kw:result['events'].append(dict(player=player,kind=kind,**kw))) for i in (0,1)]
        for product in dict.fromkeys(args.products):
            index,producer,_=CASES[product]
            queue=queues[index];w=f.worlds[index];queue.enqueue([
                dict(skill='build_structure',count=2,args=dict(structure=product))],w.seconds,'native morph control')
            original=queue.tasks[-1];start=w.seconds;wall=time.monotonic()
            while time.monotonic()-wall<40:
                f.worlds=arena.step([q.tick(f.worlds[i],set()) for i,q in enumerate(queues)])
                if original.status=='failed':raise AssertionError(original.reason)
                if original.program.pending and all(p.get('seen') for p in original.program.pending):break
                time.sleep(.02)
            if not original.program.pending:raise AssertionError('bound native morph not retained')
            tag=original.program.pending[0]['tags'][0];until=f.worlds[index].seconds+5
            while f.worlds[index].seconds<until:
                f.worlds=arena.step([q.tick(f.worlds[i],set()) for i,q in enumerate(queues)]);time.sleep(.02)
            w=f.worlds[index];actor=w.by_tag(tag)
            if not actor or original.status=='completed':raise AssertionError('morph no longer in progress')
            before=(w.minerals,w.vespene);score=arena.proto(index).observation.score.score_details
            income=(score.collected_minerals,score.collected_vespene)
            queue.enqueue([dict(skill='cancel_native_morph',args=dict(task_id=original.id))],w.seconds,'explicit native morph cancel')
            cancel=queue.tasks[-1];wall=time.monotonic()
            while time.monotonic()-wall<40:
                f.worlds=arena.step([q.tick(f.worlds[i],set()) for i,q in enumerate(queues)]);time.sleep(.02)
                if cancel.status in ('completed','failed'):break
            until=f.worlds[index].seconds+2
            while f.worlds[index].seconds<until:
                f.worlds=arena.step([q.tick(f.worlds[i],set()) for i,q in enumerate(queues)]);time.sleep(.02)
            w=f.worlds[index];actor=w.by_tag(tag);score=arena.proto(index).observation.score.score_details
            case=dict(product=product,original_state=original.status,cancel_state=cancel.status,
                original_form_restored=actor is not None and actor.type_id==producer and actor.done,
                remaining_orders=list(actor.orders) if actor else None,issued=original.program.issued,
                dispatches=cancel.program.dispatches,engine_action_errors=arena.action_errors,
                minerals_returned_minus_income=w.minerals-before[0]-(score.collected_minerals-income[0]),
                gas_returned_minus_income=w.vespene-before[1]-(score.collected_vespene-income[1]))
            case['passed']=(case['original_form_restored'] and not case['remaining_orders']
                and original.status=='cancelled' and cancel.status=='completed' and original.program.issued==1
                and cancel.program.dispatches==1 and arena.action_errors==0)
            result['cases'].append(case)
            if not case['passed']:raise AssertionError('native morph cancellation did not satisfy observed end state')
        result['passed']=True;arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:result['error']=type(error).__name__+': '+str(error)
    finally:
        for queue in queues:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k!='events'},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
