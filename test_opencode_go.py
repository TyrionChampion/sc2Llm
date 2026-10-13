import json
import os
import unittest
from unittest.mock import patch

from arena.llm import http_error, LLMError
from arena.opencode_go import OpenCodeGoPlanner
from arena.player_config import make_player


class OpenCodeGoTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'open_api_key': 'test-key'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def reply(self, **changes):
        result = {'model': 'deepseek-v4.1-flash', 'choices': [{'finish_reason': 'stop',
                  'message': {'content': '{"orders":[],"reason":"守备"}',
                              'reasoning_content': 'private reasoning'}}],
                  'usage': {'prompt_tokens': 12, 'completion_tokens': 8,
                            'prompt_tokens_details': {'cached_tokens': 4}}}
        result.update(changes)
        return result

    def test_subscription_endpoint_session_and_public_output(self):
        client = OpenCodeGoPlanner(battle=True, session_id='match-test')
        with patch.object(client, '_post', return_value=self.reply()) as post:
            answer = client.chat('system', 'state')
        url, payload, headers = post.call_args.args
        self.assertEqual(url, 'https://opencode.ai/zen/go/v1/chat/completions')
        self.assertEqual(headers['x-opencode-session'], 'match-test')
        self.assertEqual(payload['thinking'], {'type': 'disabled'})
        self.assertEqual(payload['max_tokens'], 1200)
        self.assertNotIn('private reasoning', answer)
        self.assertNotIn('private reasoning', json.dumps(client.last_metadata))
        self.assertEqual(client.last_usage['cached_tokens'], 4)

    def test_refuses_wrong_identity_truncated_empty_and_invalid_json(self):
        client = OpenCodeGoPlanner()
        cases = [self.reply(model='different-model'), self.reply(choices=[])]
        for finish, content in [('length','{}'), ('stop',''), ('stop','not json'), ('stop','[]')]:
            cases.append(self.reply(choices=[{'finish_reason':finish,'message':{'content':content}}]))
        for result in cases:
            with self.subTest(result=result), patch.object(client, '_post', return_value=result):
                with self.assertRaises(LLMError):client.chat('system','state')
        self.assertEqual(client.calls, 0)

    def test_account_failure_stops_without_retry_or_fallback(self):
        client = OpenCodeGoPlanner()
        with patch.object(client, '_post', side_effect=http_error(402,'subscription exhausted')) as post:
            for _ in range(2):
                with self.assertRaises(LLMError):client.chat('system','state')
        self.assertEqual(post.call_count,1)
        self.assertEqual(client.fatal_status,402)

    def test_player_configuration_preserves_explicit_credentials_and_model(self):
        with patch.dict(os.environ, {'ARENA_P1_API_KEY':'separate-test-key',
                                    'ARENA_P1_MODEL':'deepseek-v4-pro'}):
            client, identity = make_player(0,'opencode_go')
        self.assertIsInstance(client,OpenCodeGoPlanner)
        self.assertEqual(client.p.key_env,'ARENA_P1_API_KEY')
        self.assertEqual(identity['model'],'deepseek-v4-pro')


if __name__ == '__main__':unittest.main()
