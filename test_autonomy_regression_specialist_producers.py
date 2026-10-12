"""Explicit mixed production should use plain barracks and tech labs in parallel."""
from dataclasses import replace
import unittest
from arena.tasks import TaskQueue
from test_rally_orders import engine_backend


class SpecialistProducers(unittest.TestCase):
    def setup_queue(self,plain_busy=False,gas=1000,minerals=1000):
        fixture,backend=engine_backend('terran',available=(560,563))
        backend.available_abilities=lambda i,tags:{tag:({560,563} if tag==2 else {560}) for tag in tags}
        units=[fixture.unit(1,18,is_building=True),fixture.unit(2,21,is_building=True,addon_tag=100),
               fixture.unit(3,21,is_building=True,orders=(560,) if plain_busy else ()),
               fixture.unit(100,37,is_building=True)]
        world=replace(fixture.world(units,race='terran'),vespene=gas,minerals=minerals)
        q=TaskQueue(backend,0,lambda s:None)
        q.enqueue([dict(skill='train_unit',count=4,args=dict(unit='marine')),
                   dict(skill='train_unit',count=4,args=dict(unit='marauder'))],10)
        return q,world

    def test_two_explicit_products_use_both_ready_facilities(self):
        q,w=self.setup_queue();actions=q.tick(w,set())
        orders={a.action_raw.unit_command.ability_id:set(a.action_raw.unit_command.unit_tags) for a in actions}
        self.assertEqual(orders,{560:{3},563:{2}})
        self.assertEqual([t.count for t in q.tasks],[4,4])

    def test_busy_plain_producer_does_not_give_specialist_slot_to_marines(self):
        q,w=self.setup_queue(plain_busy=True);actions=q.tick(w,set())
        self.assertEqual([a.action_raw.unit_command.ability_id for a in actions],[563])
        self.assertEqual(q.tasks[0].program.issued,0)
        self.assertEqual(q.tasks[1].program.issued,1)

    def test_unaffordable_specialist_does_not_block_requested_marines(self):
        q,w=self.setup_queue(plain_busy=True,gas=0,minerals=50);actions=q.tick(w,set())
        self.assertEqual([a.action_raw.unit_command.ability_id for a in actions],[560])
        self.assertEqual(q.tasks[1].program.issued,0)


if __name__=='__main__':unittest.main()
