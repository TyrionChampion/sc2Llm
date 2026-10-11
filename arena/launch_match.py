"""Configured native duel worker. Invoked only by an explicit launcher Start."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from arena.launch_config import ROOT, OFFICIAL, validate_config
from arena.lifecycle import ShutdownSignals, write_json_atomic


def prepare_skills(run, index, player):
    from arena.persistent_skills import PersistentSkills
    from arena.skill_lab import SkillLab
    from arena.live_skills import restore_live
    race, model = player['race'], player['model']
    root = ROOT / 'skill-library'
    library = PersistentSkills(root if race == 'terran' else root / ('race-' + race))
    source = 'opencode-go/deepseek-v4.1-flash' if model == OFFICIAL else model
    version = library.latest(source)
    catalog = None
    strategy = '使用本族开局手册、运营、单位和科技目录，自行决定战略。'
    receipt = dict(player=index + 1, race=race, source=source, custom_python='none')
    if version:
        lab = SkillLab(run / ('p%d' % (index + 1)) / 'lab', race=race)
        lab.setup()
        catalog, prepared = library.restore(source, lab, lab.root.parent / 'accepted', version['version'])
        strategy += prepared['strategy']
        receipt.update(custom_python='loaded', persistent=prepared['persistent'])
    else:
        notice = 'P%d %s：没有该模型/种族的已验收 Python 库；使用本族宏指令、科技和战斗任务。' % (index + 1, race)
        print('[skill configuration] ' + notice, flush=True)
        receipt['notice'] = notice
        strategy += notice + '只调用当前目录中的技能，不要调用不存在的 Python 技能。'
    restored = []
    if model == OFFICIAL:
        catalog, restored = restore_live(ROOT / 'skill-library/live-official', race,
            run / ('p%d' % (index + 1)) / 'live-restored', catalog)
    receipt['live_restored'] = restored
    return catalog, strategy, receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args(argv)
    run = Path(args.run).resolve()
    config = validate_config(json.loads((run / 'config.json').read_text()))
    report = dict(status='preparing', config=config, realtime=True, opening=False,
                  models=[p['model'] for p in config['players']],
                  races=[p['race'] for p in config['players']], skills=[],
                  display=dict(vsync=True, max_fps=60))
    clients, catalogs, developments, servers = [], [], [], []
    viewer = None
    shutdown = ShutdownSignals()
    shutdown.install()
    def save(): write_json_atomic(run / 'run.json', report)
    save()
    try:
        from arena.deepseek_planner import DeepSeekFlashPlanner
        from arena.live_skills import LiveSkillDevelopment
        for i, player in enumerate(config['players']):
            catalog, strategy, receipt = prepare_skills(run, i, player)
            report['skills'].append(receipt)
            catalogs.append(catalog)
            development = None
            if player['model'] == OFFICIAL and config['live_skills'] and not args.prepare_only:
                development = LiveSkillDevelopment(run / ('p%d' % (i + 1)) / 'live-development', ROOT / 'skill-library/live-official')
                for restored in receipt['live_restored']:
                    if restored['state'] == 'loaded':
                        development.status[restored['name']] = dict(state='ready', verification=restored['verification'], persistent=restored)
                strategy += '缺少局部技能时可以 develop_skill 请求后台编写，ready 后自行派遣。'
            developments.append(development)
            if args.prepare_only:
                continue
            if player['model'] == OFFICIAL:
                client = DeepSeekFlashPlanner(reasoning_effort=config['reasoning'])
                identity = dict(side='P%d' % (i + 1), provider='deepseek', model=client.model,
                    key_source=client.p.key_env, endpoint=client.p.base_url, race=player['race'])
            else:
                from arena.opencode_bridge import OpenCodePlanner, OpenCodeServer
                report['status'] = 'starting_model_service'; save()
                server = OpenCodeServer(run / ('p%d' % (i + 1)) / 'server', [player['model']])
                servers.append(server)
                server.start()  # Local health/catalogue/session requests, no inference probe.
                client = OpenCodePlanner(server, player['model'], i, run / ('p%d' % (i + 1)) / 'battle')
                client.phase = 'battle'
                identity = {**client.identity(), 'race': player['race']}
                strategy += '此启动模式只加载现有技能，未接入 OpenCode 后台源码修复。'
            client.prepared_strategy = strategy
            clients.append((client, identity))
        report['status'] = 'prepared'; save()
        if args.prepare_only:
            print('PREPARED_ONLY: no game, no model service, no model API calls.', flush=True)
            return
        with (run / 'output-window.log').open('w') as stream:
            viewer = subprocess.Popen([str(ROOT / 'run_sc2.sh'), sys.executable,
                str(ROOT / 'llm_output_window.py'), '--run', str(run)], cwd=ROOT,
                stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        time.sleep(.5)
        if viewer.poll() is not None:
            raise RuntimeError('双方中文输出窗口启动失败，请查看 output-window.log')
        report.update(output_window_pid=viewer.pid, identities=[identity for _, identity in clients], status='starting_native_game'); save()
        import native_llm
        native_llm.main(players_override=clients, catalogs_override=catalogs,
            developments_override=developments, opening=False,
            argv=['--seconds', str(config['minutes'] * 60), '--map', 'Acropolis',
                '--game-speed', config['speed'], '--p1-race', config['players'][0]['race'],
                '--p2-race', config['players'][1]['race'], '--output', str(run / 'match.json')]
                + (['--combat-micro'] if config['combat_micro'] else [])
                + (['--tactics'] if config['tactics'] else []))
        report['status'] = 'finished'
    except BaseException as error:
        report.update(status='interrupted' if isinstance(error, KeyboardInterrupt) else 'failed',
                      error=type(error).__name__ + ': ' + str(error))
        print('[launcher error] ' + report['error'], flush=True)
        raise
    finally:
        shutdown.cleaning = True
        for label, operation in ([('development', d.close) for d in developments if d]
                + [('planner', c.close) for c, _ in clients if hasattr(c, 'close')]
                + [('opencode', s.close) for s in servers]):
            try: operation()
            except BaseException as error:
                report.setdefault('cleanup_errors', []).append(dict(stage=label, error=str(error)))
        if report['status'] in ('interrupted', 'failed') and viewer and viewer.poll() is None:
            try:
                viewer.terminate(); viewer.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired) as error:
                report.setdefault('cleanup_errors', []).append(dict(stage='viewer', error=str(error)))
        report['usage'] = [dict(side=i['side'], usage=getattr(c, 'usage', None), calls=getattr(c, 'calls', 0)) for c, i in clients]
        report['live_skill_development'] = [d.public() if d else {'enabled': False} for d in developments]
        report['shutdown_signals'] = shutdown.state
        save(); shutdown.restore()


if __name__ == '__main__':
    main()
