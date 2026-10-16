"""Zero-model mixed armed/support army attack-move using native ScanMove aliases."""
import argparse,json,time
from pathlib import Path
from absl import flags
from arena.raw_engine import RawArena,ATTACK
from arena.tasks import TaskQueue
from verify_tactical_scenarios_live import Fixture


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--label',required=True)
    args=p.parse_args();flags.FLAGS(['support-army-control'])
    folder=Path('logs/support-army-'+args.label);folder.mkdir(exist_ok=False)
    result=dict(model_calls=0,debug_fixture=True,fast_build=False,passed=False,events=[])
    arena=queue=None
    try:
        arena=RawArena(render=False,races=['terran','zerg'],game_speed='slower',seed=7)
        f=Fixture(arena);home=f.worlds[0].my(18)[0]
        direction=1 if home.x<88 else -1
        start=f.point((home.x+direction*12,home.y-direction*8))
        kinds=(48,54,56,111,499,82,129)
        actors=[f.spawn(kind,1,start)[0] for kind in kinds]
        f.advance(1)
        actors=[f.worlds[0].by_tag(u.tag) for u in actors]
        if any(u is None for u in actors):raise AssertionError('fixture actor no longer observed/owned')
        candidates=[(x+.5,y+.5) for x in range(max(2,int(start[0])-19),min(f.grid.w-2,int(start[0])+20))
            for y in range(max(2,int(start[1])-19),min(f.grid.h-2,int(start[1])+20))
            if f.grid.free(x,y) and 12<=actors[0].distance((x+.5,y+.5))<=18
            and all(u.distance((x+.5,y+.5))>8 for u in actors)]
        candidates.sort(key=lambda point:abs(actors[0].distance(point)-14))
        ground=[u.tag for u in actors if not u.flying];goal=None
        for point in candidates:
            distances=arena.unit_path_distances(0,ground,point)
            if len(distances)==len(ground) and all(0<d<100 for d in distances):goal=list(point);break
        if goal is None:raise AssertionError('no distant native-reachable fixture destination')
        available=arena.available_abilities(0,[u.tag for u in actors])
        result['native_start']=[dict(tag=u.tag,type_id=u.type_id,pos=list(u.pos),
            ready=sorted(available[u.tag]),generic_attack_available=arena.tech_tree.ability_available(ATTACK,available[u.tag]))
            for u in actors]
        if not all(row['generic_attack_available'] for row in result['native_start']):
            raise AssertionError('mixed fixture actor lacks Attack/ScanMove alias after readiness wait')
        queue=TaskQueue(arena,0,lambda _:None,
            lambda kind,**kw:result['events'].append(dict(kind=kind,**kw)))
        queue.enqueue([dict(skill='order_units',args=dict(unit='army',count='all',mode='attack_move',
            target=goal,timeout_s=90,reason='explicit mixed support army movement control'))],
            f.worlds[0].seconds,'zero-model mixed army control')
        task=queue.tasks[-1];wall=time.monotonic();began=f.worlds[0].seconds;bound=None;dispatches=0
        while f.worlds[0].seconds-began<90 and time.monotonic()-wall<225:
            out=queue.tick(f.worlds[0],set())
            dispatches+=sum(a.action_raw.unit_command.ability_id==ATTACK for a in out)
            f.worlds=arena.step([out,[]]);time.sleep(.02)
            if task.program.tags and bound is None:bound=sorted(task.program.tags)
            if task.status in ('completed','failed'):break
        end=f.worlds[0];actual=[end.by_tag(u.tag) for u in actors]
        origins={u.tag:u for u in actors}
        observed=[dict(tag=u.tag,type_id=u.type_id,pos=list(u.pos),distance=u.distance(task.program.route[-1]),
                      moved=u.distance(origins[u.tag]))
                  for u in actual if u]
        result.update(requested=goal,mission=task.program.summary(),state=task.status,bound_tags=bound,
            actual=observed,dispatches=dispatches,engine_action_errors=arena.action_errors,
            passed=task.status=='completed' and set(bound or ())=={u.tag for u in actors}
                and dispatches>=1 and len(observed)==len(actors)
                and all(row['distance']<=4 and row['moved']>2 for row in observed)
                and arena.action_errors==0)
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:result['error']=type(error).__name__+': '+str(error)
    finally:
        if queue:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k!='events'},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
