"""Zero-model native control: default contact stop, then explicit continued scouting."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import time

from absl import flags

from arena.raw_engine import MOVE, RawArena
from arena.tasks import TaskQueue
from verify_tactical_scenarios_live import Fixture


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',required=True)
    args=parser.parse_args();flags.FLAGS(['scout-contact-control'])
    folder=Path('logs/scout-contact-'+args.label);folder.mkdir(exist_ok=False)
    source=Path('arena/spatial.py')
    result=dict(model_calls=0,debug_fixture=True,fast_build=False,passed=False,events=[],
        spatial_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    arena=queue=None
    try:
        arena=RawArena(render=False,races=['protoss','terran'],game_speed='slower',seed=7)
        fixture=Fixture(arena);home=fixture.worlds[0].my(59)[0]
        start=fixture.point((home.x-12 if home.x>88 else home.x+12,home.y))
        actor=fixture.spawn(84,1,start)[0]
        # A non-attacking owned observer supplies only player-local sight.
        fixture.spawn(82,1,start)
        candidates=[]
        for x in range(max(2,int(start[0])-22),min(fixture.grid.w-2,int(start[0])+23)):
            for y in range(max(2,int(start[1])-22),min(fixture.grid.h-2,int(start[1])+23)):
                point=(x+.5,y+.5)
                if (fixture.grid.free(x,y) and 18<=math.dist(start,point)<=22
                        and all(u.tag==actor.tag or u.distance(point)>actor.distance(point)+2
                            for u in fixture.worlds[0].my(84))):
                    candidates.append(point)
        candidates.sort(key=lambda p:abs(math.dist(start,p)-20))
        goal=enemy_point=None
        for point in candidates:
            distance=arena.unit_path_distances(0,[actor.tag],point)
            if len(distance)!=1 or not 0<distance[0]<40:continue
            dx,dy=point[0]-start[0],point[1]-start[1];length=math.hypot(dx,dy)
            for side in (1,-1):
                try:near=fixture.point((start[0]-side*dy/length*9,start[1]+side*dx/length*9))
                except ValueError:continue
                if 8<=math.dist(near,start)<=10 and math.dist(near,point)>12:
                    goal=point;enemy_point=near;break
            if goal is not None:break
        if goal is None:raise AssertionError('no legal distant destination and offset contact fixture')
        enemy=fixture.spawn(45,2,enemy_point)[0]
        fixture.advance(1)
        world=fixture.worlds[0];actor=world.by_tag(actor.tag)
        if actor is None or not any(e.tag==enemy.tag for e in world.enemy_units):
            raise AssertionError('fixture worker contact not currently visible to own player')
        if not 6<actor.distance(world.by_tag(enemy.tag))<=15:
            raise AssertionError('fixture contact is too close or outside scout contact range')
        start=actor.pos
        queue=TaskQueue(arena,0,lambda _:None,
            lambda kind,**kw:result['events'].append(dict(kind=kind,**kw)))
        def enqueue(continuing):
            queue.enqueue([dict(skill='scout',args=dict(unit='probe',count=1,
                targets=[list(goal)],stop_on_contact=not continuing,timeout_s=45,
                reason='explicit continued scouting native control' if continuing else 'default contact stop control'))],
                fixture.worlds[0].seconds,'zero-model native contact control')
            return queue.tasks[-1]
        default=enqueue(False)
        out=queue.tick(world,set());fixture.worlds=arena.step([out,[]])
        if default.status!='completed' or not any(e['kind']=='scout_contact'
                and e['mission_stopped_on_contact'] and not e['goal_arrived'] for e in result['events']):
            raise AssertionError('default scout did not stop and report contact before arrival')
        continuing=enqueue(True);wall=time.monotonic();began=fixture.worlds[0].seconds
        moves=0;bound=None
        while fixture.worlds[0].seconds-began<45 and time.monotonic()-wall<120:
            out=queue.tick(fixture.worlds[0],set())
            moves+=sum(a.action_raw.unit_command.ability_id==MOVE for a in out)
            if continuing.program.tags and bound is None:bound=sorted(continuing.program.tags)
            fixture.worlds=arena.step([out,[]]);time.sleep(.02)
            if continuing.status in ('completed','failed'):break
        actor=fixture.worlds[0].by_tag(actor.tag)
        continued_contacts=[e for e in result['events'] if e['kind']=='scout_contact'
            and e['task_id']==continuing.id and not e['mission_stopped_on_contact']]
        actual=dict(pos=list(actor.pos),moved=math.dist(start,actor.pos),
            distance_to_goal=actor.distance(goal)) if actor else None
        result.update(default_state=default.status,continued_state=continuing.status,
            requested=list(goal),bound_tags=bound,move_dispatches=moves,actual=actual,
            continued_contacts=len(continued_contacts),engine_action_errors=arena.action_errors,
            mission=continuing.program.summary(),
            passed=(actor is not None and default.status=='completed' and continuing.status=='completed'
                and bound==[actor.tag]))
        result['passed']=bool(result['passed'] and moves==1 and continued_contacts
            and actual['moved']>12 and actual['distance_to_goal']<=4 and arena.action_errors==0)
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:
        result['error']=type(error).__name__+': '+str(error)
    finally:
        if queue:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k!='events'},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
