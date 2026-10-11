"""Controlled native error-code probe; debug fixture, not a natural match."""
import json, time
from pathlib import Path
from absl import app
from google.protobuf.json_format import MessageToDict
from s2clientprotocol import debug_pb2 as debug, sc2api_pb2 as pb, error_pb2
from arena.native_engine import NativeArena
from arena.raw_engine import command, MARINE, CC, ATTACK, MOVE


def main(argv):
    rows=[]
    with NativeArena(names=("Error probe P1", "Error probe P2")) as arena:
        print("FIXTURE ONLY: realtime=True, debug-created/killed units, no LLM",flush=True)
        c=arena.controllers[0]
        def observe():
            arena.last=arena._parallel(lambda x:x.observe(),arena.controllers)
            return arena.snapshots()[0]
        def wait_for(pred):
            end=time.monotonic()+15
            while time.monotonic()<end:
                w=observe()
                if pred(w):return w
                time.sleep(.05)
            raise AssertionError("fixture observation timeout")
        anchor=arena.initial[0].my(CC)[0].pos
        creates=[]
        for owner,typ,dx in [(1,MARINE,7),(2,CC,13)]:
            d=debug.DebugCommand(); u=d.create_unit
            u.owner=owner;u.unit_type=typ;u.quantity=1
            u.pos.x=anchor[0]+dx;u.pos.y=anchor[1]+4;creates.append(d)
        c.debug(creates)
        w=wait_for(lambda w: w.my(MARINE) and any(u.type_id==CC for u in w.enemy_units))
        actor=w.my(MARINE)[0].tag
        target=next(u.tag for u in w.enemy_units if u.type_id==CC)
        def trial(name,actions):
            before=observe()
            response=c.actions(pb.RequestAction(actions=actions))
            time.sleep(.15)
            after=observe()
            row=dict(name=name,before_loop=before.game_loop,after_loop=after.game_loop,
                actor_present=before.by_tag(actor) is not None,target_present=before.by_tag(target) is not None,
                actions=[MessageToDict(a,preserving_proto_field_name=True) for a in actions],
                results=[error_pb2.ActionResult.Name(x) for x in response.result],
                observed_errors=[list(e) for e in after.errors])
            rows.append(row); print("TRIAL",json.dumps(row),flush=True)
        trial("live_actor_live_target",[command(ATTACK,[actor],target=target)])
        trial("live_actor_move",[command(MOVE,[actor],pos=(anchor[0]+6,anchor[1]+5))])
        d=debug.DebugCommand();d.kill_unit.tag.append(target);c.debug([d])
        wait_for(lambda w:w.by_tag(target) is None)
        trial("live_actor_dead_target",[command(ATTACK,[actor],target=target)])
        trial("live_actor_invalid_target",[command(ATTACK,[actor],target=999999999999)])
        trial("mixed_actions_mapping",[command(MOVE,[actor],pos=anchor),command(ATTACK,[actor],target=target),command(MOVE,[actor],pos=anchor)])
        # A mobile, unarmed enemy remains alive after leaving normal vision.
        d=debug.DebugCommand();d.create_unit.owner=2;d.create_unit.unit_type=54
        d.create_unit.quantity=1;d.create_unit.pos.x=anchor[0]+8;d.create_unit.pos.y=anchor[1]+4
        c.debug([d])
        w=wait_for(lambda w:any(u.type_id==54 for u in w.enemy_units))
        target=next(u.tag for u in w.enemy_units if u.type_id==54)
        trial("live_visible_mobile_target",[command(ATTACK,[actor],target=target)])
        c.actions(pb.RequestAction(actions=[command(MOVE,[actor],pos=anchor)]))
        arena.controllers[1].actions(pb.RequestAction(actions=[command(MOVE,[target],pos=(anchor[0]+45,anchor[1]))]))
        wait_for(lambda w:w.by_tag(target) is None)
        opponent=arena.snapshots()[1].by_tag(target)
        assert opponent and opponent.health>0, "fog target must still be alive"
        print("FOG_TARGET_ALIVE",opponent,flush=True)
        trial("live_actor_hidden_live_target",[command(ATTACK,[actor],target=target)])
        trial("attack_position_of_hidden_target",[command(ATTACK,[actor],pos=opponent.pos)])
        d=debug.DebugCommand();d.kill_unit.tag.append(actor);c.debug([d])
        wait_for(lambda w:w.by_tag(actor) is None)
        trial("dead_actor_move",[command(MOVE,[actor],pos=anchor)])
        trial("dead_actor_attack",[command(ATTACK,[actor],target=target)])
        Path("logs/native_action_error_fog_probe.json").write_text(json.dumps(dict(version=arena.version,realtime=True,fixture=True,trials=rows),indent=2))
        print("PROBE_COMPLETE",flush=True)

if __name__=="__main__":app.run(main)
