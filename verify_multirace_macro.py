"""Controlled REALTIME native integration, debug resources/fast-build, no tech cheat."""
import json,time,traceback
from pathlib import Path
from absl import app
from s2clientprotocol import debug_pb2 as d
from arena.native_engine import NativeArena
from arena.tasks import TaskQueue
from arena.reflex import ReflexNet
CHAINS={
 'terran': [('build_supply',{}),('build_structure',{'structure':'refinery'}),('gather_gas',{'workers_per_refinery':3}),('build_structure',{'structure':'barracks'}),('build_structure',{'structure':'barracks_tech_lab'}),('train_unit',{'unit':'marauder'}),('research',{'upgrade':'concussive_shells'}),('expand',{})],
 'zerg': [('build_supply',{}),('build_structure',{'structure':'extractor'}),('gather_gas',{'workers_per_refinery':3}),('build_structure',{'structure':'spawning_pool'}),('build_structure',{'structure':'roach_warren'}),('train_unit',{'unit':'roach'}),('train_unit',{'unit':'ravager'}),('research',{'upgrade':'metabolic_boost'}),('expand',{})],
 'protoss': [('build_supply',{}),('build_structure',{'structure':'assimilator'}),('gather_gas',{'workers_per_refinery':3}),('build_structure',{'structure':'gateway'}),('build_structure',{'structure':'cybernetics_core'}),('train_unit',{'unit':'stalker'}),('research',{'upgrade':'warp_gate_research'}),('morph_unit',{'unit':'warp_gate'}),('train_unit',{'unit':'stalker'}),('expand',{})]}
def main(argv):
 report={'scope':'native realtime, debug minerals/gas/fast_build; NO tech-tree cheat, no model, not natural match','races':[]}
 for race,chain in CHAINS.items():
  a=None;q=None;rec={'race':race,'steps':[]};report['races'].append(rec)
  try:
   a=NativeArena(names=(race+' integration',),races=[race],difficulty='very_easy',render=False)
   late=a.controllers[0].data_raw();rec['metadata_after_startup']={'cc_minerals':next(u.mineral_cost for u in late.units if u.unit_id==18),'init_cc_minerals':a.types[18].mineral_cost}
   q=TaskQueue(a,0,feedback=lambda kind,**kw: None);reflex=ReflexNet(a.homes[0],print,combat_enabled=False)
   a.controllers[0].debug([d.DebugCommand(game_state=d.all_resources)])
   time.sleep(.2)
   a.controllers[0].debug([d.DebugCommand(game_state=d.fast_build)])
   time.sleep(.2);w=a.step([[]])[0];rec['known_minerals']=len([u for u in w.units if u.mineral]);rec['before']=w.summary();rec['engine']=a.version
   for skill,args in chain:
    q.enqueue([{'skill':skill,'args':args}],w.seconds,'controlled integration');task=q.tasks[-1];start=time.monotonic();gas_before=w.vespene
    while time.monotonic()-start<65 and task.active:
     reactions,reserved=reflex.tick(w,q.protected());out=q.tick(w,reserved);w=a.step([reactions+out])[0]
    row={'skill':skill,'args':args,'state':task.status,'reason':task.reason,'wall_s':time.monotonic()-start,'world':w.summary(),'errors':a.action_errors}
    if skill=='gather_gas':
     until=time.monotonic()+12
     while time.monotonic()<until and w.vespene<=gas_before:
      reactions,reserved=reflex.tick(w,q.protected());w=a.step([reactions+q.tick(w,reserved)])[0]
     row['gas_before']=gas_before;row['gas_after']=w.vespene
    rec['steps'].append(row);print('MACRO_STEP',json.dumps(row,ensure_ascii=False),flush=True)
    Path('logs/multirace_macro_live.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    assert task.status=='completed',(race,skill,args,task.reason)
    if skill=='gather_gas':assert w.vespene>gas_before,'gas income absent'
   rec['gas_assignments']=q.gas_assignments;rec['timing']=a.timing();rec['status']='passed';assert a.action_errors==0
   a.save_replay('logs/multirace_macro_'+race+'.SC2Replay')
  except Exception as e:
   rec.update(status='failed',error=repr(e),traceback=traceback.format_exc());print('RACE_FAILED',race,repr(e),flush=True)
  finally:
   if q:q.close()
   if a:a.close()
   Path('logs/multirace_macro_live.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
 assert all(r['status']=='passed' for r in report['races']),report
if __name__=='__main__':app.run(main)
