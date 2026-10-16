"""Local native-game agent. The coordinator sees only this player's public feed."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import threading
import time

import requests

from arena.distributed_room import PROTOCOL_VERSION
from arena.client_requests import AccountedPlanner
from arena.lan_room import digest, prompt_rules
from arena.launch_config import OFFICIAL, safe_text
from arena.lan_native import LocalSC2, LocalPlayerArena
from arena.lifecycle import write_json_atomic
from arena.operator_advice import public_decisions, submit_advice


def local_planner(model, run):
    if model == OFFICIAL:
        from arena.deepseek_planner import DeepSeekFlashPlanner
        return DeepSeekFlashPlanner(reasoning_effort='high'), None
    if model in ('opencode-go/deepseek-v4.1-flash', 'opencode-go/deepseek-v4-pro') and os.environ.get('open_api_key', '').strip():
        from arena.go_planner import GoPlanner
        client = GoPlanner(model, run / 'model')
        client.timeout = 60
        return client, None
    from arena.opencode_bridge import OpenCodeServer, OpenCodePlanner
    server = OpenCodeServer(run / 'opencode-server', [model])
    try:
        server.start()
        client = OpenCodePlanner(server, model, 0, run / 'model')
        client.phase = 'battle'
        return client, server
    except BaseException:
        server.close()
        raise


class LocalClient:
    def __init__(self, server, code, agent_token, root, engine='auto', native_factory=LocalSC2,
                 planner_factory=local_planner, transport=requests.request):
        self.server, self.code, self.agent_token = server.rstrip('/'), code, agent_token
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.engine, self.native_factory, self.planner_factory = engine, native_factory, planner_factory
        self.transport = transport
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='local-sc2')
        self.future = None
        self.native = self.planner = self.commander = self.model_server = None
        self.manifest = self.own = self.run_dir = None
        self.delivered = set()
        self.state = dict(seq=0, session=None, phase='idle', public={})
        self.last_contact = time.monotonic()
        self.closed = False
        self.stop_requested_at = None
        self.seat = 1

    def request(self, method, path, body=None, binary=False):
        response = self.transport(method, self.server + path + '?code=' + self.code,
            headers={'Authorization':'Bearer ' + self.agent_token}, json=body, timeout=10)
        if response.status_code >= 400:
            raise RuntimeError('房间接口 HTTP %d：%s' % (response.status_code, response.json().get('error', '请求失败')))
        return response.content if binary else response.json()

    def update(self, **values):
        with self.lock: self.state.update(values)

    def perform(self, action):
        try:
            if action == 'prepare':
                self.run_dir = self.root / self.manifest['session']
                self.run_dir.mkdir(exist_ok=False)
                write_json_atomic(self.run_dir / 'confirmation.json', dict(manifest=self.manifest, own=self.own))
                if self.manifest['protocol'] != PROTOCOL_VERSION or digest(prompt_rules(self.manifest['settings']['combat_micro'])) != self.manifest['rules_sha256']:
                    raise ValueError('本机工具契约与网页确认版本不同，请更新客户端')
                self.native = self.native_factory(self.run_dir, engine=self.engine)
                ping = self.native.start()
                data = self.request('GET', '/api/client/map', binary=True)
                self.native.save_map(data, self.manifest)
                self.update(phase='engine_ready', engine=dict(ping, map_sha256=self.manifest['map_sha256'],
                    rules_sha256=self.manifest['rules_sha256']))
            elif action == 'create':
                self.native.create()
                self.update(phase='created')
            elif action == 'join':
                player_id = self.native.join(self.manifest, self.own['race'], self.own['name'])
                self.update(phase='joined', player_id=player_id)
            elif action == 'play':
                self.play()
        except Exception as error:
            self.update(phase='stopped' if self.stop_event.is_set() else 'failed',
                        error=safe_text(type(error).__name__ + ': ' + str(error)))
            self.stop_event.set()
            self.cleanup_game()

    def play(self):
        from arena.commander import Commander
        self.planner, self.model_server = self.planner_factory(self.own['model'], self.run_dir)
        self.planner = AccountedPlanner(self.planner, self.run_dir / 'requests.json')
        self.planner.prepared_strategy = self.own['prompt']
        backend = LocalPlayerArena(self.native.controller, self.own['race'],
                                   self.manifest['settings']['speed'], self.state['player_id'])
        self.backend = backend
        self.commander = Commander(backend, 0, self.planner, opening=False,
            feedback_dir=self.run_dir / 'feedback', combat_micro=self.manifest['settings']['combat_micro'], display_player=self.seat)
        world = backend.initial[0]
        self.update(phase='running')
        last_save = 0
        try:
            while not self.stop_event.is_set() and not backend.ended:
                batch = self.commander.tick(world)
                world = backend.step([batch])[0]
                self.commander.ingest_feedback(world)
                if time.monotonic() - last_save >= 1:
                    self.publish(world)
                    write_json_atomic(self.run_dir / 'local-status.json', self.report())
                    last_save = time.monotonic()
            public = copy.deepcopy(self.state['public'])
            public['outcome'] = backend.result() if backend.ended else 'Unknown'
            self.update(phase='finished' if backend.ended else 'stopped', public=public)
        finally:
            self.commander.stop()
            self.publish(world)
            try:
                backend.save_replay(self.run_dir / 'match.SC2Replay')
            except Exception as error:
                write_json_atomic(self.run_dir / 'replay-error.json', dict(error=safe_text(str(error))))
            write_json_atomic(self.run_dir / 'result.json', dict(player_id=self.state['player_id'],
                outcome=backend.result() if backend.ended else 'Unknown',
                result_source=(backend.surrender_report or {}).get('result_source',
                    'engine_player_result' if backend.ended else 'none'),
                requests=copy.deepcopy(self.state['public'].get('requests', {}))))
            self.cleanup_game()

    def publish(self, world):
        stats = self.commander.stats()
        public = dict(world=world.summary(), commander={k:stats[k] for k in
            ('decisions', 'errors', 'thinking', 'engine_errors', 'model_decisions_available')},
            decisions=public_decisions(self.commander.feedback.path, 10),
            messages=self.commander.operator_advice.public()['messages'],
            chat_accepting=self.commander.operator_advice.accepting,
            chat_reason=self.commander.operator_advice.reason,
            requests=self.planner.snapshot())
        previous_outcome = self.state['public'].get('outcome')
        if previous_outcome is not None: public['outcome'] = previous_outcome
        self.update(public=json.loads(safe_text(json.dumps(public, ensure_ascii=False))))

    def cleanup_game(self):
        if self.planner and hasattr(self.planner, 'close'): self.planner.close()
        if self.model_server: self.model_server.close()
        if self.native: self.native.close()

    def report(self):
        with self.lock:
            self.state['seq'] += 1
            return copy.deepcopy(self.state)

    def relay(self, messages):
        if not self.commander or self.stop_event.is_set(): return
        for message in messages:
            if message['id'] in self.delivered: continue
            try:
                submit_advice(self.commander.operator_advice.directory, message['text'],
                    player=self.commander.operator_advice.player, message_id=message['id'])
            except ValueError:
                continue  # The real Commander queue owns backpressure and request availability.
            self.delivered.add(message['id'])

    def tick(self):
        if self.future and self.future.done():
            self.future.result()
            self.future = None
        response = self.request('POST', '/api/client/poll', self.report())
        self.seat = response['seat']
        self.last_contact = time.monotonic()
        self.relay(response.get('messages', []))
        action = response['action']
        if action == 'stop':
            self.stop_event.set()
            if self.stop_requested_at is None:
                self.stop_requested_at = time.monotonic()
                if self.planner and hasattr(self.planner, 'close'): self.planner.close()
            if self.future and time.monotonic() - self.stop_requested_at > 8 and self.native:
                self.native.terminate()
            if self.future is None:
                if self.state['phase'] not in ('finished', 'failed'): self.update(phase='stopped')
                self.cleanup_game()
                self.request('POST', '/api/client/poll', self.report())
                return False
            return True
        if action != 'idle' and self.future is None and not self.stop_event.is_set():
            manifest = response['manifest']
            if self.manifest and self.manifest['session'] != manifest['session']:
                raise RuntimeError('客户端不会自动切换到另一场比赛，请重新配对')
            self.manifest, self.own = copy.deepcopy(manifest), copy.deepcopy(response['own'])
            self.update(session=manifest['session'])
            self.future = self.pool.submit(self.perform, action)
        return True

    def run(self):
        try:
            while not self.closed:
                try:
                    if not self.tick(): break
                except (requests.RequestException, RuntimeError) as error:
                    print(safe_text(str(error)), flush=True)
                    if time.monotonic() - self.last_contact > 30: raise RuntimeError('协调器持续失联，本机停止；不推断胜负') from error
                time.sleep(1)
        finally: self.close()

    def close(self):
        if self.closed: return
        self.closed = True
        self.stop_event.set()
        if self.planner and hasattr(self.planner, 'close'): self.planner.close()
        if self.future:
            try: self.future.result(timeout=8)
            except Exception:
                if self.native: self.native.terminate()
        self.cleanup_game()
        if self.run_dir: write_json_atomic(self.run_dir / 'final-client-status.json', self.report())
        self.pool.shutdown(wait=False, cancel_futures=True)
