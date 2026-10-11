"""Fixture contracts; actual native execution is verified separately."""
from copy import deepcopy
from dataclasses import replace
import json
import tempfile
import time
import unittest
from arena.tasks import TaskQueue, PlanValidationError
from arena.commander import Commander
from test_realtime_runtime import Backend, unit, world
from arena.raw_engine import SCV, MARINE, CC, DEPOT, GATHER, ATTACK

class SkillBackend(Backend):
    def available_abilities(self,index,tags):return {tag:{380,388,390} for tag in tags}

def definition(name='cycle',kind='scv',steps=None,**kw):
    return dict(name=name,select=dict(unit=kind,count=1),steps=steps or [
        dict(action='build',structure='supply_depot'),
        dict(action='gather',target='nearest_mineral',queue=True)],**kw)

def queue():return TaskQueue(SkillBackend(),0,lambda _:None)
def enqueue(q,d):q.enqueue([{'skill':d['name']}],0,skills=[d],request_id=7)

class SkillTests(unittest.TestCase):
    def test_define_invoke_reuse_and_player_isolation(self):
        q=queue();d=definition();q.enqueue([],0,skills=[d]);self.assertEqual(q.tasks,[])
        q.enqueue([{'skill':'cycle'}],1);self.assertIsNotNone(q.tasks[0].program)
        d['steps'].clear();self.assertEqual(len(q.library['cycle']['steps']),2)
        self.assertEqual(queue().library,{})
    def test_invalid_plan_never_partially_stores_definition(self):
        q=queue()
        with self.assertRaises(PlanValidationError):q.enqueue([{'skill':'unknown'}],0,skills=[definition()])
        self.assertEqual(q.library,{});self.assertEqual(q.tasks,[])
        d=definition();d['steps'][1]['code']='print(123)'
        with self.assertRaises(PlanValidationError) as e:enqueue(q,d)
        self.assertIn('skills[0].steps[1].code',str(e.exception));self.assertEqual(q.library,{})
    def test_reject_bad_queue_selector_count_and_builtin_override(self):
        bad=[definition(steps=[dict(action='stim',queue=True)],kind='marine'),
             definition(kind='marine'),definition(name='barracks'),definition(timeout_s=float('nan'))]
        d=definition();d['select']['count']=2;bad.append(d)
        for d in bad:
            with self.subTest(d=d),self.assertRaises(PlanValidationError):enqueue(queue(),d)
    def test_active_redefinition_is_atomic(self):
        q=queue();d=definition();enqueue(q,d)
        changed=deepcopy(d);changed['timeout_s']=99
        with self.assertRaises(PlanValidationError):q.enqueue([],0,skills=[changed])
        self.assertEqual(q.library['cycle'],d)
    def test_build_native_queue_requires_finished_build_and_current_gather(self):
        q=queue();enqueue(q,definition());events=[];q.feedback=lambda kind,**kw:events.append((kind,kw))
        units=[unit(1,CC,is_building=True),unit(2,SCV),unit(3,343,34,34,3,mineral=True)]
        commands=q.tick(world(units),set());self.assertEqual(len(commands),2)
        self.assertEqual([c.action_raw.unit_command.queue_command for c in commands],[False,True])
        self.assertEqual([c['step'] for c in q.action_contexts],[1,2])
        self.assertEqual(q.protected(),{2});t=q.tasks[0];x,y=t.spot
        units[1]=replace(units[1],orders=(319,GATHER),order_targets=(0,3))
        units.append(unit(4,DEPOT,x,y,progress=.5,is_building=True))
        self.assertEqual(q.tick(world(units,10),set()),[]);self.assertEqual(t.program.index,0)
        units[-1]=replace(units[-1],progress=1)
        q.tick(world(units,11),set());self.assertEqual(t.program.index,1)
        q.tick(world(units,12),set());self.assertNotEqual(t.status,'completed')
        units[1]=replace(units[1],orders=(295,),order_targets=(3,))
        q.tick(world(units,13),set());self.assertEqual(t.status,'completed');self.assertEqual(q.protected(),set())
        self.assertEqual(len([e for e in events if e[0]=='skill_step_completed']),2)
    def test_move_arrival_does_not_pretend_tank_is_sieged(self):
        q=queue();enqueue(q,definition(kind='siege_tank',steps=[dict(action='move',target='rally'),dict(action='siege',queue=True)]))
        units=[unit(2,33)]
        commands=q.tick(world(units),set());self.assertEqual(len(commands),2)
        target=q.tasks[0].program.states[0]['pos']
        q.tick(world(units,1),set());self.assertEqual(q.tasks[0].program.index,0)
        units=[replace(units[0],x=target[0],y=target[1])]
        q.tick(world(units,2),set());q.tick(world(units,3),set());self.assertNotEqual(q.tasks[0].status,'completed')
        units=[replace(units[0],type_id=32,orders=(388,))]
        q.tick(world(units,4),set());self.assertNotEqual(q.tasks[0].status,'completed')
        units=[replace(units[0],orders=())]
        q.tick(world(units,5),set());self.assertEqual(q.tasks[0].status,'completed')
    def test_stim_waits_for_research_then_buff_before_attack(self):
        q=queue();enqueue(q,definition(kind='marine',steps=[dict(action='stim'),dict(action='attack_move',target='enemy_start')]))
        units=[unit(2,MARINE,ground_dps=10)]
        self.assertEqual(q.tick(world(units),set()),[]);self.assertIn('research missing',q.tasks[0].reason)
        w=replace(world(units,2),upgrades=(15,));cmd=q.tick(w,set())
        self.assertEqual(len(cmd),1);self.assertEqual(cmd[0].action_raw.unit_command.ability_id,3675)
        self.assertEqual(q.tick(replace(w,game_loop=67),set()),[])
        units=[replace(units[0],buffs=(27,))]
        cmd=q.tick(replace(world(units,4),upgrades=(15,)),set())
        self.assertEqual(cmd[0].action_raw.unit_command.ability_id,ATTACK)
    def test_reserved_army_cannot_be_stolen_by_legacy_attack(self):
        q=queue();enqueue(q,definition(kind='marine',steps=[dict(action='wait',condition='enemy_near')]))
        units=[unit(2,MARINE,ground_dps=10)]
        q.tick(world(units),set());q.enqueue([{'skill':'attack_nearest'}],1)
        self.assertEqual(q.tick(world(units,1),set()),[])
    def test_rejection_fails_and_clears_native_queue(self):
        q=queue();enqueue(q,definition(kind='siege_tank',steps=[dict(action='move',target='rally'),dict(action='siege',queue=True)]))
        units=[unit(2,33)];q.tick(world(units),set())
        cmd=q.tick(replace(world(units,1),errors=((2,388,1),)),set())
        self.assertEqual(q.tasks[0].status,'failed');self.assertIn('cause unknown',q.tasks[0].reason)
        self.assertEqual(cmd[0].action_raw.unit_command.ability_id,4)
        self.assertEqual(q.action_contexts[0]['source'],'composite_skill_cancel')
    def test_unit_loss_and_timeout_visible_without_replacement(self):
        for missing in (False,True):
            q=queue();enqueue(q,definition(kind='marine',steps=[dict(action='wait',condition='enemy_near',timeout_s=2)]))
            q.tick(world([unit(2,MARINE)]),set())
            q.tick(world([unit(3,MARINE)] if missing else [unit(2,MARINE)],1 if missing else 3),set())
            self.assertEqual(q.tasks[0].status,'failed');self.assertEqual(q.tasks[0].program.tags,{2})
    def test_old_attack_to_different_destination_is_not_completion(self):
        q=queue();enqueue(q,definition(kind='marine',steps=[dict(action='attack_move',target='enemy_start')]))
        units=[unit(2,MARINE,orders=(23,),order_positions=((40,40),))]
        q.tick(world(units),set());q.tick(world(units,1),set())
        self.assertNotEqual(q.tasks[0].status,'completed')
        units=[replace(units[0],order_positions=((160,160),))]
        q.tick(world(units,2),set());self.assertEqual(q.tasks[0].status,'completed')
    def test_missing_enemy_does_not_fallback_to_enemy_base(self):
        q=queue();enqueue(q,definition(kind='marine',steps=[dict(action='attack_move',target='nearest_enemy')]))
        self.assertEqual(q.tick(world([unit(2,MARINE)]),set()),[])
        self.assertIn('no visible enemy',q.tasks[0].reason)
    def test_commander_accepts_definition_and_sends_library_next_request(self):
        class Planner:
            prompts=[]
            def chat(self,system,user):
                self.prompts.append(json.loads(user))
                return json.dumps({'skills':[definition(kind='marine',steps=[dict(action='wait',condition='enemy_near')])], 'tasks':[{'skill':'cycle'}]})
        b=SkillBackend();b.initial=[world([unit(2,MARINE)])];client=Planner()
        with tempfile.TemporaryDirectory(dir='logs') as folder:
            c=Commander(b,0,client,opening=False,feedback_dir=folder,interval=99999)
            try:
                c.tick(b.initial[0]);end=time.monotonic()+2
                while c.outbox.empty() and time.monotonic()<end:time.sleep(.005)
                c.last_request=float("-inf")
                c.tick(world([unit(2,MARINE)],2));end=time.monotonic()+2
                while len(client.prompts)<2 and time.monotonic()<end:time.sleep(.005)
                self.assertEqual(c.decisions,1);self.assertIn('cycle',c.tasks.library)
                self.assertEqual(client.prompts[1]['skill_library'][0]['name'],'cycle')
                self.assertFalse(client.prompts[1]['control_policy']['combat_reflex_enabled'])
            finally:c.stop()

if __name__=='__main__':unittest.main()
