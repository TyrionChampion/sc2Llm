"""Native 5.0.15 realtime primitive probe; debug fixture, no displayed game/LLM."""
import json,time
from pathlib import Path
from absl import app
from s2clientprotocol import debug_pb2 as debug,sc2api_pb2 as pb,query_pb2 as query
from arena.native_engine import NativeArena
from arena.raw_engine import *
from arena.tasks import TaskQueue


def main(argv):
    report={'fixture':True,'realtime':True,'render':False}
    with NativeArena(names=('Skill probe',),difficulty='very_easy',render=False) as a:
        c=a.controllers[0]
        def wait(pred,timeout=45):
            end=time.monotonic()+timeout
            while time.monotonic()<end:
                w=a.step([[]])[0]
                if pred(w):return w
            raise AssertionError('primitive fixture timeout')
        home=a.homes[0]
        dc=[]
        for kind,dx in [(MARINE,6),(33,9)]:
            d=debug.DebugCommand();d.create_unit.owner=1;d.create_unit.unit_type=kind
            d.create_unit.quantity=1;d.create_unit.pos.x=home[0]+dx;d.create_unit.pos.y=home[1]+4;dc.append(d)
        c.debug(dc)
        w=wait(lambda w: w.my(MARINE) and w.my(33))
        marine=w.my(MARINE)[0].tag;tank=w.my(33)[0].tag
        def abilities(tag):
            q=query.RequestQuery();q.abilities.add(unit_tag=tag)
            return [x.ability_id for x in c.query(q).abilities[0].abilities]
        report['stim_before']=abilities(marine)
        print('STIM_BEFORE',report['stim_before'],flush=True)
        c.debug([debug.DebugCommand(game_state=debug.upgrade)])
        w=wait(lambda w:15 in a.proto(0).observation.raw_data.player.upgrade_ids)
        report['stim_after']=abilities(marine);print('STIM_AFTER',report['stim_after'],flush=True)
        response=c.actions(pb.RequestAction(actions=[command(3675,[marine])]))
        print('STIM_RESPONSE',response,flush=True);assert list(response.result)==[1]
        w=wait(lambda w:any(u.tag==marine and 27 in u.buff_ids for u in a.proto(0).observation.raw_data.units))
        report['stim_buff_confirmed']=True
        pos=(w.by_tag(tank).x+5,w.by_tag(tank).y)
        move=command(MOVE,[tank],pos=pos);siege=command(388,[tank]);siege.action_raw.unit_command.queue_command=True
        response=c.actions(pb.RequestAction(actions=[move,siege]));print('TANK_RESPONSE',response,flush=True)
        assert list(response.result)==[1,1]
        w=wait(lambda w:w.by_tag(tank) and w.by_tag(tank).type_id==32)
        report['tank_sieged_distance']=w.by_tag(tank).distance(pos);assert report['tank_sieged_distance']<3
        w=wait(lambda w:w.minerals>=100)
        q=TaskQueue(a,0);q.enqueue([{'skill':'supply_depot'}],w.seconds)
        build=q.tick(w,set())[0];worker=build.action_raw.unit_command.unit_tags[0]
        mineral=min([u for u in w.units if u.mineral],key=w.by_tag(worker).distance)
        gather=command(GATHER,[worker],target=mineral.tag);gather.action_raw.unit_command.queue_command=True
        response=c.actions(pb.RequestAction(actions=[build,gather]));print('BUILD_QUEUE_RESPONSE',response,flush=True)
        assert list(response.result)==[1,1]
        w=wait(lambda w:any(u.done for u in w.my(DEPOT)),timeout=60)
        w=wait(lambda w:any(u.tag==worker and u.orders and u.orders[0].target_unit_tag==mineral.tag
                 and u.orders[0].ability_id in (295,3666) for u in a.proto(0).observation.raw_data.units))
        report['build_then_gather_confirmed']=True
        report['worker_orders']=[{'ability':o.ability_id,'target':o.target_unit_tag} for u in a.proto(0).observation.raw_data.units if u.tag==worker for o in u.orders]
        report['engine']=a.version;report['timing']=a.timing()
        print('SKILL_PRIMITIVES_OK',json.dumps(report),flush=True)
    Path('logs/skill_primitives.json').write_text(json.dumps(report,indent=2))

if __name__=='__main__':app.run(main)
