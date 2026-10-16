"""Explicit warp areas, native-only placement and ownership of each location's purchases."""
import math,unittest
from dataclasses import replace
from arena.tasks import TaskQueue,PlanValidationError
from arena.battle_plans import output_schema
import test_multirace as fixtures


class WarpTarget(unittest.TestCase):
    def setUp(self):self.f=fixtures.MultiRace();self.f.setUp()
    def test_explicit_point_uses_only_warpgates_and_stays_in_the_selected_area(self):
        b=self.f.backend('protoss',[1414,917]);q=TaskQueue(b,0,lambda _:None)
        q.enqueue([dict(skill='train_unit',args=dict(unit='stalker',warp_target=[70,70]))],10)
        normal=self.f.unit(1,62,is_building=True);gate=self.f.unit(2,133,is_building=True)
        w=self.f.world([normal,gate],race='protoss');out=q.tick(w,set());self.assertEqual(len(out),1)
        command=out[0].action_raw.unit_command
        self.assertEqual(list(command.unit_tags),[2]);self.assertEqual(command.ability_id,1414)
        self.assertLessEqual(math.dist([command.target_world_space_pos.x,command.target_world_space_pos.y],[70,70]),3)
        self.assertEqual(q.tasks[0].program.spec['warp_target'],[70,70])
        other=TaskQueue(b,0,lambda _:None);other.enqueue([dict(skill='train_unit',args=dict(unit='stalker',warp_target=[70,70]))],10)
        self.assertEqual(other.tick(replace(w,units=(normal,)),set()),[])
        self.assertEqual(other.tasks[0].program.issued,0)
    def test_failed_requested_area_never_falls_back_to_power_at_home(self):
        b=self.f.backend('protoss',[1414]);b.placement=lambda *args:None
        q=TaskQueue(b,0,lambda _:None);q.enqueue([dict(skill='train_unit',args=dict(unit='stalker',warp_target=[70,70]))],10)
        w=self.f.world([self.f.unit(1,133,is_building=True),self.f.unit(2,60,is_building=True)],race='protoss')
        self.assertEqual(q.tick(w,set()),[]);self.assertEqual(q.tasks[0].program.issued,0)
    def test_separate_warp_goals_cannot_count_one_locations_unit_twice(self):
        q=TaskQueue(self.f.backend('protoss',[1414]),0,lambda _:None)
        q.enqueue([dict(skill='train_unit',args=dict(unit='stalker',warp_target=point))
                   for point in ([35,35],[75,75])],10)
        for task,tag,point in zip(q.tasks,(1,2),((35,35),(75,75))):
            run=task.program;run.issued=1;run.baseline=set();run.tags={tag}
            run.pending=[dict(tags=[tag],ability=1414,pos=point,warp_in=True,seen=True,sent=10)]
        gates=[self.f.unit(i,133,is_building=True) for i in (1,2)]
        unit=replace(self.f.unit(3,74),x=35,y=35)
        q.tick(self.f.world(gates+[unit],race='protoss'),set())
        self.assertEqual(q.tasks[0].status,'completed');self.assertTrue(q.tasks[1].active)
        self.assertEqual(q.tasks[1].program.born,set())
    def test_normal_gateway_purchase_does_not_claim_a_targeted_warp(self):
        q=TaskQueue(self.f.backend('protoss',[1414]),0,lambda _:None)
        q.enqueue([dict(skill='train_unit',args=dict(unit='stalker')),
                   dict(skill='train_unit',args=dict(unit='stalker',warp_target=[35,35]))],10)
        for task in q.tasks:task.program.issued=1;task.program.baseline=set()
        q.tasks[0].program.pending=[dict(tags=[1],ability=917,producer_pos=(30,30),pos=None,warp_in=False,seen=True,sent=10)]
        q.tasks[1].program.pending=[dict(tags=[2],ability=1414,pos=(35,35),warp_in=True,seen=True,sent=10)]
        w=self.f.world([self.f.unit(1,62,is_building=True,orders=(917,)),self.f.unit(2,133,is_building=True),
            replace(self.f.unit(3,74),x=35,y=35)],race='protoss')
        q.tick(w,set());self.assertTrue(q.tasks[0].active)
        self.assertEqual(q.tasks[0].program.born,set());self.assertEqual(q.tasks[1].status,'completed')
    def test_append_cannot_bypass_one_product_per_reply_using_different_warp_destinations(self):
        q=TaskQueue(self.f.backend('protoss',[1414]),0,lambda _:None)
        with self.assertRaises(PlanValidationError):
            q.enqueue([dict(skill='train_unit',count=3,args=dict(unit='stalker',queue_mode='append',warp_target=point))
                       for point in ([35,35],[75,75])],10,request_id=4)
        self.assertEqual(q.tasks,[])
    def test_rejected_old_warp_does_not_block_a_later_purchase_at_the_same_point(self):
        q=TaskQueue(self.f.backend('protoss',[1414]),0,lambda _:None)
        goal=dict(skill='train_unit',args=dict(unit='stalker',warp_target=[70,70]))
        q.enqueue([goal],10)
        gate=self.f.unit(1,133,is_building=True)
        w=self.f.world([gate],race='protoss');self.assertEqual(len(q.tick(w,set())),1)
        old=q.tasks[0];point=old.program.pending[0]['pos']
        q.tick(replace(w,game_loop=250,errors=((1,1414,2),)),set())
        self.assertEqual(old.status,'failed')
        q.enqueue([goal],12)
        self.assertEqual(len(q.tick(replace(w,game_loop=300),set())),1)
        new=q.tasks[-1];self.assertEqual(new.program.pending[0]['pos'],point)
        product=replace(self.f.unit(3,74),x=point[0],y=point[1])
        q.tick(replace(w,game_loop=325,units=(gate,product)),set())
        self.assertEqual(new.status,'completed');self.assertEqual(new.program.born,{3})
        self.assertEqual(old.program.born,set())
    def test_foreign_race_non_warp_units_and_malformed_coordinates_are_atomic(self):
        for race,unit,target in (('terran','marine',[70,70]),('protoss','immortal',[70,70]),
                ('protoss','stalker',[True,70]),('protoss','stalker',[float('inf'),70]),('protoss','stalker',None)):
            q=TaskQueue(self.f.backend(race),0,lambda _:None)
            with self.subTest(race=race,unit=unit,target=target),self.assertRaises(PlanValidationError):
                q.enqueue([dict(skill='train_worker'),dict(skill='train_unit',args=dict(unit=unit,warp_target=target))],10)
            self.assertEqual(q.tasks,[])
        schema=output_schema(dict(tech_tree=self.f.tree.public('protoss')))['anyOf'][0]
        branch=next(v for v in schema['properties']['tasks']['items']['anyOf'] if v['properties']['skill']['enum']==['train_unit'])
        self.assertIn('warp_target',branch['properties']['args']['properties'])


if __name__=='__main__':unittest.main()
