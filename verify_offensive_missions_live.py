"""Real SC2 raid/drop positive controls; debug-spawned fixtures, ZERO model calls.

Normal fog, two actual participants. No match/win or LLM strategy claim. Every run
uses a fresh evidence directory and never reads or resets existing request ledgers.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
import traceback

from absl import flags
from s2clientprotocol import debug_pb2 as debug

from arena.raw_engine import RawArena, ATTACK, MOVE
from arena.rally import GroundMap
from arena.tasks import TaskQueue


def scenario(race, folder):
    report=dict(passed=False, fixture=True, debug_spawn=True, model_calls=0, race=race,
                scope='native engine tool control, not LLM decision quality or a competitive match')
    arena=None
    sources=['arena/raids.py','arena/tasks.py','arena/spatial.py','arena/battle_plans.py',
             'arena/commander.py','arena/plan_representation.py','verify_offensive_missions_live.py']
    source_root=Path(__file__).resolve().parent
    source_hashes={p:hashlib.sha256((source_root/p).read_bytes()).hexdigest() for p in sources}
    report['source_sha256']=source_hashes
    try:
        arena=RawArena(render=False,races=[race,'terran'])
        worlds=arena.step([[],[]]);q=TaskQueue(arena,0,lambda _:None,
            lambda kind,**kw:events.append(dict(kind=kind,**kw)))
        events=[];grid=GroundMap(arena.info.start_raw)
        start=arena.homes[0]
        candidates=[]
        for x in range(4,grid.w-4,2):
            for y in range(4,grid.h-4,2):
                p=(x+.5,y+.5)
                if (grid.free(x,y) and 14<=math.dist(p,start)<=55
                    and all(grid.free(x+dx,y+dy) for dx in range(-3,4) for dy in range(-3,4))
                    and not any(u.distance(p)<7 for u in worlds[0].units if u.is_building or u.mineral or u.geyser)):
                    candidates.append(p)
        if not candidates:raise RuntimeError('no connected open fixture points')
        pickup=min(candidates,key=lambda p:math.dist(p,start))
        distances=arena.path_distances(0,pickup,candidates)
        destinations=[p for p,d in zip(candidates,distances) if 18<=math.dist(p,pickup)<=32 and 0<d<=45]
        if not destinations:raise RuntimeError('no distant reachable fixture landing point')
        target=max(destinations,key=lambda p:math.dist(p,pickup))
        retreat=pickup
        report.update(pickup=list(pickup),target=list(target),retreat_target=list(retreat))
        spec={'terran':(48,'marine',54,'medivac',2),
              'protoss':(73,'zealot',81,'warp_prism',2),
              'zerg':(105,'zergling',893,'overlord_transport',4)}[race]
        kind,selector,transport_kind,transport_name,count=spec
        def spawn(kind,owner,point,count=1):
            arena.controllers[0].debug([debug.DebugCommand(create_unit=debug.DebugCreateUnit(
                unit_type=kind,owner=owner,pos=dict(x=point[0],y=point[1]),quantity=count))])
        def tick():
            nonlocal worlds
            actions=q.tick(worlds[0],set())
            assert len(actions)==len(q.action_contexts)
            arena.register_action_context(0,[(a.SerializeToString(),c) for a,c in zip(actions,q.action_contexts)])
            worlds=arena.step([actions,[]])
            return actions
        def until(predicate,seconds=45):
            end=time.monotonic()+seconds
            while time.monotonic()<end:
                actions=tick()
                if predicate(actions):return
                failed=[t for t in q.tasks if t.status=='failed']
                if failed:raise AssertionError([(t.reason,t.program.summary()) for t in failed])
            raise TimeoutError('native fixture condition timed out: '+json.dumps(q.summary()))
        spawn(kind,1,pickup,count);spawn(transport_kind,1,pickup)
        # A durable enemy economy target survives long enough to observe native
        # target orders and actual damage, without any omniscient enemy query.
        spawn(18,2,target)
        until(lambda _:len(worlds[0].my(kind))>=count and bool(worlds[0].my(transport_kind)))
        args=dict(unit=selector,count=count,target=list(target),transport=transport_name,
            retreat_target=list(retreat),retreat_enemy_count=1,dwell_s=60,timeout_s=180,
            reason='原生阳性对照：装载空投、攻击基地、可见守军出现后撤离')
        q.enqueue([dict(skill='drop_raid',args=args)],worlds[0].seconds)
        task=q.tasks[-1];mission=task.program
        seen=set();orders=[];dispatched_attacks=[];health=[];cargo_seen=False;ground_unloaded=False
        last_progress=0
        deadline=time.monotonic()+100
        while time.monotonic()<deadline:
            actions=tick();seen.add(mission.phase)
            dispatched_attacks.extend(dict(ability=a.action_raw.unit_command.ability_id,
                target=a.action_raw.unit_command.target_unit_tag,
                tags=list(a.action_raw.unit_command.unit_tags)) for a in actions
                if arena.tech_tree.canonical_ability(a.action_raw.unit_command.ability_id)==
                   arena.tech_tree.canonical_ability(ATTACK))
            tr=worlds[0].by_tag(mission.transport_tag)
            cargo_seen|=bool(tr and set(tr.passengers)>=mission.squad_tags and mission.squad_tags)
            if mission.phase=='raiding':
                ground_unloaded|=all(worlds[0].by_tag(tag) is not None for tag in mission.squad_tags)
            for u in worlds[0].my_units:
                if u.tag in mission.squad_tags and any(arena.tech_tree.canonical_ability(a)==
                        arena.tech_tree.canonical_ability(ATTACK) for a in u.orders):
                    orders.append(dict(tag=u.tag,abilities=u.orders,targets=u.order_targets))
            for enemy in worlds[0].enemy_units:
                if enemy.type_id==18 and enemy.distance(target)<10:health.append(enemy.health)
            if time.monotonic()-last_progress>2:
                last_progress=time.monotonic()
                (folder/'progress.json').write_text(json.dumps(dict(game_s=worlds[0].seconds,
                    mission=mission.summary(),own=[dict(tag=u.tag,pos=u.pos,orders=u.orders)
                        for u in worlds[0].my_units if u.tag in mission.tags],
                    health=health[-4:],native_attack_order_count=len(orders)),ensure_ascii=False,indent=2))
            if cargo_seen and ground_unloaded and dispatched_attacks and orders and len(health)>1 and min(health)<max(health):break
            if not task.active:raise AssertionError((task.reason,mission.summary()))
        else:raise TimeoutError('drop/attack not confirmed: '+json.dumps(mission.summary()))
        assert cargo_seen and ground_unloaded
        report.update(drop_phases=sorted(seen),cargo_observed=cargo_seen,ground_unload_observed=ground_unloaded,
            native_attack_orders=orders[-8:],dispatched_attacks=dispatched_attacks,
            visible_base_damage=max(health)-min(health))
        # Hold the defenders briefly so they are visible and not killed before
        # the next observation. This is explicit debug fixture setup, not a bot.
        squad=[worlds[0].by_tag(tag) for tag in mission.squad_tags if worlds[0].by_tag(tag)]
        spawn(48,2,(squad[0].x+7,squad[0].y))
        until(lambda _:mission.phase=='withdraw',seconds=15)
        report['defender_contacts']=mission.defender_contacts
        until(lambda _:mission.phase=='holding',seconds=45)
        assert mission.squad_tags<=q.protected()
        ground=[worlds[0].by_tag(tag) for tag in mission.squad_tags]
        assert all(u and u.distance(retreat)<=6 for u in ground)
        report.update(retreat_target=list(retreat),survivor_positions=[list(u.pos) for u in ground],
                      holding_tags=sorted(q.protected()),action_errors=arena.action_errors)
        assert arena.action_errors==0
        # Explicitly release the drop, remove the scripted defender, then test
        # a separately dispatched ground raid using the surviving selected kind.
        q.enqueue([dict(skill='cancel_unit_order',args={'task_id':task.id})],worlds[0].seconds)
        tick()
        # Use the fixture's recorded spawned/contact tags even after our units
        # lose sight on withdrawal. Never feed this setup knowledge to a model.
        defenders=[u['tag'] for u in report['defender_contacts']]
        if defenders:arena.controllers[0].debug([debug.DebugCommand(kill_unit=debug.DebugKillUnit(tag=defenders))])
        report['fixture_removed_defender_tags']=defenders
        # A debug RPC acknowledgement precedes the simulation applying it. Do
        # not let the next mission see the previous fixture's still-live guard.
        cleanup_game_s=worlds[0].seconds
        until(lambda _:worlds[0].seconds>=cleanup_game_s+1 and
              not any(u.tag in defenders for u in worlds[0].enemy_units),seconds=10)
        ground_count=len(ground)
        q.enqueue([dict(skill='raid',args=dict(unit=selector,count=ground_count,targets=[list(target)],
            retreat_target=list(retreat),retreat_enemy_count=1,dwell_s=4,timeout_s=120,
            reason='原生阳性对照：独立分兵袭矿并撤到模型地点'))],worlds[0].seconds)
        ground_task=q.tasks[-1];ground_mission=ground_task.program
        until(lambda _:bool(ground_mission.contacts),seconds=45)
        report['ground_raid_economic_contacts']=ground_mission.contacts
        until(lambda _:ground_mission.phase=='holding',seconds=45)
        report['ground_raid_hold_observed']=True
        report['action_errors']=arena.action_errors
        assert arena.action_errors==0
        report['source_unchanged']=all(hashlib.sha256((source_root/p).read_bytes()).hexdigest()==digest
                                       for p,digest in source_hashes.items())
        assert report['source_unchanged'], 'verification source changed during native fixture'
        report['passed']=True
        replay=arena.controllers[0].save_replay();path=folder/'fixture.SC2Replay';path.write_bytes(replay)
        report['replay_sha256']=hashlib.sha256(replay).hexdigest()
    except Exception:
        report['error']=traceback.format_exc()
    finally:
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        (folder/'events.json').write_text(json.dumps(locals().get('events',[]),ensure_ascii=False,indent=2))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--races',nargs='+',choices=['terran','protoss','zerg'],default=['terran','protoss','zerg'])
    args=parser.parse_args()
    if not flags.FLAGS.is_parsed():flags.FLAGS(['verify_offensive_missions_live'])
    root=Path(__file__).resolve().parent
    os.environ.setdefault('SC2PATH',str(root/'engine/StarCraftII'))
    folder=Path(tempfile.mkdtemp(prefix='offensive-native-',dir=root/'logs'))
    results=[]
    for race in args.races:
        directory=folder/race;directory.mkdir()
        result=scenario(race,directory);results.append(result)
        print(json.dumps(dict(race=race,passed=result['passed'],error=result.get('error')),ensure_ascii=False),flush=True)
        if not result['passed']:break
    final=dict(passed=len(results)==len(args.races) and all(r['passed'] for r in results),model_calls=0,results=results)
    (folder/'result.json').write_text(json.dumps(final,ensure_ascii=False,indent=2))
    print('EVIDENCE',folder,flush=True)
    return 0 if final['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
