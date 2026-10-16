"""Verify reviewed prompts reach real Commander scheduling, with local adapters only."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from arena import launch_match as worker
from arena.commander import Commander
from arena.context_tables import decode_tables
from arena.lan_room import prompt_rules
from arena.launch_config import OFFICIAL
from arena.lifecycle import write_json_atomic
import test_multirace as multirace_fixtures


class RoomWorkerTests(unittest.TestCase):
    def test_worker_preserves_prompts_and_avoids_hidden_skill_tactics_and_gui(self):
        for remote_model, direct in (('opencode-go/deepseek-v4.1-flash', True), ('opencode-go/kimi-k3', False)):
            with self.subTest(model=remote_model), tempfile.TemporaryDirectory() as directory:
                run = Path(directory)
                prompts = ['甲' * 1200, '守住三矿，按侦察决定绕路增援。']
                c = dict(interface='lan', room_code='ABC234', players=[
                    dict(model=OFFICIAL, race='terran', prompt=prompts[0]),
                    dict(model=remote_model, race='zerg', prompt=prompts[1])],
                    speed='slower', minutes=45, combat_micro=True, reasoning='high', tactics=False, live_skills=False)
                write_json_atomic(run / 'config.json', c)

                class Planner:
                    calls = 0
                    model = 'deepseek-flash'
                    p = type('Provider', (), dict(key_env='DEEPSEEK_API_KEY', base_url='https://api.deepseek.com/v1'))()
                    def __init__(self, *a, **kw): pass
                    def identity(self): return dict(side='P2', provider='opencode', model=remote_model, key_source='login')
                    def close(self): pass

                class Server:
                    def __init__(self, *a, **kw): pass
                    def start(self): pass
                    def close(self): pass

                with patch.dict(os.environ, {'open_api_key':'local-fake-key' if direct else ''}), \
                     patch.object(worker, 'validate_config', return_value=c), \
                     patch.object(worker, 'prepare_skills') as hidden_skills, \
                     patch('arena.deepseek_planner.DeepSeekFlashPlanner', Planner), \
                     patch('arena.go_planner.GoPlanner', Planner), \
                     patch('arena.opencode_bridge.OpenCodeServer', Server), \
                     patch('arena.opencode_bridge.OpenCodePlanner', Planner), \
                     patch.object(worker.subprocess, 'Popen') as gui, \
                     patch('native_llm.main') as native:
                    worker.main(['--run', str(run)])
                hidden_skills.assert_not_called()
                gui.assert_not_called()
                args = native.call_args.kwargs
                self.assertFalse(args['opening'])
                self.assertEqual(args['catalogs_override'], [None, None])
                self.assertEqual([client.prepared_strategy for client, _ in args['players_override']], prompts)
                self.assertEqual([client.calls for client, _ in args['players_override']], [0, 0])
                argv = args['argv']
                self.assertEqual(argv[argv.index('--feedback-dir') + 1], str(run / 'feedback'))
                self.assertEqual(json.loads((run / 'run.json').read_text())['status'], 'finished')

    def test_reviewed_system_and_max_length_prompt_match_actual_compact_request(self):
        for micro in (False, True):
            with self.subTest(micro=micro), tempfile.TemporaryDirectory() as directory:
                fixture = multirace_fixtures.MultiRace()
                fixture.setUp()
                backend = fixture.backend()
                world = fixture.world()
                backend.initial = [world]
                backend.request_surrender = lambda *args, **kwargs:None
                backend.register_action_context = lambda *args:None

                class Planner:
                    compact_battle = True
                    soft_timeout = 15
                    timeout = 60
                    prepared_strategy = '战' * 1200
                    def __init__(self): self.done = threading.Event()
                    def chat(self, system, user):
                        self.system = system
                        self.payload = decode_tables(json.loads(user))
                        self.done.set()
                        return '{"tasks":[],"reason":"本地提示词合同验证"}'

                planner = Planner()
                commander = Commander(backend, 0, planner, opening=False, feedback_dir=directory, combat_micro=micro)
                try:
                    self.assertEqual(commander.system, prompt_rules(micro)['system'])
                    commander.tick(world)
                    self.assertTrue(planner.done.wait(2))
                    self.assertEqual(planner.system, prompt_rules(micro)['system'])
                    self.assertEqual(planner.payload['prepared_strategy']['text'], planner.prepared_strategy)
                    self.assertFalse(planner.payload['prepared_strategy']['truncated'])
                finally: commander.stop()


if __name__ == '__main__': unittest.main()
