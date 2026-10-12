"""Cancel then dispatch all must wait for released defenders to become available."""
from dataclasses import replace
import unittest
from arena.raw_engine import ATTACK
from arena.tasks import TaskQueue
from test_rally_orders import engine_backend
from test_defense_orders import marine


class CancelAllDispatch(unittest.TestCase):
    def test_cancelled_defenders_join_the_explicit_all_dispatch_after_stop(self):
        fixture,backend=engine_backend('terran',available=(16,23))
        q=TaskQueue(backend,0,lambda s:None)
        w=fixture.world([fixture.unit(1,18,is_building=True),marine(2,30,30),marine(3,50,30)],race='terran')
        q.enqueue([dict(skill='defend_base',args=dict(base='home',units=['marine'],
            count=1,stance='hold',timeout_s=60,reason='显式驻守'))],10)
        q.tick(w,set());self.assertEqual(q.tasks[0].program.tags,{2})
        q.enqueue([dict(skill='cancel_task',args=dict(task_id=1)),
            dict(skill='order_units',args=dict(unit='marine',count='all',mode='attack_move',
                 target=[80,80],timeout_s=180,reason='解除驻守后全部重新编队'))],11)
        actions=q.tick(replace(w,game_loop=250),set())
        self.assertEqual([a.action_raw.unit_command.ability_id for a in actions],[4])
        self.assertFalse(q.tasks[-1].program.tags)
        actions=q.tick(replace(w,game_loop=270),set())
        moves=[a.action_raw.unit_command for a in actions if a.action_raw.unit_command.ability_id==ATTACK]
        self.assertEqual(len(moves),1)
        self.assertEqual(set(moves[0].unit_tags),{2,3})
        self.assertEqual(q.tasks[-1].program.tags,{2,3})


if __name__=='__main__':unittest.main()
