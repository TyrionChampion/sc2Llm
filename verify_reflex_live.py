"""Explicit live-engine combat fixture. NEVER count this as a natural match."""
import json
import time
from absl import app
from s2clientprotocol import debug_pb2 as debug
from arena.raw_engine import *
from arena.reflex import ReflexNet


def main(argv):
    del argv
    with RawArena(render=False,difficulty='very_easy') as arena:
        worlds=arena.initial; base=arena.homes[0]
        print('TEST_FIXTURE_ONLY debug-created units and health; excluded from win-rate evidence',flush=True)
        anchor=worlds[0].my(SCV)[0].pos
        before={u.tag for u in worlds[0].my_units}
        dc=debug.DebugCommand(); dc.create_unit.unit_type=MARINE; dc.create_unit.owner=1
        dc.create_unit.pos.x,dc.create_unit.pos.y=anchor; dc.create_unit.quantity=2
        print('DEBUG_CREATE_RESPONSE',arena.controllers[0].debug([dc]),flush=True)
        deadline=time.monotonic()+3
        added=[]
        while time.monotonic()<deadline:
            worlds=arena.step([[]])
            added=[u for u in worlds[0].my(MARINE) if u.tag not in before]
            if len(added)==2: break
        assert len(added)==2,added
        wounded=added[0]
        dc=debug.DebugCommand(); dc.unit_value.unit_value=2; dc.unit_value.value=10; dc.unit_value.unit_tag=wounded.tag
        ec=debug.DebugCommand(); ec.create_unit.unit_type=SCV; ec.create_unit.owner=2
        ec.create_unit.pos.x=wounded.x+2; ec.create_unit.pos.y=wounded.y; ec.create_unit.quantity=1
        print('DEBUG_THREAT_RESPONSE',arena.controllers[0].debug([dc,ec]),flush=True)
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            worlds=arena.step([[]]); current=worlds[0].by_tag(wounded.tag)
            if current and current.health<=10 and worlds[0].enemy_units: break
        assert current and current.health<=10 and worlds[0].enemy_units,'fixture did not become observable'
        reflex=ReflexNet(base,combat_enabled=True); reflex.size=arena.size
        decision,reserved=reflex.tick(worlds[0])
        retreat=[a for a in decision if a.action_raw.unit_command.ability_id==MOVE and wounded.tag in a.action_raw.unit_command.unit_tags]
        defend=[a for a in decision if a.action_raw.unit_command.ability_id==ATTACK]
        assert retreat and defend,[(a.action_raw.unit_command.ability_id,list(a.action_raw.unit_command.unit_tags)) for a in decision]
        assert wounded.tag in reserved
        old=current.pos
        worlds=arena.step([decision]); deadline=time.monotonic()+1.2
        while time.monotonic()<deadline: worlds=arena.step([[]])
        new=worlds[0].by_tag(wounded.tag)
        assert new and new.distance(old)>1,(old,new)
        assert arena.action_errors==0,arena.action_errors
        print('REFLEX_FIXTURE_OK',json.dumps({'retreat_commands':len(retreat),'defend_commands':len(defend),
              'moved_distance':new.distance(old),'realtime':True}),flush=True)

if __name__=='__main__': app.run(main)
