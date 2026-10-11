import json,time,math
from pathlib import Path
from absl import app
from s2clientprotocol import debug_pb2 as d
from arena.native_engine import NativeArena
from arena.raw_engine import command
from arena.tasks import TaskQueue
from arena.python_skills import PythonSkillRun,IsolatedWorker

def main(argv):
 a=None;worker=None;report={'scope':'native realtime with injected stalker, marine and medivac; injected upgrades; isolated Python cast and raw cargo observation'}
 try:
  a=NativeArena(names=('Scoped cast verification',),difficulty='very_easy',render=False);home=a.homes[0]
  cmds=[d.DebugCommand(game_state=d.upgrade)]
  for kind,dx,dy in [(74,-8,2),(54,-4,5),(48,-4,5)]:
   dc=d.DebugCommand();dc.create_unit.unit_type=kind;dc.create_unit.owner=1;dc.create_unit.quantity=1;dc.create_unit.pos.x=home[0]+dx;dc.create_unit.pos.y=home[1]+dy;cmds.append(dc)
  a.controllers[0].debug(cmds);time.sleep(.3);w=a.step([[]])[0];stalker=w.my(74)[0];point=[stalker.x-3,stalker.y];name=a.tech_tree.ability_names[1442]
  entry={'manifest':{'name':'scoped_blink','unit':'stalker','actions':['cast'],'parameters':{}},'sha256':'controlled-native-probe',
   'source':('def tick(ctx,state):\n return {"actions":[{"action":"cast","ability":'+repr(name)+',"unit_tags":[ctx["units"][0]["tag"]],"target":'+repr(point)+'}],"state":{},"status":"running","reason":"scoped probe"}\n').encode()}
  q=TaskQueue(a,0,lambda x:None);run=PythonSkillRun(entry,{'params':{},'count':1,'target':'home','retreat_target':'home','timeout_s':30});run.tags={stalker.tag};run.mission={'objective':home,'retreat':home}
  context=run.context(w,q);worker=IsolatedWorker(entry);worker.submit(context,{});until=time.monotonic()+3
  while True:
   reply=worker.poll()
   if reply is not None:break
   if time.monotonic()>until:raise TimeoutError('isolated worker')
   time.sleep(.002)
  compiled=run.validate_result(reply['result'],w,q);start_loop=w.game_loop
  w=a.step([[command(ability,tags,pos=pos,target=tag) for _,ability,tags,pos,tag in compiled]])[0]
  samples=[]
  for _ in range(5):
   w=a.step([[]])[0];u=w.by_tag(stalker.tag);samples.append({'loop':w.game_loop,'pos':u.pos,'distance_moved':u.distance(stalker),'distance_target':u.distance(point)})
  after_abilities=a.available_abilities(0,[stalker.tag])[stalker.tag]
  report['blink']={'before':stalker.pos,'start_loop':start_loop,'requested':point,'samples':samples,'ability_available_before':name in [x['name'] for x in context['available_spells'][str(stalker.tag)]],'cooldown_after':not a.tech_tree.ability_available(1442,after_abilities)}
  assert report['blink']['ability_available_before'] and report['blink']['cooldown_after']
  assert any(s['distance_moved']>2 and s['distance_target']<1.5 and s['loop']-start_loop<12 for s in samples),'no timely blink displacement'
  marine=w.my(48)[0];transport=w.my(54)[0];load=next(x for x in a.tech_tree.public_spells(54) if 'load_medivac'==x['name'])['ability_id']
  w=a.step([[command(load,[transport.tag],target=marine.tag)]])[0];deadline=time.monotonic()+5
  while time.monotonic()<deadline:
   w=a.step([[]])[0]
   if marine.tag in w.by_tag(transport.tag).passengers:break
  run.tags={marine.tag,transport.tag};run.entry['manifest']['actions']=['move'];ctx=run.context(w,q)
  report['cargo']={'passengers':list(w.by_tag(transport.tag).passengers),'embarked':ctx['embarked'],'visible_squad':[u['tag'] for u in ctx['units']]}
  assert marine.tag in report['cargo']['passengers'];assert any(e['tag']==marine.tag for e in ctx['embarked'])
  report['action_errors']=a.action_errors;report['status']='passed';assert not a.action_errors
 finally:
  if worker:worker.close()
  if a:a.close()
  Path('logs/multirace_cast_final.json').write_text(json.dumps(report,indent=2))
if __name__=='__main__':app.run(main)
