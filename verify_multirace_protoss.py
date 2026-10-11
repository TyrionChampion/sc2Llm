"""Controlled REALTIME native integration, debug resources/fast-build, no tech cheat."""
import json,time,traceback
from pathlib import Path
from absl import app
from s2clientprotocol import debug_pb2 as d
from arena.native_engine import NativeArena
from arena.tasks import TaskQueue
from arena.reflex import ReflexNet
CHAINS={'terran': [('build_supply', {}), ('build_supply', {}), ('build_supply', {}), ('build_supply', {}), ('build_supply', {}), ('build_supply', {}), ('build_structure', {'structure': 'refinery'}), ('build_structure', {'structure': 'barracks'}), ('build_structure', {'structure': 'barracks_tech_lab'}), ('build_structure', {'structure': 'factory'}), ('build_structure', {'structure': 'factory_tech_lab'}), ('build_structure', {'structure': 'armory'}), ('build_structure', {'structure': 'starport'}), ('build_structure', {'structure': 'starport_tech_lab'}), ('build_structure', {'structure': 'fusion_core'}), ('build_structure', {'structure': 'ghost_academy'}), ('build_structure', {'structure': 'engineering_bay'}), ('train_unit', {'unit': 'marine'}), ('train_unit', {'unit': 'marauder'}), ('train_unit', {'unit': 'reaper'}), ('train_unit', {'unit': 'ghost'}), ('train_unit', {'unit': 'hellion'}), ('train_unit', {'unit': 'hellbat'}), ('train_unit', {'unit': 'cyclone'}), ('train_unit', {'unit': 'widow_mine'}), ('train_unit', {'unit': 'siege_tank'}), ('train_unit', {'unit': 'thor'}), ('train_unit', {'unit': 'viking_fighter'}), ('train_unit', {'unit': 'medivac'}), ('train_unit', {'unit': 'liberator'}), ('train_unit', {'unit': 'raven'}), ('train_unit', {'unit': 'banshee'}), ('train_unit', {'unit': 'battlecruiser'}), ('research', {'upgrade': 'stimpack'}), ('research', {'upgrade': 'combat_shield'}), ('research', {'upgrade': 'terran_infantry_weapons_level1'}), ('morph_unit', {'unit': 'siege_tank_sieged'}), ('morph_unit', {'unit': 'siege_tank'}), ('morph_unit', {'unit': 'hellbat'}), ('morph_unit', {'unit': 'viking_assault'})], 'zerg': [('build_supply', {}), ('build_supply', {}), ('build_supply', {}), ('build_supply', {}), ('build_supply', {}), ('build_supply', {}), ('build_supply', {}), ('train_worker', {}), ('train_worker', {}), ('train_worker', {}), ('train_worker', {}), ('train_worker', {}), ('train_worker', {}), ('train_worker', {}), ('train_worker', {}), ('train_worker', {}), ('train_worker', {}), ('train_worker', {}), ('train_worker', {}), ('build_structure', {'structure': 'extractor'}), ('build_structure', {'structure': 'spawning_pool'}), ('build_structure', {'structure': 'baneling_nest'}), ('build_structure', {'structure': 'roach_warren'}), ('build_structure', {'structure': 'lair'}), ('build_structure', {'structure': 'hydralisk_den'}), ('build_structure', {'structure': 'lurker_den'}), ('build_structure', {'structure': 'infestation_pit'}), ('build_structure', {'structure': 'spire'}), ('build_structure', {'structure': 'hive'}), ('build_structure', {'structure': 'greater_spire'}), ('build_structure', {'structure': 'ultralisk_cavern'}), ('build_structure', {'structure': 'evolution_chamber'}), ('build_structure', {'structure': 'nydus_network'}), ('train_unit', {'unit': 'queen'}), ('train_unit', {'unit': 'zergling'}), ('train_unit', {'unit': 'baneling'}), ('train_unit', {'unit': 'roach'}), ('train_unit', {'unit': 'ravager'}), ('train_unit', {'unit': 'hydralisk'}), ('train_unit', {'unit': 'lurker'}), ('train_unit', {'unit': 'infestor'}), ('train_unit', {'unit': 'swarm_host'}), ('train_unit', {'unit': 'mutalisk'}), ('train_unit', {'unit': 'corruptor'}), ('train_unit', {'unit': 'brood_lord'}), ('train_unit', {'unit': 'viper'}), ('train_unit', {'unit': 'ultralisk'}), ('train_unit', {'unit': 'overseer'}), ('train_unit', {'unit': 'overlord_transport'}), ('research', {'upgrade': 'burrow'}), ('research', {'upgrade': 'zerg_melee_weapons_level1'}), ('morph_unit', {'unit': 'lurker_burrowed'}), ('morph_unit', {'unit': 'lurker'})], 'protoss': [('build_supply', {}), ('build_supply', {}), ('build_supply', {}), ('build_supply', {}), ('build_supply', {}), ('build_supply', {}), ('build_supply', {}), ('build_structure', {'structure': 'assimilator'}), ('build_structure', {'structure': 'gateway'}), ('build_structure', {'structure': 'cybernetics_core'}), ('build_structure', {'structure': 'twilight_council'}), ('build_structure', {'structure': 'templar_archive'}), ('build_structure', {'structure': 'dark_shrine'}), ('build_structure', {'structure': 'robotics_facility'}), ('build_structure', {'structure': 'robotics_bay'}), ('build_structure', {'structure': 'stargate'}), ('build_structure', {'structure': 'fleet_beacon'}), ('build_structure', {'structure': 'forge'}), ('train_unit', {'unit': 'zealot'}), ('train_unit', {'unit': 'stalker'}), ('train_unit', {'unit': 'adept'}), ('train_unit', {'unit': 'sentry'}), ('train_unit', {'unit': 'high_templar'}), ('train_unit', {'unit': 'dark_templar'}), ('train_unit', {'unit': 'archon'}), ('train_unit', {'unit': 'immortal'}), ('train_unit', {'unit': 'colossus'}), ('train_unit', {'unit': 'disruptor'}), ('train_unit', {'unit': 'observer'}), ('train_unit', {'unit': 'warp_prism'}), ('train_unit', {'unit': 'phoenix'}), ('train_unit', {'unit': 'void_ray'}), ('train_unit', {'unit': 'oracle'}), ('train_unit', {'unit': 'tempest'}), ('train_unit', {'unit': 'carrier'}), ('train_unit', {'unit': 'mothership'}), ('research', {'upgrade': 'blink'}), ('research', {'upgrade': 'psi_storm'}), ('research', {'upgrade': 'protoss_ground_weapons_level1'}), ('morph_unit', {'unit': 'warp_prism_phasing'}), ('morph_unit', {'unit': 'warp_prism'})]}
def main(argv):
 report={'scope':'native realtime, debug minerals/gas/fast_build; NO tech-tree cheat, no model, not natural match','races':[]}
 for race,chain in CHAINS.items():
  if race!='protoss':continue  # Already passed 53-step native chain in multirace_advanced_second.json.
  a=None;q=None;rec={'race':race,'steps':[]};report['races'].append(rec)
  try:
   a=NativeArena(names=(race+' integration',),races=[race],difficulty='very_easy',render=False)
   late=a.controllers[0].data_raw();rec['metadata_after_startup']={'cc_minerals':next(u.mineral_cost for u in late.units if u.unit_id==18),'init_cc_minerals':a.types[18].mineral_cost}
   rec['events']=[]
   q=TaskQueue(a,0,feedback=lambda kind,**kw:rec['events'].append({'kind':kind,**kw}));reflex=ReflexNet(a.homes[0],print,combat_enabled=False)
   a.controllers[0].debug([d.DebugCommand(game_state=d.all_resources)])
   time.sleep(.2)
   a.controllers[0].debug([d.DebugCommand(game_state=d.fast_build)])
   time.sleep(.2);w=a.step([[]])[0];rec['known_minerals']=len([u for u in w.units if u.mineral]);rec['before']=w.summary();rec['engine']=a.version
   for step_no,(skill,args) in enumerate(chain):
    if step_no%10==0:
     a.controllers[0].debug([d.DebugCommand(game_state=d.all_resources)]);time.sleep(.15);w=a.step([[]])[0]
    q.enqueue([{'skill':skill,'args':args}],w.seconds,'controlled integration');task=q.tasks[-1];start=time.monotonic();gas_before=w.vespene
    while time.monotonic()-start<30 and task.active:
     reactions,reserved=reflex.tick(w,q.protected());out=q.tick(w,reserved);w=a.step([reactions+out])[0]
    row={'skill':skill,'args':args,'state':task.status,'reason':task.reason,'wall_s':time.monotonic()-start,'world':w.summary(),'errors':a.action_errors}
    if skill=='gather_gas':
     until=time.monotonic()+12
     while time.monotonic()<until and w.vespene<=gas_before:
      reactions,reserved=reflex.tick(w,q.protected());w=a.step([reactions+q.tick(w,reserved)])[0]
     row['gas_before']=gas_before;row['gas_after']=w.vespene
    rec['steps'].append(row);print('MACRO_STEP',json.dumps(row,ensure_ascii=False),flush=True)
    Path('logs/multirace_advanced_protoss_final.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    assert task.status=='completed',(race,skill,args,task.reason)
    if skill=='gather_gas':assert w.vespene>gas_before,'gas income absent'
   rec['gas_assignments']=q.gas_assignments;rec['timing']=a.timing();rec['status']='passed';assert a.action_errors==0
   a.save_replay('logs/multirace_advanced_'+race+'.SC2Replay')
  except Exception as e:
   rec.update(status='failed',error=repr(e),traceback=traceback.format_exc());print('RACE_FAILED',race,repr(e),flush=True)
  finally:
   if q:q.close()
   if a:a.close()
   Path('logs/multirace_advanced_protoss_final.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
 assert all(r['status']=='passed' for r in report['races']),report
if __name__=='__main__':app.run(main)
