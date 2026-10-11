import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from arena.external_planner import ExternalPlanner, submit, atomic_json
from arena.commander import Commander
from test_realtime_runtime import Backend, world, unit, CC

class ExternalPlannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.client=ExternalPlanner(self.root/'mailbox',timeout=1)
    def tearDown(self):
        self.client.close();self.tmp.cleanup()
    def launch(self):
        self.result={}
        def run():
            try:self.result['value']=self.client.chat('system',json.dumps({'world':{'game_s':0,'own':{'SCV':8}}}))
            except Exception as e:self.result['error']=e
        self.thread=threading.Thread(target=run);self.thread.start()
        until=time.monotonic()+1
        while not (self.client.directory/'current.json').exists() and time.monotonic()<until:time.sleep(.005)
        return json.loads((self.client.directory/'current.json').read_text())
    def test_matching_reply_and_duplicate_rejection(self):
        r=self.launch();plan={'tasks':[],'reason':'等待'}
        submit(self.client.directory,r['request_id'],plan)
        with self.assertRaises((ValueError,FileExistsError)):submit(self.client.directory,r['request_id'],plan)
        self.thread.join(2)
        self.assertEqual(json.loads(self.result['value']),plan)
        self.assertEqual(self.client.calls,1)
    def test_wrong_session_cannot_inject_plan(self):
        r=self.launch()
        with self.assertRaises(ValueError):submit(self.client.directory,'other-session',{'tasks':[]})
        atomic_json(self.client.directory/(r['request_id']+'.response.json'),{'request_id':'wrong','plan':{'tasks':[]}})
        self.thread.join(2);self.assertIsInstance(self.result['error'],ValueError)
        self.assertTrue((self.client.directory/(r['request_id']+'.error.json')).exists())
    def test_timeout_is_visible_no_plan(self):
        self.client.timeout=.08;r=self.launch();self.thread.join(2)
        self.assertIsInstance(self.result['error'],TimeoutError)
        self.assertEqual(self.client.calls,0)
        with self.assertRaises(ValueError):submit(self.client.directory,r['request_id'],{'tasks':[]})
    def test_close_interrupts_wait(self):
        self.launch();self.client.close();self.thread.join(1)
        self.assertFalse(self.thread.is_alive());self.assertIsInstance(self.result['error'],RuntimeError)
        self.assertEqual(json.loads((self.client.directory/'status.json').read_text())['state'],'closed')
    def test_commander_rejects_invalid_plan_then_accepts_fresh_reply(self):
        b=Backend();b.initial=[world([unit(1,CC,is_building=True)])]
        c=Commander(b,0,self.client,opening=False,feedback_dir=self.root/'feedback',interval=0)
        try:
            for plan,valid in [({'tasks':[{'skill':'illegal'}]},False),({'tasks':[{'skill':'train_worker','count':2}],'reason':'补充经济'},True)]:
                c.tick(b.initial[0]);until=time.monotonic()+1
                while self.client.state!='waiting' and time.monotonic()<until:time.sleep(.005)
                r=json.loads((self.client.directory/'current.json').read_text())
                submit(self.client.directory,r['request_id'],plan)
                while c.outbox.empty() and time.monotonic()<until:time.sleep(.005)
                c.tick(b.initial[0])
                self.assertEqual(c.decisions,int(valid))
            self.assertEqual(c.errors,1)
        finally:self.client.close();c.stop()

class OfficialFlashTests(unittest.TestCase):
    def client(self,response):
        from arena.deepseek_planner import DeepSeekFlashPlanner
        c=DeepSeekFlashPlanner();c._post=lambda *args:response
        return c
    def test_public_content_only(self):
        r={'model':'deepseek-flash','choices':[{'finish_reason':'stop','message':{'content':'{"tasks":[]}','reasoning_content':'private'}}]}
        c=self.client(r);self.assertEqual(json.loads(c._call_openai('system','user')),dict(tasks=[]))
        self.assertNotIn('private',str(c.last_metadata))
    def test_incomplete_reply_rejected(self):
        r={'model':'deepseek-flash','choices':[{'finish_reason':'length','message':{'content':'{"tasks":[]}'}}]}
        with self.assertRaisesRegex(Exception,'Incomplete'):self.client(r)._call_openai('system','user')
    def test_no_model_substitution(self):
        r={'model':'wrong-model','choices':[{'finish_reason':'stop','message':{'content':'{"tasks":[]}'}}]}
        with self.assertRaisesRegex(Exception,'identity mismatch'):self.client(r)._call_openai('system','user')

if __name__=='__main__':unittest.main()

