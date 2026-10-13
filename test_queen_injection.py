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
    def test_existing_buff_is_observable_noop_and_pending_intent_coalesces(self):
        self.enqueue();self.enqueue();self.assertEqual(len(self.q.tasks),1)
        self.assertEqual(self.q.tick(self.world([replace(self.base,buffs=(11,)),self.queen]),set()),[])
        self.assertEqual(self.q.tasks[0].status,'completed')
        self.assertTrue(any(e['kind']=='inject_larva_already_active' for e in self.events))
    def test_unconfirmed_cast_expires_without_retry(self):
        self.enqueue();self.q.tick(self.world(),set())
        self.assertEqual(self.q.tick(self.world(seconds=32),set()),[])
        self.assertEqual(self.q.tasks[0].status,'failed')
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
        self.enqueue();queen=replace(self.queen,x=90,orders=(251,));self.q.tick(self.world([self.base,queen]),set())
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

if __name__=='__main__':unittest.main()
