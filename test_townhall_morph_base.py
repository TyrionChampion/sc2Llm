"""Townhall upgrades honor the explicitly requested base instead of raw unit order."""
import unittest
from dataclasses import replace
from arena.tasks import TaskQueue
import test_multirace as fixtures

class TownhallMorphBase(unittest.TestCase):
    def setUp(self):self.f=fixtures.MultiRace();self.f.setUp()
    def run_order(self,race,product,base,units,**kw):
        recipe=self.f.tree.units[product]['recipes'][0]
        backend=self.f.backend(race,[recipe['ability']]);q=TaskQueue(backend,0,lambda _:None)
        q.enqueue([dict(skill='build_structure',args=dict(structure=product,base=base))],0)
        w=self.f.world(units,race=race,**kw)
        return q,q.tick(w,set())
    def test_zerg_and_terran_upgrade_the_requested_base_in_either_raw_order(self):
        for race,product,producer in (('zerg','lair',86),('zerg','hive',100),('terran','orbital_command',18)):
            home=self.f.unit(1,producer,is_building=True)
            expansion=replace(self.f.unit(2,producer,is_building=True),x=100,y=100)
            for units in ((home,expansion),(expansion,home)):
                for base,tag in (('home',1),('expansion',2)):
                    with self.subTest(race=race,product=product,base=base):
                        q,actions=self.run_order(race,product,base,units)
                        self.assertEqual(len(actions),1,q.tasks[0].reason)
                        self.assertEqual(list(actions[0].action_raw.unit_command.unit_tags),[tag])
    def test_busy_or_absent_home_never_falls_back_to_an_expansion(self):
        expansion=replace(self.f.unit(2,86,is_building=True),x=100,y=100)
        for home in (None,self.f.unit(1,86,is_building=True,orders=(1632,)),
                     self.f.unit(1,86,is_building=True,progress=.5)):
            units=[expansion]+([home] if home else [])
            q,actions=self.run_order('zerg','lair','home',units)
            self.assertEqual(actions,[]);self.assertEqual(q.tasks[0].status,'waiting')
    def test_remembered_named_site_selects_only_its_owned_base(self):
        units=[self.f.unit(1,86,is_building=True),replace(self.f.unit(2,86,is_building=True),x=100,y=100),
               replace(self.f.unit(3,86,is_building=True),x=160,y=160)]
        q,actions=self.run_order('zerg','lair','site_known',units,resource_site_memory=(('site_known',((103,103),)),))
        self.assertEqual(list(actions[0].action_raw.unit_command.unit_tags),[2])
        q,actions=self.run_order('zerg','lair','site_unknown',units)
        self.assertEqual(actions,[]);self.assertIn('site_unknown',q.tasks[0].reason)

if __name__=='__main__':unittest.main()
