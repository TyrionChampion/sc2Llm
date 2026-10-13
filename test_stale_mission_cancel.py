"""A mission can end while the model thinks; its cancellation is idempotent."""
import unittest
import test_spatial_missions as fixtures
from arena.tasks import PlanValidationError
from arena.raw_engine import ATTACK

class StaleMissionCancel(unittest.TestCase):
 def fixture(self,status):
  f=fixtures.SpatialMissions();f.setUp();f.start(fixtures.order());f.q.update(f.q.tasks[0],status,'ended while model was thinking');return f
 def test_finished_owned_mission_does_not_reject_other_orders_or_stop_new_owner(self):
  for skill in ('cancel_unit_order','cancel_task'):
   for status in ('completed','failed','cancelled'):
    with self.subTest(skill=skill,status=status):
     f=self.fixture(status)
     f.q.enqueue([dict(skill=skill,args={'task_id':1}),fixtures.order((100,100))],10)
     self.assertEqual(f.q.tasks[0].status,status)
     actions=f.q.tick(f.w,set());self.assertEqual([a.action_raw.unit_command.ability_id for a in actions],[ATTACK])
     event=next(e for e in f.events if e['kind']=='task_cancel_noop');self.assertEqual(event['state'],status)
 def test_unknown_task_is_still_an_atomic_rejection(self):
  f=self.fixture('completed');before=len(f.q.tasks)
  with self.assertRaises(PlanValidationError):f.q.enqueue([dict(skill='cancel_unit_order',args={'task_id':1}),dict(skill='cancel_unit_order',args={'task_id':99}),fixtures.order((100,100))],10)
  self.assertEqual(len(f.q.tasks),before);self.assertFalse(any(e['kind']=='task_cancel_noop' for e in f.events))
if __name__=='__main__':unittest.main()
