"""A depleted model budget must not leave a commandless game idling forever."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from arena.autonomous_bot import RequestCapIdleGuard
from test_realtime_runtime import unit,world,CC


class RequestCapIdle(unittest.TestCase):
    def setUp(self):
        self.queue=SimpleNamespace(tasks=[])
        self.w=world([unit(1,CC,is_building=True),unit(2,48,ground_dps=10)],700)

    def test_quiet_finished_commands_stop_only_after_the_grace_period(self):
        guard=RequestCapIdleGuard()
        self.assertIsNone(guard.observe(self.w,self.queue,True))
        self.assertIsNone(guard.observe(replace(self.w,game_loop=int(819*22.4)),self.queue,True))
        result=guard.observe(replace(self.w,game_loop=int(821*22.4)),self.queue,True)
        self.assertGreaterEqual(result['quiet_s'],120)

    def test_pending_production_or_attack_and_native_orders_keep_running(self):
        for skill in ('train_unit','build_supply','order_units','scout'):
            guard=RequestCapIdleGuard();self.queue.tasks=[SimpleNamespace(active=True,skill=skill)]
            self.assertIsNone(guard.observe(self.w,self.queue,True))
            self.assertIsNone(guard.observe(replace(self.w,game_loop=int(1200*22.4)),self.queue,True))
        self.queue.tasks=[];guard=RequestCapIdleGuard()
        moving=replace(self.w,units=(self.w.units[0],replace(self.w.units[1],orders=(3674,))))
        self.assertIsNone(guard.observe(moving,self.queue,True))
        self.assertIsNone(guard.started)

    def test_available_requests_visible_enemies_and_unknown_task_state_never_trigger(self):
        guard=RequestCapIdleGuard()
        self.assertIsNone(guard.observe(self.w,self.queue,False))
        hostile=replace(self.w,units=self.w.units+(unit(9,48,alliance=4),))
        self.assertIsNone(guard.observe(hostile,self.queue,True))
        self.assertIsNone(guard.observe(self.w,SimpleNamespace(),True))


if __name__=='__main__':unittest.main()
