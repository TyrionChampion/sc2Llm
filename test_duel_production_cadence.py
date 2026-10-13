"""Idle military production wakes both races without selecting units or issuing orders."""
import unittest
from dataclasses import replace
from arena.autonomous_bot import production_decision_needed
from test_realtime_runtime import unit, world

class DuelCadenceTests(unittest.TestCase):
    def test_idle_larva_with_completed_military_tech_wakes_zerg(self):
        larva=unit(1,151,30,30);pool=unit(2,89,30,30,is_building=True)
        w=replace(world([larva,pool],10),race='zerg',minerals=300)
        self.assertTrue(production_decision_needed(w))
        for units in ([larva],[larva,replace(pool,progress=.5)],
                      [replace(larva,orders=(1342,)),pool],[pool]):
            self.assertFalse(production_decision_needed(replace(w,units=tuple(units))))
        self.assertFalse(production_decision_needed(replace(w,minerals=299)))

    def test_idle_protoss_production_is_not_ignored(self):
        for kind in (62,71,67,133):
            w=replace(world([unit(1,kind,30,30,is_building=True)],10),race='protoss',minerals=300)
            self.assertTrue(production_decision_needed(w),kind)

    def test_busy_incomplete_or_worker_only_base_is_not_military_capacity(self):
        for u in (unit(1,62,30,30,is_building=True,orders=(916,)),
                  unit(1,62,30,30,is_building=True,progress=.5),
                  unit(1,59,30,30,is_building=True)):
            self.assertFalse(production_decision_needed(replace(world([u],10),race='protoss',minerals=300)))

if __name__=='__main__':unittest.main()
