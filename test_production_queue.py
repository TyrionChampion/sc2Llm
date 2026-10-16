"""Explicit finite replenishment: real queue/executor, no model calls."""
import unittest
from dataclasses import replace
from arena.tasks import TaskQueue, PlanValidationError
from arena.autonomous_bot import BotPlanner, RequestBudget
from arena.battle_plans import output_schema
import test_multirace as fixtures
from test_autonomous_bot import Client


class ProductionQueueTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.MultiRace(); self.fixture.setUp()
        self.events = []
        self.queue = TaskQueue(self.fixture.backend('terran', (560,)), 0,
            lambda _: None, lambda kind, **kw: self.events.append(dict(kind=kind, **kw)))

    def train(self, count, mode=None, skill='train_unit'):
        args = {'unit': 'marine'} if skill == 'train_unit' else {}
        if mode is not None: args['queue_mode'] = mode
        return dict(skill=skill, count=count, args=args)

    def test_append_preserves_inflight_order_and_adds_exact_quantity(self):
        q = self.queue
        q.enqueue([self.train(2)], 10)
        w = self.fixture.world([self.fixture.unit(2, 21, is_building=True)])
        self.assertEqual(len(q.tick(w, set())), 1)
        task = q.tasks[0]; pending = list(task.program.pending)
        q.enqueue([self.train(4, 'append')], 11, request_id=2)
        self.assertEqual(len(q.tasks), 1)
        self.assertEqual(task.count, 6)
        self.assertEqual(task.created, 10)
        self.assertEqual(task.program.pending, pending)
        self.assertEqual(task.program.issued, 1)
        self.assertEqual(q.tick(replace(w, game_loop=250,
            units=(replace(w.units[0], orders=(560,)),)), set()), [])
        self.assertEqual(q.summary()[0]['production_quantity'], dict(
            requested=6, issued=1, observed_completed=0, unissued=5,
            awaiting_completion=1, remaining=6))

    def test_default_repeat_is_explicit_no_add_with_existing_task_evidence(self):
        q = self.queue; q.enqueue([self.train(2)], 10)
        q.enqueue([self.train(12)], 11, request_id=2)
        self.assertEqual(q.tasks[0].count, 2)
        event = self.events[-1]
        self.assertEqual(event['kind'], 'task_coalesced')
        self.assertEqual(event['task_id'], 1)
        self.assertEqual(event['requested_count'], 12)
        self.assertEqual(event['added_quantity'], 0)
        self.assertEqual(event['production_quantity']['remaining'], 2)
    def test_old_queue_completion_before_first_purchase_does_not_fulfil_a_new_goal(self):
        q=self.queue;q.enqueue([self.train(1)],10)
        barracks=self.fixture.unit(2,21,is_building=True,orders=(560,))
        w=self.fixture.world([barracks]);self.assertEqual(q.tick(w,set()),[])
        old=self.fixture.unit(3,48)
        ready=replace(w,game_loop=250,units=(replace(barracks,orders=()),old))
        self.assertEqual(len(q.tick(ready,set())),1)
        pending=replace(ready,game_loop=251,units=(barracks,old))
        self.assertEqual(q.tick(pending,set()),[])
        self.assertTrue(q.tasks[0].active);self.assertEqual(q.tasks[0].program.born,set())
        fresh=self.fixture.unit(4,48)
        q.tick(replace(pending,game_loop=500,units=(replace(barracks,orders=()),old,fresh)),set())
        self.assertEqual(q.tasks[0].status,'completed');self.assertEqual(q.tasks[0].program.born,{4})
    def test_finite_batch_25_is_supported_without_relaxing_total_or_replay_guards(self):
        q=self.queue;q.enqueue([self.train(25)],10)
        q.enqueue([self.train(25,'append')],11,request_id=9)
        self.assertEqual(q.tasks[0].count,50)
        q.enqueue([self.train(25,'append')],12,request_id=9)
        self.assertEqual(q.tasks[0].count,50)
        with self.assertRaises(PlanValidationError):q.enqueue([self.train(201,'append')],13,request_id=10)
        with self.assertRaises(PlanValidationError):q.enqueue([self.train(151,'append')],14,request_id=11)
        self.assertEqual(q.tasks[0].count,50)

    def test_unacknowledged_unreachable_build_retries_without_counting_a_purchase(self):
        q=TaskQueue(self.fixture.backend('terran',(319,)),0,lambda _:None,
            lambda kind,**kw:self.events.append(dict(kind=kind,**kw)))
        q.enqueue([dict(skill='build_supply')],10)
        w=self.fixture.world([self.fixture.unit(1,18,is_building=True),self.fixture.unit(2,45)])
        self.assertEqual(len(q.tick(w,set())),1)
        task=q.tasks[0];point=tuple(task.spot)
        denied=replace(w,game_loop=250,errors=((2,319,208),))
        self.assertEqual(q.tick(denied,set()),[])
        self.assertTrue(task.active)
        self.assertEqual(task.program.issued,0)
        self.assertEqual(task.program.pending,[])
        self.assertEqual(task.program.retries,1)
        self.assertIn(point,task.excluded)
        retry=next(e for e in reversed(self.events) if e['kind']=='macro_retry')
        self.assertEqual(retry['errors'][0]['result'],'CouldntReachTarget')
        next_frame=replace(w,game_loop=300)
        self.assertEqual(len(q.tick(next_frame,set())),1)
        self.assertNotEqual(tuple(task.spot),point)

    def test_gateway_transition_rejects_only_the_unpaid_attempt_and_preserves_accepted_queue(self):
        q=TaskQueue(self.fixture.backend('protoss',(916,)),0,lambda _:None,
            lambda kind,**kw:self.events.append(dict(kind=kind,**kw)))
        q.enqueue([dict(skill='train_unit',count=3,args=dict(unit='zealot'))],10)
        first=self.fixture.unit(1,62,is_building=True);second=replace(first,tag=2,x=40)
        w=self.fixture.world([first,second],race='protoss');self.assertEqual(len(q.tick(w,set())),1)
        queued=replace(w,game_loop=250,units=(replace(first,orders=(916,)),second))
        self.assertEqual(len(q.tick(queued,set())),1)
        task=q.tasks[0];accepted=task.program.pending[0]
        self.assertTrue(accepted['seen']);self.assertEqual(task.program.issued,2)
        denied=replace(queued,game_loop=251,units=(replace(first,orders=(916,)),replace(second,orders=(1518,))),errors=((2,916,2),))
        self.assertEqual(q.tick(denied,set()),[])
        self.assertTrue(task.active);self.assertEqual(task.program.issued,1)
        self.assertEqual(task.program.pending,[accepted]);self.assertEqual(task.program.retries,1)
        self.assertEqual(task.count,3)

    def test_generic_not_supported_without_a_gateway_transition_remains_a_failure(self):
        q=TaskQueue(self.fixture.backend('protoss',(916,)),0,lambda _:None)
        q.enqueue([dict(skill='train_unit',args=dict(unit='zealot'))],10)
        w=self.fixture.world([self.fixture.unit(1,62,is_building=True)],race='protoss')
        q.tick(w,set());self.assertEqual(q.tick(replace(w,game_loop=250,errors=((1,916,2),)),set()),[])
        self.assertEqual(q.tasks[0].status,'failed')

    def blocked_tank_queue(self):
        q=TaskQueue(self.fixture.backend('terran',(591,)),0,lambda _:None,
            lambda kind,**kw:self.events.append(dict(kind=kind,**kw)))
        q.enqueue([dict(skill='train_unit',args=dict(unit='siege_tank'))],10)
        factory=self.fixture.unit(2,27,is_building=True,addon_tag=3)
        lab=self.fixture.unit(3,39,is_building=True)
        w=self.fixture.world([factory,lab]);self.assertEqual(len(q.tick(w,set())),1)
        accepted=replace(w,game_loop=300,units=(replace(factory,orders=(591,)),lab))
        q.tick(accepted,set())
        denied=replace(accepted,game_loop=1000,errors=((2,591,41),))
        self.assertEqual(q.tick(denied,set()),[])
        return q,denied

    def test_accepted_output_blocked_queue_is_retained_until_product_observed(self):
        q,w=self.blocked_tank_queue();task=q.tasks[0]
        self.assertTrue(task.active);self.assertEqual(task.program.issued,1)
        self.assertEqual(task.program.retries,0)
        self.assertEqual(task.program.summary()['blocked_outputs'][0]['result'],'CantFindPlacementLocation')
        self.assertEqual(q.tick(replace(w,game_loop=1100,errors=()),set()),[])
        completed=replace(w,game_loop=1200,errors=(),units=(replace(w.units[0],orders=()),w.units[1],self.fixture.unit(4,33)))
        self.assertEqual(q.tick(completed,set()),[])
        self.assertEqual(task.status,'completed');self.assertEqual(task.program.issued,1)
        self.assertEqual(task.program.born,{4})

    def test_blocked_queue_disappearing_without_output_fails_after_observation_grace(self):
        q,w=self.blocked_tank_queue();task=q.tasks[0]
        disappeared=replace(w,game_loop=1100,errors=(),units=(replace(w.units[0],orders=()),w.units[1]))
        self.assertEqual(q.tick(disappeared,set()),[]);self.assertTrue(task.active)
        self.assertEqual(q.tick(replace(disappeared,game_loop=1300),set()),[])
        self.assertEqual(task.status,'failed');self.assertEqual(task.program.issued,1)
        self.assertEqual(task.program.retries,0)

    def test_append_request_replay_cannot_double_purchase_even_after_completion(self):
        q = self.queue; plan = [self.train(4, 'append')]
        q.enqueue(plan, 10, request_id=2)
        q.enqueue(plan, 11, request_id=2)
        self.assertEqual(q.tasks[0].count, 4)
        q.update(q.tasks[0], 'completed', 'observed all requested products')
        q.enqueue(plan, 12, request_id=2)
        self.assertEqual(len(q.tasks), 1)

    def test_invalid_mix_and_excessive_append_leave_existing_quantity_unchanged(self):
        q = self.queue; q.enqueue([self.train(20)], 10)
        for i in range(9): q.enqueue([self.train(20, 'append')], 11+i, request_id=i)
        self.assertEqual(q.tasks[0].count, 200)
        with self.assertRaises(PlanValidationError):
            q.enqueue([self.train(1, 'append')], 30, request_id=20)
        self.assertEqual(q.tasks[0].count, 200)
        other = TaskQueue(self.fixture.backend(), 0, lambda _: None)
        other.enqueue([self.train(2)], 10)
        with self.assertRaises(PlanValidationError):
            other.enqueue([self.train(4, 'append'), {'skill': 'nonexistent'}], 11)
        self.assertEqual(other.tasks[0].count, 2)
    def test_completed_products_do_not_consume_the_outstanding_append_limit(self):
        q=self.queue;q.enqueue([self.train(186)],10)
        task=q.tasks[0];task.program.issued=46;task.program.born=set(range(1000,1046))
        original_pending=task.program.pending
        q.enqueue([self.train(50,'append')],20,request_id=9)
        self.assertEqual(task.count,236)
        self.assertEqual(q.production_quantity(task)['remaining'],190)
        self.assertEqual(task.program.born,set(range(1000,1046)))
        self.assertIs(task.program.pending,original_pending)
        q.enqueue([self.train(50,'append')],21,request_id=9)
        self.assertEqual(task.count,236)
        with self.assertRaises(PlanValidationError):q.enqueue([self.train(11,'append')],22,request_id=10)
        self.assertEqual(task.count,236)

    def test_cancelled_unissued_goal_does_not_consume_the_replacement_queue_limit(self):
        q=self.queue;q.enqueue([self.train(200)],10)
        old=q.tasks[0]
        plan=[dict(skill='cancel_task',args=dict(task_id=old.id)),self.train(100,'append')]
        q.enqueue(plan,11,request_id=19)
        self.assertEqual(old.status,'cancelled')
        active=[t for t in q.tasks if t.active]
        self.assertEqual(len(active),1);self.assertEqual(active[0].count,100)

    def test_workers_and_each_race_can_explicitly_append(self):
        for race, product in [('terran', 'marine'), ('zerg', 'roach'), ('protoss', 'zealot')]:
            for skill in ('train_unit', 'train_worker'):
                with self.subTest(race=race, skill=skill):
                    q = TaskQueue(self.fixture.backend(race), 0, lambda _: None)
                    args = {'unit': product} if skill == 'train_unit' else {}
                    q.enqueue([dict(skill=skill, count=2, args=args)], 10)
                    q.enqueue([dict(skill=skill, count=3,
                        args=dict(args, queue_mode='append'))], 11)
                    self.assertEqual([t.count for t in q.tasks], [5])

    def test_modes_do_not_apply_to_buildings_forms_or_ammo(self):
        for command in [dict(skill='build_supply', args={'queue_mode': 'append'}),
            dict(skill='train_unit', args={'unit': 'nuke', 'queue_mode': 'append'}),
            self.train(2, 'automatic'), dict(skill='morph_unit',
                args={'unit': 'siege_tank_sieged', 'queue_mode': 'append'})]:
            with self.subTest(command=command), self.assertRaises(PlanValidationError):
                self.queue.enqueue([command], 10)
        self.assertEqual(self.queue.tasks, [])

    def test_provider_schema_accepts_explicit_append(self):
        import jsonschema
        for race in ('terran', 'zerg', 'protoss'):
            tree = self.fixture.tree.public(race)
            schema = output_schema({'tech_tree': tree})
            product = {'terran': 'marine', 'zerg': 'roach', 'protoss': 'zealot'}[race]
            jsonschema.validate({'reason': '显式补兵', 'tasks': [dict(skill='train_unit', count=4,
                args={'unit': product, 'queue_mode': 'append'})]}, schema)
            jsonschema.validate({'reason': '显式补工', 'tasks': [self.train(4, 'append', 'train_worker')]}, schema)

    def test_append_continues_after_first_birth_and_stops_at_explicit_total(self):
        q = self.queue
        q.enqueue([self.train(1)], 10)
        producer = self.fixture.unit(2, 21, is_building=True)
        w = self.fixture.world([producer])
        q.tick(w, set())
        q.enqueue([self.train(1, 'append')], 10, request_id=2)
        q.tick(replace(w, game_loop=250, units=(replace(producer, orders=(560,)),)), set())
        first = self.fixture.unit(3, 48)
        q.tick(replace(w, game_loop=300, units=(producer, first)), set())
        actions = q.tick(replace(w, game_loop=320, units=(producer, first)), set())
        self.assertEqual(len(actions), 1)
        self.assertEqual(q.tasks[0].program.issued, 2)
        q.tick(replace(w, game_loop=350, units=(replace(producer, orders=(560,)), first)), set())
        self.assertEqual(q.tick(replace(w, game_loop=400,
            units=(producer, first, self.fixture.unit(4, 48))), set()), [])
        self.assertEqual(q.tasks[0].status, 'completed')
        self.assertEqual(q.tick(replace(w, game_loop=500,
            units=(producer, first, self.fixture.unit(4, 48))), set()), [])

    def test_rejected_defense_and_append_mix_cannot_change_current_order(self):
        q = self.queue
        q.enqueue([dict(skill='defend_base', args=dict(base='home', units='all', reason='守主矿'))], 10)
        old = q.tasks[0]
        with self.assertRaises(PlanValidationError):
            q.enqueue([dict(skill='defend_base', args=dict(base='home', units='all', stance='trade', reason='改交换')),
                       self.train(2, 'append'), dict(skill='nonexistent')], 11)
        self.assertEqual(q.tasks, [old])
        self.assertEqual(old.status, 'queued')
        self.assertFalse(getattr(old, 'superseded', False))
        self.assertEqual(q.pending_stops, [])
        self.assertEqual(list(q.production_append_receipts), [])

    def test_zerg_knowledge_exposes_native_injection_to_actual_decision_payload(self):
        from arena.knowledge import model_knowledge
        from arena.context import battle_payload
        w = self.fixture.world([self.fixture.unit(1, 126)], race='zerg')
        p = battle_payload(dict(world=w.summary(),
            game_knowledge=model_knowledge(self.fixture.tree, w), active_tasks=self.queue.summary()))
        import json
        text = json.dumps(p, ensure_ascii=False)
        self.assertIn('inject_larva', text)
        self.assertIn('不需Python', text)

    def test_legacy_adapter_cannot_overbuy_while_waiting_for_birth(self):
        from test_realtime_runtime import Backend, unit, world
        q = TaskQueue(Backend(), 0, lambda _: None)
        q.enqueue([self.train(1)], 0)
        producer = unit(1, 21, is_building=True)
        self.assertEqual(len(q.tick(world([producer], 0), set())), 1)
        # An idle observation is not permission to exceed an explicit finite batch.
        self.assertEqual(q.tick(world([producer], 2), set()), [])
        q.enqueue([self.train(1, 'append')], 3, request_id=2)
        self.assertEqual(len(q.tick(world([producer], 3), set())), 1)
        self.assertEqual(q.production_quantity(q.tasks[0])['issued'], 2)
        self.assertEqual(q.tick(world([producer, unit(2, 48), unit(3, 48)], 10), set()), [])
        self.assertEqual(q.tasks[0].status, 'completed')


class ProductionCadenceBudgetTests(unittest.TestCase):
    def planner(self):
        p = BotPlanner(Client(), RequestBudget(26), '模型自主选择', adaptive_cadence=True)
        p.client.attempts = 18; p.last_request_game_s = 500
        p.policy = dict(game_s=525, max_game_s=1800, estimated_game_deadline_s=790,
            production_decision_due=True)
        return p

    def test_idle_capacity_spreads_remaining_requests_and_reserves_execution_time(self):
        p = self.planner()
        self.assertAlmostEqual(p.decision_interval(), 265/7)
        self.assertFalse(p.ready_for_request())
        p.policy['game_s'] = 540
        self.assertTrue(p.ready_for_request())

    def test_visible_combat_still_wakes_at_25_seconds_without_bypassing_cap(self):
        p = self.planner(); p.policy['decision_urgent'] = True
        self.assertEqual(p.decision_interval(), 25)
        self.assertTrue(p.ready_for_request())
        p.client.attempts = 24
        self.assertFalse(p.ready_for_request())

    def test_persistent_idle_capacity_leaves_requests_for_late_game(self):
        p = self.planner(); p.client.attempts = 0
        now = 0; requested_at = []
        while p.client.attempts < 24:
            p.policy['game_s'] = now
            requested_at.append(now)
            p.client.attempts += 1
            now += p.decision_interval()
        self.assertLess(sum(t <= 580 for t in requested_at), 24)
        self.assertGreater(requested_at[-1], 700)
        self.assertGreater(790-requested_at[-1], 25)


if __name__ == '__main__': unittest.main()
