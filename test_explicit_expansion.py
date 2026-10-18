"""Explicit expansion orders must reach native placement; errors identify the actual filter."""
from dataclasses import replace
import unittest
from arena.tasks import TaskQueue
import test_multirace as fixtures


class ExplicitExpansionTests(unittest.TestCase):
    def setup_queue(self,base,occupied=False,armed=False):
        f=fixtures.MultiRace();f.setUp();b=f.backend('protoss',[880]);calls=[]
        b.placement=lambda i,a,t,ps:(calls.append(ps) or ps[0] if ps else None)
        minerals=[replace(f.unit(100+i,341,mineral=True),alliance=3,x=70+i%4*2,y=70+i//4*2) for i in range(8)]
        enemy=replace(f.unit(90,48 if armed else 106,ground_dps=10 if armed else 0,flying=not armed),alliance=4,x=73,y=73)
        units=[f.unit(1,59,is_building=True),f.unit(2,84),enemy,*minerals]
        if occupied:units.append(replace(f.unit(3,59,is_building=True),x=70,y=65))
        w=f.world(units,race='protoss');q=TaskQueue(b,0,lambda _:None)
        q.enqueue([dict(skill='expand',args={'base':base})],w.seconds)
        return q,w,calls

    def test_explicit_site_under_scout_reaches_native_placement(self):
        q,w,calls=self.setup_queue('site_100')
        self.assertTrue(q.tick(w,set()));self.assertTrue(calls)
        self.assertEqual(q.tasks[0].program.issued,1)
        self.assertNotEqual(q.tasks[0].status,'completed')

    def test_model_may_explicitly_accept_visible_combat_risk(self):
        q,w,calls=self.setup_queue('site_100',armed=True)
        self.assertTrue(q.tick(w,set()));self.assertTrue(calls)

    def test_unspecified_expansion_retains_threat_filter(self):
        q,w,calls=self.setup_queue('expansion')
        self.assertEqual(q.tick(w,set()),[]);self.assertEqual(calls,[])

    def test_own_occupied_site_reports_occupancy_not_creep(self):
        q,w,calls=self.setup_queue('site_100',occupied=True)
        self.assertEqual(q.tick(w,set()),[]);self.assertEqual(calls,[])
        self.assertIn('already has an own town hall',q.tasks[0].reason)
        self.assertNotIn('creep',q.tasks[0].reason)

    def test_missing_site_reports_identity_not_creep(self):
        q,w,calls=self.setup_queue('site_999')
        self.assertEqual(q.tick(w,set()),[])
        self.assertIn('not in current resource sites',q.tasks[0].reason)

    def test_native_rejection_remains_waiting_without_a_purchase(self):
        q,w,calls=self.setup_queue('site_100')
        q.backend.placement=lambda *args:None
        self.assertEqual(q.tick(w,set()),[])
        self.assertEqual(q.tasks[0].program.issued,0)
        self.assertIn('engine rejected',q.tasks[0].reason)


if __name__=='__main__':unittest.main()
