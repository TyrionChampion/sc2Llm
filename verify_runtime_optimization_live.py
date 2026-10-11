"""Controlled native realtime evidence, debug resources/units; NOT a full LLM match."""
import json,time,threading,traceback,math
from pathlib import Path
from absl import app
from s2clientprotocol import debug_pb2 as d
from arena.native_engine import NativeArena
from arena.raw_engine import command
from arena.commander import Commander

class CapturePlanner:
 compact_battle=True;soft_timeout=15;timeout=40
 def __init__(self):self.enabled=False;self.release=threading.Event();self.prompts=[]
 def ready_for_request(self):return self.enabled
 def chat(self,system,user):
  self.prompts.append(json.loads(user))
  if len(self.prompts)==1 and not self.release.wait(20):raise TimeoutError('fixture release was not signalled')
  return '{"tasks":[],"reason":"测试等待，保持任务"}'

def main(argv):
 report={'fixture':True,'realtime':True,'model':'controlled capture planner, no real API call','actions':[],'events':[]}
 c=None
 try:
  with NativeArena(names=('Runtime fixes validation',),difficulty='very_easy',render=False) as a:
   report['version']=a.version;report['native_directory']=str(a.directory)
   a.controllers[0].debug([d.DebugCommand(game_state=d.all_resources)])
   w=a.step([[]])[0];client=CapturePlanner();c=Commander(a,0,client,opening=False,interval=1,feedback_dir='logs/runtime-validation-feedback')
   def step():
    nonlocal w
    actions=c.tick(w)
    if actions:report['actions'].extend(dict(game_s=w.seconds,ability=x.action_raw.unit_command.ability_id,tags=list(x.action_raw.unit_command.unit_tags),target=x.action_raw.unit_command.target_unit_tag,pos=[x.action_raw.unit_command.target_world_space_pos.x,x.action_raw.unit_command.target_world_space_pos.y]) for x in actions)
    w=a.step([actions])[0];c.ingest_feedback(w)
    return w
   def until(predicate,seconds=30):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
     step()
     if predicate():return
    raise AssertionError('native fixture condition timed out: '+str(predicate))
   c.tasks.enqueue([{'skill':'supply_depot'}],w.seconds)
   until(lambda:bool(w.my(19)))
   t=c.tasks.tasks[0];owner=next(iter(t.program.tags));started=w.seconds
   until(lambda:w.seconds-started>=5)
   unexpected=[x for x in report['actions'] if x['ability']==1 and x['target']==w.my(19)[0].tag]
   assert not unexpected,unexpected
   assert t.program.tags=={owner}
   report['construction_before_loss']={'owner':owner,'extra_builder_commands':len(unexpected),'progress':w.my(19)[0].progress}
   kill=d.DebugCommand();kill.kill_unit.tag.append(owner);a.controllers[0].debug([kill]);until(lambda:owner not in t.program.tags)
   replacement=next(iter(t.program.tags));assert replacement!=owner
   until(lambda:t.status=='completed',35)
   resumes=[x for x in report['actions'] if x['ability']==1 and x['target']==w.my(19)[0].tag]
   report['construction']={'original_owner':owner,'replacement':replacement,'resume_commands':len(resumes),'completed':t.status=='completed'}
   assert len(resumes)==1
   # Brief sighting while planner is in flight, then the only nearby own scout dies.
   route=c.rally.grid.route(a.homes[0],a.enemy_start(0));mid=route[len(route)//2];pos=(mid[0]+.5,mid[1]+.5)
   client.enabled=True;until(lambda:len(client.prompts)==1)
   creates=[]
   for owner_id,typ,dx in [(1,82,0),(2,54,3)]:
    cmd=d.DebugCommand();cmd.create_unit.owner=owner_id;cmd.create_unit.unit_type=typ;cmd.create_unit.quantity=1;cmd.create_unit.pos.x=pos[0]+dx;cmd.create_unit.pos.y=pos[1];creates.append(cmd)
   old_scv={u.tag for u in w.my(82)};a.controllers[0].debug(creates)
   until(lambda:any(u.type_id==54 for u in w.enemy_units) and bool({u.tag for u in w.my(82)}-old_scv),8)
   scout=next(u.tag for u in w.my(82) if u.tag not in old_scv);enemy=next(u.tag for u in w.enemy_units if u.type_id==54);seen_at=w.seconds
   kill=d.DebugCommand();kill.kill_unit.tag.append(scout);a.controllers[0].debug([kill])
   until(lambda:w.by_tag(scout) is None and not any(u.tag==enemy for u in w.enemy_units),8)
   died_at=w.seconds;client.release.set()
   until(lambda:len(client.prompts)>=2,5)
   prompt=client.prompts[1];contacts=prompt['intelligence']['contacts']
   assert any(g['unit']=='Medivac' and g['state']=='last_seen' for g in contacts),contacts
   deaths=[x for x in prompt['intelligence']['pending_alerts'] if x['kind']=='own_unit_death_observed' and x['unit']['tag']==scout]
   assert deaths and deaths[0]['nearby_recent_contacts']
   report['scouting']={'seen_game_s':seen_at,'death_game_s':deaths[0]['game_s'],'lost_visibility_game_s':died_at,'next_prompt_game_s':prompt['world']['game_s'],'enemy_currently_visible':prompt['world']['visible_enemy'],'remembered':contacts,'death_alert':deaths,'delivery_after_death_game_s':prompt['world']['game_s']-deaths[0]['game_s']}
   client.enabled=False
   # Production itself remains real; fast build is enabled only in this controlled rally stage.
   a.controllers[0].debug([d.DebugCommand(game_state=d.fast_build)])
   c.tasks.enqueue([{'skill':'barracks'}],w.seconds)
   until(lambda:bool(w.my(21)) and w.my(21)[0].done,20)
   barracks=w.my(21)[0]
   until(lambda:barracks.tag in c.rally.confirmed,10)
   main_point=c.rally.confirmed[barracks.tag];main_state=c.rally.public()
   c.tasks.enqueue([{'skill':'train_unit','args':{'unit':'marine'}}],w.seconds)
   until(lambda:bool(w.my(48)),8)
   born=w.my(48)[0].pos;until(lambda:w.my(48)[0].distance(main_point)<3,30)
   report['main_rally']={'policy':main_state,'producer_rally_targets':w.by_tag(barracks.tag).rally_targets,'spawn':born,'arrival':w.my(48)[0].pos,'distance_to_anchor':w.my(48)[0].distance(main_point)}
   assert main_state['landmark']=='main_ramp_top'
   # Macro chooses a legal mineral expansion; rally should move when it is founded.
   c.tasks.enqueue([{'skill':'expand'}],w.seconds)
   until(lambda:len(w.my(18))>=2 and all(u.done for u in w.my(18)),25)
   until(lambda:c.rally.state.get('landmark')=='expansion_entrance' and math.dist(c.rally.confirmed.get(barracks.tag,main_point),main_point)>3,15)
   expansion_point=c.rally.confirmed[barracks.tag];previous={u.tag for u in w.my(48)}
   first_marine=w.my(48)[0].tag
   c.tasks.enqueue([{'skill':'train_unit','args':{'unit':'marine'}}],w.seconds)
   until(lambda:any(u.tag not in previous for u in w.my(48)),8)
   new=next(u.tag for u in w.my(48) if u.tag not in previous)
   until(lambda:w.by_tag(new) is not None and w.by_tag(new).distance(expansion_point)<3,45)
   report['expansion_rally']={'policy':c.rally.public(),'new_marine':new,'distance_to_anchor':w.by_tag(new).distance(expansion_point),'existing_marine_distance_from_original_anchor':w.by_tag(first_marine).distance(main_point)}
   assert not [x for x in report['actions'] if first_marine in x['tags']],'existing unit was recalled'
   report['action_errors']=a.action_errors;report['timing']=a.clock.report();report['feedback_log']=str(c.feedback.path)
   assert a.action_errors==0
   report['passed']=True
 except BaseException as error:
  report['error']=type(error).__name__+': '+str(error);report['traceback']=traceback.format_exc();raise
 finally:
  if c:
   client.release.set();c.stop()
  Path('logs/runtime_optimization_native_after.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
  print('VALIDATION',json.dumps({k:v for k,v in report.items() if k not in ('actions','events','scouting')},ensure_ascii=False),flush=True)
if __name__=='__main__':app.run(main)
