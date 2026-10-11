"""原生观测:两处基地同时被不同兵力进攻,部队却在远处时,主指挥怎么决定分兵。

场景形态(用户要求):己方部队**集中在远处**,不摆在基地旁;两处基地各被敌军压境
(主矿 3、二矿 1)。观测模型是否会**分兵两处**(或只守一处=放弃另一处,或不下令)。

脚本内先做前提校验(不成立就判本次无效,不做任何结论):
1. 第二基地按**玩家实际种族**生成,并确认它真的出现(地图会静默拒绝不可建造点);
2. 四个生成组都要能在观测里数到;
3. defense_report 必须分别统计到两处受袭,且两处附近都没有己方部队(部队确实在远处)。

机械层(下令→分兵执行)由 test_two_front_defense.py 覆盖;本脚本只观察模型的选择。
"""
import json
import time
from pathlib import Path

from absl import app
from s2clientprotocol import debug_pb2 as debug

from arena.commander import Commander
from arena.defense_orders import base_threats
from arena.deepseek_planner import DeepSeekFlashPlanner
from arena.native_engine import NativeArena

MARINE, ENEMY = 48, 4
HOME_ATTACKERS, SECOND_ATTACKERS = 3, 1
DETACHED_MARINES = 6
TOWNHALL_BY_RACE = {'terran': 18, 'zerg': 86, 'protoss': 84}
TOWNHALL_IDS = (18, 130, 132, 86, 100, 101, 84, 85)


def spawn(arena, kind, owner, pos, count=1):
    command = debug.DebugCommand()
    unit = command.create_unit
    unit.unit_type, unit.owner, unit.quantity = kind, owner, count
    unit.pos.x, unit.pos.y = pos
    arena.controllers[0].debug([command])


def main(argv):
    report = dict(fixture=True, model='deepseek-flash', scenario='two_front_defense',
                  attackers=dict(home=HOME_ATTACKERS, second=SECOND_ATTACKERS),
                  detached_marines=DETACHED_MARINES,
                  policy='no automatic defence; only the model may order defend_base')
    destination = Path('logs/two_front_defense_native.json')
    commander, arena = None, None
    try:
        with NativeArena(names=('TwoFront',), races=['terran'], render=False,
                         difficulty='easy', game_speed='faster') as arena:
            report['version'] = arena.version
            report['native_directory'] = str(arena.directory)
            worlds = arena.step([[]])
            home = tuple(arena.homes[0])
            enemy_start = tuple(arena.enemy_start(0))
            size = tuple(arena.size)
            race = worlds[0].race
            report['player_race'] = race
            report['map_size'] = list(size)

            def townhalls(world):
                return [u for u in world.my_units if u.type_id in TOWNHALL_IDS]

            # 1) 第二基地:逐个候选点试放,以"第二座城镇中心真的出现"为准(不可建造点会被静默拒绝)
            before = len(townhalls(worlds[0]))
            second = None
            order = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1))
            for radius in (36, 30, 42, 26):
                for ddx, ddy in order:
                    pos = (home[0] + ddx * radius, home[1] + ddy * radius)
                    if not (6 <= pos[0] <= size[0] - 6 and 6 <= pos[1] <= size[1] - 6):
                        continue
                    spawn(arena, TOWNHALL_BY_RACE[race], 1, pos)
                    for _ in range(6):
                        worlds = arena.step([[]])
                    if len(townhalls(worlds[0])) > before:
                        second = (float(pos[0]), float(pos[1]))
                        break
                if second:
                    break
            if not second:
                report['inconclusive'] = 'no buildable second base found'
                raise SystemExit(0)

            # 2) 部队集中在远离两处基地的位置(>40 格),这样"派谁去哪"才是模型的决定
            def dist2(a, b):
                return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2

            blocked = [home, second]
            candidates = sorted(((min(dist2((x + .5, y + .5), home), dist2((x + .5, y + .5), second)),
                                  (x + .5, y + .5))
                                 for x in range(6, size[0] - 6, 4) for y in range(6, size[1] - 6, 4)),
                                reverse=True)
            marshal = candidates[0][1]
            for _, cand in candidates:
                if all(dist2(cand, b) > 1600 for b in blocked):
                    marshal = cand
                    break
            blocked.append(marshal)

            def enemy_count(world):
                return sum(1 for u in world.enemy_units if u.type_id == MARINE)

            def spawn_verified(kind, owner, center, count, is_enemy):
                """试着生成并**数一遍**确认落地;地图/重叠会静默拒绝,所以以观测为准。"""
                nonlocal worlds
                for offset in (8, 10, 12, 14, 16):
                    for ddx, ddy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
                        pos = (center[0] + ddx * offset, center[1] + ddy * offset)
                        if not (4 <= pos[0] <= size[0] - 4 and 4 <= pos[1] <= size[1] - 4):
                            continue
                        if any(dist2(pos, b) < 64 for b in blocked):
                            continue
                        before_n = enemy_count(worlds[0]) if is_enemy else sum(
                            1 for u in worlds[0].my_units if u.type_id == kind)
                        spawn(arena, kind, owner, pos, count=count)
                        for _ in range(4):
                            worlds = arena.step([[]])
                        after_n = enemy_count(worlds[0]) if is_enemy else sum(
                            1 for u in worlds[0].my_units if u.type_id == kind)
                        if after_n >= before_n + count:
                            return pos
                return None

            home_attack = spawn_verified(MARINE, ENEMY, home, HOME_ATTACKERS, True)
            second_attack = spawn_verified(MARINE, ENEMY, second, SECOND_ATTACKERS, True)
            marshal = spawn_verified(MARINE, 1, marshal, DETACHED_MARINES, False) or marshal
            for _ in range(6):
                worlds = arena.step([[]])
            if home_attack is None or second_attack is None:
                report['inconclusive'] = ('an attack group could not be spawned: home=%s second=%s'
                                          % (home_attack, second_attack))
                raise SystemExit(0)

            report['layout'] = dict(home=list(home), second=list(second), enemy_start=list(enemy_start),
                                    marshal=list(marshal), home_attack=list(home_attack),
                                    second_attack=list(second_attack),
                                    marshal_to_home=round(dist2(marshal, home) ** .5, 1),
                                    marshal_to_second=round(dist2(marshal, second) ** .5, 1))
            rows = base_threats(worlds[0], arena, 0)
            report['report_before_model'] = rows
            attacked = sorted(r['visible_attackers'] for r in rows if r['visible_attackers'] > 0)
            near_bases = sum(r['own_nearby_total'] for r in rows)
            report['preconditions'] = dict(
                own_marines=sum(1 for u in worlds[0].my_units if u.type_id == MARINE),
                enemy_marines=sum(1 for u in worlds[0].enemy_units if u.type_id == MARINE),
                townhalls=len(townhalls(worlds[0])), attacked_counts=attacked,
                own_units_near_bases=near_bases)
            if report['preconditions']['own_marines'] != DETACHED_MARINES:
                report['inconclusive'] = 'spawn verification failed: %s' % report['preconditions']
                raise SystemExit(0)
            if report['preconditions']['enemy_marines'] != HOME_ATTACKERS + SECOND_ATTACKERS:
                report['inconclusive'] = 'enemy spawn verification failed: %s' % report['preconditions']
                raise SystemExit(0)
            if attacked != sorted([HOME_ATTACKERS, SECOND_ATTACKERS]):
                report['inconclusive'] = 'expected two attacked bases %s, saw %s' % (
                    sorted([HOME_ATTACKERS, SECOND_ATTACKERS]), attacked)
                raise SystemExit(0)
            if near_bases != 0:
                report['inconclusive'] = 'troops are not detached from the bases: %s' % report['preconditions']
                raise SystemExit(0)

            commander = Commander(arena, 0, DeepSeekFlashPlanner(reasoning_effort='low'),
                                  opening=False, tactics_enabled=False,
                                  feedback_dir='logs/two-front-native-feedback')
            decisions, plans = [], []
            deadline = time.monotonic() + 360
            last_loop = -1
            while time.monotonic() < deadline:
                worlds = arena.step([commander.tick(worlds[0])])
                commander.ingest_feedback(worlds[0])
                planned = getattr(commander, 'last_plan', None)
                if isinstance(planned, dict) and planned not in plans:
                    plans.append(planned)
                for task in commander.tasks.tasks:
                    if task.skill != 'defend_base':
                        continue
                    program = task.program
                    row = dict(task_id=task.id, status=task.status, reason=task.reason[:90],
                               base=task.args.get('base'), label=getattr(program, 'base_label', None),
                               units=task.args.get('units'), stance=task.args.get('stance'),
                               held=len(getattr(program, 'tags', ()) or ()),
                               state=getattr(program, 'state', None))
                    if not decisions or decisions[-1] != row:
                        decisions.append(row)
                report['decisions_seen'] = decisions
                report['report_now'] = base_threats(worlds[0], arena, 0)
                if commander.client_fatal:
                    report['client_fatal'] = commander.client_fatal
                    break
                if worlds[0].game_loop == last_loop:
                    break
                last_loop = worlds[0].game_loop
                if worlds[0].seconds > 300:
                    break

            # 权威记录来自反馈审计:任务表会丢历史,commander 也没有 last_plan
            import glob
            import os as _os
            import re
            audit = sorted(glob.glob('logs/two-front-native-feedback/*.jsonl'),
                           key=_os.path.getmtime)[-1] if glob.glob('logs/two-front-native-feedback/*.jsonl') else None
            exchange, order_count = [], 0
            if audit:
                pending = None
                for line in open(audit, encoding='utf-8', errors='replace'):
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if entry.get('record') == 'request_queued' and isinstance(entry.get('prompt'), dict):
                        pending = entry['prompt']
                    elif entry.get('record') == 'request_finished' and entry.get('response') and pending is not None:
                        response = entry['response']
                        orders = re.findall(r'\{\s*"skill"\s*:\s*"defend_base".*?\}\s*\}', response)
                        exchange.append(dict(
                            attacked=[(r['label'], r['visible_attackers'], r['own_nearby_total'])
                                      for r in pending.get('defense_report', []) if r['visible_attackers'] > 0],
                            own=(pending.get('world') or {}).get('own'),
                            skills=sorted(set(re.findall(r'"skill":"([a-z_]+)"', response))),
                            defend_orders=[json.loads(o) for o in orders] if orders else []))
                        order_count += len(orders)
                        pending = None
            report['audit'] = audit
            report['exchange'] = exchange
            report['model_defend_order_count'] = order_count
            report['bases_ordered'] = sorted({json.dumps(o['args'].get('base'), ensure_ascii=False)
                                              for e in exchange for o in e['defend_orders']})
            report['decisions_by_kind'] = dict(
                live=[d for d in decisions if d['status'] in ('running', 'waiting')],
                terminal=[d for d in decisions if d['status'] not in ('running', 'waiting')])
            report['final_report'] = base_threats(worlds[0], arena, 0)
            report['main_requests'] = commander.decisions
            report['usage'] = getattr(commander.client, 'usage', None)
            report['action_errors'] = arena.action_errors
            report['final_world'] = worlds[0].summary()
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
        print('TWO_FRONT_NATIVE', json.dumps(
            {k: v for k, v in report.items() if k not in ('traceback', 'final_world', 'exchange')},
            ensure_ascii=False)[:2000], flush=True)


if __name__ == '__main__':
    app.run(main)
