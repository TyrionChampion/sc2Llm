"""SC2Env immediate response failures must reach the model exactly once."""
import unittest
from types import SimpleNamespace
from dataclasses import replace
from s2clientprotocol import sc2api_pb2 as pb,error_pb2 as error
from arena.raw_engine import ActionResponseTap,RawArena,command
from test_realtime_runtime import world,unit


class RawResponses(unittest.TestCase):
    def arena(self,codes,observation_errors=()):
        controller=SimpleNamespace(calls=[],response=pb.ResponseAction(result=codes))
        def actions(request):controller.calls.append(request);return controller.response
        controller.actions=actions
        tap=ActionResponseTap(controller)
        state=[world([unit(1,59,is_building=True)])]
        arena=RawArena.__new__(RawArena);arena.controllers=[tap]
        arena.last=[SimpleNamespace(last=lambda:False)]
        arena._terminal_result=arena._pending_surrender=None
        arena.action_errors=0
        arena.snapshots=lambda:[state[0]]
        def step(batches):
            tap.actions(pb.RequestAction(actions=batches[0]))
            state[0]=replace(state[0],game_loop=1,errors=observation_errors)
            return arena.last
        arena.env=SimpleNamespace(step=step)
        arena.clock=SimpleNamespace(update=lambda loop:None)
        return arena,controller
    def test_immediate_rejection_has_exact_original_action_context(self):
        arena,controller=self.arena([error.NotEnoughMinerals])
        action=command(1006,[1]);ctx=dict(source='task_queue',task_id=9)
        arena.register_action_context(0,[(action.SerializeToString(),ctx)])
        after=arena.step([[action]])[0]
        self.assertEqual(after.errors,((1,1006,error.NotEnoughMinerals),))
        self.assertEqual(arena.action_errors,1)
        event=after.feedback[0]
        self.assertEqual(event['origin'],'action_response')
        self.assertEqual(event['actions'][0]['context'],ctx)
        self.assertEqual(event['actions'][0]['batch_index'],0)
        self.assertEqual(len(controller.calls),1)
    def test_observation_and_response_do_not_double_count_same_rejection(self):
        rejected=(1,1006,error.NotEnoughMinerals)
        arena,_=self.arena([error.NotEnoughMinerals],(rejected,))
        after=arena.step([[command(1006,[1])]])[0]
        self.assertEqual(after.errors,(rejected,))
        self.assertEqual(arena.action_errors,1)
        self.assertEqual(len(after.feedback),1)
    def test_success_and_noop_do_not_create_errors_or_extra_commands(self):
        for batch in ([],[command(1006,[1])]):
            arena,controller=self.arena([error.Success])
            after=arena.step([batch])[0]
            self.assertEqual(after.errors,());self.assertEqual(after.feedback,())
            self.assertEqual(len(controller.calls),1)
    def test_mixed_results_keep_exact_failed_index_and_group_actor_tags(self):
        arena,_=self.arena([error.Success,error.NotEnoughFood])
        after=arena.step([[command(1006,[1]),command(1414,[2,3],pos=[35,35])]])[0]
        self.assertEqual(len(after.errors),2)
        self.assertTrue(all(e['actions'][0]['batch_index']==1 for e in after.feedback))
        self.assertTrue(all(e['actions'][0]['actor_tags']==[2,3] for e in after.feedback))
    def test_response_mismatch_does_not_fabricate_acceptance_or_attribution(self):
        tap=ActionResponseTap(SimpleNamespace(actions=lambda request:pb.ResponseAction(result=[])))
        action=command(1006,[1]);tap.actions(pb.RequestAction(actions=[action]))
        with self.assertRaisesRegex(RuntimeError,'count mismatch'):tap.failures([action])
        tap.response=pb.ResponseAction(result=[error.Success])
        with self.assertRaisesRegex(RuntimeError,'packet changed'):tap.failures([command(1006,[2])])
    def test_terminal_none_and_next_step_clear_previous_response(self):
        tap=ActionResponseTap(SimpleNamespace(actions=lambda request:None,ping=lambda:'delegated'))
        self.assertEqual(tap.ping(),'delegated')
        tap.actions(pb.RequestAction(actions=[command(1006,[1])]))
        self.assertEqual(tap.failures([command(1006,[1])]),[])
        tap.begin();self.assertIsNone(tap.packet);self.assertIsNone(tap.sent_at)


if __name__=='__main__':unittest.main()
