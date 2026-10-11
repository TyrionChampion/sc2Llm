"""Surrender contracts using fixtures; native proof is separate."""
import json,tempfile,unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from s2clientprotocol import sc2api_pb2 as pb
from arena.match_control import surrender_reason
from arena.composite_skills import PlanValidationError
from arena.commander import Commander
from arena.native_engine import NativeArena
from arena.raw_engine import GameClock,command,MOVE
from arena.tasks import TaskQueue
from test_realtime_runtime import Backend,world,unit,CC

PLAN={'tasks':[{'skill':'surrender','args':{'reason':'No realistic recovery in this test scenario'}}]}

def observation(results=None):
    o=pb.ResponseObservation();o.observation.game_loop=140
    for player,result in (results or {}).items():o.player_result.add(player_id=player,result=pb.Result.Value(result))
    return o

class Controller:
    def __init__(self,results=None):
        self.status=SimpleNamespace(name='in_game');self.results=results;self.calls=[]
        self.save_error=False;self.leave_error=False;self.post_save_error=False
    def save_replay(self):
        self.calls.append('save')
        if self.save_error or (self.post_save_error and self.status.name=='ended'):raise RuntimeError('save failed')
        return b'fixture replay bytes'
    def leave(self):
        self.calls.append('leave')
        if self.leave_error:raise RuntimeError('network timeout')
        self.status.name='launched';return pb.ResponseLeaveGame()
    def observe(self):
        self.calls.append('observe');self.status.name='ended';return observation(self.results)


def native(index=0,dual=False):
    a=NativeArena.__new__(NativeArena)
    expected={index+1:'Defeat',2 if index==0 else 1:'Victory'}
    a.controllers=[Controller(expected) for _ in range(2 if dual else 1)]
    a.last=[observation() for _ in a.controllers]
    # 投降门控要求"确实没有翻盘手段":无部队、无工人、资源不足以重建(见 match_control)。
    a.snapshots=lambda:[replace(world([unit(1,CC,is_building=True)],
                                      sec=o.observation.game_loop/22.4),minerals=0,vespene=0,units=())
                        for o in a.last]
    a._terminal_result=None;a._pending_surrender=None;a.surrender_report=None;a._surrender_replay=None
    a.clock=GameClock(140);a.action_errors=0
    return a

class Planner:
    def chat(self,*args):raise AssertionError('fixture must not call a model')

class SurrenderTests(unittest.TestCase):
    def test_reason_and_exclusive_schema(self):
        self.assertIsNone(surrender_reason({'tasks':[]}));self.assertTrue(surrender_reason(PLAN))
        cases=[{'tasks':PLAN['tasks']+[{'skill':'train_worker'}]},
               {'tasks':PLAN['tasks'],'skills':[{'name':'x'}]},
               {'tasks':[{'skill':'surrender'}]},
               {'tasks':[{'skill':'surrender','args':{'reason':'   '}}]},
               {'tasks':[{'skill':'surrender','args':{'reason':'x','player':2}}]},
               {'tasks':[{'skill':'surrender','args':{'reason':'x'},'count':True}]}]
        for p in cases:
            with self.subTest(p=p),self.assertRaises(PlanValidationError):surrender_reason(p)
    def test_cannot_redefine_surrender_as_a_composite(self):
        q=TaskQueue(None,0,lambda _:None)
        with self.assertRaises(PlanValidationError):
            q.enqueue([],0,skills=[dict(name='surrender',select=dict(unit='marine'),steps=[dict(action='move',target='home')])])
        self.assertEqual(q.library,{})
    def test_bot_saves_before_leave_and_marks_adjudication(self):
        a=native();a.request_surrender(0,'fixture reason',3)
        w=a.step([[command(MOVE,[999],pos=(1,1))]])
        self.assertEqual(a.controllers[0].calls,['save','leave'])
        self.assertEqual(a.result(),{1:'Defeat',2:'Victory'});self.assertTrue(a.ended)
        self.assertEqual(a.surrender_report['result_source'],'acknowledged_concession_rule')
        self.assertEqual(a.surrender_report['replay_scope'],'before_leave')
        self.assertEqual(w[0].feedback[0]['kind'],'surrender_completed')
        with tempfile.TemporaryDirectory(dir='logs') as d:
            target=Path(d)/'fixture.SC2Replay';a.save_replay(target)
            self.assertEqual(target.read_bytes(),b'fixture replay bytes')
        self.assertEqual(a.controllers[0].calls,['save','leave'])
    def test_second_player_surrender_uses_first_players_terminal_result(self):
        a=native(index=1,dual=True);a.request_surrender(1,'P2 reason');a.step([[],[]])
        self.assertEqual(a.result(),{1:'Victory',2:'Defeat'})
        self.assertEqual(a.surrender_report['result_source'],'engine_player_result')
        self.assertEqual(a.surrender_report['replay_scope'],'terminal_from_opponent')
        self.assertEqual(a.controllers[1].calls,['save','leave'])
        self.assertEqual(a.controllers[0].calls,['observe','save'])
    def test_presave_failure_keeps_game_and_does_not_leave(self):
        a=native();a.controllers[0].save_error=True;a.request_surrender(0,'reason')
        worlds=a.step([[]]);self.assertFalse(a.ended);self.assertFalse(a.match_control_pending)
        self.assertEqual(a.controllers[0].calls,['save'])
        self.assertEqual(worlds[0].feedback[0]['kind'],'surrender_failed')
    def test_uncertain_leave_does_not_fabricate_result_or_retry(self):
        a=native();a.controllers[0].leave_error=True;a.request_surrender(0,'reason')
        with self.assertRaisesRegex(RuntimeError,'uncertain'):a.step([[]])
        self.assertIsNone(a._terminal_result);self.assertEqual(a.surrender_report['state'],'unknown')
        self.assertEqual(a.controllers[0].calls,['save','leave'])
    def test_terminal_replay_failure_is_visible_and_preserves_preleave_copy(self):
        a=native(dual=True);a.controllers[1].post_save_error=True;a.request_surrender(0,'reason');a.step([[],[]])
        self.assertEqual(a.surrender_report['replay_scope'],'before_leave')
        self.assertIn('Terminal replay unavailable',a.surrender_report['warnings'][0])
    def test_contradictory_engine_result_is_error(self):
        a=native(dual=True);a.controllers[1].results={1:'Victory',2:'Defeat'};a.request_surrender(0,'reason')
        with self.assertRaisesRegex(RuntimeError,'contradicts'):a.step([[],[]])
        self.assertEqual(a.surrender_report['state'],'result_mismatch');self.assertIsNone(a._terminal_result)
    def test_invalid_player_and_second_request_do_not_override(self):
        a=native(dual=True)
        with self.assertRaises(ValueError):a.request_surrender(2,'reason')
        a.request_surrender(0,'first')
        with self.assertRaises(ValueError):a.request_surrender(1,'second')
        self.assertEqual(a._pending_surrender['player'],1)
    def commander(self,folder,supported=True):
        b=Backend()
        dead=world([unit(1,CC,is_building=True)],sec=10)
        b.initial=[replace(dead,minerals=0,vespene=0,units=())]
        b.match_control_pending=False;b.requests=[]
        if supported:
            def request(index,reason,request_id):
                b.requests.append((index,reason,request_id));b.match_control_pending=True
            b.request_surrender=request
        c=Commander(b,0,Planner(),opening=False,feedback_dir=folder)
        c.last_request=1e9;return c,b
    def test_commander_accepts_without_executing_existing_queue(self):
        with tempfile.TemporaryDirectory(dir='logs') as d:
            c,b=self.commander(d)
            try:
                c.tasks.enqueue([{'skill':'train_worker'}],10)
                c.outbox.put((10,PLAN,None,.1,True,json.dumps(PLAN),7,[]))
                self.assertEqual(c.tick(b.initial[0]),[]);self.assertEqual(len(b.requests),1)
                self.assertEqual(c.tasks.tasks[0].status,'queued');self.assertEqual(c.decisions,1)
                self.assertTrue(c.control_policy['surrender_available'])
            finally:c.stop()
    def test_mixed_plan_is_rejected_without_requesting_leave(self):
        with tempfile.TemporaryDirectory(dir='logs') as d:
            c,b=self.commander(d)
            try:
                plan={'tasks':PLAN['tasks']+[{'skill':'train_worker'}]}
                c.outbox.put((10,plan,None,.1,True,json.dumps(plan),7,[]));c.tick(b.initial[0])
                self.assertEqual(b.requests,[]);self.assertEqual(c.tasks.tasks,[]);self.assertEqual(c.errors,1)
            finally:c.stop()
    def test_unsupported_backend_rejects_and_announces_capability(self):
        with tempfile.TemporaryDirectory(dir='logs') as d:
            c,b=self.commander(d,False)
            try:
                c.outbox.put((10,PLAN,None,.1,True,json.dumps(PLAN),7,[]));c.tick(b.initial[0])
                self.assertEqual(c.errors,1);self.assertFalse(c.control_policy['surrender_available'])
                self.assertIn('Surrender is unavailable',c.system)
            finally:c.stop()
    def test_native_entrypoint_reports_loss_and_saves_replay(self):
        import sys,time
        from unittest.mock import patch
        import native_llm
        class Concede:
            def chat(self,*args):return json.dumps(PLAN)
        class MainBackend(Backend):
            version='fixture';directory=Path('logs');start_location_report=[];action_errors=0
            def __init__(self):self.a=native();self.initial=self.a.snapshots();self.closed=False
            @property
            def ended(self):return self.a.ended
            @property
            def match_control_pending(self):return self.a.match_control_pending
            @property
            def surrender_report(self):return self.a.surrender_report
            def request_surrender(self,*args):return self.a.request_surrender(*args)
            def step(self,batches):
                if self.match_control_pending:return self.a.step(batches)
                time.sleep(.005);return self.initial
            def result(self):return self.a.result()
            def timing(self):return dict(speed_check='warming_up',game_wall_ratio=1)
            def save_replay(self,path):self.a.save_replay(path)
            def close(self):self.closed=True
        b=MainBackend()
        with tempfile.TemporaryDirectory(dir='logs') as d:
            path=Path(d)/'result.json'
            with patch.object(sys,'argv',['native_llm.py','--p1','mock','--bot','medium','--no-overlay','--seconds','2','--output',str(path)]), \
                 patch.object(native_llm,'NativeArena',return_value=b), \
                 patch.object(native_llm,'make_player',return_value=(Concede(),dict(provider='mock',model='fixture',side='P1',key_source='none'))):
                native_llm.main()
            result=json.loads(path.read_text())
            self.assertEqual(result['outcome'],{'1':'Defeat','2':'Victory'})
            self.assertEqual(result['surrender']['result_source'],'acknowledged_concession_rule')
            self.assertTrue(Path(result['replay']).is_file());self.assertTrue(b.closed)
            self.assertEqual(result['commanders'][0]['active_tasks'],[])

    def test_terminal_feedback_cancels_tasks_without_counting_engine_rejection(self):
        from dataclasses import replace
        with tempfile.TemporaryDirectory(dir='logs') as d:
            c,b=self.commander(d)
            try:
                c.tasks.enqueue([{'skill':'train_worker'}],10)
                a=native();a.request_surrender(0,'test')
                terminal=a.step([[]])[0]
                c.ingest_feedback(terminal)
                self.assertEqual(c.tasks.tasks[0].status,'cancelled');self.assertEqual(c.engine_errors,0)
            finally:c.stop()

if __name__=='__main__':unittest.main()
