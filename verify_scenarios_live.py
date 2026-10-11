"""原生场景运行器:在同一批战场场景里跑真模型,并按 SCENARIOS.md 的口径自动验收。

用法:
  python verify_scenarios_live.py --scenario single_base_under_attack
  python verify_scenarios_live.py --scenario two_front_under_attack

每个场景先做**前提校验**(不成立即判无效、不产生结论),然后让真模型决策若干轮,
再把"模型是否做出合理反应"按可判定的口径给出结论。判定口径只问因果链是否完整,
不评价强度。
"""
import json
import time
from pathlib import Path

from absl import app, flags
from s2clientprotocol import debug_pb2 as debug

from arena.commander import Commander
from arena.defense_orders import base_threats
from arena.deepseek_planner import DeepSeekFlashPlanner
from arena.native_engine import NativeArena

FLAGS = flags.FLAGS
flags.DEFINE_string('scenario', 'single_base_under_attack', '场景名')
MARINE, ENEMY = 48, 4
TOWNHALL_BY_RACE = {'terran': 18, 'zerg': 86, 'protoss': 84}
TOWNHALL_IDS = (18, 130, 132, 86, 100, 101, 84, 85)

SCENARIOS = {
    # ---- 受袭类:模型应当做出防守决策(或明确说明放弃) ----
    'single_base_under_attack': dict(
        kind='defence', bases=1, attackers=[3], detached=6, enemy_verify=3,
        accept='防御令在 3 轮内出现,或 reason 明确说明放弃',
        reject='连续 3 轮只回经济类任务'),
    'two_front_under_attack': dict(
        kind='defence', bases=2, attackers=[3, 1], detached=6, enemy_verify=4,
        accept='至少一条防御令在 3 轮内出现;若只守一处则 reason 说明取舍',
        reject='连续 3 轮只回经济类任务'),
    'three_front_under_attack': dict(
        kind='defence', bases=3, attackers=[3, 2, 1], detached=9, enemy_verify=6,
        accept='出现取舍(守≥1 处或明确放弃),部队不重叠',
        reject='连续 3 轮只回经济类任务'),
    'insufficient_force': dict(
        kind='defence', bases=1, attackers=[5], detached=1, enemy_verify=5,
        accept='下单防守(拖延)或 reason 说明兵力不足/放弃',
        reject='连续 3 轮无任何防守相关决策且无说明'),
}


def spawn(arena, kind, owner, pos, count=1):
    command = debug.DebugCommand()
    unit = command.create_unit
    unit.unit_type, unit.owner, unit.quantity = kind, owner, count
    unit.pos.x, unit.pos.y = pos
    arena.controllers[0].debug([command])


def main(argv):
    spec = SCENARIOS[FLAGS.scenario]
    report = dict(fixture=True, scenario=FLAGS.scenario, spec=spec, model='deepseek-flash')
    destination = Path('logs/scenario_%s.json' % FLAGS.scenario)
    arena = commander = None
    try:
        with NativeArena(names=('Scenario',), races=['terran'], render=False,
                         difficulty='easy', game_speed='faster') as arena:
            report['version'] = arena.version
            worlds = arena.step([[]])
            home = tuple(arena.homes[0])
            enemy_start = tuple(arena.enemy_start(0))
            size = tuple(arena.size)
            race = worlds[0].race

            def townhalls(w):
                return [u for u in w.my_units if u.type_id in TOWNHALL_IDS]

            blocked = [home]
            bases = [home]
            second = None

            def dist2(a, b):
                return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2

            # 额外基地:逐个候选试放,以"真的出现"为准
            order = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1))
            for index in range(spec['bases'] - 1):
                before = len(townhalls(worlds[0]))
                placed = None
                for radius in (36, 30, 42, 26, 50):
                    for ddx, ddy in order:
                        pos = (home[0] + ddx * radius, home[1] + ddy * radius * (index + 1) * 0.5)
                        if not (6 <= pos[0] <= size[0] - 6 and 6 <= pos[1] <= size[1] - 6):
                            continue
                        if any(dist2(pos, b) < 900 for b in bases):
                            continue
                        spawn(arena, TOWNHALL_BY_RACE[race], 1, pos)
                        for _ in range(6):
                            worlds = arena.step([[]])
                        if len(townhalls(worlds[0])) > before:
                            placed = (float(pos[0]), float(pos[1]))
                            break
                    if placed:
                        break
                if not placed:
                    report['inconclusive'] = 'could not place base #%d' % (index + 2)
                    raise SystemExit(0)
                bases.append(placed)
                blocked.append(placed)
            report['layout'] = dict(bases=[list(b) for b in bases], enemy_start=list(enemy_start))

            def enemy_count(w):
                return sum(1 for u in w.enemy_units if u.type_id == MARINE)

            def own_count(w):
                return sum(1 for u in w.my_units if u.type_id == MARINE)

            def spawn_verified(kind, owner, center, count, want_enemy):
                nonlocal worlds
                for offset in (8, 10, 12, 14, 16, 20):
                    for ddx, ddy in order:
                        pos = (center[0] + ddx * offset, center[1] + ddy * offset)
                        if not (4 <= pos[0] <= size[0] - 4 and 4 <= pos[1] <= size[1] - 4):
                            continue
                        if any(dist2(pos, b) < 49 for b in blocked):
                            continue
                        before = enemy_count(worlds[0]) if want_enemy else own_count(worlds[0])
                        spawn(arena, kind, owner, pos, count=count)
                        for _ in range(4):
                            worlds = arena.step([[]])
                        after = enemy_count(worlds[0]) if want_enemy else own_count(worlds[0])
                        if after >= before + count:
                            return pos
                return None

            # 每处基地的进攻组
            attack_positions = []
            for base, count in zip(bases, spec['attackers']):
                placed = spawn_verified(MARINE, ENEMY, base, count, True)
                if placed is None:
                    report['inconclusive'] = 'could not spawn attackers for base %s' % (base,)
                    raise SystemExit(0)
                attack_positions.append(placed)

            # 部队集中在远离所有基地的地方 —— 派谁去哪必须由模型决定
            # 集结区要求:离每处基地 60~110 格 —— 远到不会算进 defense_report,
            # 但也不至于走到一半基地就没了(实测 190 格时部队还在路上基地已重伤)。
            candidates = sorted(((abs(min(dist2((x + .5, y + .5), b) for b in bases) ** .5 - 85), (x + .5, y + .5))
                                 for x in range(6, size[0] - 6, 4) for y in range(6, size[1] - 6, 4)))
            marshal = candidates[0][1]
            for _, cand in candidates:
                if all(60 ** 2 < dist2(cand, b) < 110 ** 2 for b in bases):
                    marshal = cand
                    break
            blocked.append(marshal)
            if spawn_verified(MARINE, 1, marshal, spec['detached'], False) is None:
                report['inconclusive'] = 'could not spawn the detached squad'
                raise SystemExit(0)
            for _ in range(6):
                worlds = arena.step([[]])

            report['layout']['marshal'] = list(marshal)
            report['layout']['marshal_distances'] = [round(dist2(marshal, b) ** .5, 1) for b in bases]
            report['preconditions'] = dict(
                bases=len(townhalls(worlds[0])), own_marines=own_count(worlds[0]),
                enemy_marines=enemy_count(worlds[0]))
            rows = base_threats(worlds[0], arena, 0)
            report['report_before_model'] = rows
            attacked = sorted(r['visible_attackers'] for r in rows if r['visible_attackers'] > 0)
            report['preconditions']['attacked_counts'] = attacked
            report['preconditions']['own_units_near_bases'] = sum(r['own_nearby_total'] for r in rows)
            if report['preconditions']['own_marines'] != spec['detached']:
                report['inconclusive'] = 'own squad wrong: %s' % report['preconditions']
                raise SystemExit(0)
            if report['preconditions']['enemy_marines'] != spec['enemy_verify']:
                report['inconclusive'] = 'attackers wrong: %s' % report['preconditions']
                raise SystemExit(0)
            if attacked != sorted(spec['attackers']):
                report['inconclusive'] = 'expected attacked %s saw %s' % (sorted(spec['attackers']), attacked)
                raise SystemExit(0)
            if report['preconditions']['own_units_near_bases'] != 0:
                report['inconclusive'] = 'troops are not detached: %s' % report['preconditions']
                raise SystemExit(0)

            commander = Commander(arena, 0, DeepSeekFlashPlanner(reasoning_effort='low'),
                                  opening=False, tactics_enabled=False,
                                  feedback_dir='logs/scenario-%s-feedback' % FLAGS.scenario)
            deadline = time.monotonic() + 300
            last_loop = -1
            while time.monotonic() < deadline:
                if getattr(arena, 'ended', False):
                    # 基地被打掉/投降都会让比赛提前结束:这是合法结局,要如实记录而不是崩掉
                    report['match_ended_early'] = True
                    break
                try:
                    worlds = arena.step([commander.tick(worlds[0])])
                except RuntimeError as error:
                    report['match_ended_early'] = str(error)
                    break
                commander.ingest_feedback(worlds[0])
                if commander.client_fatal:
                    report['client_fatal'] = commander.client_fatal
                    break
                if worlds[0].game_loop == last_loop:
                    break
                last_loop = worlds[0].game_loop
                if worlds[0].seconds > 260:
                    break

            # 权威记录来自反馈审计:每轮受袭情况 + 模型回复
            import glob
            import os as _os
            import re
            pattern = 'logs/scenario-%s-feedback/*.jsonl' % FLAGS.scenario
            audit = sorted(glob.glob(pattern), key=_os.path.getmtime)[-1] if glob.glob(pattern) else None
            exchange, pending = [], None
            for line in (open(audit, encoding='utf-8', errors='replace') if audit else []):
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get('record') == 'request_queued' and isinstance(entry.get('prompt'), dict):
                    pending = entry['prompt']
                elif entry.get('record') == 'request_finished' and entry.get('response') and pending is not None:
                    response = entry['response']
                    exchange.append(dict(
                        attacked=[(r['label'], r['visible_attackers'], r['urgency'])
                                  for r in pending.get('defense_report', []) if r['visible_attackers'] > 0],
                        own=(pending.get('world') or {}).get('own'),
                        skills=sorted(set(re.findall(r'"skill":"([a-z_]+)"', response))),
                        reason=(re.search(r'"reason"\s*:\s*"([^"]{0,200})"', response) or [None, ''])[1]))
                    pending = None
            # 权威记录来自反馈审计:每轮受袭情况 + 模型回复
            report['audit'] = audit
            report['exchange'] = exchange
            report['final_report'] = base_threats(worlds[0], arena, 0)
            report['final_world'] = worlds[0].summary()
            report['outcome_known'] = getattr(arena, 'result', None) and arena.result()
            report['usage'] = getattr(commander.client, 'usage', None)
            report['action_errors'] = arena.action_errors

            # ---- 验收:只问因果链是否完整 ----
            economy_only = {'train_worker', 'build_supply', 'build_structure', 'gather_gas',
                            'research', 'expand', 'gather', 'train_unit', 'morph_unit'}
            first_defence, streak, worst_streak = None, 0, 0
            for index, entry in enumerate(exchange, 1):
                if 'defend_base' in entry['skills']:
                    if first_defence is None:
                        first_defence = index
                    streak = 0
                elif set(entry['skills']) and set(entry['skills']) <= economy_only:
                    streak += 1
                    worst_streak = max(worst_streak, streak)
                else:
                    streak = 0
            report['acceptance'] = dict(
                first_defence_turn=first_defence,
                worst_economy_only_streak=worst_streak,
                requested=spec['accept'],
                verdict=('pass' if (first_defence is not None and first_defence <= 3)
                         or worst_streak < 3 else 'fail'))
    except SystemExit:
        pass
    except BaseException as error:
        import traceback
        report['error'] = type(error).__name__ + ': ' + str(error)
        report['traceback'] = traceback.format_exc()
    finally:
        if commander:
            commander.stop()
        report['cleanup_complete'] = arena is not None and arena.closed
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print('SCENARIO_RESULT', json.dumps(
            {k: v for k, v in report.items() if k not in ('traceback', 'exchange')},
            ensure_ascii=False)[:1600], flush=True)


if __name__ == '__main__':
    app.run(main)
