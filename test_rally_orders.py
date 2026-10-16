"""判别实验:显式集结点与自动集结点/战术锚点的协调(记录当前代码行为).

这两组用例在实现「LLM 指定产能集结点」之前写下,用来证实两条事实:

1. 只要产能 tag 进入 busy 集合,DefensiveRally 就不会再对它重发集结点 —— 这是
   显式命令"不被自动逻辑覆盖"的现成机制(commander 目前只把 macro 动作 tag 传进去)。
2. 当所有候选点都被威胁否决时,rally.tick 只记录 waiting,**不清除**建筑身上原有
   的集结点 —— 旧目标继续生效,即用户观察到的"出一个送一个"。

fixture 是受控假 backend,不是原生对局证据;原生确认另见 verify_rally_orders_live.py。
"""
import unittest
from dataclasses import replace
from types import SimpleNamespace

import numpy as np

from arena.raw_engine import ATTACK, MOVE, command
from arena.rally import DefensiveRally
from test_realtime_runtime import Backend, unit, world

HATCHERY, TOWNHALL, ZERGLING = 86, 18, 105  # 18=CommandCenter,不是产能建筑
RALLY_ABILITY = 3673


def grid(size=200, pathing=None, height=200):
    """直接构造 GroundMap(与 test_runtime_optimization.py 同一惯例)。"""
    from arena.rally import GroundMap
    g = GroundMap.__new__(GroundMap)
    g.pathing = np.ones((size, size), dtype='uint8') if pathing is None else pathing
    g.height = np.ones((size, size), dtype='uint8') * height
    g.h = g.w = size
    g.cache = {}
    return g


def grid_info(grid):
    """把一个已构造好的 GroundMap 包成 DefensiveRally/RallyOrder 期望的 start_raw。"""
    return SimpleNamespace(start_raw=SimpleNamespace(pathing_grid=grid.pathing,
                                                      terrain_height=grid.height))


def engine_backend(race='zerg', available=(RALLY_ABILITY,)):
    """TaskQueue/RallyOrder 需要的引擎绑定 backend:可达查询 + 能力查询 + 静态地图。

    静态地图按 arena/tasks.py:_rally_grid 的约定放在 backend.grid 上:真实 backend 用
    map_info_by_player 构造 GroundMap,这里的 fake 没有原生 ImageData proto,所以直接给
    一个已构造好的 GroundMap,而不是伪造会被 Feature.unpack_layer 拒绝的假 proto。
    """
    from test_multirace import MultiRace
    fixture = MultiRace()
    fixture.setUp()
    backend = fixture.backend(race, available=list(available))
    backend.grid = grid()
    backend.path_distances = lambda index, start, points: [max(1.0, __import__('math').dist(start, p)) for p in points]
    return fixture, backend


class RallyBackend(Backend):
    """在共享 fixture 上补齐 DefensiveRally 构造所需的静态地图与可达查询。"""
    tech_tree = SimpleNamespace(abilities={}, canonical_ability=lambda a: a,
                                ability_available=lambda ability, available: ability in available)

    def __init__(self):
        self.map_info_by_player = [SimpleNamespace(start_raw=None)]
        self.homes = [(30, 30)]

    def path_distances(self, index, start, points):
        return [max(1.0, __import__('math').dist(start, p)) for p in points]

    def available_abilities(self, index, tags, ignore_resources=False):
        return {tag: {RALLY_ABILITY} for tag in tags}


def enemy(tag, x, y):
    return unit(tag, 48, x, y, alliance=4, ground_dps=40, attack_range=6)


class DefensiveRallyCoordination(unittest.TestCase):
    """第 1 组:busy 产能不被自动重发。"""

    def setUp(self):
        self.backend = RallyBackend()
        self.events = []
        self.rally = DefensiveRally.__new__(DefensiveRally)
        self.rally.backend = self.backend
        self.rally.index = 0
        self.rally.event = lambda kind, **kw: self.events.append(dict(kind=kind, **kw))
        self.rally.next_at = 0
        self.rally.pending = {}
        self.rally.confirmed = {}
        self.rally.failures = {}
        self.rally.state = {'state': 'initializing'}
        self.rally.grid = grid()
        self.rally.ability = RALLY_ABILITY
        self.rally.disabled = None
        self.base = unit(1, TOWNHALL, 30, 30, is_building=True)  # 主基地不是产能建筑,只有 HATCHERY 是被指定目标
        self.producer = unit(2, HATCHERY, 34, 30, is_building=True)
        self.world = world([self.base, self.producer], 10)
        self.point = (44.5, 30.5)

    def test_busy_producer_is_never_re_rallied(self):
        out = self.rally.tick(self.world, busy={self.producer.tag},
                              tactical_point=self.point, tactical_control=True)
        self.assertEqual(out, [], 'busy 产能被重发集结点')
        self.assertEqual(self.rally.pending, {})
        self.assertFalse(any(e['kind'] == 'defensive_rally_requested' for e in self.events))

    def test_free_producer_is_rallied_to_tactical_point(self):
        out = self.rally.tick(self.world, busy=(), tactical_point=self.point, tactical_control=True)
        self.assertEqual(len(out), 1)
        c = out[0].action_raw.unit_command
        self.assertEqual(c.ability_id, RALLY_ABILITY)
        self.assertEqual(list(c.unit_tags), [self.producer.tag])
        self.assertAlmostEqual(c.target_world_space_pos.x, self.point[0])


class StaleRallyIsNotCleared(unittest.TestCase):
    """第 2 组:候选点全被否决时,旧集结点被保留。"""

    def setUp(self):
        self.backend = RallyBackend()
        self.events = []
        self.rally = DefensiveRally.__new__(DefensiveRally)
        self.rally.backend = self.backend
        self.rally.index = 0
        self.rally.event = lambda kind, **kw: self.events.append(dict(kind=kind, **kw))
        self.rally.next_at = 0
        self.rally.pending = {}
        self.rally.failures = {}
        self.rally.state = {'state': 'initializing'}
        self.rally.grid = grid()
        self.rally.ability = RALLY_ABILITY
        self.rally.disabled = None
        self.base = unit(1, TOWNHALL, 30, 30, is_building=True)  # 主基地不是产能建筑,只有 HATCHERY 是被指定目标
        self.producer = unit(2, HATCHERY, 34, 30, is_building=True)
        self.old_target = (45.5, 30.5)
        self.rally.confirmed = {self.producer.tag: self.old_target}

    def test_blocked_candidates_leave_old_rally_target_in_place(self):
        # 敌人直接站在 home->enemy 路线上,把候选点全部否决(威胁检查用 w.units)。
        blockers = [enemy(tag, 30 + i, 30) for tag, i in ((9, 12), (10, 13), (11, 14), (12, 15), (13, 16))]
        w = replace(world([self.base, self.producer, *blockers], 20), game_loop=448)
        out = self.rally.tick(w, busy=())
        self.assertEqual(out, [], '被否决时不应产生新命令')
        self.assertEqual(self.rally.confirmed[self.producer.tag], self.old_target,
                         '旧集结点被保留(正是"出一个送一个"的机制)')
        self.assertEqual(self.rally.state.get('state'), 'waiting')
        self.assertIn('blocked or threatened', self.rally.state.get('reason', ''))
        self.assertTrue(any(e['kind'] == 'defensive_rally_anchor' for e in self.events))


class TacticalStopStillWorks(unittest.TestCase):
    """对照组:机动部队在危险路线下会被显式 stop(ability 4),产能线路没有这一手。"""

    def setUp(self):
        from arena.tactics import ArmyTactics
        from test_tactics import Engine, Grid, roach
        self.roach = roach
        self.events = []
        self.rally = SimpleNamespace(grid=Grid())
        self.t = ArmyTactics(Engine(), 0, self.rally, lambda kind, **kw: self.events.append((kind, kw)),
                             lambda _: None, clock=lambda: 100.)
        self.addCleanup(self.t.close)
        self.t.anchor = (22, 30)
        self.t.next_scan = 1000

    def test_dangerous_path_stops_a_mobile_unit(self):
        from test_tactics import unit as tactics_unit
        stop = tactics_unit(2, 110, 85, 30, ground_dps=10, attack_range=4, orders=(MOVE,))
        far_enemy = tactics_unit(99, 32, 55, 30, 4, ground_dps=40, attack_range=13)
        w = replace(world([self.roach(1), stop, far_enemy], 1), units=(self.roach(1), stop, far_enemy), race='zerg')
        self.t._remember(w)  # 威胁索引先建立,再判路线;tick 内部同样先 _remember
        self.assertFalse(self.t._safe_route((85, 30), self.t.anchor))
        out = self.t.tick(w)
        cmds = {a.action_raw.unit_command.unit_tags[0]: a.action_raw.unit_command for a in out}
        self.assertIn(stop.tag, cmds)
        self.assertEqual(cmds[stop.tag].ability_id, 4, '危险路线必须显式停止,而不是沿旧路线前送')


if __name__ == '__main__':
    unittest.main()


class RallyOrderQueue(unittest.TestCase):
    """第 3 组:LLM 侧入口 tasks.rally_units(类型+序号 -> 真实建筑 tag)。"""

    def setUp(self):
        from arena.tasks import TaskQueue
        self.fixture, self.backend = engine_backend()
        self.queue = TaskQueue(self.backend, 0, lambda _: None)
        self.world = self.fixture.world([
            self.fixture.unit(1, HATCHERY, is_building=True),
            self.fixture.unit(2, HATCHERY, is_building=True),
            self.fixture.unit(3, TOWNHALL, is_building=True),
        ])

    def commands(self, plan, seconds=5):
        from arena.tasks import PlanValidationError  # noqa: F401
        self.queue.enqueue(plan, 0)
        out = []
        for step in range(1, seconds + 1):
            out += self.queue.tick(replace(self.world, game_loop=int(step * 22.4)), set())
        # 集结点只发一次(确认后不再重发),所以累计所有 tick 的动作
        return {tuple(a.action_raw.unit_command.unit_tags): a.action_raw.unit_command for a in out}

    def test_type_and_ordinal_bind_to_sorted_tags(self):
        # hatchery#1 -> tag 1,hatchery#2 -> tag 2;townhall(18)不是产能,不参与编号
        cmds = self.commands([{'skill': 'rally_units',
                               'args': {'producers': ['hatchery#2'], 'target': 'home', 'reason': '回主矿'}}])
        self.assertEqual(list(cmds), [(2,)])
        command_ = cmds[(2,)]
        self.assertEqual(command_.ability_id, RALLY_ABILITY)
        self.assertAlmostEqual(command_.target_world_space_pos.x, 30)
        self.assertAlmostEqual(command_.target_world_space_pos.y, 30)

    def test_producer_label_survives_an_earlier_building_death_and_lower_tag_birth(self):
        from arena.rally import producer_inventory
        initial=replace(self.world,units=(self.fixture.unit(10,HATCHERY,is_building=True),self.fixture.unit(20,HATCHERY,is_building=True)))
        self.assertEqual(producer_inventory(self.backend,initial,0)[1],{10:'hatchery#1',20:'hatchery#2'})
        after=replace(initial,units=(self.fixture.unit(20,HATCHERY,is_building=True),))
        self.assertEqual(producer_inventory(self.backend,after,0)[1],{20:'hatchery#2'})
        # Native tag order need not equal the order buildings were first observed.
        after=replace(after,units=after.units+(self.fixture.unit(5,HATCHERY,is_building=True),))
        self.assertEqual(producer_inventory(self.backend,after,0)[1],{5:'hatchery#3',20:'hatchery#2'})

    def test_labels_are_player_local_and_morph_does_not_renumber_surviving_hatcheries(self):
        from arena.rally import producer_inventory
        producer_inventory(self.backend,self.world,0)
        after=replace(self.world,units=(self.fixture.unit(1,100,is_building=True),self.fixture.unit(2,HATCHERY,is_building=True)))
        self.assertEqual(producer_inventory(self.backend,after,0)[1],{1:'lair#1',2:'hatchery#2'})
        other=replace(self.world,units=(self.fixture.unit(9,HATCHERY,is_building=True),))
        self.assertEqual(producer_inventory(self.backend,other,1)[1],{9:'hatchery#1'})

    def test_late_selector_cannot_rebind_to_a_new_building_after_the_original_dies(self):
        from arena.battle_plans import capture,revalidate
        before=capture(self.world,self.queue)
        after=replace(self.world,units=(self.fixture.unit(2,HATCHERY,is_building=True),self.fixture.unit(4,HATCHERY,is_building=True)))
        plan=dict(tasks=[dict(skill='rally_units',args=dict(producers=['hatchery#1'],target='home'))])
        self.assertTrue(revalidate(plan,before,after,self.queue,(30,30),35))

    def test_late_selector_with_a_stable_ordinal_gap_still_targets_its_original_building(self):
        from arena.battle_plans import capture,revalidate
        before=capture(self.world,self.queue)
        after=replace(self.world,units=(self.fixture.unit(2,HATCHERY,is_building=True),))
        plan=dict(tasks=[dict(skill='rally_units',args=dict(producers=['hatchery#2'],target='home'))])
        self.assertEqual(revalidate(plan,before,after,self.queue,(30,30),35),[])

    def test_late_selector_for_a_building_absent_from_the_request_needs_a_fresh_decision(self):
        from arena.battle_plans import capture,revalidate
        before=capture(self.world,self.queue)
        after=replace(self.world,units=self.world.units+(self.fixture.unit(4,HATCHERY,is_building=True),))
        plan=dict(tasks=[dict(skill='rally_units',args=dict(producers=['hatchery#3'],target='home'))])
        self.assertTrue(revalidate(plan,before,after,self.queue,(30,30),35))

    def test_explicit_entrance_can_anchor_to_a_partly_built_owned_expansion(self):
        self.world=replace(self.world,units=(self.fixture.unit(1,HATCHERY,is_building=True),
            replace(self.fixture.unit(2,HATCHERY,is_building=True),x=60,progress=.59)))
        cmds=self.commands([dict(skill='rally_units',args=dict(producers='all',target='expansion_entrance'))],1)
        self.assertEqual(list(cmds),[(1,)])
        self.assertGreater(cmds[(1,)].target_world_space_pos.x,60)
        self.assertEqual(self.queue.tasks[-1].status,'running')

    def test_early_unfinished_expansion_and_nonowned_bases_are_not_rally_anchors(self):
        for progress,alliance in ((.24,1),(.59,4)):
            with self.subTest(progress=progress,alliance=alliance):
                self.setUp()
                self.world=replace(self.world,units=(self.fixture.unit(1,HATCHERY,is_building=True),
                    replace(self.fixture.unit(2,HATCHERY,is_building=True),x=60,progress=progress,alliance=alliance)))
                self.assertEqual(self.commands([dict(skill='rally_units',args=dict(producers='all',target='expansion_entrance'))],1),{})
                self.assertEqual(self.queue.tasks[-1].status,'failed')

    def test_explicit_threatened_entrance_keeps_geometry_but_does_not_choose_tactics(self):
        from arena.rally import named_rally_point
        self.world=replace(self.world,units=(self.fixture.unit(1,HATCHERY,is_building=True),
            replace(self.fixture.unit(2,HATCHERY,is_building=True),x=60)))
        point,_=named_rally_point(self.backend.grid,self.backend,0,'expansion_entrance',self.world)
        pressure=replace(self.fixture.unit(9,48),x=point[0],y=point[1],alliance=4,ground_dps=10,attack_range=20)
        self.world=replace(self.world,units=self.world.units+(pressure,))
        with self.assertRaisesRegex(ValueError,'threatened'):
            named_rally_point(self.backend.grid,self.backend,0,'expansion_entrance',self.world)
        cmds=self.commands([dict(skill='rally_units',args=dict(producers=['hatchery#1'],target='expansion_entrance'))],1)
        self.assertEqual(list(cmds),[(1,)])
        self.assertAlmostEqual(cmds[(1,)].target_world_space_pos.x,point[0])
        self.assertAlmostEqual(cmds[(1,)].target_world_space_pos.y,point[1])

    def test_explicit_entrance_still_rejects_an_occupied_approach(self):
        obstacles=[replace(self.fixture.unit(20+i,21,is_building=True),x=60+i,y=30+i) for i in range(10)]
        self.world=replace(self.world,units=(self.fixture.unit(1,HATCHERY,is_building=True),
            replace(self.fixture.unit(2,HATCHERY,is_building=True),x=60),*obstacles))
        self.assertEqual(self.commands([dict(skill='rally_units',args=dict(producers='all',target='expansion_entrance'))],1),{})
        self.assertEqual(self.queue.tasks[-1].status,'failed')

    def test_same_plan_is_coalesced_and_different_producers_coexist(self):
        self.commands([{'skill': 'rally_units', 'args': {'producers': ['hatchery#1'], 'target': 'home'}}])
        self.commands([{'skill': 'rally_units', 'args': {'producers': ['hatchery#1'], 'target': 'home'}}])
        active = [t for t in self.queue.tasks if t.active and t.skill == 'rally_units']
        self.assertEqual(len(active), 1, '相同目标+产能的重复计划应合并')
        self.commands([{'skill': 'rally_units', 'args': {'producers': ['hatchery#2'], 'target': 'home'}}])
        active = [t for t in self.queue.tasks if t.active and t.skill == 'rally_units']
        self.assertEqual(len(active), 2, '不同产能的订单应共存')
        self.assertEqual(self.queue.rally_owned_tags(), {1, 2})

    def test_generic_cancel_can_replace_owned_rally_without_rejecting_reinforcements(self):
        self.commands([dict(skill='rally_units',args=dict(producers=['hatchery#1'],target='home'))])
        old=self.queue.tasks[-1]
        self.queue.enqueue([dict(skill='cancel_task',args=dict(task_id=old.id)),
            dict(skill='rally_units',args=dict(producers=['hatchery#1'],target=[80,80],reason='前线集结')),
            dict(skill='train_worker',count=2)],6)
        self.assertEqual(old.status,'cancelled')
        commands=self.queue.tick(replace(self.world,game_loop=int(7*22.4)),set())
        rally=[a.action_raw.unit_command for a in commands
               if a.action_raw.unit_command.ability_id==RALLY_ABILITY]
        self.assertEqual(len(rally),1)
        self.assertEqual((rally[0].target_world_space_pos.x,rally[0].target_world_space_pos.y),(79.5,79.5))
        self.assertEqual(len(self.queue.rally_orders()),1)
        self.assertEqual(self.queue.tasks[-1].count,2)

    def test_ended_rally_cancel_is_noop_and_mixed_invalid_plan_preserves_active_rally(self):
        from arena.tasks import PlanValidationError
        self.commands([dict(skill='rally_units',args=dict(producers=['hatchery#1'],target='home'))])
        old=self.queue.tasks[-1]
        with self.assertRaises(PlanValidationError):self.queue.enqueue([
            dict(skill='cancel_task',args=dict(task_id=old.id)),dict(skill='invented')],6)
        self.assertTrue(old.active)
        self.queue.update(old,'completed','expired')
        self.queue.enqueue([dict(skill='cancel_task',args=dict(task_id=old.id))],7)
        self.assertEqual(old.status,'completed')
        with self.assertRaises(PlanValidationError):self.queue.enqueue([dict(skill='cancel_task',args=dict(task_id=999))],7)

    def test_late_cancel_of_a_known_rally_remains_valid_after_expiration(self):
        from arena.battle_plans import capture,revalidate
        self.commands([dict(skill='rally_units',args=dict(producers=['hatchery#1'],target='home'))])
        old=self.queue.tasks[-1];before=capture(self.world,self.queue)
        plan=dict(tasks=[dict(skill='cancel_task',args=dict(task_id=old.id))])
        self.assertEqual(revalidate(plan,before,self.world,self.queue,(30,30),35),[])
        self.assertTrue(old.active)
        self.queue.update(old,'completed','expired while the model was thinking')
        self.assertEqual(revalidate(plan,before,self.world,self.queue,(30,30),35),[])
        self.assertEqual(old.status,'completed')

    def test_late_rally_cancel_rejects_unknown_or_newly_created_targets(self):
        from arena.battle_plans import capture,revalidate
        before=capture(self.world,self.queue)
        self.commands([dict(skill='rally_units',args=dict(producers=['hatchery#1'],target='home'))])
        for task_id in (self.queue.tasks[-1].id,999):
            self.assertTrue(revalidate(dict(tasks=[dict(skill='cancel_task',args=dict(task_id=task_id))]),
                before,self.world,self.queue,(30,30),35))

    def test_late_player_two_defense_uses_its_own_published_base_labels(self):
        from arena.battle_plans import capture,revalidate
        from arena.defense_orders import base_labels
        from arena.tasks import TaskQueue
        self.backend.homes=[(30,30),(150,150)];self.backend.races=['zerg','zerg']
        q=TaskQueue(self.backend,1,lambda _:None)
        w=replace(self.world,units=(self.fixture.unit(1,HATCHERY,is_building=True),
            replace(self.fixture.unit(2,HATCHERY,is_building=True),x=150,y=150)))
        self.assertEqual(base_labels(w,self.backend.homes[1]),{1:'base_1',2:'home'})
        before=capture(w,q)
        plan=dict(tasks=[dict(skill='defend_base',args=dict(base='base_1',units='all'))])
        self.assertEqual(revalidate(plan,before,w,q,self.backend.homes[1],35),[])

    def test_coordinate_target_is_clamped_to_a_grid_cell(self):
        # 坐标永远被夹到最近的合法格心(不会直接采用浮点原始坐标);1 格内即为通过。
        raw = (40.0, 40.0)
        cmds = self.commands([{'skill': 'rally_units',
                               'args': {'producers': 'all', 'target': list(raw), 'duration_s': 600}}])
        self.assertEqual(sorted(cmds), [(1,), (2,)])
        for tag in (1, 2):
            command_ = cmds[(tag,)]
            x, y = command_.target_world_space_pos.x, command_.target_world_space_pos.y
            self.assertAlmostEqual(x % 1, .5, places=6, msg='格心坐标形如 n+0.5')
            self.assertAlmostEqual(y % 1, .5, places=6, msg='格心坐标形如 n+0.5')
            self.assertLessEqual(abs(x - raw[0]), 1.5)
            self.assertLessEqual(abs(y - raw[1]), 1.5)
            self.assertTrue(self.backend.grid.free(int(x), int(y)))

    def test_unresolvable_coordinate_fails_and_releases_automatic_rally_ownership(self):
        self.assertEqual(self.commands([dict(skill='rally_units',args=dict(producers='all',target=[250,250]))],1),{})
        task=self.queue.tasks[-1]
        self.assertEqual(task.status,'failed')
        self.assertIn('no ground cell',task.reason)
        self.assertEqual(self.queue.rally_owned_tags(),set())

    def test_missing_native_rally_identity_fails_and_releases_ownership(self):
        self.backend.tech_tree=SimpleNamespace(abilities={})
        self.assertEqual(self.commands([dict(skill='rally_units',args=dict(producers='all',target='home'))],1),{})
        self.assertEqual(self.queue.tasks[-1].status,'failed')
        self.assertIn('GeneralRallyUnits',self.queue.tasks[-1].reason)
        self.assertEqual(self.queue.rally_owned_tags(),set())

    def test_bad_selector_or_target_is_rejected_at_enqueue(self):
        from arena.tasks import PlanValidationError
        for args in ({'producers': ['hatchery'], 'target': 'home'},
                     {'producers': [], 'target': 'home'},
                     {'producers': ['hatchery#0'], 'target': 'home'},
                     {'producers': ['hatchery#1'], 'target': 'nowhere'},
                     {'producers': ['hatchery#1'], 'target': [1, 2, 3]},
                     {'producers': ['hatchery#1'], 'target': 'home', 'duration_s': 0},
                     {'producers': ['hatchery#1'], 'target': 'home', 'wat': 1}):
            with self.assertRaises(PlanValidationError, msg=repr(args)):
                self.queue.enqueue([{'skill': 'rally_units', 'args': args}], 0)

    def test_missing_building_fails_visibly_instead_of_moving_another_one(self):
        self.queue.enqueue([{'skill': 'rally_units',
                             'args': {'producers': ['hatchery#9'], 'target': 'home'}}], 0)
        out = self.queue.tick(replace(self.world, game_loop=22), set())
        self.assertEqual(out, [])
        task = self.queue.tasks[0]
        self.assertEqual(task.status, 'failed')
        self.assertIn('hatchery#9', task.reason)


class CommanderWiring(unittest.TestCase):
    """第 4 组:指挥层把显式订单的产能从自动集结点的 busy 集合里排除。"""

    def test_commander_marks_ordered_producers_busy_for_the_automatic_rally(self):
        from test_tactics import Engine, Grid, Idle, roach
        from arena.commander import Commander
        import tempfile

        class MiniEngine(Engine):
            # 引擎能力由 RallyOrder 自己解析;这里直接预置 ability,避免只为测试伪造整棵 tech_tree
            def available_abilities(self, index, tags, ignore_resources=False):
                return {tag: {RALLY_ABILITY} for tag in tags}

        backend_ = MiniEngine()
        world_ = replace(world([unit(1, HATCHERY, is_building=True), roach(2)], 0),
                         units=(unit(1, HATCHERY, is_building=True), roach(2)), race='zerg')
        backend_.initial = [world_]
        with tempfile.TemporaryDirectory() as folder:
            commander = Commander(backend_, 0, Idle(), opening=False, tactics_enabled=True,
                                  feedback_dir=folder)
            try:
                commander.rally.grid = Grid()
                commander.tactics.anchor = (22, 30)
                commander.tactics.next_scan = 1000
                plan = [{'skill': 'rally_units', 'args': {'producers': ['hatchery#1'], 'target': 'home'}}]
                seen = {}
                original = commander.rally.tick

                def spy(world__, busy=(), **kwargs):
                    seen['busy'] = set(busy)
                    return original(world__, busy, **kwargs)

                commander.rally.tick = spy
                commander.tasks.enqueue(plan, 0)
                commander.tasks.tasks[0].program.ability = RALLY_ABILITY
                commander.tick(world_)
                self.assertIn(1, seen.get('busy', set()),
                              '显式订单的产能必须进入自动集结点的 busy 集合')
                self.assertEqual(commander.tasks.rally_owned_tags(), {1})
            finally:
                commander.stop()

    def test_unknown_producer_fails_the_task_with_a_concrete_reason(self):
        from test_tactics import Engine, Idle, roach
        from arena.commander import Commander
        import tempfile

        backend_ = Engine()
        world_ = replace(world([unit(1, HATCHERY, is_building=True), roach(2)], 0),
                         units=(unit(1, HATCHERY, is_building=True), roach(2)), race='zerg')
        backend_.initial = [world_]
        with tempfile.TemporaryDirectory() as folder:
            commander = Commander(backend_, 0, Idle(), opening=False, tactics_enabled=True,
                                  feedback_dir=folder)
            try:
                commander.tasks.enqueue([{'skill': 'rally_units',
                                          'args': {'producers': ['hatchery#3'], 'target': 'home'}}], 0)
                commander.tick(world_)
                task = next(t for t in commander.tasks.tasks if t.skill == 'rally_units')
                self.assertEqual(task.status, 'failed')
                self.assertIn('hatchery#3', task.reason)
            finally:
                commander.stop()


class PromptExposure(unittest.TestCase):
    """第 5 组:模型真的能看到产能清单、地名表与订单状态(否则技能无法被使用)。"""

    def test_prompt_carries_producer_inventory_landmarks_and_order_state(self):
        import json
        import tempfile
        from pathlib import Path
        from test_tactics import Engine, roach
        from arena.commander import Commander

        class Client:
            def ready_for_request(self): return True

            def chat(self, system, prompt): return '{"tasks":[],"reason":"受控等待"}'

        from test_multirace import MultiRace
        fixture = MultiRace()
        fixture.setUp()
        backend_ = Engine()
        # rally_producers 与 tech_tree 块一起发布;真实 backend 有 tech_tree,这里用同一份缓存树
        backend_.tech_tree = fixture.tree
        backend_.races = ['zerg']
        backend_.available_abilities = lambda index, tags, ignore_resources=False: {tag: {RALLY_ABILITY} for tag in tags}
        world_ = replace(world([unit(1, HATCHERY, is_building=True), roach(2)], 0),
                         units=(unit(1, HATCHERY, is_building=True), roach(2)), race='zerg')
        backend_.initial = [world_]
        with tempfile.TemporaryDirectory() as folder:
            commander = Commander(backend_, 0, Client(), opening=False, tactics_enabled=True,
                                  feedback_dir=folder)
            try:
                commander.tasks.enqueue([{'skill': 'rally_units',
                                          'args': {'producers': ['hatchery#1'], 'target': 'home'}}], 0)
                commander.tick(world_)
                rows = [json.loads(line) for line in Path(commander.feedback.path).read_text().splitlines()]
                prompt = next(r['prompt'] for r in rows if r['record'] == 'request_queued')
                inventory = prompt.get('rally_producers')
                self.assertTrue(inventory, '输入里必须发布 rally_producers 清单')
                self.assertEqual(inventory[0]['label'], 'hatchery#1')
                self.assertIn('main_ramp_top', prompt.get('rally_landmarks', []))
                self.assertIsInstance(prompt.get('rally_orders'), list)
                self.assertIn('rally_units', commander.system, '系统提示必须告诉模型这个技能怎么用')
            finally:
                commander.stop()
