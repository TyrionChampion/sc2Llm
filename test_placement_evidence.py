"""Native placement rejection causes must remain player-local and observable."""
from types import SimpleNamespace
from dataclasses import replace
import math
import unittest
from arena.raw_engine import RawArena
from arena.tasks import TaskQueue
import test_multirace as fixtures


class PlacementEvidenceTests(unittest.TestCase):
    def crowded_queue(self):
        f=fixtures.MultiRace();f.setUp();b=f.backend('protoss',[881]);events=[]
        q=TaskQueue(b,0,lambda _:None,lambda kind,**kw:events.append(dict(kind=kind,**kw)))
        q.enqueue([dict(skill='build_supply')],10)
        world=f.world([f.unit(1,59,is_building=True,radius=2.75),f.unit(2,84),
            replace(f.unit(3,60,is_building=True,radius=1.125),x=35)])
        return f,b,q,world,events

    def test_crowded_pylon_queries_dense_points_then_waits_for_actual_completion(self):
        f,b,q,w,events=self.crowded_queue();calls=[]
        def placement(i,ability,worker,points):
            calls.append(list(points))
            return (39,30) if (39,30) in points else None
        b.placement=placement
        out=q.tick(w,set());task=q.tasks[0]
        self.assertEqual(len(calls),2);self.assertNotIn((39,30),calls[0]);self.assertIn((39,30),calls[1])
        self.assertFalse(set(calls[0])&set(calls[1]));self.assertEqual(len(out),1)
        self.assertEqual(out[0].action_raw.unit_command.ability_id,881)
        self.assertEqual(task.program.issued,1);self.assertNotEqual(task.status,'completed')
        event=next(e for e in events if e['kind']=='macro_placement_spacing_relaxed')
        self.assertEqual(event['selected'],[39,30])
        newborn=replace(f.unit(4,60,is_building=True),x=39,y=30)
        q.tick(replace(w,game_loop=300,units=w.units+(newborn,)),set())
        self.assertEqual(task.status,'completed');self.assertEqual(task.program.issued,1)

    def test_ordinary_pylon_success_does_not_try_dense_layout(self):
        f,b,q,w,events=self.crowded_queue();calls=[]
        def placement(i,a,worker,points):calls.append(points);return points[0]
        b.placement=placement
        self.assertEqual(len(q.tick(w,set())),1);self.assertEqual(len(calls),1)
        self.assertFalse(any(e['kind']=='macro_placement_spacing_relaxed' for e in events))

    def test_rejected_dense_pylon_never_buys_or_assumes_placement(self):
        f,b,q,w,events=self.crowded_queue();calls=[]
        b.placement=lambda i,a,worker,points:(calls.append(points) or None)
        self.assertEqual(q.tick(w,set()),[]);self.assertEqual(len(calls),2)
        self.assertEqual(q.tasks[0].program.issued,0)
        self.assertEqual(q.tasks[0].status,'waiting')
        self.assertFalse(any(e['kind']=='macro_placement_spacing_relaxed' for e in events))

    def test_dense_pylon_is_available_when_spacing_filters_every_candidate(self):
        f,b,q,w,events=self.crowded_queue();run=q.tasks[0].program
        original_positions=run._positions
        run._positions=lambda w,q,t,actor,recipe,allow_dense_supply=False:(
            original_positions(w,q,t,actor,recipe,allow_dense_supply=True) if allow_dense_supply else [])
        calls=[]
        def placement(i,a,worker,points):
            calls.append(points);return (39,30) if (39,30) in points else None
        b.placement=placement
        self.assertEqual(len(q.tick(w,set())),1);self.assertEqual(len(calls),1)
        self.assertEqual(q.tasks[0].program.issued,1)
        self.assertEqual(next(e for e in events if e['kind']=='macro_placement_spacing_relaxed')['original_candidates'],0)

    def test_dense_pylon_retains_footprints_reserved_points_and_exclusions(self):
        f,b,q,w,events=self.crowded_queue();task=q.tasks[0];run=task.program
        task.excluded.add((39,30))
        reservation=SimpleNamespace(active=True,spot=(30,42))
        proxy=SimpleNamespace(backend=b,tasks=[task,reservation],index=0)
        points=run._positions(w,proxy,task,w.by_tag(2),run.spec['entry']['recipes'][0],allow_dense_supply=True)
        self.assertTrue(points);self.assertNotIn((39,30),points)
        self.assertTrue(all(math.dist(p,reservation.spot)>=6 for p in points))
        size=b.tech_tree.abilities[881].footprint_radius
        self.assertTrue(all(w.by_tag(3).distance(p)>=max(2,size+w.by_tag(3).radius+.75) for p in points))

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
