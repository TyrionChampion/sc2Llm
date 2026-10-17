"""Surplus budgets and in-flight worker transfers; no game or model requests."""
import unittest
from dataclasses import replace
from types import SimpleNamespace

from arena.raw_engine import Entity
from arena.tasks import TaskQueue,Task
from test_realtime_runtime import unit,world


class MineralRebalanceTests(unittest.TestCase):
    def setUp(self):
        self.events=[]
        self.q=TaskQueue(SimpleNamespace(homes=[(30,30)]),0,lambda _:None,
                         lambda kind,**kw:self.events.append(dict(kind=kind,**kw)))
        self.task=Task(1,'gather')
        self.home=unit(1,18,30,30,is_building=True,assigned_harvesters=17,ideal_harvesters=16)
        self.new=unit(2,18,70,30,is_building=True,assigned_harvesters=0,ideal_harvesters=16)
        self.workers=[unit(i,45,31+(i-10)/10,30,orders=(295,),order_targets=(100,)) for i in range(10,27)]
        self.patches=[Entity(100,341,3,35,30,100,100,mineral=True),Entity(101,341,3,75,30,100,100,mineral=True)]

    def w(self,bases=None,workers=None,sec=10):
        return world((bases or [self.home,self.new])+(self.workers if workers is None else workers)+self.patches,sec)

    def rebalance(self,w):
        actions=[];self.q.rebalance_minerals(w,set(),actions,self.task)
        return [tag for a in actions for tag in a.action_raw.unit_command.unit_tags]

    def test_one_surplus_does_not_fill_empty_expansion_from_saturated_home(self):
        self.assertEqual(len(self.rebalance(self.w())),1)
        self.assertEqual(len(self.q.mineral_transfers),1)
        self.assertEqual(self.events[0]['kind'],'mineral_transfer_dispatched')
        self.assertFalse(self.events[0]['confirmed'])

    def test_multiple_destinations_share_one_source_budget(self):
        home=replace(self.home,assigned_harvesters=20)
        third=replace(self.new,tag=3,x=110)
        self.patches.append(replace(self.patches[-1],tag=102,x=115))
        self.assertEqual(len(self.rebalance(self.w(bases=[home,self.new,third]))),4)

    def test_repeated_stale_observations_do_not_donate_again(self):
        self.assertEqual(len(self.rebalance(self.w())),1)
        for sec in (11,12,13):self.assertEqual(self.rebalance(self.w(sec=sec)),[])

    def test_pending_arrivals_reserve_destination_capacity_across_sources(self):
        home=replace(self.home,assigned_harvesters=30)
        new=replace(self.new,ideal_harvesters=2)
        self.assertEqual(len(self.rebalance(self.w(bases=[home,new]))),2)
        self.assertEqual(self.rebalance(self.w(bases=[home,new],sec=11)),[])

    def test_actual_mineral_target_overrides_nearby_other_base(self):
        # A worker returning toward home still belongs to its target mineral line.
        workers=[replace(u,order_targets=(101,)) for u in self.workers]
        self.assertEqual(self.rebalance(self.w(workers=workers)),[])

    def test_gas_and_owned_construction_workers_are_not_reassigned(self):
        self.q.gas_worker_tags={u.tag for u in self.workers[:-1]}
        self.q.tasks=[Task(9,'build_structure',worker=self.workers[-1].tag)]
        self.assertEqual(self.rebalance(self.w()),[])

    def test_arrival_requires_position_and_matching_target(self):
        tag=self.rebalance(self.w())[0]
        workers=[replace(u,x=75,order_targets=(101,)) if u.tag==tag else u for u in self.workers]
        self.q.refresh_mineral_transfers(self.w(workers=workers,sec=20))
        self.assertNotIn(tag,self.q.mineral_transfers)
        self.assertEqual(self.events[-1]['kind'],'mineral_transfer_arrived')

    def test_failed_or_lost_transfer_is_not_reported_as_mining_success(self):
        self.rebalance(self.w())
        self.q.refresh_mineral_transfers(self.w(bases=[self.home],sec=11))
        self.assertFalse(self.q.mineral_transfers)
        self.assertEqual(self.events[-1]['kind'],'mineral_transfer_unconfirmed')

    def test_timeout_releases_reservation_without_claiming_arrival(self):
        self.rebalance(self.w());self.q.refresh_mineral_transfers(self.w(sec=41))
        self.assertFalse(self.q.mineral_transfers)
        self.assertEqual(self.events[-1]['kind'],'mineral_transfer_unconfirmed')

    def test_depleted_home_can_donate_its_actual_surplus(self):
        home=replace(self.home,ideal_harvesters=0)
        self.assertEqual(len(self.rebalance(self.w(bases=[home,self.new]))),16)


if __name__=='__main__':unittest.main()
