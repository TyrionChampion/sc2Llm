"""Real realtime dual-player movement check; injected Marines, no model calls.
Reuses the exact frozen skills involved in the reported match. Not a win-rate test.
"""
import json
import math
import time
from pathlib import Path
from absl import app
from s2clientprotocol import debug_pb2 as debug
from arena.native_engine import NativeArena
from arena.python_skills import PythonCatalog
from arena.tasks import TaskQueue
from arena.raw_engine import MARINE

ROOT=Path(__file__).resolve().parent
SOURCE=ROOT/'opencode-runtime/match-_ig80d8w'
OUT=ROOT/'logs/enemy_start_after_live.json'

def create(owner,pos):
    d=debug.DebugCommand()
    d.create_unit.unit_type=MARINE;d.create_unit.quantity=4;d.create_unit.owner=owner
    d.create_unit.pos.x,d.create_unit.pos.y=pos
    return d

def main(argv):
    report=dict(status='starting',realtime=True,render=False,
                scope='controlled real engine + production TaskQueue + original model Python sources; no LLM call',
                debug_injected='4 Marines per side; no invulnerability/fog changes',samples=[],events=[])
    arena=None;queues=[]
    def save():OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    try:
        catalogs=[PythonCatalog(SOURCE/f'p{i+1}'/'accepted') for i in range(2)]
        report['catalogs']=[c.public() for c in catalogs]
        arena=NativeArena(names=('P1 target validation','P2 target validation'),render=False)
        report.update(engine=arena.version,targets=arena.start_location_report)
        for i in range(2):
            assert math.dist(arena.enemy_start(i),arena.homes[i])>100
            assert math.dist(arena.enemy_start(i),arena.homes[1-i])<1
        cmds=[]
        for i,home in enumerate(arena.homes):
            enemy=arena.enemy_start(i);distance=math.dist(home,enemy)
            pos=tuple(home[k]+(enemy[k]-home[k])*6/distance for k in (0,1))
            cmds.append(create(i+1,pos))
        arena.controllers[0].debug(cmds)
        end=time.monotonic()+10
        while True:
            worlds=arena.step([[],[]])
            if all(len(w.my(MARINE))==4 for w in worlds):break
            if time.monotonic()>end:raise AssertionError('injected Marine observation missing')
        for i in range(2):
            def event(kind,player=i+1,**kw):report['events'].append(dict(player=player,kind=kind,**kw))
            q=TaskQueue(arena,i,feedback=event,python_catalog=catalogs[i]);queues.append(q)
            q.enqueue([dict(skill=next(iter(catalogs[i].entries)),args=dict(count=3,target='enemy_start',retreat_target='home',timeout_s=120,params={})),
                       dict(skill='attack_nearest')],worlds[i].seconds,source='explicit controlled validation')
        start=time.monotonic();start_loop=worlds[0].game_loop
        initial=[{u.tag:u.pos for u in w.my(MARINE)} for w in worlds]
        dispatched=[False,False];observed_order=[False,False]
        while time.monotonic()-start<40:
            batches=[q.tick(w,set()) for q,w in zip(queues,worlds)]
            for i,(q,batch) in enumerate(zip(queues,batches)):
                arena.register_action_context(i,q.action_contexts)
                for action in batch:
                    c=action.action_raw.unit_command
                    if c.HasField('target_world_space_pos'):
                        target=(c.target_world_space_pos.x,c.target_world_space_pos.y)
                        assert math.dist(target,arena.enemy_start(i))<.01,(i,target)
                        dispatched[i]=True
            worlds=arena.step(batches)
            assert arena.action_errors==0,'engine action rejected'
            sample=dict(game_s=worlds[0].seconds,wall_s=time.monotonic()-start,players=[])
            for i,(q,w) in enumerate(zip(queues,worlds)):
                if any(t.status=='failed' for t in q.tasks):raise AssertionError([(i,t.skill,t.reason) for t in q.tasks if t.status=='failed'])
                squad=[w.by_tag(tag) for tag in initial[i]]
                assert all(squad),'controlled test unit lost before departure evidence'
                for u in squad:
                    if any(p is not None and math.dist(p,arena.enemy_start(i))<.01 for p in u.order_positions):observed_order[i]=True
                sample['players'].append(dict(player=i+1,
                    min_home_distance=min(u.distance(arena.homes[i]) for u in squad),
                    min_enemy_distance_improvement=min(math.dist(initial[i][u.tag],arena.enemy_start(i))-u.distance(arena.enemy_start(i)) for u in squad),
                    units=[dict(tag=u.tag,pos=u.pos,orders=u.orders,order_positions=u.order_positions,
                                controller='python' if u.tag in q.tasks[0].program.tags else 'macro') for u in squad],
                    python_calls=q.tasks[0].program.calls))
            report['samples'].append(sample)
            if all(p['min_home_distance']>35 and p['min_enemy_distance_improvement']>20 for p in sample['players']):break
        else:raise AssertionError('army did not leave base toward enemy within 40 wall seconds')
        assert all(dispatched) and all(observed_order)
        report.update(status='passed',action_errors=arena.action_errors,
                      target_orders_observed=observed_order,final=sample,
                      game_s=(worlds[0].game_loop-start_loop)/22.4,wall_s=time.monotonic()-start,
                      timing=arena.timing())
        arena.save_replay(ROOT/'logs/enemy_start_after_live.SC2Replay')
        save()
        print('ENEMY_START_MOVEMENT_OK',json.dumps({k:v for k,v in report.items() if k not in ('samples','events','catalogs')},ensure_ascii=False),flush=True)
    except BaseException as error:
        report.update(status='failed',error=type(error).__name__+': '+str(error));save();raise
    finally:
        for q in queues:q.close()
        if arena:arena.close()
        save()

if __name__=='__main__':app.run(main)
