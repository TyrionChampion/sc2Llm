"""Read-only own-fog replay placement audit for a saved unissued Pylon request."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

from absl import flags
from pysc2 import run_configs
from s2clientprotocol import sc2api_pb2 as pb,query_pb2 as query,error_pb2

from arena.context_tables import decode_tables
from arena.knowledge import cached_tree
from arena.macro import MacroRun,specification
from arena.raw_engine import Entity,Snapshot


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--match',type=Path,required=True)
    parser.add_argument('--replay',type=Path,required=True)
    parser.add_argument('--request',type=int,default=22)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();flags.FLAGS(['pylon-placement-replay'])
    if args.output.exists():raise ValueError('Do not overwrite replay evidence')
    source=args.match/'p1/planner'/('request-%04d'%args.request)/'request.json'
    saved=json.loads(source.read_text());payload=decode_tables(json.loads(saved['observation']))
    loop=payload['economy_report']['current']['loop']
    task=next(t for t in payload['active_tasks'] if t['skill']=='build_supply')
    worker=task['workflow']['placement_feedback']['worker_tag']
    spots={}
    for path in (args.match/'p1/feedback').glob('*.jsonl'):
        for line in path.read_text().splitlines():
            event=json.loads(line).get('event',{})
            if event.get('game_loop',0)<=loop and event.get('kind')=='macro_order_issued':
                spots[event['task_id']]=event.get('target_pos')
    report=dict(model_calls=0,disable_fog=False,orders_sent=0,passed=False,loop=loop,
        request=args.request,worker=worker,
        scope='Recorded own observation and native placement queries. Candidate-only Pylon labels '
              'are changed in a local copy to bypass spacing while retaining collision radii; '
              'native replay units, visibility and footprint checks remain unchanged. No build/effect claim.')
    try:
        rc=run_configs.get(version='4.10.0');data=args.replay.read_bytes()
        with rc.start(want_rgb=False) as controller:
            info=controller.replay_info(data)
            controller.start_replay(pb.RequestStartReplay(replay_data=data,
                map_data=rc.map_data(info.local_map_path),observed_player_id=1,disable_fog=False,
                options=pb.InterfaceOptions(raw=True,score=True)))
            metadata={u.unit_id:u for u in controller.data_raw().units}
            size=controller.game_info().start_raw.map_size
            controller.step(loop);observation=controller.observe().observation
            if observation.game_loop!=loop:raise AssertionError('replay loop differs from request observation')
            units=[]
            for u in observation.raw_data.units:
                if u.alliance==4 and u.display_type!=1:continue
                meta=metadata[u.unit_type]
                units.append(Entity(u.tag,u.unit_type,u.alliance,u.pos.x,u.pos.y,
                    u.health+u.shield,u.health_max+u.shield_max,progress=u.build_progress,
                    is_building=8 in meta.attributes,mineral=meta.has_minerals,radius=u.radius))
            p=observation.player_common
            world=Snapshot(loop,p.minerals,p.food_used,p.food_cap,tuple(units),(0,0),race='protoss')
            actor=world.by_tag(worker)
            if actor is None or actor.alliance!=1 or actor.type_id!=84:
                raise AssertionError('saved placing Probe is not currently owned/observed')
            home_point=next(a['center'] for a in payload['battlefield']['areas'] if a['id']=='home')
            home=min(world.my(59),key=lambda u:u.distance(home_point))
            tree=cached_tree();spec=specification(tree,'protoss','build_supply',{})
            backend=SimpleNamespace(tech_tree=tree,homes=[home.pos],size=(size.x,size.y))
            current=SimpleNamespace(id=task['id'],active=True,spot=None,excluded=set())
            tasks=[current]+[SimpleNamespace(id=t['id'],active=True,spot=spots.get(t['id']))
                for t in payload['active_tasks'] if t['id']!=task['id']]
            queue=SimpleNamespace(backend=backend,tasks=tasks,index=0)
            run=MacroRun(spec,{});recipe=spec['entry']['recipes'][0]
            original=run._positions(world,queue,current,actor,recipe)
            # Preserve all coordinates, alliances and footprint radii. Only skip
            # the candidate generator's type==60 spacing predicate in this copy.
            dense_world=replace(world,units=tuple(replace(u,type_id=-60)
                if u.alliance==1 and u.type_id==60 else u for u in world.units))
            dense=[point for point in run._positions(dense_world,queue,current,actor,recipe)
                if point not in original]
            def audit(points):
                request=query.RequestQuery()
                for x,y in points:
                    item=request.placements.add(ability_id=recipe['ability'],placing_unit_tag=worker)
                    item.target_pos.x=x;item.target_pos.y=y
                results=controller.query(request).placements if points else []
                if len(results)!=len(points):raise AssertionError('incomplete placement response')
                return [dict(point=list(point),result=error_pb2.ActionResult.Name(row.result))
                    for point,row in zip(points,results)]
            original_results=audit(original);dense_results=audit(dense)
            accepted=[r for r in dense_results if r['result']=='Success']
            report.update(original=original_results,dense=dense_results,
                original_candidate_count=len(original),
                saved_candidate_count=task['workflow']['placement_feedback']['candidate_count'],
                accepted_dense_count=len(accepted),
                passed=bool(original and not any(r['result']=='Success' for r in original_results) and accepted))
            if original_results and all(r['result']=='Error' for r in original_results+dense_results):
                report.update(state='inconclusive',passed=False,
                    reason='Replay placement queries returned only generic Error, without a successful control; '
                           'this does not establish that the points are invalid. Use a live native fixture.')
            else:report['state']='hypothesis_confirmed' if report['passed'] else 'hypothesis_not_confirmed'
    except Exception as error:report['error']=type(error).__name__+': '+str(error)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('original','dense')},ensure_ascii=False),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
