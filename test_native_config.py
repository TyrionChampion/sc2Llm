import os
import unittest
from unittest.mock import patch
from arena.player_config import make_player

class NativeConfigTests(unittest.TestCase):
    def test_separate_keys_same_provider(self):
        env={'ARENA_PROVIDER':'deepseek','DEEPSEEK_API_KEY':'provider-test-key',
             'ARENA_P1_API_KEY':'player-one-test-key','ARENA_P2_API_KEY':'player-two-test-key'}
        with patch.dict(os.environ,env,clear=True):
            a,ai=make_player(0);b,bi=make_player(1)
            self.assertIsNot(a,b)
            self.assertEqual(a.p.api_key,'player-one-test-key')
            self.assertEqual(b.p.api_key,'player-two-test-key')
            self.assertNotEqual(ai['key_source'],bi['key_source'])
    def test_empty_override_is_error_not_provider_fallback(self):
        with patch.dict(os.environ,{'ARENA_PROVIDER':'deepseek','DEEPSEEK_API_KEY':'test',
                                    'ARENA_P1_API_KEY':''},clear=True):
            with self.assertRaisesRegex(ValueError,'empty'):make_player(0)
    def test_missing_key_does_not_become_mock(self):
        with patch.dict(os.environ,{'ARENA_PROVIDER':'deepseek'},clear=True):
            with self.assertRaisesRegex(ValueError,'missing'):make_player(0)
    def test_player_model_override_and_explicit_mock(self):
        with patch.dict(os.environ,{'ARENA_PROVIDER':'deepseek','DEEPSEEK_API_KEY':'test',
                                    'ARENA_P2_MODEL':'per-player-model'},clear=True):
            client,identity=make_player(1)
            self.assertEqual(client.model,'per-player-model')
            mock,info=make_player(0,'mock',12)
            self.assertEqual(info['provider'],'mock');self.assertEqual(mock.delay,12)
    def test_real_call_has_no_hidden_retries(self):
        with patch.dict(os.environ,{'ARENA_PROVIDER':'deepseek','DEEPSEEK_API_KEY':'test'},clear=True):
            client,_=make_player(0)
            with patch.object(client,'_post',side_effect=RuntimeError('network unavailable')) as post:
                with self.assertRaisesRegex(Exception,'network unavailable'):client.chat('system','state')
                self.assertEqual(post.call_count,1);self.assertEqual(client.errors,1)

if __name__=='__main__':unittest.main()
