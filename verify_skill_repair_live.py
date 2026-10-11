"""Controlled real GLM/OpenCode + native realtime game repair and LLM redeployment.
Injected faulty fixture skill and Marines; NOT natural strategy/win-rate validation.
"""
import json,tempfile,time
from pathlib import Path
from absl import app
from s2clientprotocol import debug_pb2 as debug
from arena.native_engine import NativeArena
from arena.commander import Commander
from arena.opencode_bridge import OpenCodeServer,OpenCodePlanner
from arena.lab_prepare import lab_mcp
from arena.skill_lab import SkillLab
from arena.skill_repair import SkillRepair
from arena.python_skills import PythonCatalog
from verify_python_skills_live import create


def main(argv):
    root=Path(tempfile.mkdtemp(prefix='repair-live-',dir='opencode-runtime')).resolve();lab=SkillLab(root/'lab');lab.setup()
    manifest=dict(name='repair_probe',description='Controlled probe: advance assigned Marine to mission objective.',file='repair_probe.py',unit='marine',actions=['attack_move'],parameters={})
    source="def tick(ctx,state):\n    mode = undefined_phase\n    return dict(status='running',state={'mode':mode},reason='advance',actions=[dict(action='attack_move',unit_tags=[u['tag'] for u in ctx['units']],target='objective')])\n"
    lab.write('skills/repair_probe.py',source);lab.write('skills/repair_probe.json',json.dumps(manifest))
    lab.write('tests/test_repair_probe.py',"import unittest,repair_probe\nclass Probe(unittest.TestCase):\n def test_advance(self):\n  r=repair_probe.tick({'units':[{'tag':101}],'mission':{'objective':[40,40]},'params':{},'game_s':10},{})\n  self.assertEqual(r['actions'][0]['action'],'attack_move')\n")
    model='opencode-go/glm-5.3-flash';server=OpenCodeServer(root/'server',[model],mcp=lab_mcp(lab.root))
    arena=None;c=None;clients=[];report=dict(status='starting',root=str(root),fixture=True,realtime=True,render=False,
        injected=['faulty fixture skill with undefined_phase','one Marine','invulnerability'])
    try:
        server.start();developer=OpenCodePlanner(server,model,0,root/'developer',lab_tools=True);battle=OpenCodePlanner(server,model,0,root/'battle');clients=[developer,battle];battle.phase='battle'
        repair=SkillRepair(developer,lab,root/'repairs',budget=300);catalog=PythonCatalog(lab.root/'skills');old=catalog.entries['repair_probe']['sha256']
        arena=NativeArena(names=('Repair validation',),difficulty='very_easy',render=False);home=arena.homes[0]
        arena.controllers[0].debug([create(48,1,1,(home[0]+6,home[1]+4)),debug.DebugCommand(game_state=debug.god)])
        deadline=time.monotonic()+15
        while True:
            w=arena.step([[]])[0]
            if w.my(48):break
            if time.monotonic()>deadline:raise RuntimeError('injected Marine missing')
        c=Commander(arena,0,battle,opening=False,interval=6,feedback_dir=root/'feedback',python_catalog=catalog,repair=repair)
        c.system+='\nThis is an authorized engineering fixture with one Marine and a faulty repair_probe. First invoke repair_probe with args count=1,target=enemy_start,retreat_target=home,timeout_s=300,params={}. While skill_repairs says repairing, return tasks=[]; when ready and no active repair_probe task, invoke it again. Do not build or surrender. This tests repair/redeployment, not strategy.'
        start=time.monotonic();deadline=start+420;start_loop=w.game_loop;samples=[]
        while time.monotonic()<deadline:
            actions=c.tick(w);w=arena.step([actions])[0];c.ingest_feedback(w)
            status=repair.public().get('repair_probe',{})
            samples.append(dict(game_loop=w.game_loop,wall_s=time.monotonic()-start,repair=status.get('state'),decisions=c.decisions))
            if status.get('state')=='failed':raise RuntimeError('repair failed: '+str(status))
            new=[t for t in c.tasks.tasks if t.program and t.program.entry['sha256']!=old and t.program.calls>=3]
            if new:
                report.update(status='passed',repaired_task_id=new[0].id,old_sha256=old,new_sha256=new[0].program.entry['sha256'],
                    redeployed_source=new[0].source,accepted_plans=c.decisions,engine_errors=arena.action_errors,
                    scenario_game_s=(w.game_loop-start_loop)/22.4,scenario_wall_s=time.monotonic()-start,timing=arena.timing())
                assert report['redeployed_source']=='planner'
                assert .9<report['scenario_game_s']/report['scenario_wall_s']<1.1
                arena.save_replay('logs/skill_repair_live.SC2Replay');break
        else:raise TimeoutError('real repair + redeployment exceeded 420s')
        report['samples']=samples;print('SKILL_REPAIR_LIVE_OK',json.dumps({k:v for k,v in report.items() if k!='samples'}),flush=True)
    except BaseException as e:report.update(status='failed',error=type(e).__name__+': '+str(e));raise
    finally:
        if c:report['stats']=c.stats();c.stop()
        if arena:arena.close()
        for client in clients:client.close()
        server.close();Path('logs/skill_repair_live.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':app.run(main)
