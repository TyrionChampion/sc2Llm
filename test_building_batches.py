"""Finite race-aware building batches and retained native construction ownership."""
import unittest
from dataclasses import replace

from arena.tasks import TaskQueue,PlanValidationError
from arena.battle_plans import output_schema
import test_multirace as fixtures


class BuildingBatches(unittest.TestCase):
    def setUp(self):self.f=fixtures.MultiRace();self.f.setUp()

    def prepare(self,race):
        name={'terran':'supply_depot','protoss':'pylon','zerg':'spawning_pool'}[race]
        entry=self.f.tree.units[name];recipe=entry['recipes'][0]
        backend=self.f.backend(race,[recipe['ability'],3659]);events=[]
        q=TaskQueue(backend,0,lambda _:None,lambda kind,**kw:events.append(dict(kind=kind,**kw)))
        goal=dict(skill='build_supply',count=2) if race!='zerg' else dict(skill='build_structure',count=2,args=dict(structure=name))
        q.enqueue([goal],10)
        home=self.f.unit(1,{'terran':18,'protoss':59,'zerg':86}[race],is_building=True)
        worker=self.f.unit(2,recipe['producer'])
        w=self.f.world([home,worker],race=race)
        self.assertEqual(len(q.tick(w,set())),1);task=q.tasks[0]
        building=replace(self.f.unit(3,entry['id'],is_building=True,progress=.3),
                         x=task.spot[0],y=task.spot[1])
        units=[home,building]
        if race!='zerg':units.append(replace(worker,orders=(recipe['ability'],) if race=='terran' else ()))
        else:units.append(self.f.unit(4,104))
        ongoing=replace(w,units=tuple(units),game_loop=int(11*22.4))
        self.assertEqual(q.tick(ongoing,set()),[])
        return q,task,ongoing,building,events

    def test_three_race_batches_purchase_exactly_twice_only_after_completion(self):
        for race in ('terran','protoss','zerg'):
            with self.subTest(race=race):
                q,task,w,building,events=self.prepare(race)
                self.assertEqual(task.program.issued,1)
                self.assertEqual(len(task.program.pending),1)
                later=replace(w,game_loop=int(25*22.4),
                    units=tuple(replace(u,progress=.6) if u.tag==building.tag else u for u in w.units))
                self.assertEqual(q.tick(later,set()),[])
                if race!='terran':self.assertNotIn(2,q.protected())
                done=replace(later,units=tuple(replace(u,progress=1,orders=()) if u.tag==building.tag
                    else replace(u,orders=()) for u in later.units),game_loop=int(30*22.4))
                actions=q.tick(done,set())
                if not actions:
                    # The completion observation may still reserve the SCV
                    # from the in-flight command at the start of that tick.
                    done=replace(done,game_loop=int(31*22.4));actions=q.tick(done,set())
                self.assertEqual(len(actions),1)
                self.assertEqual(task.program.issued,2)
                second=replace(self.f.unit(5,building.type_id,is_building=True),x=task.spot[0],y=task.spot[1])
                complete=replace(done,units=done.units+(second,),game_loop=int(45*22.4))
                self.assertEqual(q.tick(complete,set()),[])
                self.assertEqual(task.status,'completed')
                self.assertEqual(len(task.program.born),2)
                self.assertEqual(sum(e['kind']=='macro_order_issued' for e in events),2)
                self.assertEqual(q.tick(replace(complete,game_loop=int(60*22.4)),set()),[])

    def test_finished_worker_order_retains_cancellable_native_building(self):
        for race in ('protoss','zerg'):
            with self.subTest(race=race):
                q,task,w,building,events=self.prepare(race)
                self.assertEqual(task.program.pending[0]['tags'],[building.tag])
                late=replace(w,game_loop=int(25*22.4));self.assertEqual(q.tick(late,set()),[])
                q.enqueue([dict(skill='cancel_native_building',args=dict(task_id=task.id))],late.seconds)
                cancel=q.tasks[-1];actions=q.tick(late,set());self.assertEqual(len(actions),1)
                self.assertEqual(list(actions[0].action_raw.unit_command.unit_tags),[building.tag])
                gone=replace(late,units=tuple(u for u in late.units if u.tag!=building.tag),game_loop=int(26*22.4))
                self.assertEqual(q.tick(gone,set()),[])
                self.assertEqual(cancel.status,'completed');self.assertEqual(task.status,'cancelled')
                self.assertEqual(task.program.issued,1)
                self.assertEqual(sum(e['kind']=='macro_order_issued' for e in events),1)

    def test_batch_repetition_coalesces_and_non_batch_tasks_remain_strict(self):
        q,task,w,building,events=self.prepare('protoss')
        q.enqueue([dict(skill='build_supply',count=2)],w.seconds)
        self.assertEqual(len(q.tasks),1);self.assertEqual(task.count,2)
        for goal in (dict(skill='build_supply',count=201),dict(skill='build_supply',count=True),
                     dict(skill='build_supply',count=2,args=dict(queue_mode='append')),
                     dict(skill='gather',count=2),dict(skill='research',count=2,args=dict(upgrade='warp_gate'))):
            with self.subTest(goal=goal),self.assertRaises(PlanValidationError):q.enqueue([goal],w.seconds)
        self.assertEqual(len(q.tasks),1)
        schema=output_schema(dict(tech_tree=self.f.tree.public('protoss')))['anyOf'][0]
        variants=schema['properties']['tasks']['items']['anyOf']
        for name in ('build_supply','build_structure','expand'):
            branch=next(v for v in variants if v['properties']['skill']['enum']==[name])
            self.assertEqual(branch['properties']['count']['maximum'],200)

    def test_lost_bound_construction_fails_without_rebuying_a_consumed_drone(self):
        for race in ('protoss','zerg'):
            with self.subTest(race=race):
                q,task,w,building,events=self.prepare(race)
                lost=replace(w,units=tuple(u for u in w.units if u.tag!=building.tag),
                    dead_units=(building.tag,),game_loop=int(12*22.4))
                self.assertEqual(q.tick(lost,set()),[])
                self.assertEqual(task.status,'failed');self.assertEqual(task.program.issued,1)
                self.assertEqual(sum(e['kind']=='macro_order_issued' for e in events),1)

    def test_failed_goal_cancellation_never_targets_a_replacement_at_the_same_position(self):
        q,task,w,building,events=self.prepare('protoss')
        task.status='failed'
        replacement=replace(building,tag=99)
        current=replace(w,units=tuple(replacement if u.tag==building.tag else u for u in w.units),
                        game_loop=int(25*22.4))
        q.enqueue([dict(skill='cancel_native_building',args=dict(task_id=task.id))],current.seconds)
        cancel=q.tasks[-1]
        self.assertEqual(q.tick(current,set()),[])
        self.assertEqual(q.tick(replace(current,game_loop=int(60*22.4)),set()),[])
        self.assertEqual(cancel.status,'failed')
        self.assertFalse(any(e['kind']=='native_building_cancel_order_issued' for e in events))

    def test_same_product_at_two_bases_confirms_only_its_bound_purchase(self):
        q=TaskQueue(self.f.backend('protoss',[881]),0,lambda _:None)
        q.enqueue([dict(skill='build_structure',args=dict(structure='pylon',base=base))
                   for base in ('home','expansion')],10)
        for task,tag,pos in zip(q.tasks,(1,2),((35,30),(105,100))):
            run=task.program;run.baseline=set();run.issued=1;run.tags={tag}
            run.pending=[dict(tags=[tag],pos=pos,ability=881,seen=True,sent=10,quantity=1)]
        building=replace(self.f.unit(3,60,is_building=True),x=35,y=30)
        worker=self.f.unit(1,84)
        second=replace(self.f.unit(2,84,orders=(881,)),x=100,y=100)
        q.tick(self.f.world([building,worker,second],race='protoss'),set())
        self.assertEqual(q.tasks[0].status,'completed')
        self.assertTrue(q.tasks[1].active)
        self.assertEqual(q.tasks[1].program.born,set())

    def test_same_form_in_two_missions_confirms_only_the_selected_actor(self):
        q=TaskQueue(self.f.backend('terran',[388]),0,lambda _:None)
        q.enqueue([dict(skill='morph_unit',args=dict(unit='siege_tank_sieged',target=target))
                   for target in ('home','rally')],10)
        for task,tag in zip(q.tasks,(1,2)):
            run=task.program;run.baseline=set();run.issued=1;run.tags={tag}
            run.pending=[dict(tags=[tag],pos=None,ability=388,seen=True,sent=10,quantity=1)]
        q.tick(self.f.world([self.f.unit(1,32),self.f.unit(2,33,orders=(388,))]),set())
        self.assertEqual(q.tasks[0].status,'completed')
        self.assertTrue(q.tasks[1].active)
        self.assertEqual(q.tasks[1].program.born,set())


if __name__=='__main__':unittest.main()
