"""Zero-model native integration proof on ONE machine, not two physical-PC validation.

Starts two independent clients, two SC2 processes and a real HTTP coordinator.
Uses explicit local fixture planners; never makes an external inference request.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time

from absl import flags
from arena.distributed_room import DistributedRoomManager
from arena.game_speed import map_for_speed
from arena.lan_client import LocalClient
from arena_room import RoomServer
from arena.lifecycle import write_json_atomic


class FixturePlanner:
    compact_battle = True
    soft_timeout = 15
    timeout = 60
    def __init__(self):
        self.calls = self.attempts = 0
        self.payloads = []
    def chat(self, system, user):
        self.calls += 1
        self.attempts += 1
        self.payloads.append(json.loads(user))
        return json.dumps(dict(tasks=[dict(skill='train_worker', count=1)] if self.calls == 1 else [],
                               reason='本地夹具：真实训练一名工人；没有外部模型调用。'))
    def close(self): pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', choices=('linux-headless', 'auto'), default='linux-headless')
    args = parser.parse_args(argv)
    if not flags.FLAGS.is_parsed(): flags.FLAGS(['verify_lan_clients_live'])
    logs = Path(__file__).resolve().parent / 'logs'
    root = Path(tempfile.mkdtemp(prefix='lan-full-native-', dir=logs))
    rooms = DistributedRoomManager(root / 'coordinator',
        map_provider=lambda speed:map_for_speed('Acropolis', speed).read_bytes(), port_base=17600)
    server = RoomServer(('127.0.0.1', 0), rooms, allowed_hosts={'127.0.0.1'})
    http_thread = threading.Thread(target=server.serve_forever, daemon=True)
    http_thread.start()
    settings = dict(speed='slower', minutes=3, combat_micro=False)
    host = rooms.create('P1 native fixture', settings)
    guest = rooms.join(host['code'], 'P2 native fixture')
    players = [host, guest]
    planners, clients, threads = [], [], []
    report = dict(passed=False, model_calls=0, fixture_planners=True, realtime=True,
        scope='one-machine two native processes + HTTP coordinator + two LocalClient runners; not physical Windows/Linux LAN proof')
    try:
        for i, player in enumerate(players):
            ticket = rooms.ticket(**player)['pairing_code']
            identity = rooms.pair(host['code'], ticket, str(i + 1) * 32, '127.0.0.1', 'Linux',
                [dict(id='fixture/local', name='本地验证夹具，无模型', available=True, notice='no inference')])
            planner = FixturePlanner()
            planners.append(planner)
            client = LocalClient('http://127.0.0.1:%d' % server.server_port, host['code'], identity['agent_token'],
                root / ('p%d' % (i + 1)), engine=args.engine, planner_factory=lambda model, run, p=planner:(p, None))
            clients.append(client)
            thread = threading.Thread(target=client.run, daemon=True)
            threads.append(thread)
            thread.start()
        for w in players:
            v = rooms.view(**w)
            rooms.ready(**w, revision=v['revision'], ready=True)
        rooms.start(**host)
        deadline = time.monotonic() + 180
        messages = None
        while time.monotonic() < deadline:
            v = rooms.view(**host)
            if v['battle']['state'] == 'failed': raise RuntimeError(v['battle']['error'])
            if v['battle']['state'] == 'playing' and messages is None:
                messages = [rooms.chat(**w, text='P%d_ONLY_NATIVE_CHAT_PROOF' % (i + 1)) for i, w in enumerate(players)]
            views = [rooms.view(**w) for w in players]
            workers = [((v['battle'] or {}).get('world') or [{}])[0].get('own', {}).get('SCV', 0) for v in views]
            received = messages and all(any(m['id'] == messages[i]['id'] and m['state'] == 'received'
                for m in views[i]['battle']['chat']['messages']) for i in (0, 1))
            if all(n >= 13 for n in workers) and received: break
            time.sleep(.5)
        else: raise RuntimeError('原生客户端端到端验收超时')
        for i in (0, 1):
            assert any('P%d_ONLY_NATIVE_CHAT_PROOF' % (i + 1) in json.dumps(p) for p in planners[i].payloads)
            assert not any('P%d_ONLY_NATIVE_CHAT_PROOF' % (2 - i) in json.dumps(p) for p in planners[i].payloads)
            assert clients[i].commander.operator_advice.player == i + 1
        assert all(len(c.backend.controllers) == 1 for c in clients)
        assert all(not c.backend.initial[0].enemy_units for c in clients)
        assert [c.backend.action_errors for c in clients] == [0, 0]
        rooms.stop(**host)
        for thread in threads: thread.join(timeout=20)
        assert not any(t.is_alive() for t in threads)
        replays = []
        for client in clients:
            replay = client.run_dir / 'match.SC2Replay'
            assert replay.stat().st_size > 0
            replays.append(dict(path=str(replay), bytes=replay.stat().st_size, sha256=hashlib.sha256(replay.read_bytes()).hexdigest()))
        report.update(passed=True, room_phase=rooms.rooms[host['code']]['phase'], workers=workers,
            player_ids=[c.backend.player_id for c in clients], chat_ids=[m['id'] for m in messages],
            chat_received=True, chat_isolated=True, action_errors=[0,0], replays=replays,
            fixture_calls=[p.calls for p in planners])
    finally:
        for client in clients:
            try: client.close()
            except Exception as error: report.setdefault('cleanup_errors', []).append(str(error))
        for thread in threads: thread.join(timeout=5)
        server.shutdown()
        server.server_close()
        http_thread.join(timeout=2)
        write_json_atomic(root / 'result.json', report)
        print('LAN_NATIVE_INTEGRATION', str(root), json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__': main()
