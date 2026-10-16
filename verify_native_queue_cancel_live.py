"""Zero-model native training/research queue cancellation; explicitly debug fixtures."""
import argparse,json,time
from pathlib import Path
from absl import flags
from s2clientprotocol import debug_pb2 as debug
from arena.raw_engine import RawArena
from arena.tasks import TaskQueue
from verify_tactical_scenarios_live import Fixture


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--label',required=True)
    args=parser.parse_args();flags.FLAGS(['native-queue-cancel'])
    folder=Path('logs/native-queue-cancel-'+args.label);folder.mkdir(exist_ok=False)
    result=dict(model_calls=0,debug_fixture=True,fast_build=False,passed=False,cases=[])
    arena=queue=None;events=[]
    try:
        arena=RawArena(render=False,races=['terran','protoss'],game_speed='slower')
        fixture=Fixture(arena);home=fixture.worlds[0].my(18)[0]
        arena.controllers[0].debug([debug.DebugCommand(game_state=debug.all_resources)])
        fixture.structure_near(21,home);fixture.structure_near(22,home);fixture.advance(.25)
        queue=TaskQueue(arena,0,lambda _:None,lambda kind,**kw:events.append(dict(kind=kind,**kw)))
        for goal in (dict(skill='train_worker',count=4),dict(skill='research',args=dict(upgrade='terran_infantry_weapons_level1'))):
            w=fixture.worlds[0];queue.enqueue([goal],w.seconds,'native control')
            original=queue.tasks[-1];wall=time.monotonic()
            while time.monotonic()-wall<35:
                fixture.worlds=arena.step([queue.tick(fixture.worlds[0],set()),[]])
                if original.status=='failed':raise AssertionError(original.reason)
                if original.program.pending and all(p.get('seen') for p in original.program.pending):break
                time.sleep(.02)
            if not original.program.pending or not all(p.get('seen') for p in original.program.pending):
                raise AssertionError('native accepted queue not observed')
            actors={tag for p in original.program.pending for tag in p['tags']}
            if len(actors)!=1:raise AssertionError('fixture requires one owned producer')
            tag=next(iter(actors));w=fixture.worlds[0]
            before_workers=len(w.my(45));before_min=w.minerals;before_gas=w.vespene
            score=arena.proto(0).observation.score.score_details
            before_income=(score.collected_minerals,score.collected_vespene)
            queue.enqueue([dict(skill='cancel_native_queue',args=dict(task_id=original.id))],w.seconds,'native control')
            cancellation=queue.tasks[-1];wall=time.monotonic()
            while time.monotonic()-wall<35:
                fixture.worlds=arena.step([queue.tick(fixture.worlds[0],set()),[]])
                if cancellation.status in ('completed','failed'):break
                time.sleep(.02)
            until=fixture.worlds[0].seconds+3
            while fixture.worlds[0].seconds<until:
                fixture.worlds=arena.step([queue.tick(fixture.worlds[0],set()),[]]);time.sleep(.02)
            w=fixture.worlds[0];actor=w.by_tag(tag);score=arena.proto(0).observation.score.score_details
            no_product=(len(w.my(45))==before_workers if goal['skill']=='train_worker'
                        else original.program.spec['entry']['id'] not in w.upgrades)
            case=dict(goal=goal,original_state=original.status,cancel_state=cancellation.status,
                producer_tag=tag,native_queue_empty=actor is not None and not actor.orders,
                requested_product_absent=no_product,dispatches=cancellation.program.dispatches,
                observed_mineral_delta_minus_income=w.minerals-before_min-(score.collected_minerals-before_income[0]),
                observed_gas_delta_minus_income=w.vespene-before_gas-(score.collected_vespene-before_income[1]),
                engine_action_errors=arena.action_errors)
            case['passed']=(original.status=='cancelled' and cancellation.status=='completed'
                and case['native_queue_empty'] and no_product and case['dispatches']==1 and arena.action_errors==0)
            result['cases'].append(case)
            if not case['passed']:raise AssertionError('native queue cancellation failed: '+json.dumps(case))
        result.update(passed=True,engine_action_errors=arena.action_errors,events=events)
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:result.update(error=type(error).__name__+': '+str(error),events=events)
    finally:
        if queue:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in result.items() if k!='events'},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
