"""Offline process fixtures for the official subscription battle transport."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from arena.chatgpt_planner import ChatGPTSubscriptionPlanner, battle_output_schema


FAKE_CLI = '''import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
mode = os.environ.get('TEST_CHATGPT_MODE', 'ok')
if args == ['login', 'status']:
    print('Logged in using API key' if mode == 'api' else 'Logged in using ChatGPT')
    sys.exit(0)
prompt = sys.stdin.read()
output = Path(args[args.index('-o') + 1])
(output.parent / 'received.json').write_text(json.dumps(dict(args=args, prompt=prompt)))
if mode == 'slow':
    time.sleep(10)
if mode == 'unauthorized':
    print(json.dumps(dict(type='turn.failed', error=dict(message='Your access token could not be refreshed. Please sign in again.'))))
    sys.exit(1)
if mode == 'tool':
    print(json.dumps(dict(type='item.completed', item=dict(type='command_execution'))))
output.write_text('not json' if mode == 'malformed' else json.dumps(dict(tasks=[] if mode=='review' else [dict(skill='defend_base', args=dict(base='home'))], reason='调兵守主矿')))
if mode != 'incomplete':
    print(json.dumps(dict(type='turn.completed', usage=dict(input_tokens=10, output_tokens=5))))
'''


class ChatGPTPlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        binary = self.root / 'codex'
        binary.write_text('#!' + sys.executable + '\n' + FAKE_CLI)
        binary.chmod(0o700)
        finder = patch('arena.chatgpt_planner.shutil.which', return_value=str(binary))
        finder.start()
        self.addCleanup(finder.stop)

    def planner(self, mode='ok'):
        env = patch.dict(os.environ, {'TEST_CHATGPT_MODE': mode,
                                     'ARENA_CHATGPT_HOME': str(self.root / 'private-login')})
        env.start()
        self.addCleanup(env.stop)
        client = ChatGPTSubscriptionPlanner('chatgpt/gpt-6-luna', self.root / 'planner')
        self.addCleanup(client.close)
        return client

    def test_exact_model_observation_and_subscription_home_reach_official_cli(self):
        client = self.planner()
        plan = json.loads(client.chat('Defend threatened bases.', '{"defense_report":[{"visible_attackers":3}]}'))
        self.assertEqual(plan['tasks'][0]['skill'], 'defend_base')
        received = json.loads((client.directory / 'request-0001/received.json').read_text())
        args = received['args']
        self.assertEqual(args[args.index('-m') + 1], 'gpt-6-luna')
        self.assertEqual(args[args.index('-s') + 1], 'read-only')
        self.assertEqual(args[args.index('-C') + 1],str(client.directory))
        self.assertIn('--ephemeral',args)
        self.assertIn('"visible_attackers":3', received['prompt'])
        self.assertEqual(client.env['CODEX_HOME'], str(self.root / 'private-login'))
        self.assertEqual(client.calls, 1)
        self.assertEqual(client.attempts, 1)
        self.assertIsNone(client.last_metadata['returned_model'])
        self.assertFalse((self.root / 'private-login').exists(), 'planner must not copy/create credentials')

    def test_api_key_login_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'HTTP 401'):
            self.planner('api')

    def test_standalone_review_selects_subscription_home_without_runner_environment(self):
        with patch.dict(os.environ,{'TEST_CHATGPT_MODE':'review','CODEX_HOME':str(self.root/'other-login')}):
            with patch.dict(os.environ):
                os.environ.pop('ARENA_CHATGPT_HOME',None)
                client=ChatGPTSubscriptionPlanner('chatgpt/gpt-6-luna',self.root/'standalone-review')
                try:
                    wanted=Path(__file__).resolve().parent/'session-runtime/chatgpt-subscription'
                    self.assertEqual(client.env['CODEX_HOME'],str(wanted))
                    client.response_kind='review'
                    self.assertEqual(json.loads(client.chat('Review only.','{}'))['tasks'],[])
                    self.assertFalse((self.root/'other-login').exists())
                finally:client.close()

    def test_structured_battle_passes_actual_scout_and_count_contract_to_cli(self):
        client=self.planner();client.structured_battle=True;client.reasoning_effort='high'
        payload={'control_policy':{'spatial_missions_available':True},
                 'tech_tree':{'race':'terran','units':[{'name':'marine'}],
                              'structures':[{'name':'barracks'}],
                              'research':[{'name':'stimpack'}],'forms':['siege_tank_sieged']}}
        client.chat('Use the actual command contract.',json.dumps(payload))
        received=json.loads((client.directory/'request-0001/received.json').read_text())
        args=received['args']
        self.assertIn('model_reasoning_effort="high"',args)
        schema=json.loads(Path(args[args.index('--output-schema')+1]).read_text())
        self.assertEqual(schema['required'],['tasks','reason'])
        self.assertEqual(schema['properties']['tasks']['maxItems'],3)
        variants=schema['properties']['tasks']['items']['anyOf']
        scout=next(v for v in variants if v['properties']['skill']['enum']==['scout'])
        self.assertEqual(scout['properties']['count']['enum'],[1])
        targets=scout['properties']['args']['properties']['targets']['anyOf']
        self.assertEqual(targets[0]['enum'],['expansion_sites'])
        self.assertNotIn('expansion_sites',targets[1]['items']['anyOf'][0]['enum'])
        self.assertNotIn('target',scout['properties']['args']['properties'])
        order=next(v for v in variants if v['properties']['skill']['enum']==['order_units'])
        self.assertEqual(order['properties']['count']['enum'],[1])
        self.assertIn('count',order['properties']['args']['required'])
        expand=next(v for v in variants if v['properties']['skill']['enum']==['expand'])
        self.assertIn('base',expand['properties']['args']['required'])
        macro=next(v for v in variants if v['properties']['skill']['enum']==['macro_plan'])
        self.assertIn('worker_target',macro['properties']['args']['required'])
        self.assertNotIn('uniqueItems',macro['properties']['args']['properties']['exempt_products'])
        def check(node):
            if isinstance(node,dict):
                if node.get('type')=='object':
                    self.assertEqual(set(node['required']),set(node['properties']))
                    self.assertIs(node['additionalProperties'],False)
                for value in node.values():check(value)
            elif isinstance(node,list):
                for value in node:check(value)
        check(schema)

    def test_review_transport_uses_review_contract(self):
        client=self.planner('review');client.response_kind='review'
        self.assertEqual(json.loads(client.chat('Review real errors.','{}'))['tasks'],[])
        received=json.loads((client.directory/'request-0001/received.json').read_text())
        self.assertIn('post-game reviewer',received['prompt'])
        self.assertIn('The tasks field must be []',received['prompt'])

    def test_review_rejects_accidental_battle_dispatch(self):
        client=self.planner();client.response_kind='review'
        with self.assertRaisesRegex(RuntimeError,'must not dispatch'):
            client.chat('Review.','{}')
        self.assertEqual(client.calls,0)

    def test_revoked_subscription_is_fatal_and_never_passes(self):
        client = self.planner('unauthorized')
        with self.assertRaisesRegex(RuntimeError, 'HTTP 401'):
            client.chat('system', '{}')
        self.assertEqual(client.calls, 0)
        self.assertEqual(client.attempts, 1)
        self.assertEqual(client.request_status()['state'], 'failed')
        self.assertIsNone(client.pending)

    def test_invalid_incomplete_or_tool_using_turn_is_rejected(self):
        for mode in ('malformed', 'incomplete', 'tool'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                with patch.dict(os.environ, {'TEST_CHATGPT_MODE': mode}):
                    client = ChatGPTSubscriptionPlanner('chatgpt/gpt-6-luna', folder)
                    try:
                        with self.assertRaises((ValueError, RuntimeError)):
                            client.chat('system', '{}')
                        self.assertEqual(client.calls, 0)
                    finally:
                        client.close()

    def test_timeout_terminates_process_and_preserves_failure(self):
        client = self.planner('slow')
        client.timeout = .1
        with self.assertRaises(subprocess.TimeoutExpired):
            client.chat('system', '{}')
        self.assertIsNone(client.pending)
        self.assertEqual(client.request_status()['state'], 'failed')
        self.assertEqual(client.calls, 0)
        client.close()
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            client.chat('system', '{}')


if __name__ == '__main__':
    unittest.main()
