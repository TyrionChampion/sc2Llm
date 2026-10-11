"""Technology observations/real production executor; synthetic snapshots, no provider calls."""
import json
from dataclasses import replace
from copy import deepcopy
from pathlib import Path
import unittest
from arena.technology import report,playbook
from arena.context import battle_payload
from arena.knowledge import model_knowledge
from arena.tasks import TaskQueue
from test_multirace import MultiRace

class TechnologyTests(unittest.TestCase):
 setUp=MultiRace.setUp
 backend=MultiRace.backend
 world=MultiRace.world
 unit=MultiRace.unit
 def research_queue(self,name):
  e=self.tree.research[name];abilities=[r['ability'] for r in e['recipes']]
  q=TaskQueue(self.backend(e['race'],abilities),0,lambda s:None)
  q.enqueue([dict(skill='research',args={'upgrade':name})],10)
  producers=[self.unit(100+i,r['producer'],is_building=True) for i,r in enumerate(e['recipes'])]
  upgrades=tuple(r['required_upgrade'] for r in e['recipes'] if r.get('required_upgrade'))
  return q,self.world(producers,race=e['race'],upgrades=upgrades),e
 def test_all_bound_research_recipes_can_emit_research_not_construction(self):
  for name in self.tree.research:
   with self.subTest(name=name):
    q,w,e=self.research_queue(name);actions=q.tick(w,set())
    self.assertEqual(len(actions),1,(name,q.tasks[0].reason))
    command=actions[0].action_raw.unit_command
    self.assertIn(command.ability_id,[r['ability'] for r in e['recipes']])
    self.assertFalse(command.HasField('target_world_space_pos'))
    self.assertFalse(command.HasField('target_unit_tag'))
 def test_orders_disappear_without_upgrade_report_failure_not_eternal_running(self):
  q,w,e=self.research_queue('metabolic_boost');q.tick(w,set());producer=w.units[0]
  q.tick(replace(w,game_loop=250,units=(replace(producer,orders=(e['recipes'][0]['ability'],)),)),set())
  q.tick(replace(w,game_loop=300),set());self.assertEqual(q.tasks[0].status,'running')
  q.tick(replace(w,game_loop=800),set());self.assertEqual(q.tasks[0].status,'failed')
  self.assertIn('upgrade is absent',q.tasks[0].reason)
 def test_upgrade_after_order_disappearance_grace_period_confirms(self):
  q,w,e=self.research_queue('metabolic_boost');q.tick(w,set());producer=w.units[0]
  q.tick(replace(w,game_loop=250,units=(replace(producer,orders=(e['recipes'][0]['ability'],)),)),set())
  q.tick(replace(w,game_loop=300),set())
  q.tick(replace(w,game_loop=310,upgrades=(e['id'],)),set())
  self.assertEqual(q.tasks[0].status,'completed')
 def test_completed_upgrade_does_not_issue_again(self):
  q,w,e=self.research_queue('centrifical_hooks')
  self.assertEqual(q.tick(replace(w,upgrades=(e['id'],)),set()),[])
  self.assertEqual(q.tasks[0].status,'completed')
 def test_baneling_speed_reports_lair_prerequisite_and_gas(self):
  w=self.world([self.unit(1,9),self.unit(2,96,is_building=True)],race='zerg')
  w=replace(w,vespene=0);r=report(self.tree,w)
  c=next(x for x in r['candidates'] if x['upgrade']=='centrifical_hooks')
  self.assertIn('need completed lair',c['recipes'][0]['blockers'])
  self.assertEqual(c['resource_shortfall']['gas'],100)
  self.assertEqual(c['command'],dict(skill='research',args={'upgrade':'centrifical_hooks'}))
 def test_hive_satisfies_lair_and_idle_state_does_not_claim_engine_available(self):
  w=self.world([self.unit(1,9),self.unit(2,96,is_building=True),self.unit(3,101,is_building=True)],race='zerg')
  c=next(x for x in report(self.tree,w)['candidates'] if x['upgrade']=='centrifical_hooks')
  self.assertEqual(c['status'],'observed_prerequisites_ready')
 def test_researching_and_completed_report_real_observations(self):
  q,w,e=self.research_queue('metabolic_boost')
  w=replace(w,units=(replace(w.units[0],orders=(1253,)),self.unit(1,105)))
  self.assertIn('metabolic_boost',report(self.tree,w)['in_progress'])
  r=report(self.tree,replace(w,upgrades=(66,)))
  self.assertIn('metabolic_boost',r['relevant_completed'])
  self.assertNotIn('metabolic_boost',[x['upgrade'] for x in r['candidates']])
 def test_report_bounded_and_only_own_race(self):
  for race in ('terran','zerg','protoss'):
   units=[self.unit(i+1,e['id']) for i,e in enumerate(self.tree.race_units(race).values())]
   r=report(self.tree,self.world(units,race=race))
   self.assertLessEqual(len(r['candidates']),12)
   self.assertGreater(r['omitted'],0)
   for c in r['candidates']:self.assertEqual(self.tree.research[c['upgrade']]['race'],race)
 def test_dynamic_report_does_not_contaminate_prefix_or_mutate_input(self):
  w=self.world([self.unit(1,105)],race='zerg')
  a=dict(world=w.summary(),game_knowledge=model_knowledge(self.tree,w))
  changed=replace(w,game_loop=1000,upgrades=(66,))
  b=dict(world=changed.summary(),game_knowledge=model_knowledge(self.tree,changed))
  original=deepcopy(a);a=battle_payload(a);b=battle_payload(b)
  self.assertEqual(a['game_knowledge'],b['game_knowledge'])
  self.assertNotEqual(a['technology_report'],b['technology_report'])
  self.assertIn('technology_report',original['game_knowledge'])
  self.assertNotIn('technology_report',a['game_knowledge'])
 def test_explicit_research_commands_for_both_speed_upgrades(self):
  for name in ('metabolic_boost','centrifical_hooks'):
   q,w,e=self.research_queue(name);out=q.tick(w,set())
   self.assertEqual(out[0].action_raw.unit_command.ability_id,e['recipes'][0]['ability'])
 def test_alternate_forms_are_counted_once_under_actual_name(self):
  w=self.world([self.unit(1,32)],race='terran')
  c=next(x for x in report(self.tree,w)['candidates'] if x['upgrade']=='terran_vehicle_weapons_level1')
  self.assertEqual(c['beneficiaries'],{'siege_tank_sieged':1})
 def test_guidance_selectors_exist_in_engine(self):
  from arena.technology import FOCUS,GROUPS
  names={n for _,s,_ in FOCUS.values() for n in s.split()}|{n for s in GROUPS.values() for n in s.split()}
  self.assertFalse(names-set(self.tree.names.values()))
 def test_no_beneficiaries_does_not_invent_need_to_research_everything(self):
  r=report(self.tree,self.world([self.unit(1,104)],race='zerg'))
  self.assertEqual(r['candidates'],[])
 def test_power_and_preceding_upgrade_blockers(self):
  q,w,e=self.research_queue('protoss_ground_weapons_level2')
  w=replace(w,upgrades=(),units=(replace(w.units[0],powered=False),self.unit(1,73)))
  c=next(x for x in report(self.tree,w)['candidates'] if x['upgrade']==e['name'])
  self.assertTrue(any('need upgrade' in b for a in c['recipes'] for b in a['blockers']))

if __name__=='__main__':unittest.main()
