import json,unittest
from dataclasses import replace
from arena.context import EngagementLog,DecisionMemory,battle_payload
from arena.raw_engine import Entity,Snapshot
from arena.core import World,Unit,SkillSet
from arena.policy import Policy,AsyncPolicy,build_user_prompt
from arena.llm import LLMClient

class Context(unittest.TestCase):
 def unit(self,tag,side=4,kind=48,health=45):return Entity(tag,kind,side,100,100,health,45,on_screen=False)
 def w(self,n,units=(),dead=()):return Snapshot(n,500,12,30,tuple(units),(30,30),dead_units=dead)
 def log(self):return EngagementLog(lambda *args,**kw:None)
 def test_left_vision_not_death_reentry_not_extra_kill(self):
  l=self.log();l.update(self.w(100,[self.unit(1)]));l.update(self.w(110))
  p=l.public()['windows'][0];self.assertEqual(p['enemy_left_vision'],{'Marine':1});self.assertEqual(p['enemy_deaths_observed'],{})
  l.update(self.w(120,[self.unit(1)]));l.update(self.w(130,dead=(1,)))
  p=l.public()['windows'][0];self.assertEqual(p['enemy_deaths_observed'],{'Marine':1});self.assertEqual(p['enemy_left_vision'],{'Marine':1})
  l.update(self.w(131,dead=(1,)));self.assertEqual(l.public()['windows'][0]['enemy_deaths_observed'],{'Marine':1})
 def test_own_absence_transport_or_morph_does_not_invent_death(self):
  l=self.log();l.update(self.w(100,[self.unit(1,1),self.unit(2)]));l.update(self.w(110,[self.unit(2)]))
  self.assertEqual(l.public()['windows'][0]['our_deaths_observed'],{})
  l.update(self.w(120,[self.unit(1,1,33),self.unit(2)]));self.assertEqual(l.public()['windows'][0]['our_deaths_observed'],{})
 def test_damage_and_confirmed_own_death_retained(self):
  l=self.log();l.update(self.w(100,[self.unit(1,1),self.unit(2)]));l.update(self.w(105,[self.unit(1,1,health=20),self.unit(2)]))
  l.update(self.w(110,[self.unit(2)],(1,)));l.update(self.w(400,[self.unit(2)]))
  p=l.public();self.assertEqual(p['closed_windows'],1);self.assertTrue(p['windows'][0]['damage_observed']);self.assertEqual(p['windows'][0]['our_deaths_observed'],{'Marine':1})
 def test_windows_and_serialized_context_are_bounded(self):
  l=self.log()
  for i in range(40):
   l.update(self.w(i*900+100,[self.unit(i+1)]));l.update(self.w(i*900+120));l.update(self.w(i*900+300))
  p=l.public();self.assertEqual(p['retained_closed'],20);self.assertEqual(p['evicted_windows'],20)
  self.assertLessEqual(len(p['windows']),6);self.assertLess(len(json.dumps(p)),8000)
 def test_camera_change_explicit_in_legacy_no_casualty_claim(self):
  l=EngagementLog(lambda *a,**kw:None,mode='screen')
  l.update(World(game_loop=100,enemy_units=[Unit(48,4,10,10)]))
  l.update(World(game_loop=120,camera=(20,20)))
  p=l.public()['windows'][0];self.assertTrue(p['camera_changed']);self.assertEqual(p['enemy_deaths_observed'],{})
 def test_static_prefix_survives_enemy_and_dynamic_state_changes(self):
  p={'world':{'game_s':10},'tech_tree':{'b':2,'a':1},'game_knowledge':{'economy':{'x':1},'visible_enemy_roles':{'marine':'x'}},'control_policy':{'enabled':True}}
  a=battle_payload(p);p['world']={'game_s':20};p['game_knowledge']['visible_enemy_roles']={'tank':'y'};b=battle_payload(p)
  enc=lambda v:json.dumps(v,ensure_ascii=False,separators=(',',':'))
  self.assertEqual(enc(a).split('"world":')[0],enc(b).split('"world":')[0]);self.assertNotIn('visible_enemy_roles',a['game_knowledge'])
  self.assertEqual(a['visible_enemy_roles'],{'marine':'x'});self.assertEqual(a['tech_tree'],{'a':1,'b':2})
 def test_memory_bounded_and_long_term_intention_survives_recent_window(self):
  m=DecisionMemory()
  for i in range(40):m.add(i*224,{'tasks':[{'skill':'train_unit'}],'reason':str(i)+'a'*300})
  p=m.public();self.assertEqual(len(p['recent']),8);self.assertEqual(len(p['milestones']),6)
  self.assertTrue(all(r['reason_truncated'] for r in p['recent']));self.assertLess(p['milestones'][0]['game_s'],p['recent'][0]['game_s'])
 def test_legacy_enemy_details_and_bounded_unknown_types(self):
  w=World(enemy_units=[Unit(48,4,10,10),Unit(48,4,10,10),Unit(51,4,11,10),Unit(33,4,-1,10)])
  s=w.summary();self.assertIn('Marine x2',s);self.assertIn('Marauder x1',s);self.assertIn('visible_enemies=3',s)
  w.enemy_units=[Unit(i+10000,4,1,1) for i in range(1000)];self.assertLess(len(w.summary()),1800);self.assertIn('omitted',w.summary())
 def test_legacy_reordered_prompt_and_shared_sync_async_history(self):
  class C:
   def chat(self,*args):return '{"skill":"no_op","reason":"等待"}'
  a=Policy(C());b=AsyncPolicy(C())
  for n in range(12):
   w=World(game_loop=n*224);a._decide(w);b._think(w)
  self.assertEqual(a.history,b.history);self.assertEqual(a.decision_memory.public(),b.decision_memory.public())
  prompt=build_user_prompt(w,SkillSet(),a.history)
  self.assertLess(prompt.index('AVAILABLE SKILLS'),prompt.index('CURRENT STATE'));self.assertIn('ENGAGEMENT LOG',prompt)
 def test_mock_ignores_enemy_and_history_counts(self):
  c=LLMClient('mock');w=World(minerals=500,supply_used=12,supply_cap=30,my_units=[Unit(18,1,10,10)]+[Unit(45,1,10,10)]*22+[Unit(19,1,10,10),Unit(21,1,10,10)],enemy_units=[Unit(48,4,10,10)]*30)
  p=build_user_prompt(w,SkillSet(),['Marine x999']);answer=json.loads(c.chat('',p));self.assertEqual(answer['skill'],'train_unit')
 def test_raw_off_camera_visible_enemy_remains_in_prompt(self):
  self.assertEqual(self.w(100,[self.unit(1)]).summary()['visible_enemy'],{'Marine':1})
 def test_missing_cache_metrics_are_unavailable_not_zero(self):
  c=LLMClient('deepseek');c._record_usage({'usage':{'prompt_tokens':1000,'completion_tokens':20}})
  self.assertIsNone(c.last_usage['cached_tokens']);self.assertIsNone(c.cache_hit_rate);self.assertIn('unavailable',c.usage_summary())
  c._record_usage({'usage':{'prompt_tokens':1000,'prompt_cache_hit_tokens':768}})
  self.assertEqual(c.cache_hit_rate,.768);self.assertEqual(c.usage['cache_unavailable_calls'],1)
  c._record_usage({'usage':{'prompt_cache_hit_tokens':100}})
  self.assertIsNone(c.last_usage['hit_rate']);self.assertEqual(c.cache_hit_rate,.768)
 def test_usage_accounting_is_connected_and_has_exact_server_counts(self):
  c=LLMClient('deepseek');c._post=lambda *args:{'usage':{'prompt_tokens':1000,'prompt_cache_hit_tokens':768,'prompt_cache_miss_tokens':232,'completion_tokens':20},'choices':[{'message':{'content':'{}'}}]}
  c._call_openai('s','u');self.assertEqual(c.last_usage['cached_tokens'],768);self.assertEqual(c.last_usage['hit_rate'],.768)
  from arena.deepseek_planner import DeepSeekFlashPlanner
  d=DeepSeekFlashPlanner();d._post=lambda *args:{'model':'deepseek-flash','usage':{'prompt_tokens':1000,'prompt_cache_hit_tokens':768},'choices':[{'finish_reason':'stop','message':{'content':'{"tasks":[]}'}}]}
  d._call_openai('s','u');self.assertEqual(d.last_usage['cached_tokens'],768)

if __name__=='__main__':unittest.main()
