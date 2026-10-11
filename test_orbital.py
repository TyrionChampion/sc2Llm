"""Orbital task regressions using synthetic player-local observations; not native win tests."""
import unittest
from dataclasses import replace
from types import SimpleNamespace
from arena.knowledge import cached_tree
from arena.raw_engine import Entity, Snapshot
from arena.tasks import TaskQueue, PlanValidationError
from arena.battle_plans import output_schema, capture, revalidate

class OrbitalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree=cached_tree()
    def setUp(self):
        self.events=[]
        self.backend=SimpleNamespace(tech_tree=self.tree,races=["terran"],homes=[(30,30)],
            size=(200,200),enemy_start=lambda i:(170,170),types=self.tree.types,
            available_abilities=lambda i,tags:{t:{171,255,399,524} for t in tags})
        self.q=TaskQueue(self.backend,0,lambda x:None,lambda kind,**kw:self.events.append(dict(kind=kind,**kw)))
        self.orbital=Entity(1,132,1,30,30,1500,1500,is_building=True,energy=100,energy_max=200)
        self.depot=Entity(2,19,1,35,30,400,400,is_building=True)
        self.mineral=Entity(3,341,3,30,35,100,100,mineral=True,mineral_contents=900)
        self.w=Snapshot(224,500,12,23,(self.orbital,self.depot,self.mineral),(30,30))
    def enqueue(self,name,args=None):
        self.q.enqueue([dict(skill=name,args=args or {})],self.w.seconds)
    def test_charge_visibility_and_schema(self):
        self.assertEqual(self.w.summary()["bases"][0]["orbital_charges"],2)
        schema=output_schema({"tech_tree":self.tree.public("terran")})
        variants=schema["anyOf"][0]["properties"]["tasks"]["items"]["anyOf"]
        names={n for v in variants for n in v["properties"]["skill"]["enum"]}
        self.assertTrue({"scan","call_mule","supply_drop"}<=names)
        self.q.enqueue([{"skill":"scan","args":{"target":"enemy_start"}},
                       {"skill":"call_mule"},{"skill":"supply_drop"}],10)
        self.assertEqual(len(self.q.tasks),3)
    def test_race_count_and_invalid_targets_rejected_atomically(self):
        for task in ({"skill":"scan","args":{"target":"secret_enemy"}},
                     {"skill":"scan","count":2},{"skill":"call_mule","args":{"target":"enemy_start"}}):
            with self.assertRaises(PlanValidationError):self.q.enqueue([task],10)
        self.backend.races=["protoss"];q=TaskQueue(self.backend,0,lambda x:None)
        with self.assertRaises(PlanValidationError):q.enqueue([{"skill":"scan"}],10)
        self.assertEqual(self.q.tasks,[])
    def test_wait_for_energy_and_expire_scan_no_late_target(self):
        self.enqueue("scan",{"target":"enemy_start"})
        w=replace(self.w,units=(replace(self.orbital,energy=49),))
        self.assertEqual(self.q.tick(w,set()),[])
        self.assertEqual(self.q.tasks[0].status,"waiting")
        self.q.tick(replace(w,game_loop=224*5),set())
        self.assertEqual(self.q.tasks[0].status,"failed")
    def test_missing_expansion_never_falls_back_home(self):
        self.enqueue("call_mule",{"base":"expansion"})
        self.assertEqual(self.q.tick(self.w,set()),[])
        self.assertIn("no target fallback",self.q.tasks[0].reason)
    def test_scan_requires_new_effect_and_spent_energy(self):
        self.enqueue("scan",{"target":"enemy_start"})
        actions=self.q.tick(self.w,set())
        self.assertEqual(actions[0].action_raw.unit_command.ability_id,399)
        effect=(6,1,170,170,13)
        self.q.tick(replace(self.w,game_loop=226,effects=(effect,)),set())
        self.assertTrue(self.q.tasks[0].active)
        self.q.tick(replace(self.w,game_loop=228,effects=(effect,),
                            units=(replace(self.orbital,energy=50),)),set())
        self.assertEqual(self.q.tasks[0].status,"completed")
    def test_existing_scan_avoids_duplicate_energy_spend(self):
        self.enqueue("scan")
        w=replace(self.w,effects=((6,1,30,30,13),))
        self.assertEqual(self.q.tick(w,set()),[])
        self.assertEqual(self.q.tasks[0].status,"completed")
        self.assertTrue(any(e["kind"]=="orbital_already_covered" for e in self.events))
    def test_no_false_confirmation_on_spent_energy_alone(self):
        self.enqueue("scan")
        self.q.tick(self.w,set())
        w=replace(self.w,game_loop=500,units=(replace(self.orbital,energy=50),))
        self.q.tick(w,set())
        self.assertEqual(self.q.tasks[0].status,"failed")
        self.assertTrue(any(e["kind"]=="orbital_failed" for e in self.events))
    def test_one_pending_cast_reserves_caster_against_double_spend(self):
        self.q.enqueue([dict(skill="scan"),dict(skill="call_mule")],10)
        self.assertEqual(len(self.q.tick(self.w,set())),1)
        self.assertIsNone(self.q.tasks[1].program.pending)
    def test_mule_confirmation_is_spawn_not_income(self):
        self.enqueue("call_mule")
        actions=self.q.tick(self.w,set())
        self.assertEqual(actions[0].action_raw.unit_command.target_unit_tag,3)
        mule=Entity(4,268,1,30,35,60,60)
        self.q.tick(replace(self.w,game_loop=250,
            units=(replace(self.orbital,energy=50),self.mineral,mule)),set())
        event=next(e for e in self.events if e["kind"]=="orbital_confirmed")
        self.assertFalse(event["mining_confirmed"])
    def test_supply_drop_checks_buff_and_does_not_recast_boosted_depot(self):
        self.enqueue("supply_drop");self.q.tick(self.w,set())
        w=replace(self.w,game_loop=250,supply_cap=31,
            units=(replace(self.orbital,energy=50),replace(self.depot,buffs=(25,))))
        self.q.tick(w,set());self.assertEqual(self.q.tasks[0].status,"completed")
        self.q.enqueue([dict(skill="supply_drop")],w.seconds)
        self.assertEqual(self.q.tick(w,set()),[])
    def test_query_unavailable_and_engine_rejection_are_visible(self):
        self.enqueue("scan")
        self.backend.available_abilities=lambda i,tags:{}
        self.assertEqual(self.q.tick(self.w,set()),[])
        self.assertIn("unavailable",self.q.tasks[0].reason)
        self.backend.available_abilities=lambda i,tags:{1:{399}}
        self.q.tick(replace(self.w,game_loop=250),set())
        self.q.tick(replace(self.w,game_loop=252,errors=((1,399,9),)),set())
        self.assertEqual(self.q.tasks[0].status,"failed")
        self.assertIn("engine rejected",self.q.tasks[0].reason)
    def test_two_orbitals_do_not_double_drop_on_shared_nearby_depot(self):
        second=replace(self.orbital,tag=5,x=50)
        depot=replace(self.depot,x=40)
        w=replace(self.w,units=(self.orbital,second,depot))
        self.q.enqueue([{"skill":"supply_drop","args":{"base":"home"}},
                        {"skill":"supply_drop","args":{"base":"expansion"}}],10)
        self.assertEqual(len(self.q.tick(w,set())),1)
        self.assertIsNone(self.q.tasks[1].program.pending)
    def test_custom_owner_blocks_orbital_cast(self):
        self.q.tasks.append(SimpleNamespace(active=True,id=99,program=SimpleNamespace(tags={1}),
                            intent="custom",skill="custom",count=1,args={}))
        self.enqueue("scan")
        # Call only this program: the fixture custom owner intentionally has no tick.
        t=self.q.tasks[-1];actions=[]
        t.program.tick(self.w,self.q,t,set(),500,actions)
        self.assertEqual(actions,[])
    def test_late_scan_requires_fresh_decision(self):
        before=capture(self.w,self.q)
        reasons=revalidate({"tasks":[{"skill":"scan"}]},before,self.w,self.q,(30,30),40)
        self.assertIn("fresh observation",reasons[0])
    def test_scan_accepts_explicit_coordinates(self):
        self.enqueue("scan",{"target":[123.0,45.0]})
        actions=self.q.tick(self.w,set())
        self.assertEqual(len(actions),1)
        command=actions[0].action_raw.unit_command
        self.assertEqual(command.ability_id,399)
        self.assertAlmostEqual(command.target_world_space_pos.x,123.0)
        self.assertAlmostEqual(command.target_world_space_pos.y,45.0)
        self.assertIn("orbital",str(self.q.tasks[0].program.summary()))

    def test_scan_coordinate_is_snapped_when_a_map_is_available(self):
        class Grid:
            def nearest(self,pos):return (int(pos[0]),int(pos[1]))
        self.backend.grid=Grid()
        self.enqueue("scan",{"target":[100.7,60.2]})
        command=self.q.tick(self.w,set())[0].action_raw.unit_command
        self.assertAlmostEqual(command.target_world_space_pos.x,100.5)
        self.assertAlmostEqual(command.target_world_space_pos.y,60.5)

    def test_malformed_scan_coordinates_are_rejected(self):
        for target in ([1],[1,2,3],["a","b"],[1,float("nan")],{"x":1,"y":2}):
            with self.assertRaises(PlanValidationError,msg=repr(target)):
                self.q.enqueue([{"skill":"scan","args":{"target":target}}],10)
        self.assertEqual(self.q.tasks,[])

if __name__=="__main__":unittest.main()
