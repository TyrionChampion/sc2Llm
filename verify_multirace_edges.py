"""Native realtime edge cases: reactor, addon relocation, ammo, scoped spell cast."""
import fcntl,json,time,math
from pathlib import Path
from types import SimpleNamespace
from absl import app
from s2clientprotocol import debug_pb2 as d
from arena.native_engine import NativeArena,RUNTIME
from arena.raw_engine import command
from arena.tasks import TaskQueue
from arena.reflex import ReflexNet
from arena.python_skills import PythonSkillRun,IsolatedWorker

def main(argv):
 until=time.monotonic()+600
 while True:
  with open(RUNTIME/'native-match.lock','a') as f:
   try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:
    if time.monotonic()>until:raise TimeoutError('native test slot occupied')
   else:break
  time.sleep(1)
 a=None;q=None;worker=None;report={'scope':'native realtime; debug resources/fast_build, injected blockers/carrier/stalker and upgrades for cast only; no model','events':[],'steps':[]}
 try:
  a=NativeArena(names=('Three-race edge verification',),difficulty='very_easy',render=False)
  q=TaskQueue(a,0,feedback=lambda kind,**kw:report['events'].append({'kind':kind,**kw}));reflex=ReflexNet(a.homes[0],lambda s:None,combat_enabled=False)
  a.controllers[0].debug([d.DebugCommand(game_state=d.all_resources),d.DebugCommand(game_state=d.fast_build)]);time.sleep(.3);w=a.step([[]])[0]
  def run(skill,args={},count=1,timeout=35):
   nonlocal w
   q.enqueue([{'skill':skill,'args':args,'count':count}],w.seconds,'edge test');t=q.tasks[-1];start=time.monotonic()
   while time.monotonic()-start<timeout and t.active:
    reactions,reserved=reflex.tick(w,q.protected());w=a.step([reactions+q.tick(w,reserved)])[0]
   rec={'skill':skill,'args':args,'count':count,'state':t.status,'reason':t.reason,'world':w.summary()};report['steps'].append(rec)
   print('EDGE_STEP',json.dumps(rec),flush=True)
   if t.active:q.update(t,'cancelled','controlled test deadline; no success claimed')
   Path('logs/multirace_edges_final.json').write_text(json.dumps(report,indent=2))
   return rec
  for _ in range(3):assert run('build_supply')['state']=='completed'
  assert run('build_structure',{'structure':'barracks'})['state']=='completed'
  assert run('build_structure',{'structure':'barracks_reactor'})['state']=='completed'
  assert run('train_unit',{'unit':'marine'},2)['state']=='completed'
  assert any(e['kind']=='reactor_second_order_issued' for e in report['events'])
  assert run('build_structure',{'structure':'factory'})['state']=='completed'
  factory=w.my(27)[0]
  dc=d.DebugCommand();dc.create_unit.unit_type=19;dc.create_unit.owner=1;dc.create_unit.quantity=1;dc.create_unit.pos.x=factory.x+2.5;dc.create_unit.pos.y=factory.y-.5
  a.controllers[0].debug([dc]);time.sleep(.2);w=a.step([[]])[0]
  assert run('build_structure',{'structure':'factory_tech_lab'},timeout=50)['state']=='completed'
  assert any(e['kind']=='addon_relocation_confirmed' for e in report['events'])
  assert run('train_unit',{'unit':'siege_tank'})['state']=='completed'
  assert run('build_structure',{'structure':'ghost_academy'})['state']=='completed'
  dc=d.DebugCommand();dc.create_unit.unit_type=50;dc.create_unit.owner=1;dc.create_unit.quantity=1;dc.create_unit.pos.x=a.homes[0][0]-3;dc.create_unit.pos.y=a.homes[0][1]
  a.controllers[0].debug([dc]);time.sleep(.2);w=a.step([[]])[0]
  assert run('train_unit',{'unit':'nuke'})['state']=='completed'
  report['ammo_ability_queries']={str(k):sorted(v) for k,v in a.available_abilities(0,[u.tag for u in w.my(26)]).items()}
  home=a.homes[0];commands=[d.DebugCommand(game_state=d.upgrade)]
  for kind,dx in [(74,0),(79,-3)]:
   dc=d.DebugCommand();dc.create_unit.unit_type=kind;dc.create_unit.owner=1;dc.create_unit.quantity=1;dc.create_unit.pos.x=home[0]+dx;dc.create_unit.pos.y=home[1]+5;commands.append(dc)
  a.controllers[0].debug(commands);time.sleep(.3);w=a.step([[]])[0]
  # Carrier's auto-production stays enabled. Record actual visibility of its ammo.
  report['interceptor_initial']=len(w.my(85));report['interceptor_scope']='Protoss recipe on debug-injected owned carrier; no natural cross-race production claim'
  q.race='protoss';assert run('train_unit',{'unit':'interceptor'})['state']=='completed';q.race='terran'
  stalker=w.my(74)[0];name=a.tech_tree.ability_names[1442];point=[stalker.x-3,stalker.y]
  entry={'manifest':{'name':'probe_blink','unit':'stalker','actions':['cast'],'parameters':{}},'sha256':'controlled-probe',
   'source':('def tick(ctx,state):\n return {"actions":[{"action":"cast","ability":'+repr(name)+',"unit_tags":[ctx["units"][0]["tag"]],"target":'+repr(point)+'}],"state":{},"status":"running","reason":"probe"}\n').encode()}
  runpy=PythonSkillRun(entry,{'params':{},'count':1,'target':'home','retreat_target':'home','timeout_s':30});runpy.tags={stalker.tag};runpy.mission={'objective':home,'retreat':home}
  context=runpy.context(w,q);worker=IsolatedWorker(entry);worker.submit(context,{});until=time.monotonic()+3
  while True:
   reply=worker.poll()
   if reply is not None:break
   if time.monotonic()>until:raise TimeoutError('isolated spell worker')
   time.sleep(.002)
  compiled=runpy.validate_result(reply['result'],w,q);before=stalker.pos
  w=a.step([[command(ability,tags,pos=pos,target=tag) for _,ability,tags,pos,tag in compiled]])[0];until=time.monotonic()+2
  while time.monotonic()<until:
   w=a.step([[]])[0]
   if w.by_tag(stalker.tag).distance(point)<.5:break
  report['isolated_blink']={'before':before,'target':point,'after':w.by_tag(stalker.tag).pos,'confirmed':w.by_tag(stalker.tag).distance(point)<.5,'context_spell_available':name in [x['name'] for x in context['available_spells'][str(stalker.tag)]]}
  assert report['isolated_blink']['confirmed'];report['action_errors']=a.action_errors;report['timing']=a.timing()
  assert not a.action_errors
 finally:
  if worker:worker.close()
  if q:q.close()
  if a:a.close()
  Path('logs/multirace_edges_final.json').write_text(json.dumps(report,indent=2))
if __name__=='__main__':app.run(main)
