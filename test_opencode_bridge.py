"""OpenCode adapter and preparation fixtures; real transport verified separately."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from arena.opencode_bridge import response_text,OpenCodePlanner
from arena.opencode_prepare import write_bundle,smoke_catalog

MODEL='opencode-go/glm-5.3-flash'
def reply(text='{"tasks":[]}'):
    return dict(info=dict(providerID='opencode-go',modelID='glm-5.3-flash',finish='stop'),parts=[dict(type='reasoning',text='not answer'),dict(type='text',text=text)])
class BridgeTests(unittest.TestCase):
    def test_only_body_with_exact_identity_and_normal_completion(self):
        self.assertEqual(response_text(reply(),MODEL),'{"tasks":[]}')
        for variant in ('model','error','finish','no_text'):
            value=reply()
            if variant=='model':value['info']['modelID']='different'
            if variant=='error':value['info']['error']={'message':'Global regions required'}
            if variant=='finish':value['info']['finish']='length'
            if variant=='no_text':value['parts']=value['parts'][:1]
            with self.subTest(variant=variant),self.assertRaises(RuntimeError):response_text(value,MODEL)
    def test_structured_output_requires_exact_identity_and_never_falls_back_to_text(self):
        value=reply('not a plan');value['info'].update(structured={'tasks':[{'skill':'train_worker','count':2}]},finish='tool-calls')
        self.assertEqual(json.loads(response_text(value,MODEL,structured=True))['tasks'][0]['count'],2)
        value['info']['modelID']='wrong'
        with self.assertRaises(RuntimeError):response_text(value,MODEL,structured=True)
        value=reply('{"tasks":[]}')
        with self.assertRaisesRegex(RuntimeError,'no plain-text fallback'):response_text(value,MODEL,structured=True)
        value['info'].update(structured={'tasks':[]},finish='length')
        with self.assertRaisesRegex(RuntimeError,'finish'):response_text(value,MODEL,structured=True)
    def test_upstream_retry_aborted_with_original_reason(self):
        import threading
        done=threading.Event()
        class Server:
            def request(self,method,path,**kw):
                if path=='/session':return {'id':'ses_fixture'}
                if path.endswith('/abort'):done.set();return True
                if path=='/session/status':return {'ses_fixture':{'type':'retry','message':'Global regions required'}}
                done.wait(3);return reply()
        with tempfile.TemporaryDirectory() as folder:
            c=OpenCodePlanner(Server(),MODEL,0,folder)
            with self.assertRaisesRegex(RuntimeError,'Global regions required'):c.chat('system','user')
            self.assertTrue(done.is_set());self.assertEqual(c.calls,0)
            self.assertIn('abort_acknowledged',Path(c.audit_path).read_text());c.close()
    def test_status_poll_timeout_does_not_abort_successful_original_request(self):
        import threading,requests
        done=threading.Event()
        class Server:
            polls=0
            def request(self,method,path,**kw):
                if path=='/session':return {'id':'ses_fixture'}
                if path.endswith('/abort'):raise AssertionError('must not abort merely delayed status')
                if path=='/session/status':
                    self.polls+=1
                    if self.polls==1:raise requests.ReadTimeout('local status delayed')
                    done.set();return {}
                done.wait(3);return reply()
        with tempfile.TemporaryDirectory() as folder:
            c=OpenCodePlanner(Server(),MODEL,0,folder)
            self.assertEqual(c.chat('system','user'),'{'+'"tasks":[]}')
            self.assertIn('status_poll_delayed',Path(c.audit_path).read_text());self.assertFalse(c.closed);c.close()
    def test_qwen_max_declared_low_variant_sent_in_preparation_and_battle(self):
        class Server:
            bodies=[]
            def request(self,method,path,**kw):
                if path=='/session':return {'id':'variant_fixture'}
                if path=='/session/status':return {}
                if path.endswith('/abort'):return True
                body=kw['json'];self.bodies.append(body)
                return dict(info=dict(providerID='opencode-go',modelID='qwen3.8-max',finish='stop',structured={'tasks':[],'reason':'等待'}),parts=[dict(type='text',text='{"tasks":[]}')])
        with tempfile.TemporaryDirectory() as folder:
            server=Server();client=OpenCodePlanner(server,'opencode-go/qwen3.8-max',0,folder)
            try:
                client.chat('system','user');client.phase='battle';client.chat('system','{}')
                self.assertEqual([b['variant'] for b in server.bodies],['low','low'])
                self.assertNotIn('format',server.bodies[0]);self.assertIn('format',server.bodies[1])
            finally:client.close()
    def test_preparation_files_reject_path_escape_before_write(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'out'
            raw=json.dumps(dict(strategy='x',files=[dict(name='safe.py',content='pass'),dict(name='../escape.json',content='{}')]))
            with self.assertRaises(ValueError):write_bundle(raw,target)
            self.assertFalse(target.exists())
    def test_native_entry_uses_both_opencode_clients_without_provider_fallback(self):
        import time
        from dataclasses import replace
        import native_llm
        from test_realtime_runtime import Backend,unit,world
        class Server:
            count=0
            def request(self,method,path,**kw):
                if path=='/session':self.count+=1;return {'id':'ses_'+str(self.count)}
                if path=='/session/status':return {}
                if path.endswith('/abort'):return True
                body=kw['json'];value=reply('{"tasks":[{"skill":"no_op"}]}')
                value['info'].update(body['model']);return value
        class Engine(Backend):
            homes=[(30,30),(160,160)];version='fixture';directory=Path('logs');start_location_report=[];action_errors=0;surrender_report=None
            def __init__(self):self.initial=[world([unit(1,18,is_building=True)]),world([unit(2,18,160,160,is_building=True)])];self.n=0;self.closed=False
            @property
            def ended(self):return self.n>=40
            def step(self,batches):
                time.sleep(.005);self.n+=1
                return [replace(w,game_loop=self.n*2) for w in self.initial]
            def timing(self):return dict(speed_check='warming_up',game_wall_ratio=1)
            def result(self):return {1:'Tie',2:'Tie'}
            def save_replay(self,path):Path(path).write_bytes(b'fixture')
            def close(self):self.closed=True
        with tempfile.TemporaryDirectory() as folder:
            server=Server();clients=[OpenCodePlanner(server,m,i,Path(folder)/str(i)) for i,m in enumerate(('opencode-go/deepseek-v4.1-flash',MODEL))]
            backend=Engine();output=Path(folder)/'match.json'
            try:
                with patch.object(native_llm,'NativeArena',return_value=backend),patch.object(native_llm,'make_player',side_effect=AssertionError('fallback forbidden')):
                    native_llm.main(players_override=[(c,c.identity()) for c in clients],catalogs_override=[None,None],
                        argv=['--no-overlay','--seconds','2','--output',str(output)],opening=False)
                report=json.loads(output.read_text())
                self.assertTrue(all(c['decisions']>=1 for c in report['commanders']))
                self.assertEqual([i['model'] for i in report['identities']],['deepseek-v4.1-flash','glm-5.3-flash'])
                self.assertTrue(backend.closed)
            finally:
                for c in clients:c.close()
    def test_authored_bundle_runs_real_worker_smoke(self):
        source='def tick(ctx,state):\n    return {"actions":[{"action":"move","unit_tags":[u["tag"] for u in ctx["units"]],"target":"objective"}],"state":{},"status":"running","reason":"fixture"}'
        manifest=dict(name='fixture_move',description='Fixture',file='fixture_move.py',unit='marine',actions=['move'],parameters={})
        raw=json.dumps(dict(strategy='fixture',files=[dict(name='fixture_move.py',content=source),dict(name='fixture_move.json',content=json.dumps(manifest))]))
        with tempfile.TemporaryDirectory() as folder:
            catalog,strategy=write_bundle(raw,Path(folder)/'attempt')
            result=smoke_catalog(catalog)
            self.assertEqual([r['fixture'] for r in result],['quiet','enemy_near','low_health'])

if __name__=='__main__':unittest.main()
