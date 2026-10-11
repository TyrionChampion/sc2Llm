"""Regression discriminators: fixture results are not natural-game evidence."""
import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import test_multirace
from arena.tasks import TaskQueue
from arena.raw_engine import Entity,Snapshot
from arena.intelligence import Intelligence
from arena.external_planner import ExternalPlanner,atomic_json,submit
from arena.commander import Commander
from arena.rally import GroundMap,DefensiveRally

class Construction(unittest.TestCase):
 def setUp(self):
  f=test_multirace.MultiRace();f.setUp();self.f=f;self.events=[]
  self.q=TaskQueue(f.backend(available=[319]),0,lambda s:None,lambda kind,**kw:self.events.append(dict(kind=kind,**kw)))
  self.q.enqueue([{'skill':'supply_depot'}],10)
  self.w=f.world([f.unit(1,45),f.unit(2,45),f.unit(4,45)])
  self.q.tick(self.w,set());self.t=self.q.tasks[0];p=self.t.spot
  self.w=replace(self.w,game_loop=269,units=(replace(self.w.units[0],orders=(319,),order_targets=(0,),order_positions=(p,)),*self.w.units[1:],replace(f.unit(3,19,is_building=True,progress=.2),x=p[0],y=p[1])))
 def test_position_build_order_does_not_recruit_second_scv(self):
  for n in range(30):
   w=replace(self.w,game_loop=269+n*3,units=(*self.w.units[:-1],replace(self.w.units[-1],progress=.2+n*.01)))
   self.assertEqual(self.q.tick(w,set()),[]);self.assertEqual(self.t.program.tags,{1})
 def test_dead_owner_replaced_exactly_once(self):
  self.q.tick(self.w,set());w=replace(self.w,game_loop=300,units=self.w.units[1:],dead_units=(1,))
  out=self.q.tick(w,set());self.assertEqual(len(out),1)
  owner=out[0].action_raw.unit_command.unit_tags[0];self.assertIn(owner,(2,4));self.assertEqual(self.t.program.tags,{owner})
  w=replace(w,game_loop=330,dead_units=(),units=tuple(replace(u,progress=.3) if u.tag==3 else u for u in w.units))
  self.assertEqual(self.q.tick(w,set()),[])
 def test_interrupted_living_owner_resumes_itself(self):
  self.q.tick(self.w,set());w=replace(self.w,game_loop=350,units=(replace(self.w.units[0],orders=(3794,),order_targets=(0,),order_positions=((60,60),)),*self.w.units[1:]))
  out=self.q.tick(w,set());self.assertEqual(list(out[0].action_raw.unit_command.unit_tags),[1])
 def test_missing_is_not_death(self):
  self.q.tick(self.w,set());w=replace(self.w,game_loop=400,units=self.w.units[1:])
  self.assertEqual(self.q.tick(w,set()),[]);self.assertEqual(self.t.program.tags,{1})

class Sightings(unittest.TestCase):
 def setUp(self):
  self.events=[];self.intel=Intelligence(lambda k,**v:self.events.append((k,v)))
  self.scout=Entity(1,45,1,30,30,45,45)
  self.enemy=Entity(2,33,4,35,30,100,100)
 def w(self,loop,units,dead=()):return Snapshot(loop,0,1,15,tuple(units),(30,30),dead_units=dead)
 def test_scout_dies_between_decisions_sightings_survive(self):
  self.intel.update(self.w(10,[self.scout,self.enemy]));r=self.intel.revision
  self.intel.update(self.w(12,[],(1,)));p=self.intel.public(self.w(100,[]))
  self.assertEqual(p['contacts'][0]['state'],'last_seen');self.assertEqual(p['contacts'][0]['count'],1)
  self.assertTrue(any(a['kind']=='own_unit_death_observed' and a['nearby_recent_contacts'] for a in p['pending_alerts']))
  self.intel.acknowledge(r);self.assertEqual(len(self.intel.public(self.w(100,[]))['pending_alerts']),1)
 def test_repeat_sightings_not_double_counted_absence_not_death(self):
  for t in range(10,30):self.intel.update(self.w(t,[self.scout,self.enemy]))
  self.intel.update(self.w(31,[]));p=self.intel.public(self.w(31,[]))
  self.assertEqual(p['contacts'][0]['count'],1);self.assertEqual(len(p['pending_alerts']),1)
 def test_old_memory_and_confirmed_enemy_death(self):
  self.intel.update(self.w(10,[self.enemy]));self.intel.update(self.w(5000,[]))
  self.assertEqual(self.intel.public(self.w(5000,[]))['contacts'][0]['state'],'old_unconfirmed')
  self.intel.update(self.w(5002,[],(2,)));self.assertEqual(self.intel.public(self.w(5002,[]))['contacts'],[])
 def test_players_do_not_share_memory(self):
  self.intel.update(self.w(10,[self.enemy]));other=Intelligence(lambda *a,**kw:None)
  self.assertEqual(other.public(self.w(20,[]))['contacts'],[])

class Demand(unittest.TestCase):
 def test_no_timer_before_observation_request_and_live_freshness(self):
  f=test_multirace.MultiRace();f.setUp();b=f.backend();b.initial=[f.world([f.unit(9,18,is_building=True)])];b.register_action_context=lambda *a:None
  with tempfile.TemporaryDirectory() as td:
   client=ExternalPlanner(Path(td)/'mailbox',timeout=.5,on_demand=True)
   c=Commander(b,0,client,opening=False,interval=0,feedback_dir=Path(td)/'feedback')
   try:
    c.tick(b.initial[0]);time.sleep(.6);c.tick(replace(b.initial[0],game_loop=448))
    self.assertEqual(c.errors,0);self.assertEqual(client.sequence,0)
    atomic_json(client.directory/'observe.request.json',{'requested_at':time.time()})
    c.tick(replace(b.initial[0],game_loop=672));end=time.monotonic()+1
    while not (client.directory/'status.json').exists() or client.state!='waiting':
     if time.monotonic()>end:self.fail('no fresh observation')
     time.sleep(.005)
    # current.json is written before status waiting is published to disk.
    while not (client.directory/'current.json').exists():time.sleep(.005)
    r=json.loads((client.directory/'current.json').read_text());self.assertEqual(r['payload']['world']['game_s'],30)
    while json.loads((client.directory/'status.json').read_text())['state']=='idle':time.sleep(.005)
    submit(client.directory,r['request_id'],{'tasks':[],'reason':'等待'})
    while c.outbox.empty() and time.monotonic()<end:time.sleep(.005)
    c.tick(replace(b.initial[0],game_loop=674));self.assertEqual(c.decisions,1)
    self.assertFalse(client.ready_for_request())
   finally:client.close();c.stop()

class Rallies(unittest.TestCase):
 def test_no_worker_producers_and_no_mobile_units(self):
  from arena.rally import PRODUCERS
  self.assertFalse(PRODUCERS & {18,132,130,59,45,84,104,48,133})
  self.assertTrue({21,27,28,62,71,67,86,100,101} <= PRODUCERS)
 def test_ground_route_cannot_cut_diagonal_blockers(self):
  import numpy as np
  g=GroundMap.__new__(GroundMap);g.pathing=np.ones((24,24),dtype='uint8');g.height=np.ones((24,24),dtype='uint8')*200;g.h=g.w=24;g.cache={}
  g.pathing[:,12]=0
  with self.assertRaisesRegex(ValueError,'no static ground route'):g.route((2,2),(20,20))
  g.pathing[10,12]=1;path=g.route((2,2),(20,20));self.assertIn((12,10),path)
 def test_ramp_anchor_and_expansion_are_pathable(self):
  import numpy as np
  g=GroundMap.__new__(GroundMap);g.pathing=np.ones((60,60),dtype='uint8');g.height=np.ones((60,60),dtype='uint8')*190;g.height[:,:20]=220;g.h=g.w=60;g.cache={}
  kind,ps,_=g.candidates((10,30),(50,30),False,[])
  self.assertEqual(kind,'main_ramp_top');self.assertLess(ps[0][0],20)
  kind,ps,_=g.candidates((30,30),(50,30),True,[])
  self.assertEqual(kind,'expansion_entrance');self.assertGreater(ps[0][0],30)


class AlertBudget(unittest.TestCase):
 """告警积压是有上限的:单条告警带完整快照,不设限会吃掉半个 prompt。"""

 def setUp(self):
  from arena.intelligence import Intelligence
  self.events=[];self.intel=Intelligence(lambda k,**v:self.events.append((k,v)))

 def snap(self,loop,units=(),dead=()):
  return Snapshot(loop,500,12,30,tuple(units),(30,30),dead_units=dead)

 def test_pending_alerts_are_capped_with_a_counted_summary(self):
  from arena.intelligence import MAX_PENDING_ALERTS
  enemy=Entity(7,48,4,100,100,45,45)
  # 多波目击+死亡,制造远超上限的待办告警
  for i in range(20):
   scout=Entity(1000+i,45,1,100,100,45,45)
   alive=Entity(2000+i,48,1,100,100,45,45)
   self.intel.update(self.snap(10+i*4,[scout,alive,enemy]))
   self.intel.update(self.snap(12+i*4,[alive,enemy],dead=(scout.tag,)))
  page=self.intel.public(self.snap(2000,[Entity(3000,48,1,100,100,45,45)]))
  self.assertLessEqual(len(page['pending_alerts']),MAX_PENDING_ALERTS)
  summary=page['pending_alerts_omitted']
  self.assertIsNotNone(summary,'折叠必须可见,不能静默丢弃')
  self.assertGreater(summary['total'],0)
  self.assertTrue(summary['counts_by_kind'])
  self.assertEqual(summary['full_record'],'intelligence_observed journal')

 def test_alert_positions_are_rounded_and_death_alert_keeps_nearby_contacts(self):
  scout=Entity(1,45,1,100,100,45,45)
  nearby=Entity(2,48,4,102.3456789,103.9876543,45,45)
  self.intel.update(self.snap(10,[scout,nearby]))
  self.intel.update(self.snap(100,[],dead=(1,)))
  page=self.intel.public(self.snap(120,[]))
  death=[a for a in page['pending_alerts'] if a['kind']=='own_unit_death_observed']
  self.assertEqual(len(death),1)
  alert=death[0]
  self.assertEqual(alert['unit']['pos'],[100.0,100.0],'坐标降到 1 位小数')
  self.assertTrue(alert['nearby_recent_contacts'],'死亡告警仍要带附近的目击')
  self.assertEqual(alert['nearby_recent_contacts'][0]['pos'],[102.3,104.0])

 def test_scout_death_keeps_the_last_seen_enemy_for_later_decisions(self):
  """用户关心的那条:侦察兵死了,它死前看到的敌方单位还在不在?"""
  scout=Entity(1,45,1,100,100,45,45)
  enemy=Entity(9,33,4,110.5555,111.4444,150,150)
  self.intel.update(self.snap(10,[scout,enemy]))
  self.intel.update(self.snap(12,[],dead=(1,)))          # 侦察兵阵亡,敌方不再可见
  page=self.intel.public(self.snap(2000,[]))              # 4200 loop 之后
  contacts=[c for c in page['contacts'] if c['unit']=='SiegeTank']
  self.assertEqual(len(contacts),1,'侦察兵死前看到的敌方单位必须保留')
  self.assertEqual(contacts[0]['state'],'last_seen')
  self.assertEqual(contacts[0]['area_center'],[114,114],'位置按 12 格区域中心粗化(格号*12+6)')
  self.assertIn('Sightings survive scout death',page['meaning'])
  # 死亡告警里同样保留接触快照
  death=[a for a in page['pending_alerts'] if a['kind']=='own_unit_death_observed']
  self.assertEqual(len(death),1)

class LateMacro(unittest.TestCase):
 def setUp(self):
  from arena.battle_plans import capture
  self.f=test_multirace.MultiRace();self.f.setUp();self.q=TaskQueue(self.f.backend(),0,lambda s:None)
  self.w=self.f.world([self.f.unit(1,18,is_building=True),self.f.unit(2,21,is_building=True),self.f.unit(3,45)])
  self.before=capture(self.w,self.q)
  self.plan={'tasks':[{'skill':'train_unit','args':{'unit':'marine'},'count':2}]}
 def check(self,w=None,age=35,plan=None):
  from arena.battle_plans import revalidate
  return revalidate(plan or self.plan,self.before,w or self.w,self.q,(30,30),age)
 def test_35_second_response_can_still_be_mechanically_valid(self):self.assertEqual(self.check(),[])
 def test_changed_quantity_or_lost_producer_requires_fresh_plan(self):
  self.assertTrue(self.check(replace(self.w,units=(*self.w.units,self.f.unit(4,48)))))
  self.assertTrue(self.check(replace(self.w,units=(self.w.units[0],self.w.units[2]))))
 def test_enemy_change_and_absolute_age_not_ignored(self):
  self.assertTrue(self.check(replace(self.w,units=(*self.w.units,replace(self.f.unit(5,48),alliance=4)))))
  self.assertTrue(self.check(age=71))
 def test_late_supply_when_no_longer_tight_rejected(self):
  self.assertTrue(self.check(plan={'tasks':[{'skill':'build_supply'}]}))

if __name__=='__main__':unittest.main()
