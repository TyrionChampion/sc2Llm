"""Live regression: realtime, offscreen production, delayed planner, reflexes.

Combat fixtures run separately in verify_reflex_live.py. This is
NOT a win-rate experiment. Natural matches belong in evaluate_realtime.py.
"""
import json
import time
from absl import app
from s2clientprotocol import debug_pb2 as debug
from arena.raw_engine import *
from arena.commander import Commander,ScriptedPlanner
from arena.reflex import ReflexNet


def main(argv):
    del argv
    commanders=[]
    try:
        with RawArena(render=True) as arena:
            commanders=[Commander(arena,i,ScriptedPlanner(delay=12)) for i in range(2)]
            worlds=arena.initial
            started=time.monotonic(); next_report=started+15; moved=False
            while time.monotonic()-started<210:
                batches=[c.tick(w) for c,w in zip(commanders,worlds)]
                if not moved:
                    for batch in batches: batch.append(camera((100,100)))
                    moved=True
                worlds=arena.step(batches)
                if time.monotonic()-started>2:
                    assert all(not w.my(CC)[0].on_screen for w in worlds),'camera returned to base'
                if time.monotonic()>=next_report:
                    print('LIVE_PROGRESS',json.dumps({'worlds':[w.summary() for w in worlds],
                          'clock':arena.clock.report(),'policies':[c.stats() for c in commanders]}),flush=True)
                    next_report=time.monotonic()+15
                if all(w.my(MARINE) and any(u.done for u in w.my(BARRACKS)) for w in worlds): break
            else: raise AssertionError('offscreen build chain failed within 210 wall seconds')
            assert all(len(w.my(SCV))>12 for w in worlds)
            assert all(c.decisions>0 for c in commanders),'delayed planner never returned'
            timing=arena.clock.report()
            assert .9<timing['game_wall_ratio']<1.1,timing
            print('OFFSCREEN_CHAIN_OK',json.dumps({'worlds':[w.summary() for w in worlds],
                  'timing':timing,'planner_delay_s':12}),flush=True)
            # No controller calls during wait: distinguish true realtime from step-driven play.
            loop=worlds[0].game_loop; start=time.monotonic(); time.sleep(3)
            worlds=arena.step([[],[]]); wall=time.monotonic()-start
            ratio=(worlds[0].game_loop-loop)/22.4/wall
            assert .85<ratio<1.15,ratio
            print('NO_CALLS_REALTIME_OK',ratio,flush=True)
            print('VERDICT_OK realtime + delayed queue + offscreen build chain; combat fixture is separate',flush=True)
    finally:
        for c in commanders: c.stop()

if __name__=='__main__': app.run(main)
