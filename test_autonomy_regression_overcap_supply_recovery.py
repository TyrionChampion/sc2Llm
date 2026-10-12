"""Losing supply capacity must not prevent model-ordered supply recovery."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import unittest
from arena.knowledge import cached_tree
from arena.raw_engine import Entity,Snapshot
from arena.tasks import TaskQueue


class OvercapSupplyRecovery(unittest.TestCase):
    def setUp(self):
        self.tree=deepcopy(cached_tree())
        self.backend=SimpleNamespace(tech_tree=self.tree,races=['terran'],homes=[(30,30)],
            size=(200,200),enemy_start=lambda i:(170,170),
            available_abilities=lambda i,tags:{tag:{319,318,560} for tag in tags},
            placement=lambda i,a,t,ps:ps[0] if ps else None,
            placement_with_addon=lambda i,a,t,ps:ps[0] if ps else None)
        units=(Entity(1,18,1,30,30,1500,1500,is_building=True),
               Entity(2,21,1,40,30,1000,1000,is_building=True),
               Entity(3,45,1,29,30,45,45))
        self.world=Snapshot(224,1000,40,39,units,(30,30))

    def run_tasks(self,tasks,world=None):
        q=TaskQueue(self.backend,0,lambda s:None)
        q.enqueue(tasks,10)
        return q,q.tick(world or self.world,set())

    def test_supply_depot_can_be_ordered_while_over_capacity(self):
        q,actions=self.run_tasks([dict(skill='build_supply')])
        self.assertEqual([a.action_raw.unit_command.ability_id for a in actions],[319],q.tasks[0].reason)
        self.assertEqual(q.tasks[0].program.issued,1)

    def test_model_ordered_rebuilding_does_not_require_free_population(self):
        q,actions=self.run_tasks([dict(skill='build_structure',args=dict(structure='command_center'))])
        self.assertEqual([a.action_raw.unit_command.ability_id for a in actions],[318],q.tasks[0].reason)

    def test_combat_training_stays_blocked_and_no_automatic_supply_is_added(self):
        q,actions=self.run_tasks([dict(skill='train_unit',args=dict(unit='marine'))])
        self.assertEqual(actions,[])
        self.assertEqual([t.skill for t in q.tasks],['train_unit'])
        q,actions=self.run_tasks([dict(skill='train_unit',args=dict(unit='marine'))],
                                replace(self.world,supply_cap=41))
        self.assertEqual([a.action_raw.unit_command.ability_id for a in actions],[560])


if __name__=='__main__':unittest.main()
