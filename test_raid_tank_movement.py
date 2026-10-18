"""Raids share explicit tank movement preparation, including a later withdrawal."""
from dataclasses import replace
import unittest
from arena.raw_engine import MOVE,ATTACK
from arena.combat_micro import UNSIEGE
from arena.raids import HOLD
from arena.tasks import TaskQueue
from test_rally_orders import engine_backend
from test_realtime_runtime import unit,world
from test_offensive_missions import raid


class RaidTankMovement(unittest.TestCase):
    def setUp(self):
        _,self.b=engine_backend('terran');self.events=[]
        self.q=TaskQueue(self.b,0,lambda _:None,lambda k,**kw:self.events.append(dict(kind=k,**kw)))
        self.units=[unit(1,48,30,30,ground_dps=6,attack_range=5),
                    unit(2,32,31,30,ground_dps=35,attack_range=13)]
        self.w=world(self.units,10)
        self.b.available_abilities=lambda i,tags,**kw:{tag:({UNSIEGE,ATTACK,HOLD} if self.w.by_tag(tag).type_id==32
            else {MOVE,ATTACK,HOLD}) for tag in tags}
        self.q.enqueue([raid(unit='army',count=2,targets=[[80,80]])],self.w.seconds)

    def tick(self,units,seconds):
        self.w=world(units,seconds);return self.q.tick(self.w,set())

    def test_waits_for_actual_unsiege_before_any_squad_movement(self):
        first=self.tick(self.units,10)
        self.assertEqual([a.action_raw.unit_command.ability_id for a in first],[UNSIEGE])
        self.assertEqual(self.tick(self.units,12),[])
        mobile=[self.units[0],replace(self.units[1],type_id=33,attack_range=7)]
        out=self.tick(mobile,14)
        self.assertTrue(any(a.action_raw.unit_command.ability_id==MOVE for a in out))
        self.assertEqual(self.q.tasks[0].program.squad_tags,{1,2})

    def test_tank_may_siege_at_target_then_unsiege_again_for_authorized_retreat(self):
        self.tick(self.units,10)
        mobile=[self.units[0],replace(self.units[1],type_id=33)]
        self.tick(mobile,14)
        at=[replace(self.units[0],x=80,y=80),replace(self.units[1],x=81,y=80)]
        base=unit(99,86,82,82,alliance=4,is_building=True)
        out=self.tick([*at,base],25)
        self.assertTrue(any(a.action_raw.unit_command.ability_id==ATTACK for a in out))
        defenders=[unit(100+i,48,83+i,80,alliance=4,ground_dps=6) for i in range(2)]
        out=self.tick([*at,base,*defenders],26)
        self.assertTrue(any(a.action_raw.unit_command.ability_id==UNSIEGE for a in out))
        self.assertEqual(self.q.tasks[0].program.phase,'withdraw')
        self.assertNotEqual(self.q.tasks[0].status,'failed')
        out=self.tick([replace(u,type_id=33) if u.tag==2 else u for u in at]+[base,*defenders],30)
        self.assertTrue(any(a.action_raw.unit_command.ability_id==MOVE for a in out))

    def test_unobserved_transition_has_a_bounded_timeout_without_duplicate_cast(self):
        self.tick(self.units,10);self.assertEqual(self.tick(self.units,15),[])
        self.tick(self.units,19)
        self.assertEqual(self.q.tasks[0].status,'failed')
        self.assertIn('unsiege not observed',self.q.tasks[0].reason)

    def test_unavailable_unsiege_reports_failure_without_partial_movement(self):
        self.b.available_abilities=lambda i,tags,**kw:{tag:{ATTACK} for tag in tags}
        out=self.tick(self.units,10)
        self.assertFalse(any(a.action_raw.unit_command.ability_id==MOVE for a in out))
        self.assertEqual(self.q.tasks[0].status,'failed')
        self.assertIn('cannot currently unsiege',self.q.tasks[0].reason)


if __name__=='__main__':unittest.main()
