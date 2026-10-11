"""Actual DeepSeek + isolated Python + native realtime engine, controlled debug scene.
Not a natural match, win-rate evaluation, or model-authored-source demonstration.
"""
import json
import threading
import time
from pathlib import Path
from absl import app
from s2clientprotocol import debug_pb2 as debug
from arena.commander import Commander
from arena.native_engine import NativeArena
from arena.player_config import make_player
from arena.python_skills import PythonCatalog
from arena.tasks import ALLOWED

class DelaySecondCall:
    def __init__(self,client):self.client=client;self.calls=0;self.delaying=threading.Event()
    def chat(self,system,user):
        self.calls+=1
        if self.calls==2:
            self.delaying.set();time.sleep(5);self.delaying.clear()
        return self.client.chat(system,user)

def create(kind,count,owner,pos):
    d=debug.DebugCommand();d.create_unit.unit_type=kind;d.create_unit.quantity=count;d.create_unit.owner=owner
    d.create_unit.pos.x,d.create_unit.pos.y=pos
    return d

def main(argv):
    report=dict(fixture=True,realtime=True,render=False,status='starting',
        debug_injected=['3 own Marines','12 enemy Marines','invulnerable units'],
        forced_second_planner_delay_s=5,source='repository example, not model-authored')
    commander=None;arena=None
    path=Path('logs/python_skills_live.json')
    try:
        catalog=PythonCatalog('python_skills/examples',reserved=ALLOWED)
        client,identity=make_player(0,'deepseek');report['identity']=identity
        client=DelaySecondCall(client)
        arena=NativeArena(names=('Python skill validation',),difficulty='very_easy',render=False)
        report['engine']=arena.version;home=arena.homes[0]
        arena.controllers[0].debug([create(48,3,1,(home[0]+6,home[1]+4)),debug.DebugCommand(game_state=debug.god)])
        deadline=time.monotonic()+15
        while True:
            w=arena.step([[]])[0]
            if len(w.my(48))==3:break
            if time.monotonic()>deadline:raise AssertionError('own unit fixture missing')
        commander=Commander(arena,0,client,opening=False,interval=99999,feedback_dir='logs/python_skills_feedback',python_catalog=catalog)
        commander.system+='\nThis is an explicitly authorized controlled engineering test with injected Marines and invulnerability.\nOn your first request (no active tasks), invoke cautious_push once: count=3, target=enemy_start,\nretreat_target=home, timeout_s=120, params={}. Do not define skills or issue economy/other tasks.\nOn subsequent requests, if any python_skill_action feedback has action.target=retreat,\nreturn ONLY cancel_python_task for that active Python task id; otherwise tasks=[].\nKeep the explanation factual, this is not a test of strategic victory assessment.\n'
        deadline=time.monotonic()+90;injected=False;retreat_before=None;advanced=False
        retreat_seen=False;cancel_requested=False;enemy_tags=[];start_loop=w.game_loop;start_wall=time.monotonic()
        snapshots=[]
        while time.monotonic()<deadline:
            actions=commander.tick(w);w=arena.step([actions])[0];commander.ingest_feedback(w)
            if commander.errors or commander.engine_errors:raise AssertionError('planner/engine error; see feedback')
            if any(t.status=='failed' for t in commander.tasks.tasks):raise AssertionError('Python task failed')
            if commander.decisions and not commander.tasks.tasks:raise AssertionError('no Python task from real model')
            if not commander.tasks.tasks:continue
            task=commander.tasks.tasks[0]
            if not task.program.tags:continue
            squad=[w.by_tag(tag) for tag in task.program.tags]
            if not all(squad):raise AssertionError('fixture squad lost')
            distance=sum(u.distance(home) for u in squad)/len(squad)
            snapshots.append(dict(game_loop=w.game_loop,distance_home=distance,thinking=commander.thinking,
                                  script_calls=task.program.calls,state=task.program.state))
            if distance>17 and not injected:
                advanced=True;report['advance']=snapshots[-1]
                # Inject slightly toward home: visible to the squad, immediate value ratio > threshold.
                anchor=squad[0];dx=home[0]-anchor.x;dy=home[1]-anchor.y;length=max(1,(dx*dx+dy*dy)**.5)
                pos=(anchor.x+dx/length*5,anchor.y+dy/length*5)
                arena.controllers[0].debug([create(48,12,2,pos)])
                injected=True;retreat_before=distance
                commander.last_request=float('-inf')
            if injected and task.program.state.get('mode')=='retreat':
                if not retreat_seen:
                    report['retreat_started']=snapshots[-1]
                    report['retreat_during_planner_wait']=commander.thinking
                    report['retreat_during_forced_delay']=client.delaying.is_set()
                retreat_seen=True
                if distance<retreat_before-3 and not cancel_requested:
                    report['observed_retreat']=snapshots[-1]
                    cancel_requested=True
                    # A fresh model request must actually receive the retreat feedback.
                    commander.interval=1
            if cancel_requested and task.status=='cancelled':
                report['cancelled']=dict(status=task.status,reason=task.reason,worker_closed=task.program.worker.closed)
                break
        else:raise AssertionError('live scenario wall deadline exceeded')
        assert advanced and retreat_seen and cancel_requested
        assert report['retreat_during_planner_wait'] and report['retreat_during_forced_delay']
        assert arena.action_errors==0
        report['timing']=arena.timing();report['engine_errors']=arena.action_errors
        report['scenario_game_s']=(w.game_loop-start_loop)/22.4;report['scenario_wall_s']=time.monotonic()-start_wall
        assert report['scenario_game_s']>10 and .9<report['scenario_game_s']/report['scenario_wall_s']<1.1
        report['samples']=snapshots;report['stats']=commander.stats();report['status']='passed'
        arena.save_replay('logs/python_skills_live.SC2Replay')
        print('PYTHON_SKILLS_LIVE_OK',json.dumps({k:v for k,v in report.items() if k not in ('samples','stats')},ensure_ascii=False),flush=True)
    except BaseException as error:
        report.update(status='failed',error=type(error).__name__+': '+str(error));raise
    finally:
        if commander:report['stats']=commander.stats();commander.stop()
        if arena:arena.close()
        path.write_text(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':app.run(main)
