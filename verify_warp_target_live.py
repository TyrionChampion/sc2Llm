"""Zero-model native home/forward warp-area purchases with independent confirmation."""
import argparse,json,math,time
from pathlib import Path
from absl import flags
from s2clientprotocol import debug_pb2 as debug
from arena.raw_engine import RawArena
from arena.tasks import TaskQueue
from verify_tactical_scenarios_live import Fixture


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--label',required=True)
    args=p.parse_args();flags.FLAGS(['warp-target'])
    folder=Path('logs/warp-target-'+args.label);folder.mkdir(exist_ok=False)
    result=dict(model_calls=0,debug_fixture=True,fast_build=False,passed=False,events=[])
    arena=queue=None
    try:
        arena=RawArena(render=False,races=['protoss','terran'],game_speed='slower',seed=7)
        f=Fixture(arena);home=f.worlds[0].my(59)[0]
        pylon=f.structure_near(60,home)
        f.structure_near(62,pylon);f.structure_near(72,pylon);f.structure_near(62,pylon)
        candidates=[(x+.5,y+.5) for y in range(50,120,2) for x in range(55,120,2)
            if all(f.grid.free(x+dx,y+dy) for dy in range(-3,4) for dx in range(-3,4))
            and 30<math.dist((x+.5,y+.5),home.pos)<70
            and not any(u.distance((x+.5,y+.5))<9 for u in f.worlds[0].units
                        if u.is_building or u.mineral or u.geyser)]
        if not candidates:raise AssertionError('no clear forward power fixture area')
        forward=f.spawn(60,1,min(candidates,key=lambda point:math.dist(point,home.pos)))[0]
        arena.controllers[0].debug([debug.DebugCommand(game_state=debug.all_resources),
                                    debug.DebugCommand(game_state=debug.upgrade)])
        wall=time.monotonic()
        while time.monotonic()-wall<45:
            f.advance(.25)
            if any(u.done and u.powered for u in f.worlds[0].my(133)):break
        if not any(u.done and u.powered for u in f.worlds[0].my(133)):
            raise AssertionError('native powered WarpGate conversion not observed')
        queue=TaskQueue(arena,0,lambda _:None,
            lambda kind,**kw:result['events'].append(dict(kind=kind,**kw)))
        targets=[list(f.point((pylon.x,pylon.y+3))),list(f.point((forward.x,forward.y+3)))]
        queue.enqueue([dict(skill='train_unit',args=dict(unit='stalker',warp_target=point))
                       for point in targets],f.worlds[0].seconds,'explicit home/forward native warp control')
        tasks=list(queue.tasks);wall=time.monotonic();start=f.worlds[0].seconds
        while f.worlds[0].seconds-start<65 and time.monotonic()-wall<165:
            f.worlds=arena.step([queue.tick(f.worlds[0],set()),[]]);time.sleep(.02)
            if any(t.status=='failed' for t in tasks):raise AssertionError('warp purchase failed: '+str([t.reason for t in tasks]))
            if all(t.status=='completed' for t in tasks):break
        products=[]
        for task,target in zip(tasks,targets):
            units=[f.worlds[0].by_tag(tag) for tag in task.program.born]
            products.append(dict(requested=target,state=task.status,issued=task.program.issued,
                actual=[dict(tag=u.tag,pos=u.pos,completed=u.done) for u in units if u],
                within_area=bool(units) and all(u is not None and u.done and u.distance(target)<=3 for u in units)))
        result.update(products=products,engine_action_errors=arena.action_errors,
            passed=(all(t.status=='completed' and t.program.issued==1 and len(t.program.born)==1 for t in tasks)
                    and tasks[0].program.born.isdisjoint(tasks[1].program.born)
                    and all(row['within_area'] for row in products) and arena.action_errors==0))
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:result['error']=type(error).__name__+': '+str(error)
    finally:
        if queue:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k!='events'},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
