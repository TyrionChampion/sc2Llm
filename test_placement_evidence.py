"""Native placement rejection causes must remain player-local and observable."""
from types import SimpleNamespace
import unittest
from arena.raw_engine import RawArena
from arena.tasks import TaskQueue
import test_multirace as fixtures


class PlacementEvidenceTests(unittest.TestCase):
    def arena(self,results):
        a=RawArena.__new__(RawArena)
        a.controllers=[SimpleNamespace(query=lambda q:SimpleNamespace(
            placements=[SimpleNamespace(result=r) for r in results]))]
        return a

    def test_power_and_vision_failures_are_not_discarded(self):
        a=self.arena([50,45,50])
        self.assertIsNone(a.placement(0,883,7,[(20,20),(22,22),(24,24)]))
        d=a.placement_evidence(0,883)
        self.assertEqual(d['failure_counts'],{'CantBuildTooFarFromBuildPowerSource':2,'CantSeeBuildLocation':1})
        self.assertEqual(d['candidate_count'],3)

    def test_success_selection_and_incomplete_response_keep_original_behavior(self):
        a=self.arena([50,1])
        self.assertEqual(a.placement(0,883,7,[(20,20),(22,22)]),(22,22))
        self.assertEqual(a.placement_evidence(0,883)['accepted'],[22,22])
        b=self.arena([50])
        with self.assertRaisesRegex(RuntimeError,'incomplete'):b.placement(0,883,7,[(20,20),(22,22)])

    def test_another_player_or_ability_does_not_reuse_a_previous_failure(self):
        a=self.arena([50]);a.placement(0,883,7,[(20,20)])
        self.assertIsNone(a.placement_evidence(1,883))
        self.assertIsNone(a.placement_evidence(0,881))

    def test_macro_waiting_reason_and_summary_expose_native_cause(self):
        f=fixtures.MultiRace();f.setUp();entry=f.tree.units['gateway']
        ability=entry['recipes'][0]['ability'];b=f.backend('protoss',[ability])
        b.placement=lambda *args:None
        evidence=dict(failure_counts={'CantBuildTooFarFromBuildPowerSource':2},candidate_count=2)
        b.placement_evidence=lambda index,queried:evidence if queried==ability else None
        q=TaskQueue(b,0,emit=lambda message:None)
        q.enqueue([dict(skill='build_structure',args=dict(structure='gateway'))],0)
        self.assertEqual(q.tick(f.world([f.unit(1,84),f.unit(2,60)],race='protoss'),set()),[])
        self.assertIn('CantBuildTooFarFromBuildPowerSource',q.tasks[0].reason)
        self.assertEqual(q.tasks[0].program.summary()['placement_feedback'],evidence)


if __name__=='__main__':unittest.main()
