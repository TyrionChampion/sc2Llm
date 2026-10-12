"""Guard live evidence parsing, exact model selection, and failure reporting."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from arena.scenario_runtime import DEFAULT_MODEL, acceptance, read_exchanges, report_exit_code, scenario_planner


class ScenarioRuntimeTests(unittest.TestCase):
    def read(self, response, error=None):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'feedback.jsonl'
            rows = [dict(record='request_queued', request_id=1, prompt=dict(
                defense_report=[dict(label='home', visible_attackers=3, urgency='urgent')])),
                dict(record='request_finished', request_id=1, response=response, error=error)]
            path.write_text('\n'.join(json.dumps(row) for row in rows))
            return read_exchanges(path)

    def test_spaced_structured_output_preserves_defence_orders(self):
        order = dict(skill='defend_base', args=dict(base='home', count=3))
        exchanges = self.read(json.dumps(dict(tasks=[order], reason='调远处部队救主矿')))
        self.assertEqual(exchanges[0]['defend_orders'], [order])
        self.assertEqual(acceptance(exchanges, 'defend')['verdict'], 'pass')

    def test_no_response_and_provider_errors_cannot_pass(self):
        for response, error in [(None, None), ('not JSON', None), ('{"tasks": []}', 'HTTP 401')]:
            with self.subTest(response=response):
                result = acceptance(self.read(response, error), 'defend')
                self.assertEqual(result['verdict'], 'inconclusive')
                self.assertEqual(report_exit_code(dict(acceptance=result)), 2)

    def test_no_defence_is_failure_unless_abandonment_is_explicit(self):
        result = acceptance(self.read('{"tasks": [], "reason": "等待"}'), 'defend')
        self.assertEqual(result['verdict'], 'fail')
        self.assertEqual(report_exit_code(dict(acceptance=result)), 1)
        exchanges = self.read('{"tasks": [], "reason": "兵力不足，放弃分矿"}')
        self.assertEqual(acceptance(exchanges, 'defend')['verdict'], 'pass')

    def test_defence_after_third_attacked_turn_is_too_late(self):
        economy = self.read('{"tasks": [{"skill": "train_worker"}], "reason": "经济"}')[0]
        defence = self.read('{"tasks": [{"skill": "defend_base"}], "reason": "救主矿"}')[0]
        result = acceptance([economy] * 3 + [defence], 'defend')
        self.assertEqual(result['first_defence_turn'], 4)
        self.assertEqual(result['verdict'], 'fail')

    def test_concurrent_request_ids_do_not_use_another_observation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'feedback.jsonl'
            path.write_text('\n'.join(json.dumps(row) for row in [
                dict(record='request_queued', request_id=1, prompt={'world': {'own': {'Marine': 6}}}),
                dict(record='request_queued', request_id=2, prompt={'world': {'own': {'Marine': 2}}}),
                dict(record='request_finished', request_id=1, response='{"tasks": []}')]))
            self.assertEqual(read_exchanges(path)[0]['own'], {'Marine': 6})

    def test_default_uses_official_subscription_planner_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as folder, patch('arena.chatgpt_planner.ChatGPTSubscriptionPlanner') as factory:
            with scenario_planner(DEFAULT_MODEL, folder) as client:
                self.assertIs(client, factory.return_value)
            self.assertEqual(factory.call_args.args[0], 'chatgpt/gpt-6-luna')
            factory.return_value.close.assert_called_once()

    def test_login_failure_propagates_without_provider_fallback(self):
        with tempfile.TemporaryDirectory() as folder, patch('arena.chatgpt_planner.ChatGPTSubscriptionPlanner', side_effect=RuntimeError('HTTP 401: login required')):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 401'):
                with scenario_planner(DEFAULT_MODEL, folder):
                    self.fail('must not fall back')
        with self.assertRaisesRegex(ValueError, 'Unsupported scenario model'):
            with scenario_planner('openai/gpt-6-luna', '/tmp'):
                self.fail('must not use OpenCode')

    def test_runtime_error_overrides_apparent_pass(self):
        self.assertEqual(report_exit_code(dict(error='game not started', acceptance={'verdict': 'pass'})), 2)



if __name__ == '__main__':
    unittest.main()
