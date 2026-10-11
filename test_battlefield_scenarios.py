"""战场场景判别测试(对应 SCENARIOS.md 的 A1~A12)。

分工:
- 机械层(本文件):构造战场 → 给一个**脚本化的模型决定** → 断言脚本严格按该决定执行,
  不静默回退、不替模型改主意、单位归属唯一。
- 模型层(原生):见 verify_scenarios_live.py,由真模型在同一批场景里做决定。

凡是"项目尚未提供的手段"一律标记为待实现缺口(见 KNOWN_GAPS),并在缺口消失后自动生效,
不写成永远不会通过的期望。
"""
import unittest
from dataclasses import replace

from arena.defense_orders import base_threats
from arena.raw_engine import ATTACK, MOVE
from arena.tasks import TaskQueue, PlanValidationError
from test_realtime_runtime import Backend, unit, world

CC, MARINE, VIKING = 18, 48, 34
ENEMY = 4
# 玩家当前没有"持续进攻/撤退"语义:attack_nearest 与 move_out 都是发完即 completed
KNOWN_GAPS = {
    'A7': 'persistent attack mission (units must keep fighting, not idle on arrival)',
    'A8': 'retreat / abort semantics for units already committed to an attack',
}


class ScenarioQueue(Backend):
    def __init__(self):
        from arena.raw_engine import NAMES
        NAMES.update({CC: 'CommandCenter', MARINE: 'Marine', VIKING: 'Viking'})
        self.tech_tree = None
        self.races = ['terran']
        self.homes = [(30, 30)]
        self.types = {}
        self.register_action_context = lambda *a, **k: None

    def enemy_start(self, i):
        return (160, 160)

    def available_abilities(self, index, tags, ignore_resources=False):
        return {t: set() for t in tags}


def marine(tag, x, y):
    return unit(tag, MARINE, x, y, ground_dps=10, attack_range=5)


def viking(tag, x, y):
    return unit(tag, VIKING, x, y, ground_dps=12, attack_range=6)


def enemy(tag, x, y):
    return unit(tag, MARINE, x, y, alliance=ENEMY, ground_dps=10, attack_range=5)


class ScenarioCase(unittest.TestCase):
    """公共夹具:一个主矿 + 一个分矿,部队位置由各场景决定。"""

    def setUp(self):
        self.backend = ScenarioQueue()
        self.queue = TaskQueue(self.backend, 0, lambda _: None)
        self.home = unit(1, CC, 30, 30, is_building=True)
        self.far = unit(2, CC, 150, 150, is_building=True)

    def snap(self, units, sec=1):
        allu = (self.home, self.far) + tuple(units)
        return replace(world(list(allu), sec), units=allu)

    def ordered_tags(self):
        """生产链路里 Commander 会把"已下令的基地"传进报告;测试同样要传,否则 defended 恒为假。"""
        return {t.program.base_tag for t in self.queue.tasks
                if t.skill == 'defend_base' and t.active and t.program and t.program.base_tag}

    def sent(self, out):
        """本次 tick 实际下达的单位命令:{tag: (ability, x, y)}"""
        result = {}
        for action in out:
            raw = action.action_raw.unit_command
            for tag in raw.unit_tags:
                result[tag] = (raw.ability_id, round(raw.target_world_space_pos.x),
                               round(raw.target_world_space_pos.y))
        return result

    # ---------------- A1: 单基地受袭,部队在远处 ----------------
    def test_a1_distant_squad_is_ordered_to_the_attacked_base(self):
        self.queue.enqueue([{'skill': 'defend_base', 'args': {
            'base': 'home', 'units': ['Marine'], 'reason': '回防主矿'}}], 0)
        out = self.queue.tick(self.snap([marine(10, 140, 140), enemy(9, 36, 36)]), set())
        sent = self.sent(out)
        self.assertIn(10, sent, '远处的部队必须在受袭当轮被派往主矿')
        self.assertLess(sent[10][1], 100, '应朝主矿方向移动')

    # ---------------- A3: 兵力不足 ----------------
    def test_a3_insufficient_force_is_reported_not_silently_ignored(self):
        task = [t for t in self.queue.tasks if t.skill == 'defend_base']
        self.queue.enqueue([{'skill': 'defend_base', 'args': {
            'base': 'home', 'units': ['Viking'], 'count': 5, 'reason': '顶住'}}], 0)
        task = [t for t in self.queue.tasks if t.skill == 'defend_base'][-1]
        out = self.queue.tick(self.snap([marine(10, 140, 140), enemy(9, 36, 36)]), set())
        self.assertEqual(out, [], '没有匹配兵种时不应乱下命令')
        self.assertIn(task.status, ('waiting', 'running'), '状态必须可见')
        self.assertTrue(task.program.note or task.reason, '必须说明为什么没派兵')

    # ---------------- A4: 敌方增援 ----------------
    def test_a4_second_wave_is_visible_to_the_model(self):
        first = self.snap([marine(10, 34, 34), enemy(9, 36, 36)])
        before = [r for r in base_threats(first, self.backend, 0) if r['visible_attackers']][0]
        second = self.snap([marine(10, 34, 34), enemy(9, 36, 36), enemy(11, 37, 37),
                            enemy(12, 35, 38)], sec=30)
        after = [r for r in base_threats(second, self.backend, 0) if r['visible_attackers']][0]
        self.assertEqual(before['visible_attackers'], 1)
        self.assertEqual(after['visible_attackers'], 3, '增援必须体现在报告里')
        self.assertLess(after['seconds_to_loss'], before['seconds_to_loss'],
                        '压力上升必须让"还能撑多久"下降')

    def test_a4_same_base_override_inherits_and_can_reinforce(self):
        old = self._order(base='home', units='all', count=2)
        self.queue.tick(self.snap([marine(10, 32, 32), marine(11, 33, 33), enemy(9, 37, 37)]), set())
        held = set(old.program.tags)
        self.queue.enqueue([{'skill': 'defend_base', 'args': {
            'base': 'home', 'units': 'all', 'count': 2, 'reason': '增援'}}], 4)
        new = [t for t in self.queue.tasks if t.skill == 'defend_base' and t.active][-1]
        self.assertIn('superseded', old.reason)
        self.queue.tick(self.snap([marine(10, 32, 32), marine(11, 33, 33), enemy(9, 37, 37)], sec=5), set())
        self.assertTrue(held <= set(new.program.tags), '覆盖后必须继承原部队')

    # ---------------- A5: 威胁消失 ----------------
    def test_a5_quiet_base_keeps_the_order_but_reports_quiet(self):
        task = self._order(base='home', units='all')
        self.queue.tick(self.snap([marine(10, 32, 32), enemy(9, 37, 37)]), set())
        out = self.queue.tick(self.snap([marine(10, 32, 32)], sec=30), set())
        self.assertEqual(out, [], '敌情消失后不应继续下命令')
        self.assertEqual(task.status, 'running', '不自动结束(决定权在模型)')
        row = [r for r in base_threats(self.snap([marine(10, 32, 32)], sec=30), self.backend, 0,
                                       ordered_tags=self.ordered_tags()) if r['defended']]
        self.assertTrue(row and row[0]['urgency'] == 'quiet', '报告必须显示已安静')

    # ---------------- A6: 分矿受袭 ----------------
    def test_a6_expansion_can_be_ordered_and_tracked_separately(self):
        first = self._order(base='home', units=['Marine'])
        second = self._order(base=[152.0, 152.0], units=['Viking'])
        w = self.snap([marine(10, 32, 32), viking(12, 148, 148), enemy(8, 36, 36), enemy(9, 152, 152)])
        self.queue.tick(w, set())
        self.assertEqual(first.program.tags, {10})
        self.assertEqual(second.program.tags, {12})
        report = {r['tag']: r for r in base_threats(w, self.backend, 0,
                                                     ordered_tags=self.ordered_tags())}
        self.assertTrue(report[self.home.tag]['defended'] and report[self.far.tag]['defended'],
                        '两处都必须被标记为已下令防守')

    # ---------------- A9: 部队被歼 ----------------
    def test_a9_order_reports_when_its_units_are_gone(self):
        task = self._order(base='home', units='all')
        self.queue.tick(self.snap([marine(10, 32, 32), enemy(9, 36, 36)]), set())
        self.assertEqual(task.program.tags, {10})
        # 单位阵亡:下一拍必须可见地报"没有可用的兵",而不是假装还在防守
        out = self.queue.tick(self.snap([enemy(9, 36, 36)], sec=5), set())
        self.assertEqual(out, [])
        self.assertEqual(task.program.state, 'no_free_units')
        self.assertTrue(task.program.note, '必须给出原因')

    # ---------------- A11: 三处同时受袭 ----------------
    def test_a11_three_bases_three_orders_share_the_army_without_overlap(self):
        third = unit(3, CC, 80, 30, is_building=True)
        allu = (self.home, self.far, third) + tuple(
            [marine(20 + i, 32 + i, 32 + i) for i in range(6)] +
            [enemy(40 + i, 36 + i, 36 + i) for i in range(3)])
        w = replace(world(list(allu), 1), units=allu)
        self.queue.enqueue([{'skill': 'defend_base', 'args': {'base': 'home', 'units': 'all', 'reason': 'a'}},
                            {'skill': 'defend_base', 'args': {'base': [152.0, 152.0], 'units': 'all', 'reason': 'b'}},
                            {'skill': 'defend_base', 'args': {'base': [80.0, 30.0], 'units': 'all', 'reason': 'c'}}], 0)
        self.queue.tick(w, set())
        orders = [t.program.tags for t in self.queue.tasks if t.skill == 'defend_base']
        self.assertEqual(len(orders), 3, '三条令必须并存')
        total = set()
        for tags in orders:
            self.assertFalse(total & tags, '三条令不得共用单位')
            total |= tags
        # 真正的不变量:不得共用;没分到兵的那条必须**可见地**说明原因(不能假装在守)
        for task in [t for t in self.queue.tasks if t.skill == 'defend_base']:
            if not task.program.tags:
                self.assertTrue(task.program.note,
                                'task %s 没分到兵却没有说明原因' % task.id)

    # ---------------- A12: 敌人退入迷雾 ----------------
    def test_a12_only_visible_enemies_are_engaged(self):
        self._order(base='home', units='all')
        # 敌人已不可见(退入迷雾),基地旁没有可见目标
        out = self.queue.tick(self.snap([marine(10, 32, 32)]), set())
        self.assertEqual(out, [], '不得对不可见(仅记忆中的)目标下达攻击命令')

    def _order(self, **args):
        args.setdefault('reason', '场景')
        self.queue.enqueue([{'skill': 'defend_base', 'args': args}], 0)
        return [t for t in self.queue.tasks if t.skill == 'defend_base'][-1]


@unittest.skipUnless(not KNOWN_GAPS, '待实现缺口: ' + '; '.join(KNOWN_GAPS.values()))
class CommittedForceScenarios(unittest.TestCase):
    """A7/A8:主动进攻与撤退。缺口补齐后自动启用(见 SCENARIOS.md B1/B2)。"""

    def test_a7_attack_keeps_fighting_after_arrival(self):
        raise NotImplementedError

    def test_a8_committed_attack_can_be_aborted_and_withdrawn(self):
        raise NotImplementedError



class SurrenderGate(unittest.TestCase):
    """A13(pre):有部队/收入/可重建资源时不得投降 —— 早退是误判,不是合理行为。"""

    def test_alive_army_blocks_surrender(self):
        from types import SimpleNamespace as S
        from arena.match_control import surrender_blockers, surrender_allowed

        def world_(minerals=0, vespene=0, units=()):
            return S(minerals=minerals, vespene=vespene, my_units=list(units))

        def combat(tag=48, dps=10):
            return S(type_id=tag, ground_dps=dps, air_dps=0, is_building=False)

        def worker(tag=45):
            return S(type_id=tag, ground_dps=0, air_dps=0, is_building=False)

        self.assertTrue(surrender_blockers(world_(0, 0, [combat()])),
                        '还有战斗单位时不得投降')
        self.assertTrue(surrender_blockers(world_(0, 0, [worker()])),
                        '还有工人(有收入)时不得投降')
        self.assertTrue(surrender_blockers(world_(300, 0, [])),
                        '还有资源可重建时不得投降')
        self.assertEqual(surrender_blockers(world_(0, 0, [])), [],
                         '无兵、无工人、无资源 = 确实没翻盘手段,允许投降')
        self.assertTrue(surrender_allowed(world_(0, 0, [])))
        self.assertFalse(surrender_allowed(world_(0, 0, [combat()])))


class KnownGapsAreTracked(unittest.TestCase):
    def test_every_known_gap_is_listed_in_the_scenarios_document(self):
        text = open('SCENARIOS.md', encoding='utf-8').read()
        for code, description in KNOWN_GAPS.items():
            self.assertIn(code, text, '缺口 %s 必须在清单里' % code)
            self.assertTrue(description, '缺口 %s 必须有描述' % code)


if __name__ == '__main__':
    unittest.main()
