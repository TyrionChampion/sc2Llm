"""An explicit rally owns the rally target, not the building's production queue."""
from dataclasses import replace
import unittest
from arena.tasks import TaskQueue
from test_rally_orders import engine_backend,RALLY_ABILITY


class RallyProduction(unittest.TestCase):
    def test_new_marine_batch_runs_while_explicit_rally_remains_owned(self):
        fixture,backend=engine_backend('terran',available=(RALLY_ABILITY,560))
        q=TaskQueue(backend,0,lambda s:None)
        producer=fixture.unit(2,21,is_building=True)
        world=fixture.world([fixture.unit(1,18,is_building=True),producer],race='terran')
        q.enqueue([dict(skill='rally_units',args=dict(producers=['barracks#1'],
                   target='home',duration_s=300,reason='持续集结新兵'))],10)
        q.tick(world,set())
        self.assertEqual(q.rally_owned_tags(),{2})
        q.enqueue([dict(skill='train_unit',count=4,args=dict(unit='marine'))],11)
        actions=q.tick(replace(world,game_loop=250),set())
        self.assertIn(560,[a.action_raw.unit_command.ability_id for a in actions],q.summary())
        self.assertEqual(q.rally_owned_tags(),{2})
        self.assertTrue(q.tasks[0].active)

    def test_rally_does_not_release_other_macros_producer_reservation(self):
        fixture,backend=engine_backend('terran',available=(RALLY_ABILITY,560,563))
        q=TaskQueue(backend,0,lambda s:None)
        world=fixture.world([fixture.unit(1,18,is_building=True),
                             fixture.unit(2,21,is_building=True)],race='terran')
        q.enqueue([dict(skill='train_unit',count=4,args=dict(unit='marine'))],10)
        q.tick(world,set())
        q.enqueue([dict(skill='rally_units',args=dict(producers=['barracks#1'],
            target='home',duration_s=300,reason='集结')),dict(skill='train_unit',
            args=dict(unit='marauder'))],11)
        q.tick(replace(world,game_loop=250),set())
        self.assertIn(2,q.protected())
        self.assertEqual(q.tasks[-1].program.issued,0)


if __name__=='__main__':unittest.main()
