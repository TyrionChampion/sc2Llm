"""35-second delayed public plan in a real advancing game. Controlled fixture, no API."""
import json,time,traceback
from pathlib import Path
from absl import app
from s2clientprotocol import debug_pb2 as d
from arena.native_engine import NativeArena
from arena.commander import Commander

class Delayed:
 compact_battle=True;soft_timeout=15;timeout=40
 def __init__(self):self.enabled=False;self.called=False
 def ready_for_request(self):return self.enabled and not self.called
 def chat(self,system,user):
  self.called=True;time.sleep(35)
  return '{"tasks":[{"skill":"train_unit","args":{"unit":"marine"},"count":2}],"reason":"局势稳定，补充两名陆战队员"}'

def main(argv):
 report={'fixture':True,'realtime':True,'delayed_planner_s':35};c=None
 try:
  with NativeArena(names=('Late-plan validation',),difficulty='very_easy',render=False) as a:
   a.controllers[0].debug([d.DebugCommand(game_state=d.all_resources),d.DebugCommand(game_state=d.fast_build)])
   w=a.step([[]])[0];client=Delayed();c=Commander(a,0,client,opening=False,feedback_dir='logs/runtime-validation-feedback')
   c.tasks.enqueue([{'skill':'supply_depot'},{'skill':'barracks'}],w.seconds)
   deadline=time.monotonic()+25
   while time.monotonic()<deadline and not any(u.done for u in w.my(21)):
    w=a.step([c.tick(w)])[0];c.ingest_feedback(w)
   assert any(u.done for u in w.my(21))
   # Consume production completion before capturing the unchanged-state delayed request.
   w=a.step([c.tick(w)])[0];client.enabled=True
   deadline=time.monotonic()+48;start=w.seconds;max_tick=0
   while time.monotonic()<deadline:
    t=time.monotonic();actions=c.tick(w);max_tick=max(max_tick,time.monotonic()-t)
    w=a.step([actions])[0];c.ingest_feedback(w)
    if len(w.my(48))>=2 and all(u.done for u in w.my(48)):break
   report.update(accepted=c.decisions,late_accepted=c.late_plans_accepted,stale=c.plans_rejected,errors=c.errors,
                 marine_count=len(w.my(48)),game_s_advanced=w.seconds-start,max_commander_tick_s=max_tick,
                 action_errors=a.action_errors,timing=a.clock.report(),feedback_log=c.feedback.path)
   assert c.late_plans_accepted==1 and c.errors==0 and len(w.my(48))==2 and a.action_errors==0,report
   report['passed']=True
 except BaseException as error:
  report.update(error=str(error),traceback=traceback.format_exc());raise
 finally:
  if c:c.stop()
  Path('logs/late_macro_native.json').write_text(json.dumps(report,indent=2,ensure_ascii=False));print('LATE_MACRO',json.dumps(report,ensure_ascii=False),flush=True)
if __name__=='__main__':app.run(main)
