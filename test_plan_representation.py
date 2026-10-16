"""Normalize spelling only; never select a different product or tactical goal."""
import unittest
from copy import deepcopy
from arena.plan_representation import translate_plan
import test_multirace as fixtures
from arena.tasks import TaskQueue,PlanValidationError


class ProductSpelling(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.MultiRace();self.f.setUp()
        self.payload=dict(tech_tree=self.f.tree.public('terran'),world=dict(own={'Marine':3,'SiegeTank':1}))
    def test_observed_native_names_address_the_same_catalogue_products(self):
        source=dict(tasks=[dict(skill='train_unit',count=10,args=dict(unit='Marine',queue_mode='append')),
                           dict(skill='train_unit',count=2,args=dict(unit='SiegeTank'))],reason='保持我的原定产量')
        before=deepcopy(source);actual,changes=translate_plan(source,self.payload)
        self.assertEqual(source,before)
        self.assertEqual([t['args']['unit'] for t in actual['tasks']],['marine','siege_tank'])
        self.assertEqual([t['count'] for t in actual['tasks']],[10,2])
        self.assertEqual(actual['reason'],source['reason']);self.assertEqual(actual['tasks'][0]['args']['queue_mode'],'append')
        self.assertEqual(len(changes),2)
        q=TaskQueue(self.f.backend(),0,lambda _:None);q.enqueue(actual['tasks'],10)
        self.assertEqual([t.count for t in q.tasks],[10,2])
    def test_unknown_other_race_and_ambiguous_names_are_never_reinterpreted(self):
        for name,payload in [('Zealot',self.payload),('tank',self.payload),
                ('M ARINE',dict(tech_tree=dict(units=[dict(name='marine'),dict(name='m_arine')])) )]:
            source=dict(tasks=[dict(skill='train_unit',args=dict(unit=name))])
            actual,changes=translate_plan(source,payload)
            self.assertEqual(actual,source);self.assertEqual(changes,[])
        q=TaskQueue(self.f.backend(),0,lambda _:None)
        with self.assertRaises(PlanValidationError):q.enqueue([dict(skill='train_unit',args=dict(unit='Zealot'))],10)
    def test_destination_and_other_arguments_remain_authoritative(self):
        source=dict(tasks=[dict(skill='build_structure',args=dict(structure='SupplyDepot',base='expansion')),
                           dict(skill='order_units',args=dict(unit='Marine',count=12,mode='move',target=[33,44]))])
        actual,_=translate_plan(source,self.payload)
        self.assertEqual(actual['tasks'][0]['args'],dict(structure='supply_depot',base='expansion'))
        self.assertEqual(actual['tasks'][1],source['tasks'][1])
    def test_zerg_native_selector_and_exemptions_keep_the_selected_units(self):
        payload=dict(tech_tree=self.f.tree.public('zerg'),battlefield=dict(unit_selectors=['overlord','drone','army']))
        source=dict(tasks=[dict(skill='scout',count=1,args=dict(unit='Overlord',count=1,targets=['enemy_start'])),
                           dict(skill='macro_plan',args=dict(exempt_products=['Overlord','Drone'])),
                           dict(skill='build_supply',count=8,args={})])
        actual,changes=translate_plan(source,payload)
        self.assertEqual(actual['tasks'][0]['args'],dict(unit='overlord',count=1,targets=['enemy_start']))
        self.assertEqual(actual['tasks'][1]['args']['exempt_products'],['overlord','drone'])
        self.assertEqual(actual['tasks'][2],source['tasks'][2])
        self.assertEqual(len(changes),3)
        q=TaskQueue(self.f.backend('zerg'),0,lambda _:None)
        with self.assertRaises(PlanValidationError):q.enqueue(actual['tasks'],10)
    def test_training_argument_quantity_uses_default_outer_invocation_without_multiplication(self):
        for outer in (None,1,4):
            task=dict(skill='train_unit',args=dict(unit='marine',count=4,queue_mode='append'))
            if outer is not None:task['count']=outer
            original=dict(tasks=[task]);actual,_=translate_plan(original,self.payload)
            self.assertEqual(actual['tasks'][0],dict(skill='train_unit',count=4,args=dict(unit='marine',queue_mode='append')))
        conflicting=dict(tasks=[dict(skill='train_worker',count=2,args=dict(count=4))])
        self.assertEqual(translate_plan(conflicting,self.payload),(conflicting,[]))
    def test_native_building_argument_quantities_keep_the_explicit_finite_batch(self):
        payload=dict(tech_tree=self.f.tree.public('zerg'))
        tasks=[dict(skill='build_supply',count=1,args=dict(count=2)),
            dict(skill='build_structure',args=dict(structure='evolution_chamber',base='home',count=2)),
            dict(skill='expand',count=2,args=dict(base='expansion',count=2))]
        source=dict(tasks=tasks,reason='补2王虫及2栋产能、明确开2矿')
        before=deepcopy(source);actual,changes=translate_plan(source,payload)
        self.assertEqual(source,before);self.assertEqual(len(changes),3)
        self.assertEqual(actual['reason'],source['reason'])
        self.assertTrue(all(t['count']==2 and 'count' not in t['args'] for t in actual['tasks']))
        q=TaskQueue(self.f.backend('zerg'),0,lambda _:None);q.enqueue(actual['tasks'],10)
        self.assertEqual([t.count for t in q.tasks],[2,2,2])
        conflicting=dict(tasks=[dict(skill='build_supply',count=3,args=dict(count=2))])
        self.assertEqual(translate_plan(conflicting,payload),(conflicting,[]))
        self.assertEqual(translate_plan(dict(tasks=[tasks[0]]),{}),(dict(tasks=[tasks[0]]),[]))
        morph=dict(tasks=[dict(skill='morph_unit',args=dict(unit='siege_tank_sieged',target='home',count=2))])
        actual,changes=translate_plan(morph,self.payload)
        self.assertEqual(len(changes),1);self.assertEqual(actual['tasks'][0]['count'],2)
        self.assertEqual(actual['tasks'][0]['args'],dict(unit='siege_tank_sieged',target='home'))
        q=TaskQueue(self.f.backend(),0,lambda _:None);q.enqueue(actual['tasks'],10)
        self.assertEqual(q.tasks[0].count,2)
    def test_outer_training_queue_mode_preserves_the_rejected_zerg_append_plan(self):
        payload=dict(tech_tree=self.f.tree.public('zerg'))
        source=dict(tasks=[dict(skill='inject_larva',count=1,args=dict(base='home')),
            dict(skill='train_unit',count=6,args=dict(unit='roach'),queue_mode='append'),
            dict(skill='train_worker',count=4,args={},queue_mode='append'),
            dict(skill='build_supply',count=2,args={})],reason='三矿补农，续排六蟑螂')
        before=deepcopy(source);q=TaskQueue(self.f.backend('zerg'),0,lambda _:None)
        with self.assertRaises(PlanValidationError):q.enqueue(source['tasks'],300)
        actual,changes=translate_plan(source,payload)
        self.assertEqual(source,before);self.assertEqual(len(changes),2)
        self.assertEqual(actual['reason'],source['reason'])
        for i in (1,2):
            self.assertEqual(actual['tasks'][i]['args'],source['tasks'][i]['args']|dict(queue_mode='append'))
            self.assertEqual(actual['tasks'][i]['count'],source['tasks'][i]['count'])
            self.assertNotIn('queue_mode',actual['tasks'][i])
        q.enqueue(actual['tasks'],300)
        self.assertEqual([t.count for t in q.tasks],[1,6,4,2])
    def test_outer_queue_mode_conflicts_and_other_skills_are_not_reinterpreted(self):
        for task in (dict(skill='train_unit',args=dict(unit='marine',queue_mode='coalesce'),queue_mode='append'),
                     dict(skill='train_worker',args={},queue_mode='repeat'),
                     dict(skill='build_supply',args={},queue_mode='append')):
            source=dict(tasks=[task]);self.assertEqual(translate_plan(source,self.payload),(source,[]))
            with self.assertRaises(PlanValidationError):TaskQueue(self.f.backend(),0,lambda _:None).enqueue(source['tasks'],10)
        source=dict(tasks=[dict(skill='train_unit',args=dict(unit='marine',queue_mode='append'),queue_mode='append')])
        actual,changes=translate_plan(source,self.payload)
        self.assertEqual(len(changes),1);self.assertNotIn('queue_mode',actual['tasks'][0])
        self.assertEqual(actual['tasks'][0]['args'],source['tasks'][0]['args'])
    def test_mission_reason_text_is_preserved_at_the_supported_field(self):
        source=dict(tasks=[dict(skill='scout',count=1,reason='查敌',args=dict(unit='scv',count=2,
            targets=['enemy_start'],timeout_s=120,reason='查敌主营兵种与高坡守军定推进'))])
        actual,changes=translate_plan(source,self.payload)
        self.assertNotIn('reason',actual['tasks'][0])
        self.assertEqual(actual['tasks'][0]['args']['reason'],'查敌；查敌主营兵种与高坡守军定推进')
        self.assertEqual(actual['tasks'][0]['args']['targets'],['enemy_start'])
        self.assertEqual(len(changes),1)
    def test_free_form_name_spelling_is_resolved_only_from_published_forms(self):
        source=dict(tasks=[dict(skill='morph_unit',count=2,args=dict(unit='SiegeTankSieged',target='home'))])
        actual,changes=translate_plan(source,self.payload)
        self.assertEqual(actual['tasks'][0]['args'],dict(unit='siege_tank_sieged',target='home'))
        self.assertEqual(actual['tasks'][0]['count'],2);self.assertEqual(len(changes),1)

    def test_redundant_equal_defence_quantities_do_not_reject_selected_replenishment(self):
        source=dict(tasks=[dict(skill='train_unit',count=24,args=dict(unit='marine',queue_mode='append')),
            dict(skill='defend_base',count=6,args=dict(base='home',units='all',count=6,
                stance='hold',timeout_s=300,reason='明确留六名守军'))])
        before=deepcopy(source);actual,changes=translate_plan(source,self.payload)
        self.assertEqual(source,before);self.assertEqual(len(changes),1)
        self.assertEqual(actual['tasks'][0],source['tasks'][0])
        self.assertEqual(actual['tasks'][1]['count'],1)
        self.assertEqual(actual['tasks'][1]['args'],source['tasks'][1]['args'])
        q=TaskQueue(self.f.backend(),0,lambda _:None);q.enqueue(actual['tasks'],10)
        self.assertEqual([t.count for t in q.tasks],[24,1]);self.assertEqual(q.tasks[1].args['count'],6)
        source['tasks'][1]['count']=4
        self.assertEqual(translate_plan(source,self.payload),(source,[]))
        with self.assertRaises(PlanValidationError):q.enqueue(source['tasks'],11)

    def test_singleton_expansion_route_selector_keeps_the_same_scout_goal(self):
        source=dict(tasks=[dict(skill='scout',args=dict(unit='scv',count=2,targets=['expansion_sites'],
            timeout_s=200,reason='侦察扩张点'))])
        original=deepcopy(source);actual,changes=translate_plan(source,self.payload)
        self.assertEqual(source,original);self.assertEqual(len(changes),1)
        self.assertEqual(actual['tasks'][0]['args']['targets'],'expansion_sites')
        self.assertEqual(actual['tasks'][0]['args']['count'],2)
        q=TaskQueue(self.f.backend(),0,lambda _:None);q.enqueue(actual['tasks'],10)
        self.assertEqual(q.tasks[0].args['targets'],'expansion_sites')
        source['tasks'][0]['args']['targets'].append('enemy_start')
        self.assertEqual(translate_plan(source,self.payload),(source,[]))


if __name__=='__main__':unittest.main()
