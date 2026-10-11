"""Native real-time error fixture -> configured real LLM feedback, no win-rate claim."""
import json
import time
from pathlib import Path
from absl import app
from s2clientprotocol import debug_pb2 as debug, sc2api_pb2 as pb
from arena.native_engine import NativeArena
from arena.raw_engine import CC, MARINE, MOVE, ATTACK, command
from arena.commander import Commander
from arena.player_config import make_player


def main(argv):
    client,identity=make_player(0)
    if identity['provider']=='mock':raise RuntimeError('This verification requires a real configured LLM')
    result={'fixture':True,'realtime':True,'identity':identity};c=None
    try:
        with NativeArena(names=('Feedback fixture P1','Feedback fixture P2')) as arena:
            result['engine']=arena.version
            print('FIXTURE_ONLY: debug-created units; not a natural match or win-rate test',flush=True)
            c=Commander(arena,0,client,opening=False,feedback_dir='logs/feedback_live')
            def wait_for(predicate):
                end=time.monotonic()+20
                while time.monotonic()<end:
                    worlds=arena.step([[],[]])
                    if predicate(worlds):return worlds
                raise AssertionError('fixture observation timeout')
            home=arena.homes[0];commands=[]
            for owner,kind,dx in ((1,MARINE,7),(2,54,8)):
                d=debug.DebugCommand();u=d.create_unit;u.owner=owner;u.unit_type=kind
                u.quantity=1;u.pos.x=home[0]+dx;u.pos.y=home[1]+4;commands.append(d)
            arena.controllers[0].debug(commands)
            worlds=wait_for(lambda ws:ws[0].my(MARINE) and any(u.type_id==54 for u in ws[0].enemy_units))
            actor=worlds[0].my(MARINE)[0].tag
            target=next(u.tag for u in worlds[0].enemy_units if u.type_id==54)
            arena.controllers[0].actions(pb.RequestAction(actions=[command(MOVE,[actor],pos=home)]))
            arena.controllers[1].actions(pb.RequestAction(actions=[command(MOVE,[target],pos=(home[0]+45,home[1]))]))
            worlds=wait_for(lambda ws:ws[0].by_tag(target) is None)
            assert worlds[1].by_tag(target) and worlds[1].by_tag(target).health>0
            # Opponent confirmation above is test-only. Never include its snapshot in the P1 prompt.
            action=command(ATTACK,[actor],target=target)
            arena.register_action_context(0,[(action.SerializeToString(),{'source':'controlled_probe','task_id':None,'behavior':'stale_target_attack'})])
            worlds=arena.step([[action],[]]);c.ingest_feedback(worlds[0])
            assert any(e['result_name']=='NotSupported' for e in worlds[0].feedback),worlds[0].feedback
            d=debug.DebugCommand();d.kill_unit.tag.append(actor);arena.controllers[0].debug([d])
            worlds=wait_for(lambda ws:ws[0].by_tag(actor) is None)
            action=command(MOVE,[actor],pos=home)
            arena.register_action_context(0,[(action.SerializeToString(),{'source':'controlled_probe','task_id':None,'behavior':'dead_actor_move'})])
            worlds=arena.step([[action],[]]);c.ingest_feedback(worlds[0])
            assert any(e['result_name']=='Error' for e in worlds[0].feedback),worlds[0].feedback
            initial_ids=[e['id'] for e in c.feedback.pending if e['kind']=='engine_action_rejected']
            assert len(initial_ids)==2
            end=time.monotonic()+60
            while time.monotonic()<end:
                batch=c.tick(worlds[0]);worlds=arena.step([batch,[]]);c.ingest_feedback(worlds[0])
                if c.decisions and not any(e['id'] in initial_ids for e in c.feedback.pending):break
            assert c.decisions>0,'real LLM never produced an accepted plan'
            assert not any(e['id'] in initial_ids for e in c.feedback.pending),'error feedback was not delivered'
            records=[json.loads(line) for line in Path(c.feedback.path).read_text().splitlines()]
            request=next(r for r in records if r['record']=='request_queued')
            events=request['prompt']['feedback']['events']
            assert {e['result_name'] for e in events if e['kind']=='engine_action_rejected'}=={'Error','NotSupported'}
            for e in events:
                if e['kind']=='engine_action_rejected':
                    assert e['action_match']=='unique' and e['actions'][0]['context']['source']=='controlled_probe'
                    assert e['cause'].startswith('unknown')
            result.update(outcome='verified',timing=arena.timing(),commander=c.stats(),
                          feedback_ids=initial_ids,real_calls=client.calls,response=client.last_raw)
            print('REAL_LLM_FEEDBACK_VERIFIED',json.dumps(result,ensure_ascii=False),flush=True)
    finally:
        if c:c.stop();result['commander']=c.stats()
        Path('logs/feedback_live_result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False))

if __name__=='__main__':app.run(main)
