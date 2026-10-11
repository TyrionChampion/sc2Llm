"""Shutdown behavior with real self-signals and fake engine/clients; no paid API."""
import json
import os
from pathlib import Path
import signal
import tempfile
import time
import unittest
from unittest.mock import patch
import native_llm
from arena.lifecycle import ShutdownSignals, read_match_status, write_json_atomic, match_status_label


class NativeLifecycleTests(unittest.TestCase):
    def stopped_match(self, fail_stop=False):
        with tempfile.TemporaryDirectory(prefix="sc2-shutdown-") as folder:
            target=Path(folder)/"match.json"
            target.write_text('{"outcome":"running"}')
            events=[]
            class Client: pass
            class Arena:
                version="fixture";directory=folder;start_location_report={}
                initial=[object(),object()];ended=False;surrender_report=None
                def __init__(self,*a,**kw):pass
                def save_replay(self,path):
                    events.append("replay");path.write_bytes(b"fixture only")
                def close(self):events.append("arena_closed")
            class Commander:
                def __init__(self,backend,index,*a,**kw):self.index=index
                def tick(self,w):os.kill(os.getpid(),signal.SIGTERM)
                def stop(self):
                    events.append("stop_"+str(self.index))
                    self_report=json.loads(target.read_text())
                    events.append(self_report["outcome"])
                    if self.index==0:
                        os.kill(os.getpid(),signal.SIGTERM)
                        if fail_stop:raise RuntimeError("broken worker cleanup")
                def stats(self):return dict(index=self.index)
            before={n:signal.getsignal(n) for n in (signal.SIGTERM,signal.SIGINT)}
            with patch.object(native_llm,"NativeArena",Arena),patch.object(native_llm,"Commander",Commander):
                players=[(Client(),dict(side="P%d"%(i+1),provider="fixture",model="fixture",key_source="none")) for i in range(2)]
                with self.assertRaisesRegex(KeyboardInterrupt,"SIGTERM"):
                    native_llm.main(players_override=players,catalogs_override=[None,None],
                        opening=False,argv=["--output",str(target)])
            self.assertEqual({n:signal.getsignal(n) for n in before},before)
            return json.loads(target.read_text()),events

    def test_second_real_sigterm_does_not_interrupt_terminal_save_or_cleanup(self):
        report,events=self.stopped_match()
        self.assertEqual(report["outcome"],"interrupted")
        self.assertEqual(report["cleanup_status"],"complete")
        self.assertEqual(events,["stop_0","interrupted","stop_1","interrupted","replay","arena_closed"])
        signals=report["shutdown_signals"]["events"]
        self.assertEqual([s["signal"] for s in signals],["SIGTERM","SIGTERM"])
        self.assertEqual([s["during_cleanup"] for s in signals],[False,True])

    def test_one_cleanup_failure_does_not_skip_other_player_or_engine(self):
        report,events=self.stopped_match(fail_stop=True)
        self.assertEqual(report["cleanup_status"],"failed")
        self.assertEqual(report["cleanup_errors"][0]["stage"],"commander_1_stop")
        self.assertIn("stop_1",events);self.assertIn("arena_closed",events)
        self.assertEqual(report["outcome"],"interrupted")

    def test_signal_during_normal_cleanup_is_recorded_without_interrupt(self):
        shutdown=ShutdownSignals();shutdown.install()
        try:
            shutdown.cleaning=True
            os.kill(os.getpid(),signal.SIGTERM)
            self.assertTrue(shutdown.state["events"][0]["during_cleanup"])
        finally:shutdown.restore()

    def test_atomic_replace_failure_preserves_previous_valid_report(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"match.json";p.write_text('{"outcome":"running"}')
            with patch("arena.lifecycle.os.replace",side_effect=OSError("filesystem error")):
                with self.assertRaises(OSError):write_json_atomic(p,dict(outcome="interrupted"))
            self.assertEqual(json.loads(p.read_text())["outcome"],"running")
            self.assertEqual(list(Path(folder).iterdir()),[p])

    def status_case(self,meta=None,age=0):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"match.json";p.write_text('{"outcome":"running","samples":[]}')
            now=time.time();os.utime(p,(now-age,now-age))
            if meta is not None:(Path(folder)/"run.json").write_text(json.dumps(meta))
            data,notice=read_match_status(folder,now=now)
            self.assertEqual(json.loads(p.read_text())["outcome"],"running")
            return data,notice

    def test_launcher_interrupted_overrides_old_running_status_visibly(self):
        d,n=self.status_case(dict(status="interrupted",error="SIGTERM"),age=100)
        self.assertEqual(d["outcome"],"interrupted");self.assertIn("SIGTERM",n)

    def test_stale_report_is_unknown_not_live_or_a_fabricated_loss(self):
        d,n=self.status_case(age=100)
        self.assertEqual(d["outcome"],"stale");self.assertIn("未知",n)

    def test_natural_engine_result_dictionary_is_renderable(self):
        self.assertEqual(match_status_label({"1":"Defeat","2":"Victory"}),"对局已结束：P1 负 · P2 胜")

    def test_fresh_running_report_stays_running(self):
        d,n=self.status_case(age=5)
        self.assertEqual(d["outcome"],"running");self.assertEqual(n,"")

    def test_finished_launcher_without_final_result_does_not_fabricate_win(self):
        d,n=self.status_case(dict(status="finished"))
        self.assertEqual(d["outcome"],"ended_unknown");self.assertTrue(n)

if __name__=="__main__":unittest.main()
