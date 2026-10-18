"""Explicit mixed recon route semantics; current observations only, no model/API."""
from dataclasses import replace
import unittest
import jsonschema

from arena.battle_plans import output_schema
from arena.raw_engine import MOVE, ATTACK
from arena.spatial import validate_args
from arena.tasks import TaskQueue
from test_rally_orders import engine_backend
from test_realtime_runtime import world, unit


class MixedScoutRoutes(unittest.TestCase):
    def setUp(self):
        self.fixture,self.b=engine_backend('terran',available=(MOVE,ATTACK))
        self.q=TaskQueue(self.b,0,lambda _:None)
        self.w=replace(world([unit(1,45),unit(2,18,is_building=True)],10),
            resource_site_memory=(('site_101',((35,30),)),('site_202',((80,60),)),('site_303',((140,130),))))

    def task(self,targets):
        return dict(skill='scout',args=dict(unit='scv',count=1,targets=targets,reason='明确组合侦察路线'))

    def test_explicit_main_then_expansion_group_then_coordinate_keeps_model_order(self):
        self.q.enqueue([self.task(['enemy_start','expansion_sites',[60,100]])],10)
        actions=self.q.tick(self.w,set())
        mission=self.q.tasks[0].program
        self.assertEqual(mission.requested_route,[(170,170),(140,130),(80,60),(60.,100.)])
        self.assertEqual(len(actions),1)
        self.assertEqual(mission.tags,{1})

    def test_dynamic_group_at_start_sorts_from_selected_squad_not_enemy(self):
        self.q.enqueue([self.task(['expansion_sites','enemy_start'])],10)
        self.q.tick(self.w,set())
        self.assertEqual(self.q.tasks[0].program.requested_route,[(80,60),(140,130),(170,170)])

    def test_empty_group_does_not_remove_explicit_other_waypoints(self):
        w=replace(self.w,resource_site_memory=(('site_101',((35,30),)),))
        self.q.enqueue([self.task(['expansion_sites','enemy_start'])],10)
        self.q.tick(w,set())
        self.assertEqual(self.q.tasks[0].program.requested_route,[(170,170)])

    def test_order_units_still_cannot_use_a_search_group_as_a_point(self):
        with self.assertRaises(ValueError):
            validate_args('order_units',dict(unit='scv',count=1,target='expansion_sites',mode='move',reason='非法单点'),(200,200))

    def test_schema_advertises_the_same_mixed_route_and_excludes_it_from_move(self):
        schema=output_schema(dict(control_policy=dict(spatial_missions_available=True)))
        jsonschema.validate(dict(tasks=[self.task(['enemy_start','expansion_sites'])],reason='组合侦察'),schema)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(dict(tasks=[dict(skill='order_units',args=dict(unit='scv',count=1,
                mode='move',target='expansion_sites',reason='非法单点'))],reason='拒绝非法坐标'),schema)


if __name__=='__main__':unittest.main()
