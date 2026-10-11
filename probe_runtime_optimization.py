"""Before-change native discriminators: debug resources, realtime, no model."""
import json,time
from pathlib import Path
from absl import app
from s2clientprotocol import debug_pb2 as d
from google.protobuf.json_format import MessageToDict
from arena.native_engine import NativeArena
from arena.tasks import TaskQueue

def main(argv):
 report={'fixture':True,'realtime':True,'rows':[]}
 try:
  with NativeArena(names=('Construction/rally probe',),difficulty='very_easy',render=False) as a:
   report['version']=a.version
   a.controllers[0].debug([d.DebugCommand(game_state=d.all_resources)])
   w=a.step([[]])[0];events=[]
   q=TaskQueue(a,0,lambda s:None,lambda kind,**kw:events.append(dict(kind=kind,**kw)))
   q.enqueue([{'skill':'supply_depot'}],w.seconds)
   end=time.monotonic()+8
   while time.monotonic()<end:
    actions=q.tick(w,set());t=q.tasks[0]
    report['rows'].append({'game_s':w.seconds,'progress':[(u.tag,u.progress) for u in w.my(19)],'commands':[MessageToDict(x,preserving_proto_field_name=True) for x in actions],
      'owners':sorted(t.program.tags),'workers':[{'tag':u.tag,'orders':u.orders,'targets':u.order_targets,'positions':u.order_positions} for u in w.my(45) if u.orders]})
    w=a.step([actions])[0]
   report['events']=events;report['errors']=a.action_errors
   report['rally_abilities']=[MessageToDict(v,preserving_proto_field_name=True) for v in a.tech_tree.abilities.values() if 'rally' in (v.link_name+' '+v.friendly_name).lower()]
   info=a.info.start_raw
   Path('logs/runtime_map_info.pb').write_bytes(info.SerializeToString())
   report['map']={'home':a.homes[0],'enemy':a.enemy_start(0),'size':a.size,'terrain_bits':info.terrain_height.bits_per_pixel,'path_bits':info.pathing_grid.bits_per_pixel,'placement_bits':info.placement_grid.bits_per_pixel}
   report['raw_rally_fields']=[f.name for f in a.proto(0).observation.raw_data.units[0].DESCRIPTOR.fields if 'rally' in f.name]
   print('PREMISE',json.dumps({k:v for k,v in report.items() if k not in ('rows','events')},ensure_ascii=False),flush=True)
 finally:
  Path('logs/runtime_optimization_native_before.json').write_text(json.dumps(report,indent=2))
if __name__=='__main__':app.run(main)
