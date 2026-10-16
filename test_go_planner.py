"""Provider switching preserves accounting and never leaks or substitutes credentials."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import sys
import unittest
from unittest.mock import Mock, patch

from arena.go_planner import (GO_MODEL, GO_ENDPOINT, GoPlanner, GoRequestError,
                              perform_request, transport_failure)
from arena.autonomous_review import repair_game
from arena.autonomous_bot import RequestBudget


class GoTests(unittest.TestCase):
    def test_explicit_pro_uses_same_go_endpoint_and_exact_requested_identity(self):
        request=self.request();request['requested_model']='opencode-go/deepseek-v4-pro'
        post=Mock(return_value=self.response(model='deepseek-v4-pro'))
        with patch.dict(os.environ,{'open_api_key':'test-secret'}):
            result=perform_request(request,post)
        self.assertEqual(post.call_args.args[0],GO_ENDPOINT+'/chat/completions')
        self.assertEqual(post.call_args.kwargs['json']['model'],'deepseek-v4-pro')
        self.assertEqual(result['metadata']['requested_model'],request['requested_model'])
        with patch.dict(os.environ,{'open_api_key':'test-secret'}):
            mismatch=perform_request(request,Mock(return_value=self.response()))
        self.assertIn('identity mismatch',mismatch['error'])

    def test_readiness_works_in_fresh_checkout_without_log_directory(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('cloud_go_status',
            Path(__file__).resolve().parent/'bin/cloud-go-status.py')
        status=importlib.util.module_from_spec(spec);spec.loader.exec_module(status)
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{},clear=True):
            with patch.object(status,'ROOT',Path(directory)),patch('builtins.print') as output:
                self.assertEqual(status.main(),2)
            result=json.loads(output.call_args.args[0])
            self.assertEqual(result['error_kind'],'credential_binding_missing')
            self.assertFalse(result['request_sent'])
            self.assertTrue(Path(result['evidence']).is_dir())

    def request(self):
        return dict(requested_model=GO_MODEL,system='JSON only',observation='{}',timeout=40)

    def response(self, **overrides):
        data=dict(model='deepseek-v4.1-flash',usage={'prompt_tokens':10},
            choices=[dict(finish_reason='stop',message=dict(content='{"tasks":[],"reason":"wait"}',
                reasoning_content='hidden reasoning'))])
        data.update(overrides)
        return SimpleNamespace(status_code=200,json=lambda:data)

    def test_exact_destination_and_public_answer_only(self):
        post=Mock(return_value=self.response())
        with patch.dict(os.environ,{'open_api_key':'test-secret-not-real'}):
            result=perform_request(self.request(),post)
        args,kw=post.call_args
        self.assertEqual(args,(GO_ENDPOINT+'/chat/completions',))
        self.assertEqual(kw['json']['model'],'deepseek-v4.1-flash')
        self.assertEqual(kw['json']['reasoning_effort'],'low')
        self.assertEqual(kw['headers']['Authorization'],'Bearer test-secret-not-real')
        self.assertNotIn('test-secret',json.dumps(result))
        self.assertNotIn('hidden reasoning',json.dumps(result))
        self.assertEqual(json.loads(result['answer'])['tasks'],[])
        self.assertEqual(result['metadata']['usage']['input_tokens'],10)

    def test_go_routing_requires_and_preserves_match_session(self):
        # Actual Go gateway rejected the imported client with HTTP 400 MissingSessionID.
        sessions=[]
        def route(url, **kwargs):
            session=kwargs['headers'].get('x-opencode-session')
            if not session:
                return SimpleNamespace(status_code=400)
            sessions.append(session)
            return self.response()
        request=self.request();request['session_id']='sc2-persistent-match'
        with patch.dict(os.environ,{'open_api_key':'test-secret'}):
            for _ in range(2):
                result=perform_request(request,route)
                self.assertIn('answer',result)
        self.assertEqual(sessions,['sc2-persistent-match']*2)

    def test_review_output_budget_does_not_change_default_battle_budget(self):
        post=Mock(return_value=self.response())
        with patch.dict(os.environ,{'open_api_key':'test-secret'}):
            perform_request(self.request(),post)
            self.assertEqual(post.call_args.kwargs['json']['max_tokens'],16000)
            request=self.request();request['max_tokens']=12000
            perform_request(request,post)
            self.assertEqual(post.call_args.kwargs['json']['max_tokens'],12000)

    def test_absent_binding_cannot_use_other_provider_key(self):
        post=Mock()
        with patch.dict(os.environ,{'OPENAI_API_KEY':'other-key'},clear=True):
            result=perform_request(self.request(),post)
        self.assertEqual(result['error_kind'],'credential_binding_missing')
        self.assertFalse(result['request_sent']);post.assert_not_called()

    def test_http_rejection_is_not_retried_and_body_is_not_logged(self):
        post=Mock(return_value=SimpleNamespace(status_code=429,text='echoed secret'))
        with patch.dict(os.environ,{'open_api_key':'test-secret'}):
            result=perform_request(self.request(),post)
        self.assertEqual(result['error_kind'],'transient_http')
        self.assertNotIn('quota',result['error']);self.assertEqual(post.call_count,1)
        self.assertNotIn('secret',json.dumps(result))

    def test_confirmed_quota_and_account_rejection_are_permanent(self):
        for code,body,kind in (
                (429,{'error':{'type':'insufficient_quota','message':'test-secret'}},'quota_exhausted'),
                (429,{'error':{'type':'rate_limit_error','message':'request quota per minute'}},'transient_http'),
                (503,{},'transient_http'),(401,{},'account_rejected')):
            post=Mock(return_value=SimpleNamespace(status_code=code,json=lambda:body))
            with patch.dict(os.environ,{'open_api_key':'test-secret'}):
                result=perform_request(self.request(),post)
            self.assertEqual(result['error_kind'],kind)
            self.assertNotIn('test-secret',json.dumps(result))
            self.assertEqual(post.call_count,1)

    def test_transport_error_exports_type_without_sensitive_message(self):
        import requests
        for error in (requests.exceptions.ReadTimeout('test-secret'),
                      requests.exceptions.ConnectionError('test-secret')):
            result=transport_failure(error)
            self.assertEqual(result['error_kind'],'transient_transport')
            self.assertNotIn('test-secret',json.dumps(result))

    def test_parent_worker_deadline_is_recorded_as_transient(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'open_api_key':'test-secret'}):
            planner=GoPlanner(GO_MODEL,directory)
            process=Mock()
            process.wait.side_effect=subprocess.TimeoutExpired('worker',63)
            with patch('arena.go_planner.subprocess.Popen',return_value=process), \
                 patch('arena.go_planner.ChatGPTSubscriptionPlanner._stop') as stop:
                with self.assertRaises(GoRequestError) as failure:planner.chat('x','{}')
                self.assertEqual(failure.exception.error_kind,'transient_transport')
                self.assertTrue(stop.called)
            record=json.loads((Path(directory)/'request-0001/result.json').read_text())
            self.assertEqual(record['error_kind'],'transient_transport')
            self.assertEqual(planner.attempts,1)
            self.assertEqual(planner.calls,0)
            self.assertEqual(planner.request_status()['state'],'failed')
            self.assertIsNone(planner.pending)

    def test_wrong_returned_model_is_rejected(self):
        with patch.dict(os.environ,{'open_api_key':'test-secret'}):
            result=perform_request(self.request(),Mock(return_value=self.response(model='expensive-other-model')))
        self.assertIn('identity mismatch',result['error']);self.assertNotIn('answer',result)

    def test_invalid_api_json_is_distinct_from_invalid_public_model_json(self):
        response=SimpleNamespace(status_code=200,json=Mock(side_effect=json.JSONDecodeError('test-secret','x',0)))
        with patch.dict(os.environ,{'open_api_key':'test-secret'}):
            api=perform_request(self.request(),Mock(return_value=response))
            model=perform_request(self.request(),Mock(return_value=self.response(
                choices=[dict(finish_reason='stop',message=dict(content='test-secret invalid public JSON',reasoning_content='private reasoning'))])))
        self.assertEqual(api['error_kind'],'transient_http')
        self.assertEqual(api['response_stage'],'api_envelope')
        self.assertEqual(model['error_kind'],'transient_model_output')
        self.assertEqual(model['response_stage'],'public_answer')
        self.assertEqual(model['metadata']['usage']['input_tokens'],10)
        for result in (api,model):
            self.assertNotIn('answer',result)
            self.assertNotIn('test-secret',json.dumps(result))
            self.assertNotIn('private reasoning',json.dumps(result))

    def test_missing_choices_is_a_sanitized_api_envelope_error(self):
        with patch.dict(os.environ,{'open_api_key':'test-secret'}):
            result=perform_request(self.request(),Mock(return_value=SimpleNamespace(status_code=200,json=lambda:dict(error='test-secret'))))
        self.assertEqual(result['error_kind'],'transient_http')
        self.assertEqual(result['response_stage'],'api_envelope')
        self.assertNotIn('test-secret',json.dumps(result))

    def test_truncated_answer_is_rejected(self):
        response=self.response(usage=dict(prompt_tokens=22640,completion_tokens=8000,
            completion_tokens_details=dict(reasoning_tokens=8000)),
            choices=[dict(finish_reason='length',message={'content':'','reasoning_content':'hidden reasoning'})])
        post=Mock(return_value=response)
        with patch.dict(os.environ,{'open_api_key':'test-secret'}):
            result=perform_request(self.request(),post)
        self.assertIn('incomplete',result['error'])
        self.assertEqual(result['metadata']['finish_reason'],'length')
        self.assertEqual(result['metadata']['usage']['completion_tokens_details']['reasoning_tokens'],8000)
        self.assertNotIn('answer',result);self.assertNotIn('hidden reasoning',json.dumps(result))
        post.assert_called_once()

    def test_unsupported_requested_model_never_contacts_api(self):
        request=self.request();request['requested_model']='opencode-go/gpt-6-luna';post=Mock()
        with self.assertRaises(ValueError):perform_request(request,post)
        post.assert_not_called()

    def test_go_repair_cannot_fall_back_to_chatgpt(self):
        budget=RequestBudget(2)
        with patch('arena.autonomous_review.ChatGPTSubscriptionPlanner',side_effect=AssertionError('fallback')):
            result=repair_game(Path('/tmp'),GO_MODEL,Path('/tmp/unused-go-repair'),budget,{})
        self.assertFalse(result['applied']);self.assertEqual(budget.used,0)

    def test_resume_switch_keeps_history_and_worker_uses_go(self):
        import autonomous_bot as runner
        parent=Path(runner.__file__).resolve().parent/'logs/autonomous-bot'
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            folder=Path(directory)
            old=dict(model='chatgpt/gpt-6-luna',limits=dict(max_matches=1,max_requests=24,
                max_game_seconds=1800,max_wall_seconds=1860,seed=1),
                budget=dict(limit=26,attempted=20,by_role={'battle':19,'review':1}),
                matches=[{'number':1,'model':'chatgpt/gpt-6-luna'}],next_difficulty='hard')
            (folder/'campaign.json').write_text(json.dumps(old))
            worker=SimpleNamespace(returncode=1,wait=lambda **kwargs:None)
            with patch('sys.argv',['autonomous_bot.py','--resume',str(folder),'--until-hard-win',
                                   '--switch-model',GO_MODEL]),patch.object(runner.subprocess,'Popen',return_value=worker) as spawn:
                self.assertEqual(runner.main(),2)
                self.assertEqual(spawn.call_args.args[0][0],sys.executable)
            state=json.loads((folder/'campaign.json').read_text())
            config=json.loads((folder/'worker-02.json').read_text())
            self.assertEqual(state['matches'],old['matches'])
            self.assertEqual(state['budget']['attempted'],20)
            self.assertEqual(state['model_changes'][0]['previous_attempted'],20)
            self.assertEqual(config['args']['model'],GO_MODEL)
            self.assertEqual(config['args']['max_game_seconds'],1800)
            (folder/'campaign.json').write_text(json.dumps(state))
            with patch('sys.argv',['autonomous_bot.py','--resume',str(folder),'--until-hard-win']), \
                 patch.object(runner.subprocess,'Popen',return_value=worker):
                runner.main()
            self.assertEqual(json.loads((folder/'worker-02.json').read_text())['args']['model'],GO_MODEL)

    def test_worker_reads_saved_model_instead_of_cli_default(self):
        import autonomous_bot as runner
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            config=folder/'worker.json'
            config.write_text(json.dumps(dict(folder=str(folder),difficulty='hard',strategy='test',number=23,
                args={'model':GO_MODEL},budget={'limit':26,'attempted':0,'by_role':{}})))
            with patch('sys.argv',['autonomous_bot.py','--worker-config',str(config)]), \
                 patch.object(runner,'run_match') as run:
                self.assertEqual(runner.main(),0)
            self.assertEqual(run.call_args.args[2],GO_MODEL)


if __name__=='__main__':unittest.main()
