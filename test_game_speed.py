"""Normal speed selection/clock tests; real loop rates live in the native probe logs."""
import unittest
from unittest.mock import patch
from arena.game_speed import map_for_speed
from arena.raw_engine import GameClock

class SpeedTests(unittest.TestCase):
    def test_normal_map_checked_and_faster_original_available(self):
        self.assertIn("AcropolisNormal",str(map_for_speed("Acropolis","normal")))
        self.assertIn("AcropolisLE",str(map_for_speed("Acropolis","faster")))
    def test_unsupported_normal_map_never_silently_uses_faster(self):
        with self.assertRaises(ValueError):map_for_speed("Simple64","normal")
    def test_normal_clock_accepts_16_loops_and_rejects_22_4(self):
        with patch("arena.raw_engine.time.monotonic",return_value=100):
            c=GameClock(0,expected_loops_per_second=16)
        with patch("arena.raw_engine.time.monotonic",return_value=120):
            c.update(320);r=c.report()
            self.assertEqual(r["speed_check"],"ok")
            self.assertAlmostEqual(r["expected_game_wall_ratio"],16/22.4)
            self.assertEqual(r["loops_per_wall_second"],16)
            c.update(448);self.assertEqual(c.report()["speed_check"],"DRIFT")
    def test_slower_map_and_clock_reject_normal_speed(self):
        self.assertIn("AcropolisSlower",str(map_for_speed("Acropolis","slower")))
        with patch("arena.raw_engine.time.monotonic",return_value=100):
            c=GameClock(0,expected_loops_per_second=9.6)
        with patch("arena.raw_engine.time.monotonic",return_value=120):
            c.update(192);self.assertEqual(c.report()["speed_check"],"ok")
            c.update(320);self.assertEqual(c.report()["speed_check"],"DRIFT")
    def test_faster_clock_does_not_accept_accidental_slowdown(self):
        with patch("arena.raw_engine.time.monotonic",return_value=100):c=GameClock(0)
        with patch("arena.raw_engine.time.monotonic",return_value=120):
            c.update(320);self.assertEqual(c.report()["speed_check"],"DRIFT")

if __name__=="__main__":unittest.main()
