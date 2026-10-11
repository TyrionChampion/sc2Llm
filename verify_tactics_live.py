"""Short headless SC2 fixture for army ownership and reinforcement batching; no LLM calls."""
import json
import time
import traceback
from pathlib import Path
from absl import app
from s2clientprotocol import debug_pb2 as debug

from arena.commander import Commander
from arena.native_engine import NativeArena
from arena.raw_engine import ATTACK, MOVE, command


class Idle:
    def ready_for_request(self):return False
    def chat(self,*args):raise AssertionError('fixture must never call a model')


def main(argv):
    report=dict(fixture=True,model_calls=0,checks={},realtime=True)
    commander=None;arena=None
    destination=Path('logs/tactics_native_validation.json')
    def save():destination.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    try:
        with NativeArena(names=('Tactics Zerg fixture','Idle Terran fixture'),races=['zerg','terran'],render=False) as arena:
            report.update(version=arena.version,native_directory=str(arena.directory))
            worlds=arena.step([[],[]])
            commander=Commander(arena,0,Idle(),opening=False,tactics_enabled=True,combat_micro=True,
                                feedback_dir='logs/tactics-validation-feedback')
            def tick():
                nonlocal worlds
                started=time.monotonic()
                batch=commander.tick(worlds[0])
                report.setdefault('tick_ms',[]).append(round((time.monotonic()-started)*1000,3))
                worlds=arena.step([batch,[]]);commander.ingest_feedback(worlds[0])
                return batch
            def until(predicate,seconds=8):
                end=time.monotonic()+seconds
                while time.monotonic()<end:
                    tick()
                    if predicate():return
                raise AssertionError('native fixture condition timed out')
            def spawn(count,pos,owner=1,kind=110):
                arena.controllers[0].debug([debug.DebugCommand(create_unit=debug.DebugCreateUnit(
                    unit_type=kind,owner=owner,pos=dict(x=pos[0],y=pos[1]),quantity=count))])
            tick();anchor=commander.tactics.anchor
            assert anchor is not None,commander.tactics.public()
            report['initial_anchor']=list(anchor)
            spawn(2,anchor)
            until(lambda:len(worlds[0].my(110))==2)
            tags=[u.tag for u in worlds[0].my(110)]
            # A native move rally is explicitly present, as in the user's failure case.
            worlds=arena.step([[command(MOVE,tags,pos=arena.enemy_start(0))],[]])
            commander.tactics.last_orders.clear()
            batch=tick()
            intercepted={tag for a in batch if a.action_raw.unit_command.ability_id==ATTACK
                         for tag in a.action_raw.unit_command.unit_tags}
            assert set(tags)<=intercepted
            until(lambda:all(u.distance(anchor)<7 for u in worlds[0].my(110)))
            report['checks']['native_move_rally_intercepted']=True
            commander.tasks.enqueue([dict(skill='move_out')],worlds[0].seconds)
            tick();assert not commander.tactics.fighting
            report['checks']['two_units_wait_for_batch']=True
            spawn(4,anchor)
            until(lambda:len(commander.tactics.fighting)==6)
            # Verify actual engine orders point toward the mission, not just Python dispatch.
            target=arena.enemy_start(0)
            until(lambda:sum(any(abs(p[0]-target[0])<2 and abs(p[1]-target[1])<2 for p in u.order_positions)
                             for u in worlds[0].my(110))>=4)
            report['checks']['six_units_attack_with_observed_orders']=True
            spawn(1,anchor)
            until(lambda:len(worlds[0].my(110))==7)
            newest=max(worlds[0].my(110),key=lambda u:u.tag)
            assert newest.tag not in commander.tactics.fighting
            report['checks']['new_reinforcement_stays_in_assembly']=True
            # A visible tank over the former anchor must invalidate it and redirect units.
            spawn(1,anchor,owner=2,kind=32)
            until(lambda:any(u.type_id==32 for u in worlds[0].enemy_units))
            until(lambda:commander.tactics.anchor is None or commander.tactics.anchor!=anchor)
            point=commander.tactics.anchor
            assert point is None or commander.tactics._risk(point)==0
            report['checks']['threatened_anchor_invalidated']=True
            report['final_tactics']=commander.tactics.public()
            report['action_errors']=arena.action_errors
            assert arena.action_errors==0,arena.action_errors
            report['passed']=True
    except BaseException as error:
        report.update(error=type(error).__name__+': '+str(error),traceback=traceback.format_exc())
        raise
    finally:
        if commander:commander.stop()
        report['cleanup_complete']=arena is not None and arena.closed
        save()
        print('TACTICS_VALIDATION',json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':app.run(main)
