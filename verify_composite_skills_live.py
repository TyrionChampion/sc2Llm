"""Real DeepSeek authors workflows; native realtime controlled fixture executes them.
No displayed window, no natural-match/win-rate/FPS claim. Debug units/upgrades explicit.
"""
import json
import time
from pathlib import Path
from absl import app
from s2clientprotocol import debug_pb2 as debug
from arena.native_engine import NativeArena
from arena.commander import Commander
from arena.player_config import make_player
from arena.raw_engine import MARINE

FIXTURE_REQUEST = """
This run is an explicit CONTROLLED TEST with injected units/upgrades, not a natural match.
On the FIRST request, author exactly three differently named reusable skills and invoke all
three once: (1) one SCV builds a supply depot then returns to the nearest minerals using a
native queued gather; (2) one Marine uses Stimpack then attack-moves to rally; (3) one tank
moves to rally then sieges using native queue. You choose skill names and compose the steps.
Do not add other tasks or wait for enemies. Available fixtures: one Marine, one mobile tank,
researched Stimpack, normal starting SCVs and minerals. Use timeout_s=120 for each skill.
On subsequent requests when skill_library is nonempty, return tasks=[] with no definitions
and briefly summarize the actual step/task feedback, including any failures. Do not invent success.
"""

def main(argv):
    report=dict(fixture=True,realtime=True,render=False,debug_injected=['Marine','SiegeTank','upgrades'],status='starting')
    path=Path('logs/composite_skills_live.json');commander=None
    try:
        client,identity=make_player(0,'deepseek');report['identity']=identity
        with NativeArena(names=('Skill fixture',),difficulty='very_easy',render=False) as arena:
            report['engine']=arena.version;home=arena.homes[0];commands=[]
            for kind,dx in ((MARINE,6),(33,9)):
                d=debug.DebugCommand();d.create_unit.owner=1;d.create_unit.unit_type=kind;d.create_unit.quantity=1
                d.create_unit.pos.x=home[0]+dx;d.create_unit.pos.y=home[1]+4;commands.append(d)
            commands.append(debug.DebugCommand(game_state=debug.upgrade));arena.controllers[0].debug(commands)
            deadline=time.monotonic()+15
            while True:
                w=arena.step([[]])[0]
                if w.my(MARINE) and w.my(33) and 15 in w.upgrades:break
                if time.monotonic()>deadline:raise AssertionError('fixture setup failed')
            commander=Commander(arena,0,client,opening=False,interval=99999,feedback_dir='logs/composite_skills_feedback')
            commander.system+=FIXTURE_REQUEST
            deadline=time.monotonic()+100;final_requested=False
            while time.monotonic()<deadline:
                actions=commander.tick(w);w=arena.step([actions])[0];commander.ingest_feedback(w)
                if commander.errors:raise AssertionError('real model plan/API failed; see journal')
                if any(t.status=='failed' for t in commander.tasks.tasks):raise AssertionError('workflow failed')
                if commander.decisions>=1 and not commander.tasks.tasks:raise AssertionError('no custom tasks returned')
                if commander.tasks.tasks and all(not t.active for t in commander.tasks.tasks):
                    assert len(commander.tasks.library)==3 and len(commander.tasks.tasks)==3
                    assert all(t.program for t in commander.tasks.tasks)
                    if not final_requested:
                        report['completed_at']=arena.timing();commander.last_request=-99999;final_requested=True
                    elif commander.decisions>=2 and not commander.thinking:
                        pending=[e for e in commander.feedback.pending if e['kind']!='plan_wait']
                        if pending:
                            commander.last_request=float('-inf')
                        else:
                            report['all_workflow_feedback_delivered']=True
                            report['status']='passed';break
            if report['status']!='passed':raise AssertionError('controlled test wall-clock deadline exceeded')
            assert arena.action_errors==0
            report['timing']=arena.timing();report['engine_errors']=arena.action_errors
            report['skills']=list(commander.tasks.library.values())
            report['tasks']=[dict(id=t.id,skill=t.skill,status=t.status,reason=t.reason) for t in commander.tasks.tasks]
            report['stats']=commander.stats()
            report['final_world']=w.summary()
            arena.save_replay('logs/composite_skills_live.SC2Replay')
            print('COMPOSITE_SKILLS_LIVE_OK',json.dumps(report,ensure_ascii=False),flush=True)
    except Exception as e:
        report['status']='failed';report['error']=type(e).__name__+': '+str(e);raise
    finally:
        if commander:
            report['stats']=commander.stats();commander.stop()
        path.write_text(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':app.run(main)
