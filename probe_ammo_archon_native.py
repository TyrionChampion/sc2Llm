import fcntl,json,time
from pathlib import Path
from absl import app
from s2clientprotocol import debug_pb2 as d
from google.protobuf.json_format import MessageToDict
from arena.native_engine import NativeArena,RUNTIME
from arena.raw_engine import command

def main(argv):
 deadline=time.monotonic()+400
 while True:
  with open(RUNTIME/'native-match.lock','a') as f:
   try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:
    if time.monotonic()>deadline:raise TimeoutError('slot occupied')
   else:break
  time.sleep(1)
 a=None;report={'scope':'debug-injected owned Terran/Protoss units on native realtime; ability/observation discriminator'}
 try:
  a=NativeArena(names=('Ammo and Archon discriminator',),difficulty='very_easy',render=False);home=a.homes[0]
  cmds=[d.DebugCommand(game_state=d.all_resources),d.DebugCommand(game_state=d.fast_build)]
  for kind,dx,dy in [(26,6,0),(50,3,0),(79,0,3),(75,-3,0),(76,-4,0)]:
   dc=d.DebugCommand();dc.create_unit.unit_type=kind;dc.create_unit.owner=1;dc.create_unit.quantity=1;dc.create_unit.pos.x=home[0]+dx;dc.create_unit.pos.y=home[1]+dy;cmds.append(dc)
  a.controllers[0].debug(cmds);time.sleep(.3);w=a.step([[]])[0]
  tags=[u.tag for u in w.my_units if u.type_id in (26,50,79,75,76)];report['before_abilities']={str(k):sorted(v) for k,v in a.available_abilities(0,tags,ignore_resources=True).items()}
  report['actors']={str(u.type_id):u.tag for u in w.my_units if u.tag in tags}
  w=a.step([[command(710,[w.my(26)[0].tag]),command(1766,[w.my(75)[0].tag,w.my(76)[0].tag])]])[0];deadline=time.monotonic()+8;report['errors']=[]
  while time.monotonic()<deadline:report['errors']+=list(w.errors);w=a.step([[]])[0]
  report['after_abilities']={str(k):sorted(v) for k,v in a.available_abilities(0,[u.tag for u in w.my_units if u.type_id in (26,50,79,141)],ignore_resources=True).items()}
  report['world']=w.summary();report['raw_units']=[MessageToDict(u,preserving_proto_field_name=True) for u in a.proto(0).observation.raw_data.units if u.unit_type in (26,50,79,141,58,85)]
  carrier=w.my(79)[0];dc=d.DebugCommand();dc.create_unit.unit_type=48;dc.create_unit.owner=2;dc.create_unit.quantity=1;dc.create_unit.pos.x=carrier.x-8;dc.create_unit.pos.y=carrier.y
  a.controllers[0].debug([dc]);time.sleep(.2);w=a.step([[]])[0];a.step([[command(23,[carrier.tag],pos=(carrier.x-8,carrier.y))]])
  deadline=time.monotonic()+3;count=0
  while time.monotonic()<deadline:w=a.step([[]])[0];count=max(count,len(w.my(85)))
  report['interceptors_after_launch']=count;report['action_errors']=a.action_errors
  print('AMMO_ARCHON_OBSERVATIONS',json.dumps(report),flush=True)
 finally:
  Path('logs/ammo_archon_native.json').write_text(json.dumps(report,indent=2))
  if a:a.close()
if __name__=='__main__':app.run(main)
