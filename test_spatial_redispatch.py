"""Resume an owned explicit order when native orders end before arrival."""
from dataclasses import replace
import unittest
from arena.raw_engine import MOVE,ATTACK
from arena.tasks import TaskQueue
from test_rally_orders import engine_backend
from test_realtime_runtime import unit,world


class IdleRedispatch(unittest.TestCase):
    def setUp(self):
        _,b=engine_backend('terran',available=(MOVE,ATTACK));self.events=[]
        self.q=TaskQueue(b,0,lambda _:None,lambda k,**kw:self.events.append(dict(kind=k,**kw)))
        self.q.enqueue([dict(skill='order_units',args=dict(unit='marine',count=2,
            mode='attack_move',target=[80,80],timeout_s=60,reason='explicit original goal'))],0)
        self.start=[unit(1,48,30,30,ground_dps=10,attack_range=5),unit(2,48,30,30,ground_dps=10,attack_range=5)]
        self.q.tick(world(self.start,0),set())
        self.resolved=tuple(self.q.tasks[0].program.route[0])
        self.stalled=[replace(self.start[0],x=80,y=80),replace(self.start[1],x=65,y=80)]
        self.q.tick(world(self.stalled,1),set())

    def test_only_idle_distant_member_receives_same_attack_goal_once(self):
        out=self.q.tick(world(self.stalled,7),set())
        self.assertEqual(len(out),1);raw=out[0].action_raw.unit_command
        self.assertEqual((raw.ability_id,list(raw.unit_tags)),(ATTACK,[2]))
        self.assertEqual((raw.target_world_space_pos.x,raw.target_world_space_pos.y),self.resolved)
        self.assertEqual(self.q.tasks[0].program.changed_at,world([],1).seconds)
        self.assertEqual(self.q.tick(world(self.stalled,14),set()),[])
        self.q.tick(world(self.stalled,23),set())
        self.assertEqual(self.q.tasks[0].status,'failed')
        self.assertEqual(sum(e['kind']=='spatial_order_redispatched' for e in self.events),1)

    def test_pending_movement_or_engagement_is_not_interrupted(self):
        for changes in [dict(orders=(ATTACK,)),dict(engaged_target=99)]:
            with self.subTest(changes=changes):
                self.setUp();units=[self.stalled[0],replace(self.stalled[1],**changes)]
                self.assertEqual(self.q.tick(world(units,7),set()),[])
        self.setUp();enemy=unit(99,45,67,80,alliance=4)
        self.assertEqual(self.q.tick(world([*self.stalled,enemy],7),set()),[])

    def test_actual_arrival_after_reissue_finishes_without_replacement(self):
        self.q.tick(world(self.stalled,7),set())
        self.q.tick(world([self.stalled[0],replace(self.stalled[1],x=75)],8),set())
        self.q.tick(world([self.stalled[0],replace(self.stalled[1],x=80)],9),set())
        self.assertEqual(self.q.tasks[0].status,'completed')
        self.assertEqual(self.q.tasks[0].program.stop_tags,{1,2})
        self.assertEqual(self.q.tasks[0].program.allocated_count,2)

    def test_move_order_stays_move_and_original_deadline_stays_absolute(self):
        self.q.tasks[0].program.args['mode']='move'
        out=self.q.tick(world(self.stalled,7),set())
        self.assertEqual(out[0].action_raw.unit_command.ability_id,MOVE)
        out=self.q.tick(world(self.stalled,61),set())
        self.assertEqual(self.q.tasks[0].status,'failed')
        self.assertIn('mission game-time deadline',self.q.tasks[0].reason)


if __name__=='__main__':unittest.main()
