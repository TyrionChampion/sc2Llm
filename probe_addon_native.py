import fcntl,json,time,math
from pathlib import Path
from absl import app
from s2clientprotocol import debug_pb2 as d,query_pb2 as query
from arena.native_engine import NativeArena,RUNTIME
from arena.raw_engine import command
from arena.tasks import TaskQueue

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
  old=json.loads(Path('logs/multirace_advanced_live.json').read_text())['races'][0]
  events=[e for e in old['events'] if e['kind']=='macro_order_issued' and e.get('target_pos')]
  a=NativeArena(names=('Add-on footprint discriminator',),difficulty='very_easy',render=False)
  commands=[d.DebugCommand(game_state=d.all_resources),d.DebugCommand(game_state=d.fast_build)]
  for e in events:
   if e['product'].endswith('tech_lab'):continue
   dc=d.DebugCommand();dc.create_unit.unit_type=a.tech_tree.units[e['product']]['id'];dc.create_unit.owner=1;dc.create_unit.quantity=1;dc.create_unit.pos.x=e['target_pos'][0];dc.create_unit.pos.y=e['target_pos'][1];commands.append(dc)
  a.controllers[0].debug(commands);time.sleep(.4);w=a.step([[]])[0];factory=w.my(27)[0];worker=w.my(45)[0]
  addon=a.tech_tree.units['factory_tech_lab']['recipes'][0]['ability'];spot=(factory.x+2.5,factory.y-.5)
  req=query.RequestQuery()
  for ability,tag in [(addon,factory.tag),(319,0)]:
   p=req.placements.add(ability_id=ability,placing_unit_tag=tag);p.target_pos.x,p.target_pos.y=spot
  report['blocked_site']={'factory':factory.pos,'addon':spot,'results':[r.result for r in a.controllers[0].query(req).placements]}
  candidates=[]
  for dx in range(-18,19,2):
   for dy in range(-18,19,2):candidates.append((a.homes[0][0]+dx,a.homes[0][1]+dy))
  req=query.RequestQuery()
  for x,y in candidates:
   p=req.placements.add(ability_id=328,placing_unit_tag=worker.tag);p.target_pos.x=x;p.target_pos.y=y
   p=req.placements.add(ability_id=319);p.target_pos.x=x+2.5;p.target_pos.y=y-.5
  res=a.controllers[0].query(req).placements
  legal=[p for i,p in enumerate(candidates) if res[2*i].result==1 and res[2*i+1].result==1]
  report['joint_valid_count']=len(legal);assert legal
  spot=min(legal,key=lambda p:math.dist(p,a.homes[0]));before={u.tag for u in w.my(27)}
  w=a.step([[command(328,[worker.tag],pos=spot)]])[0];end=time.monotonic()+20
  while time.monotonic()<end:
   w=a.step([[]])[0];new=next((u for u in w.my(27) if u.tag not in before and u.done),None)
   if new:break
  assert new,'factory did not complete'
  report['new_factory']=new.pos;report['addon_placement']=a.placement(0,addon,new.tag,[(new.x+2.5,new.y-.5)])
  w=a.step([[command(addon,[new.tag])]])[0];end=time.monotonic()+10
  while time.monotonic()<end:
   w=a.step([[]])[0]
   if any(u.done for u in w.my(39)):break
  report['addon_completed']=any(u.done for u in w.my(39));report['action_errors']=a.action_errors
  assert report['addon_completed'] and not a.action_errors
  print('ADDON_FOOTPRINT_PROVED',json.dumps(report),flush=True)
 finally:
  Path('logs/addon_native_discriminator.json').write_text(json.dumps(report,indent=2))
  if a:a.close()
if __name__=='__main__':app.run(main)
