"""Next-round delivery, retry and player isolation without model/network calls."""
import json,tempfile,threading,time,unittest
from pathlib import Path
from arena.operator_advice import OperatorAdvice,submit_advice,read_status,MAX_TEXT
from arena.commander import Commander
from arena.autonomous_bot import BotPlanner,RequestBudget
from arena.context_tables import decode_tables
import test_realtime_runtime as fixtures

class LocalPlanner:
 timeout=60;soft_timeout=12;compact_battle=False;closed=False
 def __init__(self,block=False,fail=False):
  self.calls=self.attempts=0;self.payloads=[];self.started=threading.Event();self.release=threading.Event();self.block=block;self.fail=fail;self.last_metadata={}
 def chat(self,system,user):
  self.attempts+=1;self.payloads.append(decode_tables(json.loads(user)));self.started.set()
  if self.block:self.release.wait(2)
  if self.fail:self.fail=False;raise RuntimeError('local simulated request failure')
  self.calls+=1;return json.dumps({'tasks':[],'reason':'已考虑建议，先守二矿'},ensure_ascii=False)
 def close(self):self.closed=True;self.release.set()

class AdviceTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
 def mailbox(self,player=1):
  return OperatorAdvice(Path(self.tmp.name)/f'p{player}.jsonl',player,lambda row:None)
 def commander(self,client,index=0):
  b=fixtures.Backend();b.homes=[(30,30),(160,160)];b.initial=[fixtures.world([]),fixtures.world([])]
  c=Commander(b,index,client,opening=False,interval=25,feedback_dir=self.tmp.name)
  self.addCleanup(c.stop);self.addCleanup(client.close);return c
 def wait(self,predicate):
  end=time.monotonic()+2
  while not predicate() and time.monotonic()<end:time.sleep(.005)
  self.assertTrue(predicate())
 def tick(self,c,seconds):
  c.operator_advice.last_poll=float('-inf')
  if isinstance(c.client,BotPlanner):c.client.policy={'game_s':seconds}
  c.tick(fixtures.world([],seconds))
 def test_arriving_during_thinking_goes_to_next_round_only(self):
  client=LocalPlanner(block=True);c=self.commander(client);self.tick(c,0);self.assertTrue(client.started.wait(1))
  m=submit_advice(c.operator_advice.directory,'先守二矿，不要进攻',player=1);self.tick(c,1)
  self.assertNotIn('operator_advice',client.payloads[0]);client.block=False;client.release.set();self.wait(lambda:not c.outbox.empty());self.tick(c,2)
  self.assertEqual(client.attempts,1);self.tick(c,25);self.wait(lambda:len(client.payloads)==2 and not c.outbox.empty());self.tick(c,26)
  self.assertEqual(client.payloads[1]['operator_advice']['messages'][0]['text'],m['text'])
  self.assertEqual(c.operator_advice.messages[0]['state'],'received');self.assertEqual(c.last_decision['reason'],'已考虑建议，先守二矿')
  self.tick(c,50);self.wait(lambda:len(client.payloads)==3);self.assertNotIn('operator_advice',client.payloads[2])
 def test_request_failure_keeps_the_same_message_for_retry(self):
  client=LocalPlanner(fail=True);c=self.commander(client);m=submit_advice(c.operator_advice.directory,'补充防空')
  self.tick(c,0);self.wait(lambda:not c.outbox.empty());self.tick(c,1);self.assertEqual(c.operator_advice.messages[0]['state'],'retry')
  self.tick(c,25);self.wait(lambda:not c.outbox.empty());self.tick(c,26)
  self.assertEqual([p['operator_advice']['messages'][0]['id'] for p in client.payloads],[m['id'],m['id']])
  self.assertEqual(c.operator_advice.messages[0]['state'],'received')
 def test_player_isolation_and_receiver_check(self):
  a,b=self.mailbox(),self.mailbox(2);m=submit_advice(a.directory,'P1专属建议',player=1);a.poll();b.poll()
  self.assertEqual(a.batch()['messages'][0]['id'],m['id']);self.assertEqual(b.batch()['messages'],[])
  with self.assertRaises(ValueError):submit_advice(b.directory,'不能错发',player=1)
 def test_go_bounded_compaction_receives_advice_without_extra_requests(self):
  local=LocalPlanner();local.model='opencode-go/deepseek-v4.1-flash';budget=RequestBudget(1)
  client=BotPlanner(local,budget,'自主选择战术',request_limit=1);c=self.commander(client)
  submit_advice(c.operator_advice.directory,'观察高地，考虑扫描获取视野')
  self.tick(c,0);self.wait(lambda:not c.outbox.empty());self.tick(c,1)
  self.assertIn('考虑扫描',local.payloads[0]['operator_advice']['messages'][0]['text']);self.assertEqual(budget.used,1)
  self.assertEqual(c.operator_advice.messages[0]['state'],'received')
  with self.assertRaisesRegex(ValueError,'请求已用完'):submit_advice(c.operator_advice.directory,'不能突破上限')
 def test_long_queue_deferred_without_truncation_and_stop_disables_send(self):
  a=self.mailbox();texts=['甲'*1000,'乙'*1000]
  for text in texts:submit_advice(a.directory,text)
  a.poll();batch=a.batch();self.assertEqual(len(batch['messages']),1);self.assertEqual(batch['messages'][0]['text'],texts[0]);self.assertEqual(batch['deferred_count'],1)
  a.close();self.assertFalse(read_status(a.directory)['accepting'])
  with self.assertRaises(ValueError):submit_advice(a.directory,'结束后不能发')
 def test_invalid_input_does_not_enqueue_anything(self):
  a=self.mailbox()
  for value in (' ','x'*(MAX_TEXT+1),None):
   with self.assertRaises(ValueError):submit_advice(a.directory,value)
  self.assertEqual(list((a.directory/'inbox').glob('*.json')),[])
if __name__=='__main__':unittest.main()
