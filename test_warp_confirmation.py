"""Warp orders cannot use normal production's short acknowledgement timeout."""
import unittest
from dataclasses import replace
from arena.tasks import TaskQueue
from arena.macro import MacroRun,specification
import test_multirace as fixtures

class WarpConfirmation(unittest.TestCase):
 def setUp(self):
  self.f=fixtures.MultiRace();self.f.setUp();self.b=self.f.backend('protoss');self.q=TaskQueue(self.b,0,lambda kind,**kw:None)
  self.q.enqueue([dict(skill='train_unit',args=dict(unit='zealot'),count=2)],0);self.t=self.q.tasks[0]
  self.run=MacroRun(specification(self.f.tree,'protoss','train_unit',{'unit':'zealot'}),{'unit':'zealot'})
  self.run.baseline=set();self.run.issued=2;self.run.next_at=100
  self.run.pending=[dict(tags=[1],ability=1413,sent=0,seen=False,pos=(35,35),ack_timeout_s=30,consumes_actor=False)]
 def test_slow_warp_remains_pending_until_a_real_unit_is_observed(self):
  w=replace(self.f.world([self.f.unit(1,133)],race='protoss'),game_loop=224)
  self.run.tick(w,self.q,self.t,set(),500,[])
  self.assertTrue(self.t.active);self.assertFalse(self.run.pending[0]['seen'])
  w=replace(w,game_loop=280,units=w.units+(replace(self.f.unit(2,73),x=35,y=35,progress=.5),))
  self.run.tick(w,self.q,self.t,set(),500,[])
  self.assertEqual(len(self.run.pending),0);self.assertEqual(self.run.born,set())
 def test_no_observed_warp_still_fails_after_bounded_grace(self):
  w=replace(self.f.world([self.f.unit(1,133)],race='protoss'),game_loop=700)
  self.run.tick(w,self.q,self.t,set(),500,[])
  self.assertEqual(self.t.status,'failed');self.assertIn('not observed',self.t.reason)
 def test_normal_production_keeps_eight_second_guard(self):
  self.run.pending[0].pop('ack_timeout_s');self.run.pending[0]['ability']=916
  w=replace(self.f.world([self.f.unit(1,62)],race='protoss'),game_loop=224)
  self.run.tick(w,self.q,self.t,set(),500,[])
  self.assertEqual(self.t.status,'failed');self.assertIn('not observed',self.t.reason)
 def test_failed_confirmation_retains_expected_and_observed_positions(self):
  events=[];self.q.feedback=lambda kind,**kw:events.append(dict(kind=kind,**kw))
  w=replace(self.f.world([self.f.unit(1,133),replace(self.f.unit(2,73),x=42,y=35)],race='protoss'),game_loop=700)
  self.run.tick(w,self.q,self.t,set(),500,[])
  e=next(e for e in events if e['kind']=='macro_failed')['observation_evidence']
  self.assertEqual(e['pending_orders'][0]['pos'],(35,35))
  self.assertEqual(e['observed_new_products'],[dict(tag=2,position=[42,35],progress=1.0)])
if __name__=='__main__':unittest.main()
