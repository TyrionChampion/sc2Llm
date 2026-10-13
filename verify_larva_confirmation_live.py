"""Observe native larva purchases; default no debug, optional explicit batch fixture, zero LLM calls."""
import argparse,json,time
from pathlib import Path
from absl import flags
from arena.raw_engine import RawArena,NAMES
from arena.tasks import TaskQueue
from arena.intelligence import Intelligence
from arena.context import EngagementLog

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',required=True)
    parser.add_argument('--batch-fixture',action='store_true',help='Explicit resource/pool fixture for six zerglings; no fast build')
    parser.add_argument('--mixed-fixture',action='store_true',help='Explicit two-roach/one-overlord fixture for concurrent egg claims')
    parser.add_argument('--legacy-claims',action='store_true',help='Controlled replay of the earlier reservation rule; does not modify project sources')
    args=parser.parse_args();flags.FLAGS(['larva-confirmation'])
    folder=Path('logs/larva-confirmation-'+args.label);folder.mkdir(exist_ok=False)
    arena=queue=None;events=[];samples=[]
    if args.batch_fixture and args.mixed_fixture:parser.error('Select only one fixture')
    report=dict(model_calls=0,debug_used=args.batch_fixture or args.mixed_fixture,fast_build=False,legacy_claims=args.legacy_claims)
    try:
        arena=RawArena(render=False,races=['zerg','terran'],seed=7,game_speed='slower')
        w=arena.initial[0];product=104;count=1
        if args.batch_fixture or args.mixed_fixture:
            from verify_tactical_scenarios_live import Fixture
            from s2clientprotocol import debug_pb2
            fixture=Fixture(arena);fixture.structure_near(89,w.my(86)[0])
            if args.mixed_fixture:fixture.structure_near(97,w.my(86)[0])
            fixture.spawn(106,1,(w.my(86)[0].x+3,w.my(86)[0].y+3))
            arena.controllers[0].debug([debug_pb2.DebugCommand(game_state=debug_pb2.all_resources)])
            fixture.advance(.5);w=fixture.worlds[0];product=110 if args.mixed_fixture else 105;count=2 if args.mixed_fixture else 6
        if args.legacy_claims:
            import types
            import arena.tasks as task_module
            source=Path('arena/macro.py').read_text()
            a=source.index('                claimed=set()');b=source.index('                eggs=',a)
            source=source[:a]+("                claimed={tag for task in q.tasks if task.active and isinstance(task.program,MacroRun)\n"
                "                    for pending in task.program.pending for tag in pending['tags']}\n")+source[b:]
            legacy=types.ModuleType('legacy_egg_claims_control');exec(source,legacy.__dict__)
            task_module.MacroRun=legacy.MacroRun
        baseline={u.tag for u in w.my(product)}
        intel=Intelligence(lambda kind,**kw:events.append(dict(kind=kind,**kw)))
        engagements=EngagementLog(lambda kind,**kw:events.append(dict(kind=kind,**kw)))
        intel.update(w);engagements.update(w)
        queue=TaskQueue(arena,0,emit=lambda _:None,
            feedback=lambda kind,**kw:events.append(dict(kind=kind,**kw)))
        plans=[dict(skill='train_worker' if product==104 else 'train_unit',count=count,
            args={} if product==104 else dict(unit='roach' if args.mixed_fixture else 'zergling'))]
        if args.mixed_fixture:plans.append(dict(skill='build_supply',count=1,args={}))
        before_overlords={u.tag for u in w.my(106)}
        queue.enqueue(plans,w.seconds,'native confirmation control')
        task=queue.tasks[0];start=w.seconds;wall=time.monotonic();last=-1
        while w.seconds-start<30 and time.monotonic()-wall<90:
            actions=queue.tick(w,set());w=arena.step([actions,[]])[0];intel.update(w);engagements.update(w)
            if w.seconds-start>=last+0.5:
                last=w.seconds-start
                samples.append(dict(game_s=last,state=task.status,reason=task.reason,
                    larvae_and_eggs=[dict(tag=u.tag,type_id=u.type_id,name=NAMES.get(u.type_id),
                        progress=u.progress,orders=u.orders) for u in w.my_units if u.type_id in (151,103)],
                    new_products=[u.tag for u in w.my(product) if u.tag not in baseline]))
            if all(t.status=='completed' for t in queue.tasks):break
            time.sleep(.02)
        report.update(state=task.status,reason=task.reason,product=NAMES[product],count=count,
            new_products=[u.tag for u in w.my(product) if u.tag not in baseline],
            samples=samples,events=events,engine_action_errors=arena.action_errors)
        report['lifecycle']=dict(alerts=[e['intel_event'] for e in events if e['kind']=='intelligence_event'],
            observation_windows=engagements.public()['windows'])
        report['task_states']=[dict(skill=t.skill,state=t.status,reason=t.reason) for t in queue.tasks]
        if args.mixed_fixture:report['new_overlords']=[u.tag for u in w.my(106) if u.tag not in before_overlords]
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as e:report['error']=type(e).__name__+': '+str(e)
    finally:
        if queue:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in report.items() if k not in ('samples','events')},ensure_ascii=False),flush=True)
    return 0 if (report.get('state')=='completed' and len(report.get('new_products',[]))>=report.get('count',1)
        and all(t['state']=='completed' for t in report.get('task_states',[])) and not report['engine_action_errors']) else 1

if __name__=='__main__':raise SystemExit(main())
