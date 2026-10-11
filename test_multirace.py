"""Capability, atomicity and resource-ownership regression; synthetic, not native wins."""
import unittest
from types import SimpleNamespace
from dataclasses import replace
from arena.knowledge import cached_tree,model_knowledge
from arena.tasks import TaskQueue,PlanValidationError
from arena.raw_engine import Entity,Snapshot
from arena.macro import specification,expansion_clusters
from arena.battle_plans import output_schema
from arena.python_skills import PythonSkillRun
class MultiRace(unittest.TestCase):
 def setUp(self):self.tree=cached_tree()
 def backend(self,race='terran',available=()):
  return SimpleNamespace(tech_tree=self.tree,races=[race],homes=[(30,30)],size=(200,200),types=self.tree.types,
   available_abilities=lambda i,tags,ignore_resources=False:{t:set(available) for t in tags},enemy_start=lambda i:(170,170),placement=lambda i,a,t,ps:ps[0] if ps else None)
 def world(self,units=(),**kw):return Snapshot(224,500,12,30,tuple(units),(30,30),vespene=500,**kw)
 def unit(self,tag,kind,**kw):return Entity(tag,kind,1,30,30,100,100,**kw)
 def test_every_catalogue_entry_has_a_valid_own_race_macro(self):
  for race in ('terran','zerg','protoss'):
   q=TaskQueue(self.backend(race),0,lambda s:None)
   public=self.tree.public(race)
   self.assertGreater(len(public['research']),20)
   for e in self.tree.race_units(race).values():
    skill='build_structure' if e['structure'] else 'train_unit';args={'structure' if e['structure'] else 'unit':e['name']}
    spec=specification(self.tree,race,skill,args);self.assertEqual(spec['entry']['id'],e['id'])
    for recipe in e['recipes']:self.assertTrue(self.tree.abilities[recipe['ability']].available)
   self.assertTrue(output_schema({'tech_tree':public}))
 def test_published_catalogue_stays_compact_without_losing_prerequisites(self):
  """tech_tree 每轮重发,必须紧凑;但前置条件名称不能省(模型要靠它排建筑顺序)。"""
  import json
  tree=self.tree
  budget={'terran':13900,'zerg':15400,'protoss':13200}  # 实测 + 少量余量
  for race,limit in budget.items():
   pub=tree.public(race)
   size=len(json.dumps(pub,ensure_ascii=False))
   self.assertLess(size,limit,'%s tech_tree 体积超预算: %d >= %d' % (race,size,limit))
   self.assertNotIn('data_adjustments',pub,'引擎内部 ID 诊断不该进 prompt')
   # 有 requires 的条目必须给出真实前置名(而不是被压缩成空)
   with_requires=[r for e in pub['units']+pub['structures']+pub['research'] for r in e.get('recipes',[]) if 'requires' in r]
   self.assertTrue(with_requires,'前置条件必须保留')
   names={n for r in with_requires for n in r['requires']}
   self.assertTrue(names,'requires 不能是空数组')
   if race=='protoss':
    # 供电前置只存在于神族;用短 token 代替长串,并有图例说明
    self.assertIn(tree.POWER_TOKEN,names,'供电前置用短 token 表示')
    self.assertTrue(any(tree.POWER_TOKEN in line for line in pub['limitations']),'短 token 必须有图例')
   else:
    self.assertNotIn(tree.POWER_TOKEN,names,'该族没有供电前置,不应凭空出现')

 def test_foreign_race_or_invented_upgrade_is_atomic_rejection(self):
  q=TaskQueue(self.backend('zerg'),0,lambda s:None)
  with self.assertRaises(PlanValidationError):q.enqueue([{'skill':'train_worker'},{'skill':'train_unit','args':{'unit':'marine'}}],10)
  self.assertEqual(q.tasks,[])
  with self.assertRaises(ValueError):specification(self.tree,'protoss','research',{'upgrade':'imaginary'})
 def test_distinct_products_coexist_without_false_coalescing(self):
  q=TaskQueue(self.backend(),0,lambda s:None)
  q.enqueue([{'skill':'train_unit','args':{'unit':'marine'}},{'skill':'train_unit','args':{'unit':'marauder'}}],10)
  self.assertEqual(len(q.tasks),2)
 def test_gas_worker_temporarily_hidden_does_not_get_replaced(self):
  q=TaskQueue(self.backend(),0,lambda s:None);q.gas_workers_per_refinery=3;q.gas_assignments={1:10,2:10,3:10}
  refinery=self.unit(10,20,gas_building=True,vespene_contents=1000,assigned_harvesters=3)
  w=self.world([refinery,self.unit(2,45,orders=(296,)),self.unit(3,45,orders=(296,)),self.unit(4,45)])
  out=[];q.balance_gas(w,set(),out)
  self.assertEqual(q.gas_worker_tags,{1,2,3});self.assertEqual(out,[])
 def test_dead_gas_worker_replaced_and_releases_survive_cooldown(self):
  q=TaskQueue(self.backend(),0,lambda s:None);q.gas_workers_per_refinery=1;q.gas_assignments={1:10}
  refinery=self.unit(10,20,gas_building=True,vespene_contents=1000);worker=self.unit(2,45)
  w=self.world([refinery,worker],dead_units=(1,));out=[];q.balance_gas(w,set(),out)
  self.assertEqual(q.gas_worker_tags,{2})
  q.gas_workers_per_refinery=0;q.gas_next_at=20;q.balance_gas(w,set(),[])
  self.assertEqual(q.gas_release_pending,{2})
 def test_unavailable_ability_waits_without_fabricated_command(self):
  q=TaskQueue(self.backend(),0,lambda s:None);q.enqueue([{'skill':'train_unit','args':{'unit':'marauder'}}],10)
  self.assertEqual(q.tick(self.world([self.unit(10,21,is_building=True)]),set()),[])
  self.assertIn('unavailable',q.tasks[0].reason)
 def test_zerg_morph_costs_and_pair_production(self):
  r=self.tree.units['ravager']['recipes'][0];self.assertEqual((r['minerals'],r['gas'],r['supply']),(25,75,1))
  r=self.tree.units['zergling']['recipes'][0];self.assertEqual((r['minerals'],r['quantity']),(50,2));self.assertTrue(r['consumes_actor'])
  self.assertEqual(self.tree.units['auto_turret']['recipes'][0]['minerals'],0)
 def test_new_patch_ability_identity_is_not_numeric_coincidence(self):
  r=self.tree.units['baneling']['recipes'][0];self.assertEqual(r['ability'],4124)
  self.assertEqual(self.tree.abilities[r['ability']].link_name,'MorphToBaneling')
  self.assertNotEqual(self.tree.abilities[4121].link_name,'MorphToBaneling')
  self.assertTrue(any(d['kind']=='ability_identity_binding' for d in self.tree.diagnostics))
 def test_gas_worker_protected_even_before_explicit_allocation(self):
  q=TaskQueue(self.backend(),0,lambda s:None);q.gas_assignments={1:10}
  self.assertIn(1,q.protected())
 def test_army_selector_excludes_unobservable_hangar_and_mission_ammo(self):
  from arena.python_skills import KINDS
  self.assertFalse(KINDS['army'] & {58,85,151,268})
  self.assertTrue({48,110,74,54} <= KINDS['army'])
 def test_transport_passenger_remains_allocated_during_tick(self):
  entry={'manifest':{'actions':[],'unit':'army','parameters':{}},'sha256':'test'}
  run=PythonSkillRun(entry,{'params':{},'count':2,'target':'home','retreat_target':'home','timeout_s':30})
  run.tags={1,2};run.mission={'objective':[30,30],'retreat':[30,30]};run.next_at=100
  run.worker=SimpleNamespace(poll=lambda:None,future=None)
  q=TaskQueue(self.backend(),0,lambda s:None)
  t=SimpleNamespace(created=0)
  w=self.world([self.unit(2,54,passengers=(1,),cargo_taken=1)])
  run.tick(w,q,t,set(),(500,500,18),[])
  self.assertEqual(run.tags,{1,2})
  context=run.context(w,q)
  self.assertEqual(context['embarked'][0]['tag'],1)
  self.assertEqual([u['tag'] for u in context['units']],[2])
 def test_ammo_exact_counts_are_not_faked(self):
  q=TaskQueue(self.backend(),0,lambda s:None)
  with self.assertRaises(PlanValidationError):q.enqueue([{'skill':'train_unit','args':{'unit':'nuke'},'count':2}],10)
 def test_archon_pair_does_not_require_unrepresentable_single_unit_query(self):
  b=self.backend('protoss');q=TaskQueue(b,0,lambda s:None)
  q.enqueue([{'skill':'train_unit','args':{'unit':'archon'}}],10)
  orders=q.tick(self.world([self.unit(1,75),self.unit(2,76)],race='protoss'),set())
  self.assertEqual(len(orders),1);self.assertEqual(orders[0].action_raw.unit_command.ability_id,1766)
  self.assertEqual(set(orders[0].action_raw.unit_command.unit_tags),{1,2})
 def test_spell_validation_cannot_bypass_production_or_ownership(self):
  entry={'manifest':{'actions':['cast'],'unit':'stalker','parameters':{}},'sha256':'test'}
  run=PythonSkillRun(entry,{'params':{},'count':1,'target':'home','retreat_target':'home','timeout_s':30});run.tags={1};run.mission={'objective':[30,30],'retreat':[30,30]}
  q=SimpleNamespace(index=0,backend=self.backend('protoss',[1442]))
  result={'actions':[{'action':'cast','ability':self.tree.ability_names[1442],'unit_tags':[1],'target':[32,32]}],'state':{},'status':'running','reason':'test'}
  w=self.world([self.unit(1,74)],race='protoss');self.assertEqual(run.validate_result(result,w,q)[0][1],1442)
  result['actions'][0]['unit_tags']=[2]
  with self.assertRaises(ValueError):run.validate_result(result,w,q)
  result['actions'][0].update(unit_tags=[1],ability=self.tree.ability_names[917])
  with self.assertRaises(ValueError):run.validate_result(result,w,q)

 def test_every_advertised_skill_is_also_in_the_output_schema(self):
  """提示词里写了的技能,输出 schema 必须有 —— 否则模型选了也会被参数校验挡掉。

  2026-10-11 实测抓到的真 bug:rally_units 在提示词与队列里都有,但漏了 schema 分支。
  """
  import json
  from arena import battle_plans
  payload={'tech_tree':self.tree.public('terran'),'world':{'own':{'Marine':1}},
           'control_policy':{'spatial_missions_available':True,'defense_orders_available':True},
           'python_skill_library':[],'rally_producers':[{'label':'barracks#1'}],
           'rally_landmarks':['home','expansion','enemy_start','nearest_enemy','main_ramp_top'],
           'defense_report':[{'label':'home'}]}
  items=battle_plans.output_schema(payload)['anyOf'][0]['properties']['tasks']['items']
  in_schema=set()
  for variant in items.get('anyOf',[]):
   for name in variant.get('properties',{}).get('skill',{}).get('enum',[]) or []: in_schema.add(name)
  # 提示词里以反引号/裸词提到的可调用技能:只取队列认可的名字,避免误报普通英文词
  from arena.tasks import ALLOWED
  advertised={name for name in ALLOWED if name in battle_plans.SYSTEM}
  missing=sorted(advertised-in_schema)
  self.assertEqual(missing,[],'这些技能在提示词里出现但 schema 里没有: %s' % missing)
  self.assertTrue(in_schema<=ALLOWED,'schema 暴露了队列不接受的技能: %s' % sorted(in_schema-ALLOWED))


if __name__=="__main__":unittest.main()
