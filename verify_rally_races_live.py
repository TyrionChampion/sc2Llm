"""Native Zerg/Protoss rally discriminator; accelerated debug fixture, no LLM."""
import json,time,traceback
from pathlib import Path
from absl import app
from s2clientprotocol import debug_pb2 as d
from arena.native_engine import NativeArena
from arena.commander import Commander
from arena.raw_engine import command

class Idle:
 def ready_for_request(self):return False
 def chat(self,*args):raise AssertionError('no model call allowed in fixture')

def main(argv):
 report={'fixture':True,'realtime':True};cs=[]
 try:
  with NativeArena(names=('Zerg rally test','Protoss rally test'),races=['zerg','protoss'],render=False) as a:
   report['version']=a.version;report['native_directory']=str(a.directory)
   a.controllers[0].debug([d.DebugCommand(game_state=d.all_resources),d.DebugCommand(game_state=d.fast_build)])
   ws=a.step([[],[]]);cs=[Commander(a,i,Idle(),opening=False,feedback_dir='logs/runtime-validation-feedback') for i in (0,1)]
   hatch=ws[0].my(86)[0]
   # Establish an explicit WORKER mineral rally before automatic UNIT rally is enabled.
   mineral=min((u for u in ws[0].units if u.mineral),key=lambda u:u.distance(hatch))
   ws=a.step([[command(212,[hatch.tag],target=mineral.tag)],[]]);report['worker_rally_before']={'mineral_tag':mineral.tag,'rallies':ws[0].by_tag(hatch.tag).rally_targets}
   cs[0].tasks.enqueue([{'skill':'build_structure','args':{'structure':'spawning_pool'}},{'skill':'train_unit','args':{'unit':'zergling'},'count':2},{'skill':'train_worker'}],ws[0].seconds)
   cs[1].tasks.enqueue([{'skill':'build_structure','args':{'structure':'pylon'}},{'skill':'build_structure','args':{'structure':'gateway'}},{'skill':'train_unit','args':{'unit':'zealot'}}],ws[1].seconds)
   deadline=time.monotonic()+80
   done=[False,False]
   while time.monotonic()<deadline:
    batches=[c.tick(w) for c,w in zip(cs,ws)];ws=a.step(batches)
    for c,w in zip(cs,ws):c.ingest_feedback(w)
    for i,typ in enumerate((105,73)):
     point=cs[i].rally.state.get('pos')
     done[i]=bool(point and ws[i].my(typ) and any(u.distance(point)<3 for u in ws[i].my(typ)) and cs[i].rally.confirmed)
    if all(done):break
   report['players']=[{'race':a.races[i],'rally':cs[i].rally.public(),'new_units':[(u.tag,u.pos,u.distance(cs[i].rally.state['pos'])) for u in ws[i].my(typ)],'arrived':done[i]} for i,typ in enumerate((105,73))]
   report['worker_rally_after']=ws[0].by_tag(hatch.tag).rally_targets
   report['action_errors']=a.action_errors;report['timing']=a.clock.report()
   assert all(done),report['players']
   assert any(x[2]==mineral.tag for x in report['worker_rally_after']),report
   assert a.action_errors==0
   report['passed']=True
 except BaseException as error:
  report['error']=type(error).__name__+': '+str(error);report['traceback']=traceback.format_exc();raise
 finally:
  for c in cs:c.stop()
  Path('logs/rally_races_native.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
  print('RALLY_RACES',json.dumps(report,ensure_ascii=False),flush=True)
if __name__=='__main__':app.run(main)
