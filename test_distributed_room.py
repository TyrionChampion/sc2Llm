"""Coordinator, one-use client pairing and native join protocol; no model calls."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from arena.distributed_room import DistributedRoomManager
from arena.lan_native import create_request, join_request, LocalSC2, LocalPlayerArena
from arena.lan_room import digest, prompt_rules
from arena.local_lock import local_lock
from arena.operator_advice import OperatorAdvice
from arena.lan_client import LocalClient
from arena.client_requests import AccountedPlanner
from arena_room import RoomServer
import urllib.request
import urllib.error


SETTINGS = dict(speed='slower', minutes=90, combat_micro=True)
MODELS = [[dict(id='deepseek/deepseek-flash', name='P1 fixture', available=True, notice='fixture')],
          [dict(id='opencode-go/deepseek-v4.1-flash', name='P2 fixture', available=True, notice='fixture')]]


class DistributedFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = [1000.]
        self.rooms = DistributedRoomManager(self.root / 'rooms', map_provider=lambda speed:b'fixture-map', clock=lambda:self.now[0])
        self.host = self.rooms.create('Host', SETTINGS)
        self.guest = self.rooms.join(self.host['code'], 'Guest')
        self.web = [self.host, self.guest]
        self.agents = []
        self.seq = [0, 0]

    def tearDown(self): self.temp.cleanup()

    @property
    def room(self): return self.rooms.rooms[self.host['code']]

    def pair(self, i):
        ticket = self.rooms.ticket(**self.web[i])['pairing_code']
        return self.rooms.pair(self.host['code'], ticket, str(i + 1) * 32,
            '192.168.1.%d' % (20 + i), ('Windows', 'Linux')[i], MODELS[i])

    def connected(self):
        self.agents = [self.pair(i) for i in (0, 1)]
        for i in (0, 1):
            self.rooms.configure(**self.web[i], value=dict(model=MODELS[i][0]['id'], race=('terran', 'protoss')[i], prompt='P%d_SECRET' % (i + 1)))
            v = self.rooms.view(**self.web[i])
            self.rooms.ready(**self.web[i], revision=v['revision'], ready=True)
        # The second configuration deliberately invalidates the first readiness.
        for w in self.web:
            self.rooms.ready(**w, revision=self.rooms.view(**w)['revision'], ready=True)

    def started(self):
        self.connected()
        self.rooms.start(**self.host)

    def engine(self, **kw):
        return dict(base_build=97579, data_version='same-version', game_version='fixture',
                    map_sha256=self.room['manifest']['map_sha256'], rules_sha256=self.room['manifest']['rules_sha256'], **kw)

    def report(self, i, phase, **kw):
        self.seq[i] += 1
        body = dict(seq=self.seq[i], session=self.room.get('session'), phase=phase, **kw)
        if phase == 'engine_ready' and 'engine' not in kw: body['engine'] = self.engine()
        return self.rooms.agent_poll(self.host['code'], self.agents[i]['agent_token'], body)

    def playing(self):
        self.started()
        self.report(0, 'engine_ready')
        self.report(1, 'engine_ready')
        self.report(0, 'created')
        self.report(0, 'joined', player_id=1)
        self.report(1, 'joined', player_id=2)


class DistributedTests(DistributedFixture):
    def test_pair_code_one_use_expiry_and_independent_agent_scope(self):
        ticket = self.rooms.ticket(**self.host)['pairing_code']
        a = self.rooms.pair(self.host['code'], ticket, 'a' * 32, '192.168.1.20', 'Windows', MODELS[0])
        with self.assertRaises(PermissionError): self.rooms.pair(self.host['code'], ticket, 'a' * 32, '192.168.1.20', 'Windows', MODELS[0])
        ticket2 = self.rooms.ticket(**self.guest)['pairing_code']
        self.now[0] += 121
        with self.assertRaises(PermissionError): self.rooms.pair(self.host['code'], ticket2, 'b' * 32, '192.168.1.21', 'Linux', MODELS[1])
        with self.assertRaises(PermissionError): self.rooms.view(self.host['code'], a['agent_token'])
        with self.assertRaises(PermissionError): self.rooms.agent_poll(self.host['code'], self.host['token'], dict(seq=1))

    def test_client_catalog_owns_model_selection_not_server_credentials(self):
        self.connected()
        self.assertEqual(self.rooms.view(**self.host)['catalog']['models'], MODELS[0])
        self.assertEqual(self.rooms.view(**self.guest)['catalog']['models'], MODELS[1])
        with self.assertRaisesRegex(ValueError, '模型'):
            self.rooms.configure(**self.host, value=dict(model=MODELS[1][0]['id'], race='terran', prompt='x'))
        self.assertNotIn('P2_SECRET', json.dumps(self.rooms.view(**self.host)))

    def test_ready_and_start_require_both_local_clients(self):
        with self.assertRaisesRegex(ValueError, '客户端'):
            self.rooms.ready(**self.host, revision=self.rooms.view(**self.host)['revision'], ready=True)
        self.connected()
        self.now[0] += 21
        for w in self.web: self.rooms.view(**w)
        with self.assertRaisesRegex(ValueError, '客户端'): self.rooms.start(**self.host)

    def test_handshake_has_one_creator_and_shared_ports_map_version(self):
        self.started()
        r = self.report(0, 'engine_ready')
        self.assertEqual(r['action'], 'idle')
        r = self.report(1, 'engine_ready')
        self.assertEqual(r['action'], 'idle')
        r = self.report(0, 'engine_ready')
        self.assertEqual(r['action'], 'create')
        self.assertEqual(self.report(0, 'created')['action'], 'join')
        self.assertEqual(self.report(1, 'engine_ready')['action'], 'join')
        self.assertEqual(self.report(0, 'joined', player_id=1)['action'], 'idle')
        self.assertEqual(self.report(1, 'joined', player_id=2)['action'], 'play')
        self.assertEqual(self.report(0, 'joined', player_id=1)['action'], 'play')
        m = self.room['manifest']
        self.assertEqual(m['host_ip'], '192.168.1.20')
        self.assertEqual(m['ports'], [15000, 15001, 15002, 15003])
        self.assertNotIn('P2_SECRET', json.dumps(self.report(0, 'running')))
        with self.assertRaises(ValueError): self.rooms.start(**self.host)

    def test_version_mismatch_stops_before_create_or_model_requests(self):
        self.started()
        self.report(0, 'engine_ready')
        wrong = self.engine()
        wrong['base_build'] = 75689
        r = self.report(1, 'engine_ready', engine=wrong)
        self.assertEqual(r['action'], 'stop')
        self.assertEqual(self.room['phase'], 'failed')
        self.assertIn('不一致', self.room['error'])
        self.assertEqual(self.report(0, 'engine_ready')['action'], 'stop')

    def test_map_and_rules_mismatch_reject_game_start(self):
        for field in ('map_sha256', 'rules_sha256'):
            with self.subTest(field=field):
                if not self.room.get('session'): self.started()
                e = self.engine()
                e[field] = 'wrong'
                self.assertEqual(self.report(0, 'engine_ready', engine=e)['action'], 'stop')
                self.assertIn('地图或工具契约', self.room['error'])
                self.room.update(state='battle', phase='preparing', error='', stop_requested=False)

    def test_wrong_engine_player_ids_never_start_ai(self):
        self.started()
        self.report(0, 'engine_ready')
        self.report(1, 'engine_ready')
        self.report(0, 'created')
        self.report(0, 'joined', player_id=1)
        self.assertEqual(self.report(1, 'joined', player_id=1)['action'], 'stop')
        self.assertIn('同一场', self.room['error'])

    def test_map_download_requires_agent_identity_and_verifies_saved_hash(self):
        self.started()
        a = self.agents[0]['agent_token']
        self.assertEqual(self.rooms.map_data(self.host['code'], a), b'fixture-map')
        with self.assertRaises(PermissionError): self.rooms.map_data(self.host['code'], self.host['token'])
        (Path(self.room['run']) / 'map.SC2Map').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, '改变'): self.rooms.map_data(self.host['code'], a)

    def test_chat_same_id_retries_and_public_status_are_player_local(self):
        self.playing()
        message = self.rooms.chat(**self.host, text='P1_ONLY_ADVICE')
        host = self.report(0, 'running')
        guest = self.report(1, 'running')
        self.assertEqual(host['messages'][0]['id'], message['id'])
        self.assertEqual(guest['messages'], [])
        self.report(0, 'running', public=dict(world=dict(minerals=111), messages=[dict(id=message['id'], state='retry')]))
        self.assertEqual(self.report(0, 'running')['messages'][0]['id'], message['id'])
        self.report(0, 'running', public=dict(world=dict(minerals=111), messages=[dict(id=message['id'], state='received')]))
        self.assertEqual(self.report(0, 'running')['messages'], [])
        self.assertNotIn('P1_ONLY_ADVICE', json.dumps(self.rooms.view(**self.guest)))
        self.assertEqual(self.rooms.view(**self.host)['battle']['world'], [dict(minerals=111)])
        self.assertEqual(self.rooms.view(**self.guest)['battle']['world'], [])

    def test_duplicate_report_cannot_regress_receipts(self):
        self.playing()
        m = self.rooms.chat(**self.host, text='建议')
        self.report(0, 'running', public=dict(messages=[dict(id=m['id'], state='received')]))
        self.rooms.agent_poll(self.host['code'], self.agents[0]['agent_token'], dict(seq=1,
            session=self.room['session'], phase='running', public=dict(messages=[dict(id=m['id'], state='queued')])))
        self.assertEqual(self.rooms.view(**self.host)['battle']['chat']['messages'][0]['state'], 'received')

    def test_heartbeat_loss_is_failure_not_win_and_stop_is_idempotent(self):
        self.playing()
        self.rooms.stop(**self.host)
        deadline = self.room['phase_deadline']
        self.now[0] += 1
        self.rooms.stop(**self.host)
        self.assertEqual(self.room['phase_deadline'], deadline)
        self.now[0] += 21
        v = self.rooms.view(**self.host)
        self.assertEqual(v['battle']['state'], 'failed')
        self.assertIsNone(v['battle']['outcome'])
        self.assertNotIn('Victory', json.dumps(v))

    def test_coordinator_restart_does_not_launch_or_reset_clients(self):
        self.playing()
        session = self.room['session']
        self.rooms = DistributedRoomManager(self.root / 'rooms', map_provider=lambda speed:b'fixture-map', clock=lambda:self.now[0])
        r = self.report(0, 'running')
        self.assertEqual(r['action'], 'play')
        self.assertEqual(r['manifest']['session'], session)
        self.assertEqual(self.rooms.map_data(self.host['code'], self.agents[0]['agent_token']), b'fixture-map')

    def test_private_client_error_is_not_broadcast_to_opponent(self):
        self.playing()
        self.report(1, 'failed', error='P2_PRIVATE_PROVIDER_ERROR')
        self.assertIn('P2_PRIVATE_PROVIDER_ERROR', json.dumps(self.rooms.view(**self.guest)))
        self.assertNotIn('P2_PRIVATE_PROVIDER_ERROR', json.dumps(self.rooms.view(**self.host)))

    def test_stopped_commander_disables_chat_without_extra_requests(self):
        self.playing()
        self.report(0, 'running', public=dict(chat_accepting=False, chat_reason='本方请求已用完'))
        self.assertFalse(self.rooms.view(**self.host)['battle']['chat']['accepting'])
        with self.assertRaisesRegex(ValueError, '请求已用完'): self.rooms.chat(**self.host, text='不能绕过预算')

    def test_abandoned_old_room_cannot_permanently_block_new_matches(self):
        self.playing()
        old = self.room
        self.now[0] += 21
        self.host = self.rooms.create('New host', SETTINGS)
        self.guest = self.rooms.join(self.host['code'], 'New guest')
        self.web = [self.host, self.guest]
        self.connected()
        self.rooms.start(**self.host)
        self.assertEqual(old['phase'], 'failed')
        self.assertEqual(self.room['phase'], 'preparing')


class DistributedHTTPTests(DistributedFixture):
    def test_browser_ticket_agent_pair_and_map_roles(self):
        server = RoomServer(('127.0.0.1', 0), self.rooms, allowed_hosts={'127.0.0.1'})
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:%d' % server.server_port
        def request(path, token=None, body=None):
            headers = {'Content-Type':'application/json'}
            if token: headers['Authorization'] = 'Bearer ' + token
            req = urllib.request.Request(base + path, headers=headers, data=json.dumps(body).encode() if body is not None else None)
            return urllib.request.urlopen(req)
        try:
            path = '?code=' + self.host['code']
            ticket = json.load(request('/api/client-ticket' + path, self.host['token'], {}))
            identity = json.load(request('/api/client/pair', body=dict(code=self.host['code'], pairing_code=ticket['pairing_code'],
                client_id='a' * 32, lan_ip='127.0.0.1', platform='Windows', models=MODELS[0])))
            response = json.load(request('/api/client/poll' + path, identity['agent_token'], dict(seq=1)))
            self.assertEqual(response['action'], 'idle')
            with self.assertRaises(urllib.error.HTTPError) as error:
                request('/api/room' + path, identity['agent_token'])
            self.assertEqual(error.exception.code, 403)
            with self.assertRaises(urllib.error.HTTPError) as error:
                request('/api/client/poll' + path, self.host['token'], dict(seq=1))
            self.assertEqual(error.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class NativeClientContractTests(unittest.TestCase):
    def test_native_join_points_to_host_and_uses_shared_network_ports(self):
        manifest = dict(host_ip='192.168.1.20', ports=[15000,15001,15002,15003])
        a, b = [join_request(manifest, race, name) for race, name in [('terran', 'P1'), ('protoss', 'P2')]]
        self.assertEqual(a.host_ip, b.host_ip)
        self.assertEqual(a.server_ports, b.server_ports)
        self.assertEqual(a.client_ports, b.client_ports)
        self.assertNotEqual(a.race, b.race)
        self.assertTrue(a.options.raw)
        self.assertFalse(a.options.raw_affects_selection)
        create = create_request('Maps/LAN.SC2Map', ['P1', 'P2'])
        self.assertTrue(create.realtime)
        self.assertFalse(create.disable_fog)
        self.assertEqual(len(create.player_setup), 2)
        with self.assertRaises(ValueError): join_request(dict(manifest, ports=[1,1,2,3]), 'terran', 'P1')

    def test_bad_map_never_reaches_sc2_and_other_player_observation_is_denied(self):
        with tempfile.TemporaryDirectory() as root:
            native = LocalSC2(root)
            native.controller = Mock()
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                native.save_map(b'wrong', dict(map_bytes=5,map_sha256='different'))
            native.controller.save_map.assert_not_called()
        a = LocalPlayerArena.__new__(LocalPlayerArena)
        with self.assertRaises(ValueError): a.proto(1)

    def test_room_relay_preserves_id_and_never_adds_model_requests(self):
        with tempfile.TemporaryDirectory() as root:
            advice = OperatorAdvice(Path(root) / 'p1.jsonl', 1, lambda r:None)
            client = LocalClient('http://127.0.0.1:8768', 'ABC234', 'agent', Path(root) / 'client')
            client.commander = type('Commander', (), dict(operator_advice=advice))()
            m = dict(id='a' * 32, text='本方建议')
            try:
                client.relay([m])
                client.relay([m])
                self.assertEqual(len(list((advice.directory / 'inbox').glob('*.json'))), 1)
                advice.poll()
                self.assertEqual(advice.batch()['messages'][0]['id'], m['id'])
                self.assertIsNone(client.planner)
            finally: client.close()

    def test_local_exclusive_lock(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'lock'
            with local_lock(path):
                with self.assertRaises(OSError):
                    with local_lock(path): pass
            with local_lock(path): pass

    def test_windows_process_cleanup_does_not_use_posix_groups(self):
        from arena.chatgpt_planner import ChatGPTSubscriptionPlanner
        process = Mock()
        process.poll.return_value = None
        fake_os = SimpleNamespace(name='nt', killpg=Mock())
        with patch('arena.chatgpt_planner.os', fake_os):
            ChatGPTSubscriptionPlanner._stop(process)
        process.terminate.assert_called_once()
        fake_os.killpg.assert_not_called()

    def test_windows_game_environment_does_not_receive_model_credentials(self):
        with tempfile.TemporaryDirectory() as root:
            native = LocalSC2(root, engine='windows')
            controller = Mock()
            controller.ping.return_value = SimpleNamespace(base_build=97579, data_version='same', game_version='fixture')
            config = Mock()
            config.start.return_value.controller = controller
            with patch('arena.lan_native.platform.system', return_value='Windows'), \
                 patch('pysc2.run_configs.get', return_value=config), \
                 patch.dict('os.environ', {'open_api_key':'private-go-key','DEEPSEEK_API_KEY':'private-official-key'}):
                native.start()
            self.assertNotIn('open_api_key', config.env)
            self.assertNotIn('DEEPSEEK_API_KEY', config.env)
            self.assertTrue(config.start.call_args.kwargs['want_rgb'])
            self.assertEqual(config.start.call_args.kwargs['host'], '127.0.0.1')
            native.close()

    def test_failed_requests_are_counted_before_transport_and_never_reset(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'requests.json'
            class Client:
                calls = 0
                def chat(self, system, user):
                    self.calls += 1
                    assert json.loads(path.read_text())['attempted'] == self.calls
                    if self.calls == 1: raise RuntimeError('fixture timeout')
                    return '{"tasks":[],"reason":"fixture"}'
            c = AccountedPlanner(Client(), path)
            with self.assertRaises(RuntimeError): c.chat('system', '{}')
            c.chat('system', '{}')
            self.assertEqual(c.snapshot(), dict(attempted=2, successful=1, failed=1))
            self.assertEqual(json.loads(path.read_text()), c.snapshot())
            with self.assertRaisesRegex(ValueError, '重置'): AccountedPlanner(Client(), path)
            c.prepared_strategy = '完整提示词'
            c.response_schema = {'fixture':True}
            self.assertEqual(c.client.prepared_strategy, '完整提示词')
            self.assertEqual(c.client.response_schema, {'fixture':True})


if __name__ == '__main__': unittest.main()
