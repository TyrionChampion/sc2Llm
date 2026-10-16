import json
import unittest
from dataclasses import replace
from pathlib import Path

from arena.raw_engine import MOVE, ATTACK
from arena.tasks import TaskQueue
from test_rally_orders import engine_backend
from test_realtime_runtime import unit, world


class FormationArrival(unittest.TestCase):
    def mission(self,units,target,mode='move'):
        _,backend=engine_backend('terran',available=(MOVE,ATTACK))
        events=[]
        queue=TaskQueue(backend,0,lambda s:None,
                        lambda kind,**kw:events.append(dict(kind=kind,**kw)))
        queue.enqueue([dict(skill='order_units',args=dict(unit='army',count='all',
            mode=mode,target=target,reason='模型明确撤退到坡口',timeout_s=120))],0)
        # Initial location is away from the selected target; engine movement is explicit.
        start=[replace(u,x=30,y=30) for u in units]
        queue.tick(world(start,0),set())
        queue.tick(world(units,10),set())
        queue.tick(world(units,31),set())
        return queue.tasks[0],events

    def test_saved_large_retreat_formation_counts_as_observed_arrival(self):
        saved=json.loads((Path(__file__).parent/'fixtures/match-29-retreat.json').read_text())
        units=[unit(row['tag'],33 if row['radius']==.875 else 51 if row['radius']==.5625 else 48,
                    *row['pos'],radius=row['radius'],orders=tuple(row['orders']),
                    engaged_target=row['engaged_target']) for row in saved['units']]
        task,events=self.mission(units,saved['target'])
        self.assertEqual(task.status,'completed')
        self.assertTrue(any(e['kind']=='spatial_arrival' for e in events))
        self.assertFalse(any(e['kind']=='spatial_mission_error' for e in events))

    def test_missing_distant_member_and_attack_orders_do_not_count_as_arrival(self):
        target=[80,80]
        for units,mode in (([unit(1,48,80,80),unit(2,48,90,80)],'move'),
                           ([unit(1,48,75,80,orders=(ATTACK,)),unit(2,48,85,80,orders=(ATTACK,))],'attack_move'),
                           ([unit(1,48,75,80,orders=(MOVE,)),unit(2,48,85,80,orders=(MOVE,))],'move')):
            task,events=self.mission(units,target,mode)
            self.assertEqual(task.status,'failed')
            self.assertFalse(any(e['kind']=='spatial_arrival' for e in events))


if __name__=='__main__':unittest.main()
