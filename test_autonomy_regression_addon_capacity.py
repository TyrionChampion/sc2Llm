"""Existing addon construction must not be starved by a long training batch."""
from dataclasses import replace
import unittest
from arena.tasks import TaskQueue
from test_rally_orders import engine_backend


class AddonCapacity(unittest.TestCase):
    def test_explicit_techlab_gets_the_free_unattached_barracks(self):
        fixture,backend=engine_backend('terran')
        addon=fixture.tree.units['barracks_tech_lab']['recipes'][0]['ability']
        backend.available_abilities=lambda i,tags:{tag:{560,addon} for tag in tags}
        w=fixture.world([fixture.unit(1,18,is_building=True),
            fixture.unit(2,21,is_building=True,orders=(563,),addon_tag=100),
            fixture.unit(3,21,is_building=True),fixture.unit(100,37,is_building=True)],race='terran')
        q=TaskQueue(backend,0,lambda s:None)
        q.enqueue([dict(skill='train_unit',count=12,args=dict(unit='marine')),
                   dict(skill='build_structure',args=dict(structure='barracks_tech_lab'))],10)
        actions=q.tick(w,set())
        self.assertEqual([a.action_raw.unit_command.ability_id for a in actions],[addon])
        self.assertEqual(set(actions[0].action_raw.unit_command.unit_tags),{3})
        self.assertEqual(q.tasks[0].program.issued,0)
        self.assertEqual(q.tasks[1].program.issued,1)


if __name__=='__main__':unittest.main()
