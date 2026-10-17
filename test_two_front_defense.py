"""双线受袭场景单测:两个基地同时被打(敌兵力不同),模型下令后脚本必须按模型意图分兵。

这层证明的是**机制**:给定"模型选了哪几处、派哪些兵",脚本真的能同时守住两处、不越界、
不双指挥。模型是否会在真实对局里做出这种选择属于模型侧行为,不在本文件证明范围
(那需要一局原生对局,见 verify_defense_two_front_live.py)。
"""
import unittest
from dataclasses import replace
from types import SimpleNamespace

from arena.commander import Commander
from arena.defense_orders import base_threats
from arena.raw_engine import ATTACK
from test_rally_orders import engine_backend, HATCHERY, RALLY_ABILITY
from test_realtime_runtime import unit, world

TOWNHALL, MARINE, VIKING, ENEMY_MARINE = 18, 48, 34, 48


class ScriptedModel:
    """脚本化"模型":看到 prompt 后按预设意图回包,用来验证机制而非模型能力。"""

    def __init__(self, plan):
        self.plan = plan          # prompt -> list of tasks
        self.prompts = []

    def chat(self, system, prompt):
        import json
        self.prompts.append(json.loads(prompt))
        return json.dumps(self.plan(self.prompts[-1]))

    def ready_for_request(self):
        return True


class TwoFrontDefense(unittest.TestCase):
    def setUp(self):
        # The engine-bound defense executor checks native Attack availability;
        # the default rally-only fixture cannot execute a combat order.
        self.fixture, self.backend = engine_backend('terran',available=(ATTACK,RALLY_ABILITY))
        self.home = unit(1, TOWNHALL, 30, 30, is_building=True, progress=1.0)
        self.far = unit(2, TOWNHALL, 150, 150, is_building=True, progress=1.0)
        # Commander 需要一份初始观测;真正的世界由每个用例单独构造
        self.backend.initial = [replace(world([self.home, self.far], 0),
                                        units=(self.home, self.far), race='terran')]
        self.backend.register_action_context = lambda *a, **k: None
        # 真实链路会在加载科技数据时把引擎名字灌进 NAMES(raw_engine.NAMES.update);
        # fixture 这里补上同样的名字,否则报告与校验会看到不同的字符串。
        from arena.raw_engine import NAMES
        NAMES.update({TOWNHALL: 'CommandCenter', MARINE: 'Marine', VIKING: 'Viking'})

    def build(self, units, sec=5):
        allu = (self.home, self.far) + tuple(units)
        return replace(world(list(allu), sec), units=allu, race='terran')

    def test_two_bases_under_different_pressure_are_both_defended_as_ordered(self):
        """两个基地同时受袭(敌方 3 个 vs 1 个):模型按压力下令两处,脚本各派就近部队。"""
        w = self.build([
            unit(10, MARINE, 34, 34, ground_dps=10, attack_range=5),      # 主矿就近
            unit(11, VIKING, 146, 146, ground_dps=12, attack_range=6),     # 二矿就近
            unit(90, ENEMY_MARINE, 38, 38, alliance=4, ground_dps=10, attack_range=5),
            unit(91, ENEMY_MARINE, 39, 37, alliance=4, ground_dps=10, attack_range=5),
            unit(92, ENEMY_MARINE, 37, 39, alliance=4, ground_dps=10, attack_range=5),
            unit(93, ENEMY_MARINE, 154, 154, alliance=4, ground_dps=10, attack_range=5),
        ])
        seen = {}

        def plan(payload):
            seen['payload'] = payload
            report = {row['label']: row for row in payload['defense_report']}
            marks = [label for label, row in report.items() if row['visible_attackers'] > 0]
            self.assertEqual(len(marks), 2, '报告必须指出两处同时受袭: %s' % report)
            heavy = max(marks, key=lambda m: report[m]['visible_attackers'])
            light = [m for m in marks if m != heavy][0]
            # 用报告 own_nearby 里的真实名字下令(模型只看得到这些名字)
            heavy_kind = sorted(report[heavy]['own_nearby'])[0]
            light_kind = sorted(report[light]['own_nearby'])[0]
            return {'tasks': [
                {'skill': 'defend_base', 'args': {'base': heavy, 'units': [heavy_kind], 'reason': '压力大的一处先顶住'}},
                {'skill': 'defend_base', 'args': {'base': light, 'units': [light_kind], 'reason': '压力小的一处交给就近部队'}},
            ], 'reason': '两处同时防守,按压力分配兵种'}

        import tempfile, time
        with tempfile.TemporaryDirectory() as folder:
            commander = Commander(self.backend, 0, ScriptedModel(plan), opening=False,
                                  tactics_enabled=False, feedback_dir=folder)
            try:
                # 决策间隔 6 游戏秒:推进必须够大,否则只发一次请求
                step = 134
                out = []
                for i in range(6):
                    # Initial guard orders can be issued while the asynchronous
                    # reply is first consumed. Do not discard that half of the trace.
                    out += commander.tick(replace(w, game_loop=w.game_loop + step * i))
                    time.sleep(0.03)      # 让异步工作线程把回包放进 outbox
                self.assertIn('payload', seen, '模型必须收到一次请求')
                report = {r['label']: r for r in seen['payload']['defense_report']}
                attacked = sorted(report[l]['visible_attackers'] for l in report if report[l]['visible_attackers'] > 0)
                self.assertEqual(attacked, [1, 3], '两处压力必须如实反映')
                # 让脚本执行模型的两条令
                for i in range(6, 12):
                    out += commander.tick(replace(w, game_loop=w.game_loop + step * i))
                    time.sleep(0.02)
                issued = {}
                for a in out:
                    uc = a.action_raw.unit_command
                    if uc.ability_id != ATTACK:
                        continue
                    for tag in uc.unit_tags:
                        issued[tag] = (round(uc.target_world_space_pos.x), round(uc.target_world_space_pos.y))
                self.assertIn(10, issued, '主矿就近的部队必须被派去防守')
                self.assertIn(11, issued, '二矿就近的部队必须被派去防守')
                self.assertLess(issued[10][0], 100, '主矿部队应守主矿方向')
                self.assertGreater(issued[11][0], 100, '二矿部队应守二矿方向')
                orders = [t.program for t in commander.tasks.tasks if t.skill == 'defend_base' and t.active]
                self.assertEqual(len(orders), 2, '两处防守必须并存')
                self.assertFalse(orders[0].tags & orders[1].tags, '两条令不得共用单位')
            finally:
                commander.stop()


    def test_report_lists_both_bases_with_their_own_pressure(self):
        w = self.build([
            unit(90, ENEMY_MARINE, 38, 38, alliance=4, ground_dps=10, attack_range=5),
            unit(93, ENEMY_MARINE, 154, 154, alliance=4, ground_dps=10, attack_range=5),
            unit(94, ENEMY_MARINE, 155, 153, alliance=4, ground_dps=10, attack_range=5),
        ])
        rows = base_threats(w, self.backend, 0)
        self.assertEqual(len(rows), 2, '每个己方基地一行')
        counts = sorted(r['visible_attackers'] for r in rows)
        self.assertEqual(counts, [1, 2], '两处压力必须分别统计')
        for r in rows:
            self.assertIn('own_nearby', r)
            self.assertFalse(r['defended'], '未下令时报告必须显示未防守')


if __name__ == '__main__':
    unittest.main()
