"""Deterministic unit tests; live guarantees are in verify_realtime.py."""
import unittest
import json
import threading
import time
from arena.commander import Commander
from arena.raw_engine import *
from arena.tasks import TaskQueue
from arena.reflex import ReflexNet
from evaluate_realtime import aggregate

class Backend:
    register_action_context=RawArena.register_action_context
    homes=[(30,30)]; size=(200,200)
    def placement(self,i,a,w,points): return points[0]
    def enemy_start(self,i): return (160,160)

def unit(tag,kind,x=30,y=30,alliance=1,**kw):
    return Entity(tag,kind,alliance,x,y,kw.pop('health',45),45,**kw)
def world(units,sec=0,minerals=500,supply=15):
    return Snapshot(int(sec*22.4),minerals,12,supply,tuple(units),(100,100))

class RuntimeTests(unittest.TestCase):
    def queue(self): return TaskQueue(Backend(),0,lambda _:None)
    def test_offscreen_train_uses_tag(self):
        q=self.queue(); q.enqueue([{'skill':'train_worker'}],0)
        result=q.tick(world([unit(42,CC,is_building=True)]),set())
        self.assertEqual(list(result[0].action_raw.unit_command.unit_tags),[42])
        self.assertFalse(result[0].HasField('action_feature_layer'))
    def test_build_once_until_observed(self):
        q=self.queue(); q.enqueue([{'skill':'supply_depot'}],0)
        units=[unit(1,CC,is_building=True),unit(2,SCV)]
        first=q.tick(world(units),set()); self.assertEqual(len(first),1)
        for sec in range(1,10): self.assertEqual(q.tick(world(units,sec),set()),[])
        t=q.tasks[0]; x,y=t.spot
        units.append(unit(3,DEPOT,x,y,progress=.2,is_building=True))
        q.tick(world(units,10),set()); self.assertEqual(t.status,'running')
        units[-1]=unit(3,DEPOT,x,y,is_building=True)
        self.assertEqual(q.tick(world(units,11),set()),[])
        self.assertEqual(t.status,'completed')
    def test_duplicate_does_not_reset_build(self):
        q=self.queue(); plan=[{'skill':'supply_depot'}]; q.enqueue(plan,0)
        q.tick(world([unit(1,CC,is_building=True),unit(2,SCV)]),set())
        original=q.tasks[0]; q.enqueue(plan,2)
        self.assertIs(q.tasks[0],original); self.assertEqual(len(q.tasks),1)
        self.assertIsNotNone(original.spot)
    def test_explicit_wait_preserves_active_plan(self):
        q=self.queue();q.enqueue([{'skill':'supply_depot'}],0)
        original=q.tasks[0];events=[];q.emit=events.append
        q.enqueue([],1,'LLM')
        self.assertIs(q.tasks[0],original)
        self.assertTrue(any('explicit wait' in line and '1 active' in line for line in events))
    def test_non_list_wait_is_rejected(self):
        for malformed in (None,{},'',0):
            with self.assertRaises(ValueError):self.queue().enqueue(malformed,0)
    def test_reject_entire_invalid_plan(self):
        q=self.queue()
        with self.assertRaises(ValueError): q.enqueue([{'skill':'barracks'},{'skill':'hack'}],0)
        self.assertEqual(q.tasks,[])
    def test_reject_coordinates_and_bad_count(self):
        for p in ({'skill':'move_out','args':{'x':10}}, {'skill':'train_worker','count':True},
                  {'skill':'train_unit','args':{'unit':'carrier'}}):
            with self.assertRaises(ValueError): self.queue().enqueue([p],0)
    def test_reflex_reservation_blocks_macro_worker(self):
        worker=unit(2,SCV,health=5,ground_dps=5)
        enemy=unit(3,MARINE,33,30,4,ground_dps=10)
        w=world([unit(1,CC,is_building=True),worker,enemy])
        r=ReflexNet((30,30),lambda _:None,combat_enabled=True)
        cmds,used=r.tick(w)
        self.assertIn(worker.tag,used); self.assertEqual(cmds[0].action_raw.unit_command.ability_id,MOVE)
        q=self.queue(); q.enqueue([{'skill':'supply_depot'}],0)
        self.assertEqual(q.tick(w,used),[])
    def test_healthy_army_defends_during_other_task(self):
        marine=unit(2,MARINE,ground_dps=10)
        intruder=unit(3,SCV,33,30,4,ground_dps=3)
        r=ReflexNet((30,30),lambda _:None,combat_enabled=True)
        cmds,used=r.tick(world([marine,intruder]))
        self.assertEqual(cmds[0].action_raw.unit_command.ability_id,ATTACK)
        self.assertIn(2,used)
    def test_default_reflex_does_not_take_army_from_attack_task(self):
        wounded=unit(2,MARINE,health=10,ground_dps=10)
        enemy=unit(3,MARINE,33,30,4,ground_dps=10)
        w=world([unit(1,CC,is_building=True),wounded,enemy],10)
        r=ReflexNet((30,30),lambda _:None)
        reactions,reserved=r.tick(w)
        self.assertFalse(r.combat_enabled)
        self.assertEqual(reactions,[]);self.assertNotIn(2,reserved)
        q=self.queue();q.enqueue([{'skill':'attack_nearest'}],10)
        actions=q.tick(w,reserved)
        self.assertEqual(len(actions),1)
        self.assertEqual(actions[0].action_raw.unit_command.ability_id,ATTACK)
        self.assertEqual(list(actions[0].action_raw.unit_command.unit_tags),[2])
        self.assertEqual(r.counts['retreat'],0);self.assertEqual(r.counts['defend'],0)
    def test_legacy_attack_waits_for_unit_completion(self):
        q=self.queue();q.enqueue([{'skill':'attack_nearest'}],0)
        forming=unit(2,73,ground_dps=10,progress=.2)
        target=unit(3,MARINE,33,30,4,ground_dps=10)
        self.assertEqual(q.tick(world([forming,target]),set()),[])
        self.assertTrue(q.tasks[0].active)
        ready=replace(forming,progress=1)
        actions=q.tick(world([ready,target],5),set())
        self.assertEqual(len(actions),1)
        self.assertEqual(list(actions[0].action_raw.unit_command.unit_tags),[2])

    def test_default_reflex_only_gathers_idle_unprotected_workers(self):
        r=ReflexNet((30,30),lambda _:None)
        r.retreat_until[2]=100  # Even stale retreat state cannot re-enable combat.
        workers=[unit(2,SCV,health=5,ground_dps=5),unit(3,SCV)]
        enemy=unit(4,MARINE,33,30,4,ground_dps=10)
        mineral=unit(5,343,35,30,3,mineral=True)
        actions,reserved=r.tick(world(workers+[enemy,mineral],10),protected={3})
        self.assertEqual(len(actions),1)
        self.assertEqual(actions[0].action_raw.unit_command.ability_id,GATHER)
        self.assertEqual(list(actions[0].action_raw.unit_command.unit_tags),[2])
        self.assertNotIn(3,reserved)
    def test_commander_tells_actual_client_combat_reflex_is_off(self):
        class Capture:
            prompt=None;system=None
            def chat(self,system,user):
                self.system=system;self.prompt=json.loads(user);return '{"tasks":[]}'
        b=Backend();b.initial=[world([unit(1,CC,is_building=True)])];client=Capture()
        c=Commander(b,0,client,opening=False)
        try:
            c.tick(b.initial[0]);limit=time.monotonic()+1
            while c.outbox.empty() and time.monotonic()<limit:time.sleep(.005)
            self.assertIsNotNone(client.prompt)
            self.assertFalse(client.prompt['control_policy']['combat_reflex_enabled'])
            self.assertTrue(client.prompt['control_policy']['idle_worker_gathering'])
            self.assertIn('Combat reflex is OFF',client.system)
            self.assertFalse(c.stats()['combat_reflex_enabled'])
        finally:c.stop()
    def test_gather_does_not_steal_builder(self):
        r=ReflexNet((30,30),lambda _:None,combat_enabled=True)
        cmds,used=r.tick(world([unit(2,SCV),unit(3,343,35,30,3,mineral=True)]),{2})
        self.assertEqual(cmds,[])
    def test_budget_reserved_across_tasks(self):
        q=self.queue(); q.enqueue([{'skill':'supply_depot'},{'skill':'train_worker'}],0)
        result=q.tick(world([unit(1,CC,is_building=True),unit(2,SCV)],minerals=100),set())
        self.assertEqual(len(result),1)
    def test_simultaneous_builds_reserve_distinct_sites(self):
        q=self.queue(); q.enqueue([{'skill':'supply_depot'},{'skill':'barracks'}],0)
        units=[unit(1,CC,is_building=True),unit(2,SCV),unit(3,SCV),
               unit(4,DEPOT,20,20,is_building=True)]
        batch=q.tick(world(units),set())
        self.assertEqual(len(batch),2)
        self.assertGreaterEqual(math.dist(q.tasks[0].spot,q.tasks[1].spot),5)
    def test_training_completes_on_new_unit(self):
        q=self.queue(); q.enqueue([{'skill':'train_worker','count':1}],0)
        units=[unit(1,CC,is_building=True),unit(2,SCV)]
        q.tick(world(units),set())
        q.tick(world(units+[unit(3,SCV)],15),set())
        self.assertEqual(q.tasks[0].status,'completed')
    def test_unconfirmed_build_fails_visibly(self):
        q=self.queue(); q.enqueue([{'skill':'supply_depot'}],0)
        units=[unit(1,CC,is_building=True),unit(2,SCV)]
        for sec in range(0,80,13): q.tick(world(units,sec),set())
        self.assertEqual(q.tasks[0].status,'failed')
    def test_delayed_planner_does_not_block_opening(self):
        class Client:
            release=threading.Event()
            def chat(self,system,user):
                self.release.wait()
                return json.dumps({'tasks':[{'skill':'train_worker'}]})
        backend=Backend()
        backend.initial=[world([unit(1,CC,is_building=True),unit(2,SCV)])]
        client=Client(); c=Commander(backend,0,client)
        returned=threading.Event();batches=[];errors=[]
        def tick():
            try:batches.append(c.tick(backend.initial[0]))
            except BaseException as error:errors.append(error)
            finally:returned.set()
        worker=threading.Thread(target=tick,daemon=True)
        try:
            worker.start()
            self.assertTrue(returned.wait(1),'game tick waited for a model reply that remains explicitly blocked')
            self.assertFalse(errors);self.assertFalse(client.release.is_set())
            self.assertTrue(batches[0]); self.assertTrue(c.thinking)
            self.assertEqual(c.decisions,0)
            client.release.set()
            limit=time.monotonic()+1
            while c.outbox.empty() and time.monotonic()<limit: time.sleep(.005)
            c.tick(world(backend.initial[0].units,2))
            self.assertEqual(c.decisions,1)
        finally:
            client.release.set();worker.join(2);c.stop()
    def test_stale_plan_is_visible_and_discarded(self):
        class Client:
            def chat(self,*args): return '{"tasks":[{"skill":"no_op"}]}'
        b=Backend(); b.initial=[world([unit(1,CC,is_building=True),unit(2,SCV)])]
        c=Commander(b,0,Client(),opening=False)
        try:
            c.outbox.put((0,{'tasks':[{'skill':'barracks'}]},None,40,True,None,1,[]))
            c.tick(world(b.initial[0].units,40))
            self.assertEqual(c.plans_rejected,1)
            self.assertFalse(c.tasks.tasks)
            self.assertTrue(any('discarded stale' in line for line in c.log))
        finally: c.stop()
    def test_timeout_is_not_a_win_or_draw(self):
        stats=aggregate([{'outcome':'timeout'},{'outcome':'Victory'},{'outcome':'error'}])
        self.assertEqual(stats['completed'],1); self.assertEqual(stats['win_rate_completed'],1)
        self.assertEqual(stats['wins_per_started_match'],1/3)
        self.assertIsNone(aggregate([{'outcome':'timeout'}])['win_rate_completed'])

if __name__=='__main__': unittest.main()
