"""Zero-model native placement control using the third duel's owned building layout."""
import argparse
from dataclasses import replace
import json
import hashlib
import math
from pathlib import Path
import time

from absl import flags
from s2clientprotocol import debug_pb2 as debug

from arena.context_tables import decode_tables
from arena.raw_engine import RawArena
from arena.tasks import TaskQueue
from arena.tech_tree import snake
from verify_tactical_scenarios_live import Fixture


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request',type=Path,required=True)
    parser.add_argument('--label',required=True)
    parser.add_argument('--build',action='store_true',help='Also require the actual macro to build one Pylon')
    args=parser.parse_args();flags.FLAGS(['pylon-spacing-control'])
    folder=Path('logs/pylon-spacing-'+args.label);folder.mkdir(exist_ok=False)
    result=dict(model_calls=0,debug_fixture=True,debug_resources=True,fast_build=False,
        passed=False,events=[],macro_sha256=hashlib.sha256(Path('arena/macro.py').read_bytes()).hexdigest(),
        scope='Owned building layout recreated in a fresh native debug fixture; '
            'not an exact replay state or a match win. Original mineral depletion, mobile units and pending tasks differ.')
    arena=queue=None
    try:
        saved=json.loads(args.request.read_text());payload=decode_tables(json.loads(saved['observation']))
        saved_home=next(a['center'] for a in payload['battlefield']['areas'] if a['id']=='home')
        match=args.request.parents[3];seed=json.loads((match/'match.json').read_text())['seed']
        loop=payload['economy_report']['current']['loop'];active={t['id'] for t in payload['active_tasks']}
        reserved={}
        for path in (match/'p1/feedback').glob('*.jsonl'):
            for line in path.read_text().splitlines():
                event=json.loads(line).get('event',{})
                if (event.get('kind')=='macro_order_issued' and event.get('game_loop',0)<=loop
                        and event['task_id'] in active):reserved[event['task_id']]=event.get('target_pos')
        result['spawn_attempts']=[]
        # Native 4.10 may swap spawn assignments even with the same seed.
        # Select the saved orientation in at most four zero-model launches;
        # otherwise fail the fixture rather than change the tested geometry.
        for _ in range(4):
            arena=RawArena(render=False,races=['protoss','terran'],game_speed='slower',seed=seed)
            actual=arena.initial[0].my(59)[0]
            result['spawn_attempts'].append(list(actual.pos))
            if math.dist(actual.pos,saved_home)<=3:break
            arena.close();arena=None
        if arena is None:raise AssertionError('saved spawn orientation not selected within four fixture launches')
        fixture=Fixture(arena);home=fixture.worlds[0].my(59)[0]
        mirror=math.dist(home.pos,saved_home)>3
        if mirror and math.dist(saved_home,arena.enemy_start(0))>3:
            raise AssertionError('saved source is not the opposite fixture spawn')
        # Acropolis has map padding: spawn symmetry does not use full map height.
        transform=lambda p:(home.x+saved_home[0]-p[0],home.y+saved_home[1]-p[1]) if mirror else tuple(p)
        if math.dist(transform(saved_home),home.pos)>3:raise AssertionError('fixture spawn geometry differs')
        result.update(source_home=saved_home,fixture_home=list(home.pos),mirrored=mirror)
        by_name={snake(name):kind for kind,name in arena.tech_tree.names.items()}
        actors=payload['native_abilities']['actors'];columns=actors['columns']
        layout=[]
        for row in actors['rows']:
            actor=dict(zip(columns,row));kind=by_name[actor['unit']]
            if kind==59 or 8 not in arena.types[kind].attributes:continue
            position=transform(actor['pos'])
            building=fixture.spawn(kind,1,position)[0]
            layout.append(dict(type_id=kind,pos=list(building.pos),done=building.done))
        arena.controllers[0].debug([debug.DebugCommand(game_state=debug.all_resources)])
        fixture.advance(1);world=fixture.worlds[0]
        queue=TaskQueue(arena,0,lambda _:None,
            lambda kind,**kw:result['events'].append(dict(kind=kind,**kw)))
        queue.enqueue([dict(skill='build_supply',count=1)],world.seconds,'native crowded Pylon control')
        task=queue.tasks[-1];run=task.program;recipe=run.spec['entry']['recipes'][0]
        reserved_points=[transform(p) for p in reserved.values() if p is not None]
        runtime_positions=run._positions
        def positions(w,q,t,actor,recipe,**kwargs):
            # Recreate the saved tasks' geometric keep-out regions only. No
            # synthetic purchases or hidden model tasks are executed in this fixture.
            return [p for p in runtime_positions(w,q,t,actor,recipe,**kwargs)
                if not any(math.dist(p,spot)<6 for spot in reserved_points)]
        run._positions=positions
        result.update(seed=seed,reserved_points=[list(p) for p in reserved_points])
        worker=world.my(84)[0]
        original=run._positions(world,queue,task,worker,recipe)
        dense_world=replace(world,units=tuple(replace(u,type_id=-60)
            if u.alliance==1 and u.type_id==60 else u for u in world.units))
        dense=[p for p in run._positions(dense_world,queue,task,worker,recipe) if p not in original]
        spaced_point=arena.placement(0,recipe['ability'],worker.tag,original)
        spaced_evidence=arena.placement_evidence(0,recipe['ability'])
        dense_point=arena.placement(0,recipe['ability'],worker.tag,dense)
        dense_evidence=arena.placement_evidence(0,recipe['ability'])
        result.update(layout=layout,original=spaced_evidence,dense=dense_evidence,
            placement_reproduced=spaced_point is None and dense_point is not None)
        if not result['placement_reproduced']:
            raise AssertionError('fresh layout does not reproduce spaced rejection with a legal dense control')
        if args.build:
            baseline={u.tag for u in world.my(60)};wall=time.monotonic();began=world.seconds
            while world.seconds-began<65 and time.monotonic()-wall<165:
                fixture.worlds=arena.step([queue.tick(world,set()),[]]);world=fixture.worlds[0];time.sleep(.02)
                if task.status in ('completed','failed'):break
            products=[u for u in world.my(60) if u.done and u.tag not in baseline]
            result.update(state=task.status,reason=task.reason,
                observed_products=[dict(tag=u.tag,pos=list(u.pos)) for u in products],
                issued=run.issued,engine_action_errors=arena.action_errors,
                passed=task.status=='completed' and run.issued==1 and len(products)==1 and arena.action_errors==0)
        else:result['passed']=True
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:result['error']=type(error).__name__+': '+str(error)
    finally:
        if queue:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k not in ('events','layout')},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
