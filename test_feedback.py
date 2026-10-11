"""Feedback contract fixtures; native/API evidence lives in verify_feedback_live.py."""
import json
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from s2clientprotocol import error_pb2
from arena.feedback import FeedbackJournal, describe_rejections
from arena.commander import Commander
from arena.tasks import PlanValidationError, TaskQueue
from arena.raw_engine import *
from test_realtime_runtime import Backend, unit, world


class CaptureClient:
    def __init__(self, responses): self.responses=iter(responses); self.prompts=[]
    def chat(self,system,user):
        self.prompts.append(json.loads(user)); response=next(self.responses)
        if isinstance(response,Exception): raise response
        return response


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
    def commander(self,responses):
        b=Backend();b.initial=[world([unit(1,CC,is_building=True)])]
        client=CaptureClient(responses)
        c=Commander(b,0,client,opening=False,feedback_dir=self.tmp.name)
        self.addCleanup(c.stop)
        return c,client,b.initial[0]
    def wait(self,c):
        end=time.monotonic()+2
        while c.outbox.empty() and time.monotonic()<end:time.sleep(.002)
        self.assertFalse(c.outbox.empty())
    def test_pagination_never_drops_deferred_events(self):
        j=FeedbackJournal(1,self.tmp.name)
        for n in range(60):j.add('error',n,detail=str(n))
        seen=[]
        while j.pending:
            batch=j.batch(max_events=7,max_chars=700)
            ids=[e['id'] for e in batch['events']];self.assertTrue(ids)
            seen.extend(ids);j.acknowledge(ids,1)
        self.assertEqual(seen,list(range(1,61)))
        records=[json.loads(l) for l in Path(j.path).read_text().splitlines()]
        self.assertEqual(sum(r['record']=='feedback_event' for r in records),60)
    def test_network_failure_retains_feedback_and_reports_failure(self):
        c,client,w=self.commander([RuntimeError('network unavailable'),'{"tasks":[]}'])
        old=c.record_feedback('engine_action_rejected',result_name='Error')
        c.tick(w);self.wait(c);c.tick(replace(w,game_loop=150));self.wait(c)
        ids=[e['id'] for e in client.prompts[1]['feedback']['events']]
        self.assertIn(old['id'],ids)
        self.assertTrue(any(e['kind']=='model_request_failed' for e in client.prompts[1]['feedback']['events']))
        c.tick(replace(w,game_loop=151))
        self.assertNotIn(old['id'],[e['id'] for e in c.feedback.pending])
    def test_acknowledge_only_events_in_that_request(self):
        c,client,w=self.commander(['{"tasks":[]}'])
        old=c.record_feedback('old_error')
        c.tick(w);new=c.record_feedback('new_error');self.wait(c)
        c.tick(replace(w,game_loop=1))
        ids=[e['id'] for e in c.feedback.pending]
        self.assertNotIn(old['id'],ids);self.assertIn(new['id'],ids)
    def test_validation_feedback_survives_noisy_reflex_logs(self):
        bad={'tasks':[{'skill':'supply_depot','count':2},
                      {'skill':'attack_nearest','args':{'unit':'Marine'},'count':18}]}
        c,client,w=self.commander([json.dumps(bad),'{"tasks":[]}'])
        c.tick(w);self.wait(c);c.tick(replace(w,game_loop=1))
        for n in range(150):c.emit('[reflex] noise '+str(n))
        c.tick(replace(w,game_loop=150));self.wait(c)
        events=client.prompts[1]['feedback']['events']
        rejected=next(e for e in events if e['kind']=='plan_rejected')
        self.assertEqual(rejected['plan'],bad)
        self.assertEqual(len(rejected['issues']),3)
        self.assertFalse(c.tasks.tasks)
        self.assertTrue(all('planner-error' not in x for x in client.prompts[1]['recent_events']))
    def test_invalid_json_and_stale_response_are_feedback(self):
        c,client,w=self.commander(['not json','{"tasks":[]}'])
        c.tick(w);self.wait(c);c.tick(replace(w,game_loop=1))
        self.assertTrue(any(e['kind']=='model_response_invalid' and e['response']=='not json' for e in c.feedback.pending))
        c.outbox.put((0,{'tasks':[]},None,40,True,'{"tasks":[]}',99,[]))
        c.last_request=40;c.tick(replace(w,game_loop=896))
        self.assertTrue(any(e['kind']=='plan_expired' for e in c.feedback.pending))
    def test_failed_tasks_remain_in_feedback_after_leaving_active_queue(self):
        c,client,w=self.commander(['{"tasks":[]}'])
        c.tasks.enqueue([{'skill':'barracks'}],0)
        c.tick(replace(w,game_loop=10000));self.wait(c)
        self.assertFalse(c.tasks.summary())
        events=client.prompts[0]['feedback']['events']
        self.assertTrue(any(e['kind']=='task_status' and e['state']=='failed' and e['task_id']==1 for e in events))
    def test_action_evidence_is_player_local_and_does_not_guess_death(self):
        actor=unit(1,MARINE,ground_dps=10);target=unit(2,SCV,alliance=4)
        before=world([actor,target]);after=replace(world([actor],1),errors=((1,ATTACK,error_pb2.NotSupported),))
        a=command(ATTACK,[1],target=2)
        rows=describe_rejections(before,after,[a],[(a.SerializeToString(),{'source':'reflex','behavior':'defend'})],[(0,error_pb2.NotSupported)],123.0)
        r=rows[0];self.assertEqual(r['action_match'],'unique')
        self.assertEqual(r['actions'][0]['target_after']['status'],'absent_from_player_observation')
        self.assertTrue(r['cause'].startswith('unknown'))
        self.assertEqual(r['batch_sent_unix_s'],123.0)
        self.assertEqual(r['actions'][0]['target_before']['tag'],2)
    def test_ambiguous_actions_are_not_assigned_to_one_task(self):
        w=world([unit(1,MARINE)])
        after=replace(w,errors=((1,ATTACK,error_pb2.Error),))
        actions=[command(ATTACK,[1],target=2),command(ATTACK,[1],target=3)]
        r=describe_rejections(w,after,actions,[])[0]
        self.assertEqual(r['action_match'],'ambiguous');self.assertEqual(len(r['actions']),2)
        self.assertIn('not proven',r['association'])
        r=describe_rejections(w,after,actions,[],[(1,error_pb2.Error)])[0]
        self.assertEqual(r['action_match'],'unique');self.assertEqual(r['actions'][0]['target_tag'],3)
        self.assertEqual(r['association'],'exact response action')
    def test_engine_feedback_reaches_prompt_once_and_without_log_dependency(self):
        c,client,w=self.commander(['{"tasks":[]}'])
        w=replace(w,errors=((10,MOVE,error_pb2.Error),),
                  feedback=({'kind':'engine_action_rejected','result_name':'Error','unit_tag':10},))
        c.ingest_feedback(w);c.tick(w);self.wait(c)
        ev=client.prompts[0]['feedback']['events']
        self.assertEqual(sum(e['kind']=='engine_action_rejected' for e in ev),1)
        self.assertEqual(c.engine_errors,1)
        audit=[json.loads(x) for x in Path(c.feedback.path).read_text().splitlines()]
        self.assertEqual(next(r['prompt'] for r in audit if r['record']=='request_queued'),client.prompts[0])
    def test_training_action_has_task_identity_and_terminal_feedback(self):
        c,client,w=self.commander(['{"tasks":[]}'])
        c.tasks.enqueue([{'skill':'train_worker'}],0,request_id=7)
        c.tick(w);self.wait(c)
        binding=c.backend._action_bindings[0][0][1]
        self.assertEqual(binding,{'source':'task_queue','task_id':1,'skill':'train_worker','request_id':7})
        c.tick(world(list(w.units)+[unit(2,SCV)],2))
        self.assertTrue(any(e['kind']=='task_status' and e['state']=='completed' for e in c.feedback.pending))

if __name__=='__main__':unittest.main()
