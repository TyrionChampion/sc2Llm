import unittest
from unittest.mock import patch
from pathlib import Path
from arena.raw_engine import headless_map_for_speed
from pysc2 import maps


class HeadlessSpeed(unittest.TestCase):
    def test_speed_selection_uses_verified_derivative_and_keeps_original_faster(self):
        with patch('arena.game_speed.map_for_speed',return_value=Path('/tmp/AcropolisSlower.SC2Map')) as checked:
            a=headless_map_for_speed('Acropolis','slower');b=headless_map_for_speed('Acropolis','slower')
        self.assertEqual(a.path,'Arena/AcropolisSlower.SC2Map')
        self.assertIs(type(a),type(b))
        self.assertIs(maps.get(a),a)
        self.assertEqual(checked.call_count,2)
        with patch('arena.game_speed.map_for_speed',side_effect=AssertionError('must keep original')):
            self.assertEqual(headless_map_for_speed('Acropolis','faster'),'Acropolis')

    def test_missing_or_invalid_speed_never_falls_back(self):
        with self.assertRaises(ValueError):headless_map_for_speed('Acropolis','invalid')
        with patch('arena.game_speed.map_for_speed',side_effect=FileNotFoundError('verified derivative missing')):
            with self.assertRaises(FileNotFoundError):headless_map_for_speed('Acropolis','slower')


if __name__=='__main__':unittest.main()
