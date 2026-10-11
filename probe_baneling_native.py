import fcntl,json,time
from pathlib import Path
from absl import app
from s2clientprotocol import debug_pb2 as d
from arena.native_engine import NativeArena,RUNTIME
from arena.raw_engine import command

def main(argv):
 deadline=time.monotonic()+600
 while True:
  with open(RUNTIME/'native-match.lock','a') as f:
   try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:
    if time.monotonic()>deadline:raise TimeoutError('test slot occupied')
   else:break
  time.sleep(1)
 a=None;report={}
 try:
  a=NativeArena(names=('Baneling ability discriminator',),races=['zerg'],difficulty='very_easy',render=False)
  home=a.homes[0];commands=[d.DebugCommand(game_state=d.all_resources),d.DebugCommand(game_state=d.fast_build)]
  for kind,n,dx in [(96,1,7),(105,3,3)]:
   dc=d.DebugCommand();dc.create_unit.unit_type=kind;dc.create_unit.owner=1;dc.create_unit.quantity=n;dc.create_unit.pos.x=home[0]+dx;dc.create_unit.pos.y=home[1];commands.append(dc)
  a.controllers[0].debug(commands);time.sleep(.4);w=a.step([[]])[0];lings=w.my(105);assert len(lings)==3
  report['before']={'world':w.summary(),'abilities':{str(k):sorted(v) for k,v in a.available_abilities(0,[u.tag for u in lings]).items()},'unit_creation':a.types[9].ability_id,'reference_recipe':a.tech_tree.units['baneling']}
  report['orders']=[{'tag':u.tag,'ability':aid} for u,aid in zip(lings,[4121,80,4124])]
  w=a.step([[command(aid,[u.tag]) for u,aid in zip(lings,[4121,80,4124])]])[0];report['errors']=[]
  until=time.monotonic()+5
  while time.monotonic()<until:
   report['errors']+=list(w.errors);w=a.step([[]])[0]
  report['after']={'world':w.summary(),'units':[{'tag':u.tag,'type':u.type_id,'orders':list(u.orders)} for u in w.my_units if u.type_id in (9,105,8)]}
  print('BANELING_PROBE',json.dumps(report),flush=True)
 finally:
  Path('logs/baneling_native_bound.json').write_text(json.dumps(report,indent=2))
  if a:a.close()
if __name__=='__main__':app.run(main)
