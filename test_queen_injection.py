"""Explicit injection ownership, single spending and evidence-based confirmation."""
import unittest
from dataclasses import replace
from arena.tasks import TaskQueue,PlanValidationError
from arena.battle_plans import output_schema
from arena.chatgpt_planner import battle_output_schema
from arena.queen import report
import test_multirace as fixtures

class QueenInjection(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.MultiRace();self.f.setUp();self.backend=self.f.backend('zerg',[251])
        self.events=[];self.q=TaskQueue(self.backend,0,lambda _:None,lambda kind,**kw:self.events.append(dict(kind=kind,**kw)))
        self.base=self.f.unit(1,86,is_building=True);self.queen=self.f.unit(2,126,energy=50)
    def world(self,units=None,seconds=1):return replace(self.f.world(units if units is not None else [self.base,self.queen],race='zerg'),game_loop=int(seconds*22.4))
    def enqueue(self,base='home'):self.q.enqueue([dict(skill='inject_larva',count=1,args=dict(base=base))],0)
    def test_explicit_cast_requires_both_energy_and_buff_then_never_recasts(self):
        self.enqueue();actions=self.q.tick(self.world(),set())
        self.assertEqual(len(actions),1);raw=actions[0].action_raw.unit_command
        self.assertEqual((raw.ability_id,list(raw.unit_tags),raw.target_unit_tag),(251,[2],1))
        self.assertEqual(self.q.tick(self.world([self.base,replace(self.queen,energy=25)],2),set()),[])
        self.assertEqual(self.q.tasks[0].status,'running')
        self.q.tick(self.world([replace(self.base,buffs=(11,)),replace(self.queen,energy=25)],3),set())
        self.assertEqual(self.q.tasks[0].status,'completed')
        self.assertFalse(next(e for e in self.events if e['kind']=='inject_larva_confirmed')['larva_spawn_confirmed'])
        self.assertEqual(self.q.tick(self.world(seconds=35),set()),[])
    def test_low_energy_allocated_enemy_or_missing_requested_base_never_casts(self):
        cases=[([self.base,replace(self.queen,energy=24)],'home',False),
               ([self.base,replace(self.queen,alliance=4)],'home',False),
               ([self.base,self.queen],'expansion',False),([self.base,self.queen],'site_unknown',False),
               ([self.base,self.queen],'home',True)]
        for units,base,allocated in cases:
            with self.subTest(base=base,units=units,allocated=allocated):
                self.setUp();self.enqueue(base)
                if allocated:self.q.defense_tags={2}
                self.assertEqual(self.q.tick(self.world(units),set()),[])
                self.assertEqual(self.q.tasks[0].status,'waiting')
    def test_old_native_injection_keeps_a_free_queen_busy_until_the_order_ends(self):
        expansion=replace(self.base,tag=3,x=60)
        self.enqueue('expansion')
        busy=replace(self.queen,energy=70,orders=(251,),order_targets=(1,))
        self.assertEqual(self.q.tick(self.world([self.base,expansion,busy]),set()),[])
        self.assertEqual(self.q.tasks[0].status,'waiting')
        out=self.q.tick(self.world([self.base,expansion,replace(busy,orders=(),order_targets=())],seconds=2),set())
        self.assertEqual(len(out),1);self.assertEqual(out[0].action_raw.unit_command.target_unit_tag,3)
    def test_injection_to_another_base_is_not_an_observed_order_for_this_goal(self):
        expansion=replace(self.base,tag=3,x=60);self.enqueue('expansion')
        self.q.tick(self.world([self.base,expansion,self.queen]),set())
        other_order=replace(self.queen,orders=(251,),order_targets=(1,))
        self.assertEqual(self.q.tick(self.world([self.base,expansion,other_order],seconds=3),set()),[])
        self.assertFalse(self.q.tasks[0].program.pending['native_order_observed'])
    def test_existing_buff_is_observable_noop_and_pending_intent_coalesces(self):
        self.enqueue();self.enqueue();self.assertEqual(len(self.q.tasks),1)
        self.assertEqual(self.q.tick(self.world([replace(self.base,buffs=(11,)),self.queen]),set()),[])
        self.assertEqual(self.q.tasks[0].status,'completed')
        self.assertTrue(any(e['kind']=='inject_larva_already_active' for e in self.events))
    def test_unconfirmed_cast_expires_without_retry(self):
        self.enqueue();self.q.tick(self.world(),set())
        self.assertEqual(self.q.tick(self.world(seconds=32),set()),[])
        self.assertEqual(self.q.tasks[0].status,'failed')
    def test_failure_keeps_current_orders_and_owned_actor_evidence_without_overwriting_them(self):
        self.enqueue();self.q.tick(self.world(),set())
        current=replace(self.queen,energy=56,orders=(16,),order_targets=(0,),order_positions=((60,60),))
        foreign=replace(self.base,tag=9,alliance=4)
        self.assertEqual(self.q.tick(self.world([self.base,current,foreign],seconds=12),set()),[])
        failure=next(e for e in self.events if e['kind']=='inject_larva_failed')
        proof=failure['observation_evidence']
        self.assertEqual(proof['queen']['orders'],[16]);self.assertEqual(proof['queen']['order_positions'],[(60,60)])
        self.assertEqual(proof['queen']['energy'],56);self.assertEqual(proof['base']['tag'],1)
        self.assertEqual([u['tag'] for u in proof['own_nearby_buildings']],[1])
        self.assertEqual(report(self.world([self.base,current]),self.q)['queens'][0]['orders'],[16])

    def test_pre_dispatch_expiry_retains_wait_reason_and_distinguishes_live_base_from_missing_queen(self):
        self.enqueue()
        self.q.tick(self.world([self.base,replace(self.queen,energy=24)]),set())
        reason=self.q.tasks[-1].reason
        later=self.world([self.base],seconds=92)
        self.assertEqual(self.q.tick(later,set()),[])
        self.assertEqual(self.q.tasks[-1].status,'failed')
        proof=next(e for e in self.events if e['kind']=='inject_larva_failed')['observation_evidence']
        self.assertEqual(proof['phase'],'before_dispatch')
        self.assertEqual(proof['last_wait_reason'],reason)
        self.assertIsNone(proof['queen']);self.assertIsNone(proof['base'])
        self.assertEqual(proof['own_queens'],[])
        self.assertEqual([u['tag'] for u in proof['own_bases']],[1])
        self.assertTrue(proof['own_bases'][0]['completed'])
        self.assertFalse(any(e['kind']=='inject_larva_order_issued' for e in self.events))

    def test_reported_deadline_includes_waiting_and_does_not_restart_at_dispatch(self):
        self.enqueue()
        self.q.tick(self.world([self.base,replace(self.queen,energy=24)]),set())
        self.assertEqual(self.q.tasks[-1].program.summary()['deadline_game_s'],90)
        out=self.q.tick(self.world(seconds=50),set())
        self.assertEqual(len(out),1)
        summary=self.q.tasks[-1].program.summary()
        self.assertEqual(summary['request_created_game_s'],0)
        self.assertEqual(summary['pending']['sent'],50)
        self.assertEqual(summary['deadline_game_s'],90)
        self.assertEqual(summary['remaining_game_s'],40)
        self.q.tick(self.world(seconds=92),set())
        self.assertEqual(self.q.tasks[-1].status,'failed')
        self.assertEqual(self.q.tasks[-1].program.summary()['remaining_game_s'],0)
    def test_explicit_base_cast_can_move_a_distant_free_queen_and_keeps_it_reserved(self):
        self.enqueue();queen=replace(self.queen,x=55)
        self.assertEqual(len(self.q.tick(self.world([self.base,queen]),set())),1)
        self.assertIn(2,self.q.protected())
        self.assertEqual(self.q.tick(self.world([self.base,replace(queen,x=42)],10),set()),[])
        self.assertEqual(self.q.tasks[0].status,'running')
        self.q.tick(self.world([replace(self.base,buffs=(11,)),replace(queen,x=30,energy=25)],20),set())
        self.assertEqual(self.q.tasks[0].status,'completed')
    def test_energy_regenerated_while_walking_does_not_mask_actual_cast_spending(self):
        self.enqueue();self.q.tick(self.world(),set())
        self.q.tick(self.world([self.base,replace(self.queen,energy=61)],7),set())
        self.q.tick(self.world([replace(self.base,buffs=(11,)),replace(self.queen,energy=36)],16),set())
        self.assertEqual(self.q.tasks[0].status,'completed')
        event=next(e for e in self.events if e['kind']=='inject_larva_confirmed')
        self.assertEqual((event['energy_at_send'],event['energy_before'],event['energy_now']),(50,61,36))
    def test_observed_approach_can_exceed_thirty_seconds_but_deadline_is_bounded(self):
        self.enqueue();queen=replace(self.queen,x=90);self.q.tick(self.world([self.base,queen]),set())
        queen=replace(queen,orders=(251,),order_targets=(self.base.tag,))
        for seconds,x in ((10,80),(20,65),(31,50)):
            self.q.tick(self.world([self.base,replace(queen,x=x,energy=50+seconds*.7)],seconds),set())
            self.assertEqual(self.q.tasks[0].status,'running')
        self.q.tick(self.world([self.base,replace(queen,x=45,energy=90)],91),set())
        self.assertEqual(self.q.tasks[0].status,'failed');self.assertIn('deadline',self.q.tasks[0].reason)
    def test_spell_is_published_only_for_zerg_and_observation_contains_real_energy(self):
        for race in ('zerg','terran','protoss'):
            payload=dict(tech_tree=self.f.tree.public(race),world={'race':race})
            schema=battle_output_schema(payload);names={n for branch in schema['properties']['tasks']['items']['anyOf'] for n in branch['properties']['skill']['enum']}
            self.assertEqual('inject_larva' in names,race=='zerg')
        data=report(self.world(),self.q);self.assertEqual(data['queens'][0]['energy'],50)
        self.assertFalse(data['bases'][0]['inject_active'])
        other=TaskQueue(self.f.backend('terran',[251]),0,lambda _:None)
        with self.assertRaises(PlanValidationError):other.enqueue([dict(skill='inject_larva',args=dict(base='home'))],0)
        self.assertEqual(other.tasks,[])

    def test_explicit_travel_deadline_keeps_one_cast_reserved_after_ninety_seconds(self):
        self.q.enqueue([dict(skill='inject_larva',args=dict(base='home',timeout_s=120))],0)
        queen=replace(self.queen,x=180)
        self.assertEqual(len(self.q.tick(self.world([self.base,queen]),set())),1)
        queen=replace(queen,orders=(251,),order_targets=(self.base.tag,))
        for seconds in range(10,101,10):
            queen=replace(queen,x=180-seconds*1.4,energy=50+seconds*.7)
            self.assertEqual(self.q.tick(self.world([self.base,queen],seconds),set()),[])
            self.assertEqual(self.q.tasks[0].status,'running')
            self.assertIn(2,self.q.protected())
        self.q.tick(self.world([replace(self.base,buffs=(11,)),replace(queen,energy=queen.energy-25)],101),set())
        self.assertEqual(self.q.tasks[0].status,'completed')
        self.assertEqual(sum(e['kind']=='inject_larva_order_issued' for e in self.events),1)
        self.assertNotIn(2,self.q.protected())

    def test_explicit_deadline_is_bounded_and_stalled_approach_still_fails_early(self):
        for timeout in (0,301,True,90.5,'120',None):
            with self.subTest(timeout=timeout):
                with self.assertRaises(PlanValidationError):
                    self.q.enqueue([dict(skill='inject_larva',args=dict(base='home',timeout_s=timeout))],0)
                self.assertEqual(self.q.tasks,[])
        self.q.enqueue([dict(skill='inject_larva',args=dict(base='home',timeout_s=300))],0)
        queen=replace(self.queen,x=180)
        self.assertEqual(len(self.q.tick(self.world([self.base,queen]),set())),1)
        queen=replace(queen,orders=(251,),order_targets=(self.base.tag,))
        self.assertEqual(self.q.tick(self.world([self.base,queen],22),set()),[])
        self.assertEqual(self.q.tasks[0].status,'failed')
        self.assertIn('stalled',self.q.tasks[0].reason)

if __name__=='__main__':unittest.main()
