"""Large armies must not hide one moving straggler from the stall watchdog."""
from dataclasses import replace
import unittest
from arena.raw_engine import MOVE,ATTACK
from arena.tasks import TaskQueue
from test_rally_orders import engine_backend
from test_realtime_runtime import unit,world


class StragglerProgress(unittest.TestCase):
    def mission(self,seconds=24,moving=True,opposite=False,timeout=120):
        _,b=engine_backend('zerg',available=(MOVE,ATTACK));q=TaskQueue(b,0,lambda _:None)
        q.enqueue([dict(skill='order_units',args=dict(unit='army',count='all',mode='attack_move',
            target=[100,100],timeout_s=timeout,reason='explicit rendezvous'))],0)
        army=[unit(i+1,110,30,30,ground_dps=10) for i in range(32)]
        queens=[unit(100,126,30,100,ground_dps=4,orders=(ATTACK,))]
        if opposite:queens.append(unit(101,126,170,100,ground_dps=4,orders=(ATTACK,)))
        q.tick(world([*army,*queens],0),set())
        settled=[replace(u,x=100,y=100) for u in army]
        for s in range(1,seconds+1):
            offset=.8*s if moving else 0
            qs=[replace(queens[0],x=30+offset)]
            if opposite:qs.append(replace(queens[1],x=170-offset))
            q.tick(world([*settled,*qs],s),set())
            if not q.tasks[0].active:break
        return q.tasks[0]

    def test_one_slow_straggler_keeps_making_observed_progress(self):
        self.assertTrue(self.mission().active)

    def test_opposite_moving_members_do_not_cancel_each_others_progress(self):
        self.assertTrue(self.mission(opposite=True).active)

    def test_truly_stationary_distant_member_still_triggers_stall(self):
        task=self.mission(moving=False)
        self.assertEqual(task.status,'failed');self.assertIn('no observed movement progress',task.reason)

    def test_observed_movement_does_not_extend_model_deadline(self):
        task=self.mission(seconds=62,timeout=60)
        self.assertEqual(task.status,'failed');self.assertIn('mission game-time deadline',task.reason)


if __name__=='__main__':unittest.main()
