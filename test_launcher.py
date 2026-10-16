"""Launcher checks use local fake planners/engine; no model calls or SC2 game."""
import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from arena.launch_config import OFFICIAL, ROOT, model_catalog, validate_config
from arena.lifecycle import write_json_atomic
from arena_launcher import MatchManager, LauncherServer, process_birth, safe_text
import arena.launch_match as worker


def configuration():
    return dict(players=[dict(model=OFFICIAL, race='terran'), dict(model=OFFICIAL, race='zerg')],
        speed='slower', minutes=90, reasoning='high', tactics=False, live_skills=True, combat_micro=True)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'fake-local-key', 'open_api_key': ''}); self.env.start()
    def tearDown(self): self.env.stop(); self.temp.cleanup()

    def test_catalog_never_returns_auth(self):
        for p in ('.local/share/opencode', '.cache/opencode'): (self.root / p).mkdir(parents=True)
        (self.root / '.local/share/opencode/auth.json').write_text(json.dumps({'opencode-go': {'key':'auth-must-not-escape'}}))
        (self.root / '.cache/opencode/models.json').write_text(json.dumps({'opencode-go': {'models': {
            'kimi-k3': {'name':'Kimi K3','tool_call':True}, 'old': {'tool_call':True,'status':'deprecated'}}}}))
        result = model_catalog(self.root)
        self.assertEqual([m['id'] for m in result['models']], [OFFICIAL, 'opencode-go/kimi-k3'])
        self.assertNotIn('auth-must-not-escape', json.dumps(result))
        self.assertNotIn('fake-local-key', json.dumps(result))

    def test_missing_catalog_is_visible(self):
        self.assertIn('不可读取', model_catalog(self.root)['notices'][0])

    def test_configuration_rejects_unknown_and_unavailable(self):
        c = configuration(); self.assertEqual(validate_config(c), c)
        for field, value in [('speed','turbo'), ('minutes',True), ('minutes',0), ('tactics','yes')]:
            bad=copy.deepcopy(c); bad[field]=value
            with self.assertRaises(ValueError): validate_config(bad)
        bad=copy.deepcopy(c); bad['players'][0]['model']='shell/anything'
        with self.assertRaises(ValueError): validate_config(bad)
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY':''}):
            with self.assertRaises(ValueError): validate_config(c)

    def test_mixed_provider_and_race_are_preserved(self):
        c=configuration();c['players'][1]=dict(model='opencode-go/kimi-k3',race='protoss')
        catalog={'models':[dict(id=OFFICIAL,available=True),dict(id='opencode-go/kimi-k3',available=True)]}
        self.assertEqual(validate_config(c,catalog),c)
        c['tactics']=True
        with self.assertRaisesRegex(ValueError,'副官'): validate_config(c,catalog)

    def test_two_agent_commander_requires_high_reasoning(self):
        c=configuration();c['tactics']=True
        self.assertEqual(validate_config(c),c)
        c['reasoning']='low'
        self.assertEqual(validate_config(c),c)

    def test_process_lifecycle_and_restart_recovery(self):
        commands=[]
        def spawn(command,**kw):
            commands.append(command)
            return subprocess.Popen([sys.executable,'-c',
                'import time; time.sleep(30)'],**kw)
        manager=MatchManager(self.root,spawn=spawn,busy=lambda:None)
        try:
            status=manager.start(configuration())
            self.assertTrue(status['active'])
            self.assertEqual(commands[0][0],str(ROOT/'run_sc2.sh'))
            run=Path(status['run'])
            self.assertEqual(json.loads((run/'config.json').read_text()),configuration())
            with self.assertRaisesRegex(ValueError,'重复'):manager.start(configuration())
            restored=MatchManager(self.root,busy=lambda:None)
            self.assertTrue(restored.alive())
            # Allow wrapped test interpreter to install its default SIGINT handler.
            time.sleep(.2)
            restored.stop(); manager.child.wait(timeout=5)
            self.assertFalse(restored.alive())
            self.assertEqual(restored.status()['state'],'lost')
        finally:
            if manager.child and manager.child.poll() is None:
                manager.child.kill();manager.child.wait(timeout=5)

    def test_external_match_blocks_start(self):
        manager=MatchManager(self.root,busy=lambda:'外部对局仍在运行')
        with self.assertRaisesRegex(ValueError,'外部对局'):manager.start(configuration())
        self.assertEqual(manager.status()['state'],'idle')

    def test_exit_and_stale_are_not_reported_running(self):
        run=self.root/'match-test';run.mkdir()
        manager=MatchManager(self.root,busy=lambda:None)
        manager.current=dict(run=str(run),pid=os.getpid(),birth=process_birth(os.getpid()),config=configuration())
        write_json_atomic(run/'run.json',dict(status='starting_native_game'))
        write_json_atomic(run/'match.json',dict(outcome='running',samples=[]))
        os.utime(run/'match.json',(time.time()-60,time.time()-60))
        self.assertIn('失联',manager.status()['label'])
        manager.current['birth']='different-birth'
        self.assertEqual(manager.status()['state'],'lost')
        with self.assertRaises(ValueError):manager.stop()

    def test_redaction(self): self.assertNotIn('fake-local-key',safe_text('HTTP error fake-local-key'))

    def test_process_creation_failure_is_visible(self):
        def fail(*a,**kw):raise OSError('local launch denied')
        manager=MatchManager(self.root,spawn=fail,busy=lambda:None)
        with self.assertRaisesRegex(OSError,'launch denied'):manager.start(configuration())
        report=json.loads(next(self.root.glob('match-*/run.json')).read_text())
        self.assertEqual(report['status'],'failed');self.assertIn('denied',report['error'])

    def test_skill_preparation_failure_stops_before_game(self):
        write_json_atomic(self.root/'config.json',configuration())
        with patch.object(worker,'prepare_skills',side_effect=ValueError('library hash mismatch')), \
             patch.object(worker.subprocess,'Popen') as launch:
            with self.assertRaisesRegex(ValueError,'hash mismatch'):worker.main(['--run',str(self.root)])
        launch.assert_not_called()
        report=json.loads((self.root/'run.json').read_text())
        self.assertEqual(report['status'],'failed');self.assertIn('hash mismatch',report['error'])

    def test_http_mutation_auth_and_actual_payload(self):
        class Manager:
            received=None
            def status(self):return dict(active=False,state='idle')
            def start(self,body):self.received=body;return dict(active=True)
        manager=Manager();server=LauncherServer(('127.0.0.1',0),manager)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url='http://127.0.0.1:%d'%server.server_port
        try:
            page=urllib.request.urlopen(url).read().decode()
            self.assertIn(server.token,page);self.assertNotIn('__TOKEN__',page)
            body=json.dumps(configuration()).encode()
            req=urllib.request.Request(url+'/api/start',data=body,headers={'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as result:urllib.request.urlopen(req)
            self.assertEqual(result.exception.code,403)
            req.add_header('X-Arena-Token',server.token);req.add_header('Origin',url)
            self.assertTrue(json.load(urllib.request.urlopen(req))['active'])
            self.assertEqual(manager.received,configuration())
            req.add_header('Origin','https://untrusted.example')
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(req)
        finally:server.shutdown();server.server_close();thread.join(timeout=2)

    def test_worker_hands_mixed_clients_and_config_to_native(self):
        c=configuration();c['players'][1]=dict(model='opencode-go/kimi-k3',race='protoss');c['live_skills']=False
        write_json_atomic(self.root/'config.json',c)
        class Planner:
            calls=0;model='deepseek-flash'
            p=type('Provider',(),dict(key_env='DEEPSEEK_API_KEY',base_url='https://api.deepseek.com/v1'))()
            def __init__(self,*a,**kw):pass
            def identity(self):return dict(side='P2',provider='opencode-go',model='kimi-k3',key_source='existing OpenCode login')
            def close(self):pass
        class Server:
            def __init__(self,*a,**kw):pass
            def start(self):pass
            def close(self):pass
        class Viewer:
            pid=123
            def poll(self):return None
        with patch.object(worker,'validate_config',return_value=c), \
             patch.object(worker,'prepare_skills',side_effect=lambda r,i,p:(None,'strategy',dict(player=i+1,live_restored=[]))), \
             patch('arena.deepseek_planner.DeepSeekFlashPlanner',Planner), \
             patch('arena.opencode_bridge.OpenCodeServer',Server), \
             patch('arena.opencode_bridge.OpenCodePlanner',Planner), \
             patch.object(worker.subprocess,'Popen',return_value=Viewer()), \
             patch.object(worker.time,'sleep'), patch('native_llm.main') as native:
            worker.main(['--run',str(self.root)])
        options=native.call_args.kwargs
        self.assertEqual([i['race'] for _,i in options['players_override']],['terran','protoss'])
        self.assertEqual(options['players_override'][1][0].phase,'battle')
        self.assertEqual(options['argv'][options['argv'].index('--seconds')+1],'5400')
        self.assertIn('--combat-micro',options['argv']);self.assertNotIn('--tactics',options['argv'])
        self.assertEqual(json.loads((self.root/'run.json').read_text())['status'],'finished')


if __name__=='__main__':unittest.main()
