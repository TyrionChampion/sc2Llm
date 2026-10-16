"""Native counterfactual: a lagging stock snapshot must not double-spend food. No LLM."""
import argparse,json,math,time
from dataclasses import replace
from pathlib import Path
from absl import flags
from s2clientprotocol import debug_pb2 as d
from arena.raw_engine import RawArena
from arena.tasks import TaskQueue
from verify_tactical_scenarios_live import Fixture


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--label',required=True)
    p.add_argument('--legacy-reservations',action='store_true')
    args=p.parse_args();flags.FLAGS(['pending-purchase-control'])
    folder=Path('logs/pending-purchase-'+args.label);folder.mkdir(exist_ok=False)
    a=q=None;events=[]
    report=dict(model_calls=0,debug_fixture=True,fast_build=False,
        legacy_reservations=args.legacy_reservations,
        verification_goal='reproduce lost warp with reservations disabled' if args.legacy_reservations else 'no duplicate purchase and actual first-wave products',
        controlled_stale_snapshot=True,resource_checks_disabled_in_fixture_ability_queries=True)
    try:
        a=RawArena(render=False,races=['protoss','terran'],seed=7,game_speed='slower')
        f=Fixture(a);home=f.worlds[0].my(59)[0]
        dx,dy=88-home.x,86-home.y;distance=math.hypot(dx,dy)
        remote=f.point((home.x+24*dx/distance,home.y+24*dy/distance))
        f.structure_near(60,home);f.spawn(60,1,remote)
        for _ in range(4):f.structure_near(133,home)
        f.structure_near(72,home);f.structure_near(71,home)
        f.spawn(84,1,f.point((home.x,home.y+12)),count=12)
        a.controllers[0].debug([d.DebugCommand(game_state=d.all_resources)])
        f.advance(.5);w=f.worlds[0];initial=w
        report['initial_supply']=[w.supply_used,w.supply_cap]
        if w.supply_cap-w.supply_used!=7:raise AssertionError('fixture must have exactly seven free food')
        availability=a.available_abilities
        a.available_abilities=lambda index,tags,ignore_resources=False:availability(index,tags,ignore_resources=True)
        placement=a.placement
        a.placement=lambda index,ability,actor,points:placement(index,ability,actor,
            [xy for xy in points if math.dist(xy,remote)<6] if ability in (1413,1414) else points)
        q=TaskQueue(a,0,lambda _:None,lambda kind,**kw:events.append(dict(kind=kind,**kw)))
        if args.legacy_reservations:
            q.pending_purchase_reservations=lambda:dict(minerals=0,gas=0,supply=0,orders=0)
        q.enqueue([dict(skill='train_unit',args=dict(unit='immortal')),
            dict(skill='train_unit',count=2,args=dict(unit='zealot'))],w.seconds,'native stale-stock control')
        baseline={u.tag for u in w.my_units};start=w.seconds;wall=time.monotonic()
        first=q.tick(w,set());report['first_orders']=len(first)
        if len(first)!=2:raise AssertionError('first wave must cost six food: immortal, zealot')
        w=a.step([first,[]])[0]
        # Controlled one-frame observation lag, with native unit legality/cooldowns retained by queries.
        stale=replace(initial,game_loop=w.game_loop+12)
        extra=q.tick(stale,set());report['extra_orders_on_stale_stock']=len(extra)
        w=a.step([extra,[]])[0]
        while w.seconds-start<60 and time.monotonic()-wall<160:
            w=a.step([q.tick(w,set()),[]])[0];time.sleep(.02)
            owned={u.type_id for u in w.my_units if u.tag not in baseline and u.done}
            if {73,83}<=owned and (not args.legacy_reservations or
                any(e['kind'] in ('macro_failed','macro_retry') for e in events)):break
        report.update(game_s=w.seconds-start,engine_action_errors=a.action_errors,
            actual_products={str(kind):len([u for u in w.my(kind) if u.tag not in baseline and u.done]) for kind in (73,83)},
            task_states=[dict(skill=t.skill,state=t.status,product=t.program.spec['entry']['name'],
                requested=t.count,issued=t.program.issued,completed=len(t.program.born)) for t in q.tasks],events=events)
        report['task_states_meaning']='Queue states before the last native observation was ingested; actual_products directly verifies the final observed units.'
        report['passed']=(report['extra_orders_on_stale_stock']==(1 if args.legacy_reservations else 0)
            and all(report['actual_products'][str(k)]==1 for k in (73,83))
            and (args.legacy_reservations or (a.action_errors==0 and not any(e['kind']=='macro_failed' for e in events))))
        a.save_replay(folder/'control.SC2Replay')
    except Exception as e:report.update(passed=False,error=type(e).__name__+': '+str(e))
    finally:
        if q:q.close()
        if a:a.close()
        (folder/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in report.items() if k!='events'},ensure_ascii=False),flush=True)
    return 0 if report.get('passed') else 1


if __name__=='__main__':raise SystemExit(main())
