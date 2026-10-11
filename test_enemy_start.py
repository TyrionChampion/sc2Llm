"""Regression from actual 5.0.15 dual-player GameInfo; not live movement evidence."""
import contextlib
import io
import unittest
from types import SimpleNamespace
from s2clientprotocol import sc2api_pb2 as pb
from arena.raw_engine import RawArena, MARINE, ATTACK
from arena.tasks import TaskQueue
from arena.python_skills import PythonSkillRun
from test_realtime_runtime import unit, world

P1=(142.5,33.5)
P2=(33.5,138.5)

def info(points,size=(176,172)):
    out=pb.ResponseGameInfo()
    out.start_raw.map_size.x,out.start_raw.map_size.y=size
    for x,y in points:out.start_raw.start_locations.add(x=x,y=y)
    return out

def backend(points=None,homes=None):
    b=RawArena.__new__(RawArena)
    b.homes=[P1,P2] if homes is None else homes
    b.size=(176,172)
    with contextlib.redirect_stdout(io.StringIO()):
        b.configure_start_locations([info(p) for p in (points if points is not None else [[P2],[P1]])])
    return b

class EnemyStartTests(unittest.TestCase):
    def test_each_player_gets_own_game_info_candidates(self):
        b=backend()
        self.assertEqual(b.enemy_start(0),P2)
        self.assertEqual(b.enemy_start(1),P1)
        self.assertNotEqual(b.enemy_start(1),b.homes[1])
        self.assertEqual(b.start_location_report[1]['enemy_start'],P1)
    def test_never_uses_opponent_private_home_observation(self):
        b=backend(homes=[(70,70),P2])
        self.assertEqual(b.enemy_start(1),P1)
    def test_single_api_bot_and_swapped_spawns(self):
        self.assertEqual(backend(points=[[P2]],homes=[P1]).enemy_start(0),P2)
        b=backend(points=[[P1],[P2]],homes=[P2,P1])
        self.assertEqual(b.enemy_start(0),P1)
        self.assertEqual(b.enemy_start(1),P2)
    def test_missing_and_own_only_candidates_fail_without_fallback(self):
        for points in ([[P2],[]],[[P2],[P2]]):
            with self.subTest(points=points),self.assertRaisesRegex(RuntimeError,'P2.*no non-home'):
                backend(points=points)
    def test_own_candidate_is_filtered_if_other_candidates_exist(self):
        b=backend(points=[[P1,P2],[P2,P1]])
        self.assertEqual(b.enemy_start(1),P1)
    def test_invalid_metadata_and_index_fail(self):
        with self.assertRaisesRegex(RuntimeError,'count mismatch'):backend(points=[[P2]])
        with self.assertRaisesRegex(RuntimeError,'invalid map start'):backend(points=[[(-1,0)],[P1]])
        b=backend()
        with self.assertRaisesRegex(RuntimeError,'map size'):b.configure_start_locations([info([P2]),info([P1],(10,10))])
        for i in (-1,2,True):
            with self.assertRaises(ValueError):b.enemy_start(i)
    def test_actual_macro_commands_use_p2_enemy_start(self):
        for skill in ('attack_nearest','move_out'):
            q=TaskQueue(backend(),1,lambda _:None)
            try:
                q.enqueue([dict(skill=skill)],0)
                actions=q.tick(world([unit(2,MARINE,*P2,ground_dps=7)]),set())
                c=actions[0].action_raw.unit_command
                self.assertEqual(c.ability_id,ATTACK)
                self.assertEqual((c.target_world_space_pos.x,c.target_world_space_pos.y),P1)
            finally:q.close()
    def test_python_objective_and_retreat_resolve_distinctly(self):
        q=SimpleNamespace(backend=backend(),index=1)
        run=PythonSkillRun.__new__(PythonSkillRun)
        self.assertEqual(run.resolve('enemy_start',world([]),q),list(P1))
        self.assertEqual(run.resolve('home',world([]),q),list(P2))
        self.assertNotEqual(run.resolve('rally',world([]),q),list(P2))

if __name__=='__main__':unittest.main()
