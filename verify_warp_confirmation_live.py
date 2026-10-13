"""Controlled native slow-power warp acknowledgement, zero LLM requests."""
import argparse,json,math,time
from pathlib import Path
from absl import flags
from s2clientprotocol import debug_pb2 as d
from arena.raw_engine import RawArena
from arena.tasks import TaskQueue
from verify_tactical_scenarios_live import Fixture

def main():
 p=argparse.ArgumentParser();p.add_argument('--label',required=True);p.add_argument('--legacy-ack',action='store_true');p.add_argument('--count',type=int,default=1);args=p.parse_args();flags.FLAGS(['warp-confirmation'])
 folder=Path('logs/warp-confirmation-'+args.label);folder.mkdir(exist_ok=False);a=None;q=None
 report=dict(label=args.label,model_calls=0,debug_fixture=True,fast_build=False,legacy_eight_second_ack=args.legacy_ack)
 events=[]
 try:
  a=RawArena(render=False,races=['protoss','terran'],seed=7,game_speed='slower');f=Fixture(a);main=f.worlds[0].my(59)[0]
  dx,dy=88-main.x,86-main.y;length=math.hypot(dx,dy)
  remote=f.point((main.x+dx/length*24,main.y+dy/length*24));f.spawn(60,1,remote)
  f.structure_near(60,main);gate=f.structure_near(133,main);f.structure_near(72,main)
  for _ in range(args.count-1):f.structure_near(133,main)
  a.controllers[0].debug([d.DebugCommand(game_state=d.all_resources)]);f.advance(.5);w=f.worlds[0]
  original_placement=a.placement
  a.placement=lambda index,ability,actor,points:original_placement(index,ability,actor,
      [p for p in points if math.dist(p,remote)<6] if ability==1413 else points)
  q=TaskQueue(a,0,emit=lambda message:None,feedback=lambda kind,**kw:events.append(dict(kind=kind,**kw)))
  q.enqueue([dict(skill='train_unit',args=dict(unit='zealot'),count=args.count)],w.seconds,'controlled warp confirmation');task=q.tasks[-1];start=w.seconds;wall=time.monotonic();baseline={u.tag for u in w.my(73)}
  while w.seconds-start<20 and time.monotonic()-wall<70:
   if args.legacy_ack and task.program:
    for pending in task.program.pending:pending['ack_timeout_s']=8
   actions=q.tick(w,set());w=a.step([actions,[]])[0];time.sleep(.02)
   if task.status=='completed':break
  report.update(state=task.status,reason=task.reason,observed_new_zealots=[dict(tag=u.tag,progress=u.progress,pos=u.pos) for u in w.my(73) if u.tag not in baseline],events=events,engine_action_errors=a.action_errors,game_s=w.seconds-start,remote_pylon=remote,warpgate=gate.pos)
  a.save_replay(folder/'control.SC2Replay')
 except Exception as e:report.update(error=type(e).__name__+': '+str(e))
 finally:
  if q:q.close()
  if a:a.close()
  (folder/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in report.items() if k!='events'},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
