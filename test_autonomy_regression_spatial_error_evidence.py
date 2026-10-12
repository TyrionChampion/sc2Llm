"""Executor errors preserve actual own-squad and visible-engagement evidence."""
from dataclasses import replace
import unittest
from arena.raw_engine import ATTACK
from arena.tasks import TaskQueue
from test_rally_orders import engine_backend
from test_realtime_runtime import unit,world

class SpatialErrorEvidence(unittest.TestCase):
    def test_engine_rejection_keeps_current_engagement_evidence(self):
        _,backend=engine_backend('terran',available=(16,ATTACK,3673,390))
        events=[]
        q=TaskQueue(backend,0,lambda s:None,lambda kind,**kw:events.append(dict(kind=kind,**kw)))
        marine=unit(1,48,30,30,engaged_target=2,ground_dps=10,attack_range=5)
        enemy=unit(2,45,34,30,alliance=4)
        initial=world([marine,enemy],10)
        q.enqueue([{'skill':'order_units','args':{'unit':'marine','count':1,'mode':'attack_move',
                   'target':[80,80],'reason':'明确进攻'}}],10)
        q.tick(initial,set())
        q.tick(replace(initial,game_loop=246,errors=((1,ATTACK,3),)),set())
        error=next(row for row in events if row['kind']=='spatial_mission_error')
        self.assertEqual(error['observed_squad'][0]['engaged_target'],2)
        self.assertEqual(error['visible_engaged_targets'][0]['tag'],2)
        self.assertEqual(q.tasks[0].status,'failed','Combat context must not hide actual engine rejection')
