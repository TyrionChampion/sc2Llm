"""判别测试:防守由模型显式下令,脚本只执行、不替它决定。

重点钉住四件事(用户明确要求):
1. 没下令的基地,脚本绝不自动派兵 —— "弃守"必须表现为"没有动作 + 如实报告";
2. 模型能按兵种/数量分兵,两条令各持其兵、互不偷;
3. 显式任务持有的单位不被防守令抢走;
4. 取消/结束后必须释放,否则转攻发不出命令。
受控 Snapshot/TaskQueue,不调模型、不开游戏。
"""
import unittest
from dataclasses import replace

from arena.defense_orders import base_threats
from arena.raw_engine import ATTACK, command
from arena.tasks import TaskQueue, PlanValidationError
from test_realtime_runtime import Backend, unit, world

CC, MARINE, VIKING = 18, 48, 34


class DefenseQueue(Backend):
    """假 backend。单位名走 NAMES,与 Snapshot.summary/world.own 同一命名源。"""

    def __init__(self):
        from arena.raw_engine import NAMES
        NAMES.update({48: 'Marine', 51: 'Marauder', 34: 'Viking', 18: 'CommandCenter'})
        self.tech_tree = None
        self.races = ['terran']
        self.homes = [(30, 30)]
        self.types = {}

    def enemy_start(self, i):
        return (160, 160)

    def available_abilities(self, index, tags, ignore_resources=False):
        return {t: set() for t in tags}


def marine(tag, x, y):
    return unit(tag, MARINE, x, y, ground_dps=10, attack_range=5)


def viking(tag, x, y):
    return unit(tag, VIKING, x, y, ground_dps=12, attack_range=6)


def enemy(tag, x, y):
    return unit(tag, MARINE, x, y, alliance=4, ground_dps=10, attack_range=5)


class DefendBaseOrders(unittest.TestCase):
    def setUp(self):
        self.backend = DefenseQueue()
        self.queue = TaskQueue(self.backend, 0, lambda _: None)
        self.base = unit(1, CC, 30, 30, is_building=True)
        self.far_base = unit(2, CC, 150, 150, is_building=True)

    def snap(self, units, sec=1):
        allu = (self.base, self.far_base) + tuple(units)
        return replace(world(list(allu), sec), units=allu)

    def order(self, **args):
        args.setdefault('reason', '防守')
        args.setdefault('units', 'all')
        self.queue.enqueue([{'skill': 'defend_base', 'args': args}], 0)
        return [t for t in self.queue.tasks if t.skill == 'defend_base'][-1]

    # -- 1. 脚本不替模型决定 -------------------------------------------------
    def test_unordered_base_is_never_auto_defended(self):
        """二矿被打但模型没下令 => 绝不自动派兵,只如实报告该基地无人防守。"""
        w = self.snap([marine(10, 145, 145), enemy(9, 152, 152)])
        self.assertEqual(self.queue.tick(w, set()), [], '没有防守令时不得产生任何防守命令')
        report = base_threats(w, self.backend, 0)
        attacked = [r for r in report if r['visible_attackers'] > 0]
        self.assertTrue(attacked, '受袭基地必须出现在报告里')
        self.assertFalse(any(r['defended'] for r in attacked), '未下令的基地必须显示为未防守')

    def test_report_shows_visible_attackers_and_nearby_own_units(self):
        w = self.snap([marine(10, 145, 145), enemy(9, 152, 152)])
        row = [r for r in base_threats(w, self.backend, 0) if r['visible_attackers'] > 0][0]
        self.assertEqual(row['own_nearby'].get('Marine'), 1, '报告要给出附近可用兵力')
        self.assertGreater(row['attacker_dps'], 0)
        self.assertGreaterEqual(row['nearest_attacker_distance'], 0)

    # -- 2. 显式分兵 ---------------------------------------------------------
    def test_incomplete_warp_units_wait_until_ready_before_defending(self):
        task = self.order(base='home', units='all')
        forming = replace(unit(10, 73, 33, 33, ground_dps=10), progress=.12)
        threat = enemy(8, 37, 37)
        self.assertEqual(self.queue.tick(self.snap([forming, threat]), set()), [])
        self.assertFalse(task.program.tags)
        self.assertNotIn(forming.tag, self.queue.defense_tags)
        ready = replace(forming, progress=1)
        out = self.queue.tick(self.snap([ready, threat], sec=5), set())
        self.assertEqual(task.program.tags, {ready.tag})
        self.assertTrue(any(ready.tag in a.action_raw.unit_command.unit_tags for a in out))
    def test_unacknowledged_defense_command_waits_for_a_fresh_order_before_resending(self):
        task=self.order(base='home',units='all')
        w=self.snap([marine(10,32,32),enemy(8,37,37)],sec=1)
        self.assertEqual(len(self.queue.tick(w,set())),1)
        self.assertEqual(self.queue.tick(replace(w,game_loop=w.game_loop+1),set()),[])
        self.assertEqual(task.program.issued,1)
        self.assertEqual(len(self.queue.tick(self.snap([marine(10,32,32),enemy(8,37,37)],sec=2.1),set())),1)
    def test_completed_newborn_waits_for_native_attack_availability(self):
        import test_multirace as fixtures
        f=fixtures.MultiRace();f.setUp();backend=f.backend('zerg',[1,4,251,1694])
        q=TaskQueue(backend,0,lambda _:None)
        q.enqueue([dict(skill='defend_base',args=dict(base='home',units='all',count=1,reason='守基地'))],10)
        queen=replace(f.unit(3,126),ground_dps=10)
        threat=replace(f.unit(4,48),alliance=4,x=37,ground_dps=10)
        w=f.world([f.unit(1,86,is_building=True),queen,threat],race='zerg')
        self.assertTrue(queen.done);self.assertEqual(q.tick(w,set()),[])
        self.assertEqual(q.tasks[0].program.tags,{3})
        self.assertEqual(q.tasks[0].program.summary()['command_waiting'][0]['unit_tag'],3)
        backend.available_abilities=lambda index,tags,**kw:{tag:{23} for tag in tags}
        actions=q.tick(replace(w,game_loop=w.game_loop+23),set())
        self.assertEqual(len(actions),1)
        self.assertEqual(list(actions[0].action_raw.unit_command.unit_tags),[3])
        self.assertEqual(q.tasks[0].program.summary()['command_waiting'],[])
    def test_native_rejection_backs_off_only_the_affected_defender_and_is_bounded(self):
        events=[];self.queue.feedback=lambda kind,**kw:events.append(dict(kind=kind,**kw))
        task=self.order(base='home',units='all',count=2)
        actors=[marine(10,32,32),marine(11,33,33),enemy(8,37,37)]
        at=1.;w=self.snap(actors,sec=at)
        self.assertEqual(len(self.queue.tick(w,set())),2)
        for attempt in range(5):
            rejected=replace(self.snap([actors[0],replace(actors[1],orders=(ATTACK,)),actors[2]],sec=at+.1),errors=((10,ATTACK,3),))
            self.assertEqual(self.queue.tick(rejected,set()),[])
            # A moving enemy must not defeat this actor's retry delay.
            moved=self.snap([actors[0],replace(actors[1],orders=(ATTACK,)),replace(actors[2],x=40+attempt)],sec=at+.2)
            self.assertTrue(all(10 not in a.action_raw.unit_command.unit_tags for a in self.queue.tick(moved,set())))
            at+=min(4,2**attempt)+.2
            actions=self.queue.tick(self.snap([actors[0],replace(actors[1],orders=(ATTACK,)),actors[2]],sec=at),set())
            if attempt<4:self.assertTrue(any(10 in a.action_raw.unit_command.unit_tags for a in actions))
            else:self.assertTrue(all(10 not in a.action_raw.unit_command.unit_tags for a in actions))
        self.assertEqual(len([e for e in events if e['kind']=='defense_command_rejected']),5)
        row=task.program.summary()['command_rejections'][0]
        self.assertEqual(row['unit_tag'],10);self.assertTrue(row['suspended'])
        self.assertEqual(task.program.tags,{10,11})

    def test_model_can_split_kinds_and_counts_between_two_bases(self):
        w = self.snap([marine(10, 32, 32), marine(11, 33, 33), viking(12, 148, 148),
                       enemy(8, 34, 34), enemy(9, 152, 152)])
        first = self.order(base='home', units=['Marine'], count=1)
        second = self.order(base=[150.0, 150.0], units=['Viking'])
        out = self.queue.tick(w, set())
        by_tag = {}
        for a in out:
            uc = a.action_raw.unit_command
            for tag in uc.unit_tags:
                by_tag[tag] = (uc.ability_id, uc.target_world_space_pos.x, uc.target_world_space_pos.y)
        self.assertIn(10, by_tag, '下令的基地必须收到派兵')
        self.assertIn(12, by_tag, '第二处基地的独立令必须同时生效')
        self.assertNotIn(11, by_tag, 'count=1 时不应派出两个枪兵')
        self.assertEqual(first.program.base_tag, self.base.tag)
        self.assertEqual(second.program.base_tag, self.far_base.tag)

    def test_the_two_orders_hold_their_own_units(self):
        w = self.snap([marine(10, 32, 32), viking(12, 148, 148), enemy(8, 34, 34), enemy(9, 152, 152)])
        first = self.order(base='home', units=['Marine'])
        second = self.order(base=[150.0, 150.0], units=['Viking'])
        self.queue.tick(w, set())
        self.assertEqual(first.program.tags, {10})
        self.assertEqual(second.program.tags, {12})

    def test_explicit_mission_units_are_not_raided(self):
        w = self.snap([marine(10, 32, 32), marine(11, 33, 33), enemy(8, 34, 34)])
        self.order(base='home', units=['Marine'])
        out = self.queue.tick(w, reserved={10})
        issued = {t for a in out for t in a.action_raw.unit_command.unit_tags}
        self.assertNotIn(10, issued, '已被其它任务占用的单位不得被防守令抢走')
        self.assertIn(11, issued)

    # -- 3. 取消与释放 -------------------------------------------------------
    def test_cancel_releases_units_so_an_attack_can_follow(self):
        task = self.order(base='home', units=['Marine'])
        w = self.snap([marine(10, 32, 32), marine(11, 33, 33), enemy(8, 34, 34)])
        self.queue.tick(w, set())
        self.assertTrue(self.queue.defense_tags, '防守中应预留单位')
        self.queue.enqueue([{'skill': 'cancel_task', 'args': {'task_id': task.id}},
                            {'skill': 'attack_nearest'}], 4)
        self.assertEqual(task.status, 'cancelled')
        # 取消当拍:先发停止令并归还预留;进攻令在同一个 tick 里仍会被 defense_tags 拦下,
        # 下一拍这批单位才真正可用(这是刻意的:不把"刚解除预留"和"新命令"混在同一拍)。
        self.queue.tick(self.snap([marine(10, 32, 32), marine(11, 33, 33), enemy(8, 34, 34)], sec=5), set())
        self.assertEqual(self.queue.defense_tags, set(), '取消后不得残留预留')
        out = self.queue.tick(self.snap([marine(10, 32, 32), marine(11, 33, 33), enemy(8, 34, 34)], sec=7), set())
        issued = {t for a in out for t in a.action_raw.unit_command.unit_tags}
        self.assertIn(10, issued, '取消后进攻命令必须能重新指挥这些单位')

    def test_no_threat_keeps_the_order_but_reports(self):
        task = self.order(base='home', units=['Marine'])
        w = self.snap([marine(10, 32, 32)])
        out = self.queue.tick(w, set())
        self.assertEqual(out, [], '无敌情时不应乱动单位')
        self.assertEqual(task.status, 'running', '无敌情不自动结束,等模型决定')
        self.assertEqual(task.program.state, 'no_threat')

    # -- 4. 参数校验 ---------------------------------------------------------
    def test_base_and_units_are_required(self):
        for args in ({'reason': 'x'}, {'base': 'home', 'reason': 'x'},
                     {'units': ['Marine'], 'reason': 'x'},
                     {'base': 'nowhere', 'units': 'all', 'reason': 'x'},
                     {'base': 'home', 'units': [], 'reason': 'x'},
                     {'base': 'home', 'units': 'all', 'count': 0, 'reason': 'x'},
                     {'base': 'home', 'units': 'all', 'stance': 'yolo', 'reason': 'x'},
                     {'base': 'home', 'units': 'all', 'reason': ''}):
            with self.assertRaises(PlanValidationError, msg=repr(args)):
                self.queue.enqueue([{'skill': 'defend_base', 'args': args}], 0)

    # -- 5. 与"模型主动派兵"的关系 ------------------------------------------
    def test_explicit_dispatch_cannot_steal_units_held_by_a_defence_order(self):
        """防守令持有的兵,order_units/scout 一律拿不到(硬隔离,不是靠顺序)。"""
        self.order(base='home', units=['Marine'])
        w = self.snap([marine(10, 32, 32), enemy(8, 34, 34)])
        self.queue.tick(w, set())
        self.assertEqual(self.queue.defense_tags, {10})
        # 模型想把这批兵派去别处:必须显式失败,而不是静默换个兵
        self.queue.enqueue([{'skill': 'order_units', 'args': {
            'unit': 'marine', 'count': 'all', 'mode': 'attack_move',
            'target': 'home', 'reason': '调去别处'}}], 2)
        task = [t for t in self.queue.tasks if t.skill == 'order_units'][-1]
        self.queue.tick(self.snap([marine(10, 32, 32), enemy(8, 34, 34)], sec=3), set())
        self.assertEqual(task.status, 'waiting', '拿不到兵时必须可见地等待而不是静默换兵')
        self.assertIn('unavailable', task.reason)

    def test_same_reply_dispatch_wins_but_never_shares_a_unit(self):
        """同一回复里既有派兵令又有防守令:一个单位只能归一处,绝不双指挥。

        实际语义(主循环按 task id 字母序执行,order_units 先分配):显式派兵优先,防守令
        只能拿剩下的空闲单位。这里断言"不重叠"这个不变量,而不是指定谁赢 —— 谁赢属于
        调度实现,重叠才是 bug。
        """
        self.queue.enqueue([{'skill': 'defend_base', 'args': {'base': 'home', 'units': ['Marine'], 'reason': '守家'}},
                            {'skill': 'order_units', 'args': {'unit': 'marine', 'count': 'all',
                             'mode': 'attack_move', 'target': 'home', 'reason': '派去别处'}}], 0)
        w = self.snap([marine(10, 32, 32), enemy(8, 34, 34)])
        out = self.queue.tick(w, set())
        touched = [t for a in out for t in a.action_raw.unit_command.unit_tags]
        self.assertEqual(len(touched), len(set(touched)), '同一拍里不得对同一单位下两次令')
        disp = [t for t in self.queue.tasks if t.skill == 'order_units']
        defence = [t for t in self.queue.tasks if t.skill == 'defend_base'][-1]
        owners = set()
        for task in disp + [defence]:
            if task.active and task.program: owners |= set(getattr(task.program, 'tags', ()))
        self.assertFalse(owners & set(self.queue.defense_tags) - set(defence.program.tags),
                         '防守预留与显式派兵不得重叠')
        self.queue.tick(self.snap([marine(10, 32, 32), enemy(8, 34, 34)], sec=3), set())
        if defence.program.tags:
            self.assertTrue(defence.program.tags <= set(self.queue.defense_tags), '防守令的兵必须仍受其预留保护')

    # -- 6. 同基地覆盖 ------------------------------------------------------
    def test_new_order_for_the_same_base_supersedes_and_inherits_units(self):
        """同基地再下令即覆盖:旧的标记 superseded,新令继承原部队,而不是按距离重挑。"""
        old = self.order(base='home', units='all')
        w = self.snap([marine(10, 32, 32), marine(11, 33, 33), enemy(8, 34, 34)])
        self.queue.tick(w, set())
        held = set(old.program.tags)
        self.assertTrue(held)
        # 同基地重下:改姿态/数量,不该换掉正在防守的单位
        self.queue.enqueue([{'skill': 'defend_base', 'args': {'base': 'home', 'units': 'all',
                            'stance': 'trade', 'reason': '改打交换'}}], 4)
        self.assertEqual(old.status, 'cancelled')
        self.assertIn('superseded', old.reason)
        new = [t for t in self.queue.tasks if t.skill == 'defend_base' and t.active][-1]
        self.assertNotEqual(new.id, old.id, '必须是一条新令')
        self.queue.tick(self.snap([marine(10, 32, 32), marine(11, 33, 33), enemy(8, 34, 34)], sec=5), set())
        self.assertTrue(held <= set(new.program.tags), '新令必须继承原部队: %s -> %s' % (held, new.program.tags))
        self.assertEqual(new.program.stance, 'trade')
        self.assertFalse(set(old.program.tags) & set(self.queue.defense_tags) - set(new.program.tags),
                         '旧令残留不得与新令重叠')

    def test_orders_for_different_bases_still_coexist(self):
        # 坐标指认基地要求贴近该基地(30 格内);各自的兵就站在各自基地旁
        first = self.order(base='home', units='all')
        second = self.order(base=[152.0, 152.0], units='all')
        self.assertNotEqual(first.id, second.id)
        w = self.snap([marine(10, 32, 32), viking(12, 148, 148), enemy(8, 34, 34), enemy(9, 152, 152)])
        self.queue.tick(w, set())
        self.assertTrue(first.program.tags and second.program.tags,
                        '两处防守必须同时在位: %s / %s' % (first.program.tags, second.program.tags))
        self.assertEqual(first.program.tags, {10})
        self.assertEqual(second.program.tags, {12})
        self.assertFalse(first.program.tags & second.program.tags, '两条令不得共用同一个单位')

    def test_a_distant_squad_can_be_ordered_to_any_base(self):
        """全军团都可选:远处(另一个基地旁)的部队也是合法援军,由模型决定派它去哪。"""
        self.order(base='home', units='all', count=2)
        w = self.snap([marine(10, 32, 32), viking(12, 148, 148), enemy(8, 34, 34)])
        self.queue.tick(w, set())
        claim = [t for t in self.queue.tasks if t.skill == 'defend_base'][-1]
        self.assertEqual(claim.program.tags, {10, 12}, 'count=2 时应把远处部队也调来增援')

    def test_count_is_how_the_model_splits_the_army_between_orders(self):
        """分兵靠模型给每条令写明兵种/数量,而不是脚本按距离配额。"""
        first = self.order(base='home', units=['Marine'], count=1)
        second = self.order(base=[152.0, 152.0], units=['Viking'], count=1)
        w = self.snap([marine(10, 34, 34), viking(12, 146, 146),
                       enemy(8, 36, 36), enemy(9, 150, 150)])
        self.queue.tick(w, set())
        self.assertEqual(first.program.tags, {10}, '第一条令按模型指定拿 Marine 1 个')
        self.assertEqual(second.program.tags, {12}, '第二条令按模型指定拿 Viking 1 个')

    def test_units_all_without_count_commits_the_reachable_army_for_a_sole_order(self):
        """唯一一条防守令 + 基地受袭:小军团应当全上(上限 16 只对更大军团生效)。"""
        task = self.order(base='home', units='all')
        w = self.snap([marine(10, 32, 32), marine(11, 33, 33), marine(12, 34, 34),
                       marine(13, 35, 35), marine(14, 36, 36), enemy(8, 37, 37)])
        self.queue.tick(w, set())
        self.assertEqual(task.program.tags, {10, 11, 12, 13, 14}, '基地挨打时唯一一条令要顶住')

    def test_units_all_without_count_is_capped_when_several_orders_share_the_army(self):
        """多条令并存时,units:"all" 不写 count 只按威胁取(约 2 倍、至少 4、至多 16),
        不会把全军团吞掉,好让另一条令也有兵。"""
        first = self.order(base='home', units='all')
        second = self.order(base=[152.0, 152.0], units='all')
        marines = [marine(20 + i, 32 + i, 32 + i) for i in range(12)]
        w = self.snap(marines + [enemy(8, 37, 37), enemy(9, 150, 150)])
        self.queue.tick(w, set())
        self.assertTrue(first.program.tags, '主矿令必须拿到兵')
        self.assertTrue(second.program.tags, '二矿令也必须拿到兵')
        self.assertFalse(first.program.tags & second.program.tags, '两条令不得共用单位')
        self.assertLessEqual(len(first.program.tags), 4, '1 个攻击者时按威胁只取 4 个')

    def test_two_orders_do_not_swap_ownership_every_tick(self):
        """两条令共用一支军队时,不能每拍互相把对方的兵释放掉(实测见过 held 6↔0 抖动)。"""
        first = self.order(base='home', units='all', count=2)
        second = self.order(base=[152.0, 152.0], units='all', count=2)
        marines = [marine(20 + i, 30 + i, 30 + i) for i in range(4)]
        far_marines = [viking(40 + i, 150 + i, 150 + i) for i in range(2)]
        w = self.snap(marines + far_marines + [enemy(8, 37, 37), enemy(9, 152, 152)])
        self.queue.tick(w, set())
        snapshot = (set(first.program.tags), set(second.program.tags))
        self.assertTrue(snapshot[0] and snapshot[1], '两条令都要有兵')
        for sec in (3, 5, 7):
            self.queue.tick(self.snap(marines + far_marines + [enemy(8, 37, 37), enemy(9, 152, 152)], sec=sec), set())
            self.assertEqual((set(first.program.tags), set(second.program.tags)), snapshot,
                             '持续受袭时部队归属不得每拍变化')


if __name__ == '__main__':
    unittest.main()
