"""Explicit engine steps and planner pauses; no native process or paid model."""
import json
import unittest
import tempfile
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace as S
from unittest.mock import patch
from s2clientprotocol import sc2api_pb2 as pb

from arena.native_engine import NativeArena
from arena.raw_engine import GameClock,ATTACK,command
from native_llm import planners_thinking,save_replay_checkpoint
from test_realtime_runtime import world,unit
from test_battle_agent import BattleTests


class StepController:
    def __init__(self):self.loop=112;self.steps=[];self.sent=[];self.drift=False
    def step(self,count):self.steps.append(count);self.loop+=count
    def actions(self,request):self.sent.extend(request.actions);return pb.ResponseAction(result=[1]*len(request.actions))
    def observe(self):
        if self.drift:self.loop+=1
        return pb.ResponseObservation(observation=pb.Observation(game_loop=self.loop))


class NativeStepTests(unittest.TestCase):
    def test_replay_checkpoint_replaces_atomically_and_preserves_last_good_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'match.json'
            replay=target.with_suffix('.SC2Replay');replay.write_bytes(b'previous')
            def failed_save(path):
                path.write_bytes(b'partial');raise OSError('fixture')
            with self.assertRaises(OSError):save_replay_checkpoint(S(save_replay=failed_save),target)
            self.assertEqual(replay.read_bytes(),b'previous')
            self.assertFalse(list(Path(directory).glob('*.tmp')))
            saved=save_replay_checkpoint(S(save_replay=lambda p:p.write_bytes(b'complete')),target)
            self.assertEqual(saved,replay);self.assertEqual(saved.read_bytes(),b'complete')

    def engine(self,realtime=False,count=1):
        a=NativeArena.__new__(NativeArena)
        a.realtime=realtime;a.step_loops=8;a.game_speed='normal'
        a.stepped_loops=a.paused_polls=a.pause_loop_checks=a.pause_loop_violations=0;a.last_advance=True
        a.controllers=[StepController() for _ in range(count)]
        a.last=[c.observe() for c in a.controllers]
        a._terminal_result=None;a._pending_surrender=None
        a._parallel=lambda fn,values:list(map(fn,values))
        a.snapshots=lambda:[replace(world([unit(1,18,is_building=True)]),game_loop=o.observation.game_loop) for o in a.last]
        a.period=0;a.next_poll=0;a.clock=GameClock(112);a.action_errors=0
        return a

    def test_pause_sends_commands_but_does_not_advance_engine(self):
        a=self.engine();w=a.step([[command(ATTACK,[10],pos=(50,50))]],advance=False)[0]
        self.assertEqual(w.game_loop,112);self.assertFalse(a.controllers[0].steps)
        self.assertEqual(len(a.controllers[0].sent),1)
        self.assertEqual(a.pause_loop_checks,1);self.assertEqual(a.pause_loop_violations,0)

    def test_step_advances_exact_count_for_all_participants(self):
        a=self.engine(count=2);ws=a.step([[],[]])
        self.assertEqual([w.game_loop for w in ws],[120,120])
        self.assertEqual([c.steps for c in a.controllers],[[8],[8]])
        self.assertEqual(a.stepped_loops,8)

    def test_variable_steps_advance_both_players_by_identical_counts(self):
        a=self.engine(count=2)
        a.step([[],[]],step_loops=64);first=a.timing()['step_counts']
        for count in (32,8,3):a.step([[],[]],step_loops=count)
        self.assertEqual([c.steps for c in a.controllers],[[64,32,8,3]]*2)
        self.assertEqual(a.stepped_loops,107);self.assertEqual(a.last_step_loops,3)
        self.assertEqual(a.timing()['step_counts'],{64:1,32:1,8:1,3:1})
        self.assertEqual(first,{64:1})  # Historical samples must not change with later steps.

    def test_advancing_step_mode_skips_wall_throttle_but_pause_keeps_it(self):
        a=self.engine();a.period=.1;a.next_poll=20
        with patch('arena.native_engine.time.monotonic',return_value=10),patch('arena.native_engine.time.sleep') as sleep:
            a.step([[]]);sleep.assert_not_called()
            a.step([[]],advance=False);sleep.assert_called_once_with(10)

    def test_realtime_keeps_poll_throttle_and_rejects_variable_steps(self):
        a=self.engine(realtime=True);a.period=.1;a.next_poll=20
        with patch('arena.native_engine.time.monotonic',return_value=10),patch('arena.native_engine.time.sleep') as sleep:
            a.step([[]]);sleep.assert_called_once_with(10)
        with self.assertRaisesRegex(ValueError,'non-realtime'):a.step([[]],step_loops=32)

    def test_invalid_variable_counts_are_rejected_without_stepping(self):
        a=self.engine()
        for count in (0,225,True,8.5):
            with self.subTest(count=count),self.assertRaises(ValueError):a.step([[]],step_loops=count)
        self.assertFalse(a.controllers[0].steps)

    def test_realtime_never_issues_request_step(self):
        a=self.engine(realtime=True);a.step([[]])
        self.assertFalse(a.controllers[0].steps)

    def test_pause_violation_fails_instead_of_claiming_pause(self):
        a=self.engine();a.controllers[0].drift=True
        with self.assertRaisesRegex(RuntimeError,'pause guarantee violated'):a.step([[]],advance=False)
        self.assertEqual(a.pause_loop_violations,1)

    def test_step_timing_does_not_claim_realtime_drift(self):
        a=self.engine();a.step([[]],advance=False);report=a.timing()
        self.assertEqual(report['speed_check'],'step_mode')
        self.assertTrue(report['paused_for_llm']);self.assertFalse(report['realtime'])

    def test_any_participant_or_tactical_planner_blocks_advancement(self):
        c=S(thinking=False,tactics=S(thinking=True),defense=None)
        self.assertTrue(planners_thinking([c]))
        c.tactics.thinking=False;self.assertFalse(planners_thinking([c]))
        self.assertTrue(planners_thinking([c,S(thinking=True)]))

    def test_invalid_step_size_rejected_before_native_launch(self):
        for value in (0,225,True):
            with self.subTest(value=value),self.assertRaises(ValueError):NativeArena(step_loops=value)

    def test_frozen_frame_can_consume_tactical_reply_without_deadlock(self):
        fixture=BattleTests();fixture.setUp()
        try:
            fixture.b.realtime=False
            w=fixture.w();fixture.t.tick(w);fixture.t.thinking=True
            fixture.reply();fixture.now=125.  # slow wall response, same game frame remains valid
            actions=fixture.t.tick(w)
            self.assertTrue(actions);self.assertFalse(fixture.t.thinking)
            self.assertEqual(fixture.t.expired,0)
        finally:fixture.doCleanups()

    def test_realtime_frozen_frame_keeps_existing_deduplication(self):
        fixture=BattleTests();fixture.setUp()
        try:
            w=fixture.w();fixture.t.tick(w);fixture.reply()
            self.assertEqual(fixture.t.tick(w),[])
            self.assertEqual(fixture.t.accepted,0)
        finally:fixture.doCleanups()

    def test_go_binding_is_not_exposed_to_native_game(self):
        from arena import native_runtime
        with patch.dict(native_runtime.os.environ,{'open_api_key':'test-go-key','DEEPSEEK_API_KEY':'test-official-key'}), \
             patch.object(native_runtime,'prepare',return_value='/tmp/fake-proton'), \
             patch.object(native_runtime.subprocess,'call',return_value=0) as launch:
            native_runtime.launch(['fixture'])
        env=launch.call_args.kwargs['env']
        self.assertNotIn('open_api_key',env);self.assertNotIn('DEEPSEEK_API_KEY',env)


if __name__=='__main__':unittest.main()
