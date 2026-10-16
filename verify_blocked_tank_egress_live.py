"""Native own-wall observation, explicit Depot lowering and renewed movement; zero LLM."""
import argparse,json,math,time
from pathlib import Path
from absl import flags
from arena.raw_engine import RawArena
from arena.tasks import TaskQueue
from verify_tactical_scenarios_live import Fixture


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--label',required=True)
    args=p.parse_args();flags.FLAGS(['blocked-tank-egress'])
    folder=Path('logs/blocked-tank-egress-'+args.label);folder.mkdir(exist_ok=False)
    result=dict(model_calls=0,debug_fixture=True,fast_build=False,passed=False,events=[],
        scope='Independent raised-Depot enclosure. Match replay separately confirms real native blockage; this control does not identify the original blockers.')
    arena=queue=None
    try:
        arena=RawArena(render=False,races=['terran','protoss'],game_speed='slower',seed=7)
        f=Fixture(arena)
        clear=[(x+.5,y+.5) for y in range(65,105) for x in range(70,106)
            if all(f.grid.free(x+dx,y+dy) for dy in range(-5,6) for dx in range(-5,6))
            and not any(u.distance((x+.5,y+.5))<12 for u in f.worlds[0].units if u.is_building or u.mineral or u.geyser)]
        if not clear:raise AssertionError('No open terrain for the controlled Depot enclosure')
        center=min(clear,key=lambda point:math.dist(point,(88,86)))
        tanks=[f.spawn(33,1,(center[0]+dx,center[1]))[0] for dx in (-1,1)]
        tags=[u.tag for u in tanks];target=None
        for dx,dy in ((15,0),(0,15),(-15,0),(0,-15),(12,12),(-12,-12)):
            candidate=list(f.point((center[0]+dx,center[1]+dy)))
            distances=arena.unit_path_distances(0,tags,candidate)
            if all(d>0 for d in distances):
                target=candidate;result['open_terrain_paths']=distances;break
        if target is None:raise AssertionError('No positive native Tank path before constructing the enclosure')
        depots=[f.spawn(19,1,(center[0]+dx,center[1]+dy))[0]
            for dx,dy in ((-3,0),(3,0),(-1,-2),(1,-2),(-1,2),(1,2))]
        result['fixture_center']=center
        queue=TaskQueue(arena,0,lambda _:None,
            lambda kind,**data:result['events'].append(dict(kind=kind,**data)))
        move=dict(skill='order_units',args=dict(unit='siege_tank',count=2,mode='move',
                  target=target,timeout_s=120,reason='明确测试两辆坦克去同一目标'))
        queue.enqueue([move],f.worlds[0].seconds,'native egress control');original=queue.tasks[-1]
        f.worlds=arena.step([queue.tick(f.worlds[0],set()),[]])
        result['requested_target']=target
        if original.program.route:target=list(original.program.route[0])
        result['resolved_target']=target
        before=arena.unit_path_distances(0,tags,target)
        result['before_paths']=before;result['before_report']=original.program.summary().get('native_paths')
        if not all(d==0 for d in before):raise AssertionError('fixture did not reproduce both blocked native paths')
        ability=arena.tech_tree.ability_names[556]
        queue.enqueue([dict(skill='use_ability',args=dict(caster=u.tag,ability=ability,target=None,timeout_s=15))
                       for u in depots],f.worlds[0].seconds,'explicit native lower-depot control')
        casts=queue.tasks[-len(depots):];wall=time.monotonic()
        while time.monotonic()-wall<45:
            f.worlds=arena.step([queue.tick(f.worlds[0],set()),[]]);time.sleep(.02)
            if any(t.status=='failed' for t in casts):raise AssertionError('explicit Depot lowering failed')
            if all(t.status=='completed' for t in casts) and all(
                f.worlds[0].by_tag(u.tag) is not None and f.worlds[0].by_tag(u.tag).type_id==47
                and f.worlds[0].by_tag(u.tag).done for u in depots):break
        after=arena.unit_path_distances(0,tags,target)
        result['after_paths']=after
        lowered=all(f.worlds[0].by_tag(u.tag).type_id==47 for u in depots)
        result['depot_states']=[dict(tag=u.tag,type_id=f.worlds[0].by_tag(u.tag).type_id,
            progress=f.worlds[0].by_tag(u.tag).progress) for u in depots]
        if not lowered:raise AssertionError('lowered Depot forms not actually observed')
        wall=time.monotonic();path_wait_start=f.worlds[0].seconds
        while not all(d>0 for d in after) and time.monotonic()-wall<20:
            f.worlds=arena.step([queue.tick(f.worlds[0],set()),[]]);time.sleep(.02)
            after=arena.unit_path_distances(0,tags,target)
        result['ready_paths']=after
        result['native_path_wait_s']=f.worlds[0].seconds-path_wait_start
        if not all(d>0 for d in after):raise AssertionError('native routes not ready after lowering; movement not claimed')
        positions={tag:f.worlds[0].by_tag(tag).pos for tag in tags}
        queue.enqueue([dict(skill='cancel_unit_order',args=dict(task_id=original.id)),move],
                      f.worlds[0].seconds,'explicit renewed movement control')
        wall=time.monotonic();moved=False
        while time.monotonic()-wall<45:
            f.worlds=arena.step([queue.tick(f.worlds[0],set()),[]]);time.sleep(.02)
            moved=all(f.worlds[0].by_tag(tag) is not None and
                      math.dist(f.worlds[0].by_tag(tag).pos,positions[tag])>3 for tag in tags)
            if moved and queue.tasks[-1].status=='completed':break
        result.update(depots_lowered=lowered,both_tanks_moved=moved,
            renewed_mission_state=queue.tasks[-1].status,
            final_paths=arena.unit_path_distances(0,tags,target),
            native_dispatches=sum(e['kind']=='native_ability_order_issued' for e in result['events']),
            engine_action_errors=arena.action_errors,
            passed=lowered and moved and queue.tasks[-1].status=='completed'
                and all(t.status=='completed' for t in casts) and arena.action_errors==0)
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:
        result['error']=type(error).__name__+': '+str(error)
        if arena:result['engine_action_errors']=arena.action_errors
    finally:
        if queue:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k!='events'},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
