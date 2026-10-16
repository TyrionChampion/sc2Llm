"""A native gas building is never one of its allocated workers."""
from dataclasses import replace
import unittest

from arena.tasks import TaskQueue
import test_multirace as fixtures


class GasAllocation(unittest.TestCase):
    def setUp(self):self.f=fixtures.MultiRace();self.f.setUp()

    def approached_gas(self):
        q=TaskQueue(self.f.backend('zerg',[1154]),0,lambda _:None)
        geysers=[replace(self.f.unit(tag,342,geyser=True,vespene_contents=2250),x=x,alliance=3)
            for tag,x in ((3,35),(4,25))]
        w=self.f.world([self.f.unit(1,86,is_building=True),self.f.unit(2,104),*geysers],race='zerg')
        q.enqueue([dict(skill='build_structure',args=dict(structure='extractor'))],w.seconds)
        self.assertEqual(len(q.tick(w,set())),1)
        w=replace(w,game_loop=250,units=tuple(replace(u,orders=(1154,)) if u.tag==2 else u for u in w.units))
        self.assertEqual(q.tick(w,set()),[]);self.assertTrue(q.tasks[0].program.pending[0]['seen'])
        return q,w

    def test_rejected_unstarted_drone_gas_approach_reselects_a_different_geyser(self):
        for code in (42,44):
            with self.subTest(code=code):
                q,w=self.approached_gas();task=q.tasks[0];old=task.spot
                rejected=replace(w,game_loop=280,errors=((2,1154,code),),
                    units=tuple(replace(u,orders=()) if u.tag==2 else u for u in w.units))
                self.assertEqual(q.tick(rejected,set()),[])
                self.assertTrue(task.active);self.assertEqual(task.program.issued,0)
                self.assertEqual(task.program.pending,[]);self.assertIn(old,task.excluded)
                out=q.tick(replace(rejected,game_loop=320,errors=()),set())
                self.assertEqual(len(out),1);self.assertEqual(out[0].action_raw.unit_command.target_unit_tag,4)
                self.assertEqual(task.count,1);self.assertEqual(task.program.issued,1)

    def test_gas_rejection_never_rebuys_when_the_drone_has_become_a_building(self):
        q,w=self.approached_gas();task=q.tasks[0]
        building=replace(self.f.unit(2,88,is_building=True,gas_building=True,progress=.3),x=task.spot[0],y=task.spot[1])
        w=replace(w,game_loop=280,units=tuple(building if u.tag==2 else u for u in w.units),errors=((2,1154,44),))
        self.assertEqual(q.tick(w,set()),[]);self.assertEqual(task.program.issued,1)
        self.assertEqual(task.program.retries,0)

    def test_gas_rejection_never_replaces_a_missing_drone(self):
        q,w=self.approached_gas();task=q.tasks[0]
        w=replace(w,game_loop=280,units=tuple(u for u in w.units if u.tag!=2),errors=((2,1154,42),),dead_units=(2,))
        self.assertEqual(q.tick(w,set()),[]);self.assertEqual(task.status,'failed')
        self.assertEqual(task.program.retries,0)

    def test_unstarted_gas_approach_keeps_the_existing_five_retry_limit(self):
        q,w=self.approached_gas();task=q.tasks[0];task.program.retries=5
        self.assertEqual(q.tick(replace(w,game_loop=280,errors=((2,1154,44),)),set()),[])
        self.assertEqual(task.status,'failed');self.assertEqual(task.program.issued,1)

    def test_completed_native_construction_does_not_register_its_building_as_a_worker(self):
        for race,name,base,worker in (('zerg','extractor',86,104),('protoss','assimilator',59,84),('terran','refinery',18,45)):
            with self.subTest(race=race):
                entry=self.f.tree.units[name];ability=entry['recipes'][0]['ability']
                q=TaskQueue(self.f.backend(race,[ability]),0,lambda _:None)
                home=self.f.unit(1,base,is_building=True);builder=self.f.unit(2,worker)
                geyser=replace(self.f.unit(4,342,geyser=True,vespene_contents=2250),x=35,alliance=3)
                w=self.f.world([home,builder,geyser],race=race)
                q.enqueue([dict(skill='build_structure',args=dict(structure=name))],w.seconds)
                self.assertEqual(len(q.tick(w,set())),1)
                building=replace(self.f.unit(5,entry['id'],is_building=True,gas_building=True,progress=.3,vespene_contents=2250),x=35)
                units=[home,geyser,building]
                if race!='zerg':units.append(replace(builder,orders=(ability,) if race=='terran' else ()))
                w=replace(w,game_loop=250,units=tuple(units));q.tick(w,set())
                w=replace(w,game_loop=400,units=tuple(replace(u,progress=1,orders=()) if u.tag==5 else u for u in w.units))
                q.tick(w,set())
                self.assertEqual(q.tasks[0].status,'completed')
                self.assertNotIn(building.tag,q.gas_assignments)
                if race=='terran':self.assertEqual(q.gas_assignments,{builder.tag:building.tag})

    def test_observed_nonworker_assignment_is_removed_and_replaced_without_ordering_building(self):
        q=TaskQueue(self.f.backend('zerg'),0,lambda _:None);q.gas_workers_per_refinery=3
        q.gas_assignments={10:10,1:10,2:10}
        ref=self.f.unit(10,88,is_building=True,gas_building=True,vespene_contents=1000)
        w=self.f.world([ref,self.f.unit(1,104,orders=(1183,),order_targets=(10,)),
            self.f.unit(2,104,orders=(1183,),order_targets=(10,)),self.f.unit(3,104)],race='zerg')
        out=[];q.balance_gas(w,set(),out)
        self.assertEqual(q.gas_assignments,{1:10,2:10,3:10})
        self.assertEqual([list(a.action_raw.unit_command.unit_tags) for a in out],[[3]])
        self.assertNotIn(10,q.gas_release_pending)
        self.assertNotIn(10,q.gas_worker_tags)

    def test_hidden_actual_worker_keeps_its_slot_without_an_extra_purchase_or_assignment(self):
        q=TaskQueue(self.f.backend('zerg'),0,lambda _:None);q.gas_workers_per_refinery=3
        q.gas_assignments={1:10,2:10,3:10}
        ref=self.f.unit(10,88,is_building=True,gas_building=True,vespene_contents=1000)
        w=self.f.world([ref,self.f.unit(2,104,orders=(1183,),order_targets=(10,)),
            self.f.unit(3,104,orders=(1184,),order_targets=(20,)),self.f.unit(4,104)],race='zerg')
        out=[];q.balance_gas(w,set(),out)
        self.assertEqual(q.gas_assignments,{1:10,2:10,3:10});self.assertEqual(out,[])

    def test_disabling_gas_discards_an_invalid_building_release_even_during_cooldown(self):
        q=TaskQueue(self.f.backend('zerg'),0,lambda _:None);q.gas_workers_per_refinery=0
        q.gas_assignments={10:10};q.gas_release_pending={10};q.gas_next_at=100
        w=self.f.world([self.f.unit(10,88,is_building=True,gas_building=True,vespene_contents=1000)],race='zerg')
        q.balance_gas(w,set(),[])
        self.assertEqual(q.gas_assignments,{})
        self.assertEqual(q.gas_release_pending,set());self.assertEqual(q.gas_worker_tags,set())


if __name__=='__main__':unittest.main()
