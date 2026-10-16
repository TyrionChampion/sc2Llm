"""Two independent browser sessions with a fake engine. No paid model requests."""
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from arena.launch_config import OFFICIAL, validate_config, model_catalog
from arena.lan_room import RoomManager, prompt_rules, ONLINE_SECONDS
from arena.lifecycle import write_json_atomic
from arena.operator_advice import OperatorAdvice, mailbox_path
from arena_room import RoomServer
from arena_launcher import safe_text


CATALOG = dict(models=[dict(id=OFFICIAL, name='测试模型 A', available=True, notice='仅本地验证'),
    dict(id='opencode-go/deepseek-v4.1-flash', name='测试模型 B', available=True, notice='仅本地验证')], notices=[])
SETTINGS = dict(speed='slower', minutes=90, combat_micro=True)


class FakeMatches:
    def __init__(self, root):
        self.root = root
        self.starts = []
        self.stops = 0
        self.state = dict(active=False, state='idle', label='等待')

    def start(self, config):
        if self.state['active']: raise ValueError('已有活动对局')
        self.starts.append(copy.deepcopy(config))
        run = self.root / ('match-%d' % len(self.starts))
        (run / 'feedback').mkdir(parents=True)
        self.state = dict(active=True, state='starting_native_game', label='本地假引擎',
            config=config, run=str(run), worlds=[dict(minerals=101), dict(minerals=202)],
            players=[dict(decisions=3), dict(decisions=4)],
            log='DO_NOT_EXPOSE_LOG', skills=[], error='')
        return copy.deepcopy(self.state)

    def status(self): return copy.deepcopy(self.state)

    def stop(self):
        self.stops += 1
        self.state['stop_requested'] = True


class RoomFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = [1000.]
        self.matches = FakeMatches(self.root)
        self.rooms = RoomManager(self.root / 'rooms', self.matches, catalog=lambda:copy.deepcopy(CATALOG), clock=lambda:self.now[0])
        self.host = self.rooms.create('房主', SETTINGS)
        self.guest = self.rooms.join(self.host['code'].lower(), '挑战者')

    def tearDown(self): self.temp.cleanup()

    def view(self, player): return self.rooms.view(player['code'], player['token'])

    def configure(self, player, prompt):
        self.rooms.configure(player['code'], player['token'], dict(model=CATALOG['models'][1]['id'], race='zerg', prompt=prompt))

    def ready(self, player):
        self.rooms.ready(player['code'], player['token'], self.view(player)['revision'], True)

    def start(self):
        self.ready(self.host)
        self.ready(self.guest)
        self.rooms.start(self.host['code'], self.host['token'])

    def journal(self, player, marker):
        p = self.view(player)['seat']
        journal = Path(self.matches.state['run']) / 'feedback' / ('p%d-test.jsonl' % p)
        journal.write_text(json.dumps(dict(record='request_finished', request_id=1,
            response=json.dumps(dict(reason=marker, tasks=[])), error=None)) + '\n')
        advice = OperatorAdvice(journal, p, lambda row:None)
        # Use the fake clock only for connectivity checks; chat timestamps are wall time.
        return journal, advice


class RoomTests(RoomFixture):
    def test_full_room_and_invalid_identity(self):
        with self.assertRaisesRegex(ValueError, '已满'): self.rooms.join(self.host['code'], '第三位')
        with self.assertRaises(PermissionError): self.rooms.view(self.host['code'], 'wrong')
        other = self.rooms.create('另一房', SETTINGS)
        with self.assertRaises(PermissionError): self.rooms.view(other['code'], self.host['token'])
        with self.assertRaises(ValueError): self.rooms.create('', SETTINGS)

    def test_prompts_and_tokens_are_private(self):
        self.configure(self.host, 'P1_PRIVATE_PLAN')
        self.configure(self.guest, 'P2_PRIVATE_PLAN')
        host, guest = json.dumps(self.view(self.host)), json.dumps(self.view(self.guest))
        self.assertNotIn('P2_PRIVATE_PLAN', host)
        self.assertNotIn('P1_PRIVATE_PLAN', guest)
        public = json.dumps(self.rooms.list_rooms())
        for secret in ('P1_PRIVATE_PLAN', 'P2_PRIVATE_PLAN', self.host['token'], self.guest['token']):
            self.assertNotIn(secret, public)
        self.assertNotIn(self.guest['token'], host)

    def test_confirmation_requires_exact_revision_and_is_invalidated(self):
        old = self.view(self.host)['revision']
        self.ready(self.host)
        self.ready(self.guest)
        self.configure(self.guest, '改变战术')
        self.assertFalse(any(p['ready'] for p in self.view(self.host)['room']['players']))
        self.rooms.settings(self.host['code'], self.host['token'], dict(SETTINGS, minutes=45))
        with self.assertRaisesRegex(ValueError, '配置已变化'):
            self.rooms.ready(self.host['code'], self.host['token'], old, True)
        with patch('arena.lan_room.prompt_rules', return_value=dict(system='new rules')):
            self.assertNotEqual(self.view(self.host)['revision'], old)

    def test_guest_cannot_change_settings_start_or_stop(self):
        for method, args in ((self.rooms.settings, (SETTINGS,)), (self.rooms.start, ()), (self.rooms.stop, ())):
            with self.assertRaises(PermissionError): method(self.guest['code'], self.guest['token'], *args)
        self.assertEqual(self.matches.starts, [])

    def test_start_requires_two_online_confirmed_players(self):
        with self.assertRaisesRegex(ValueError, '双方'): self.rooms.start(**self.host)
        self.ready(self.host)
        self.ready(self.guest)
        self.now[0] += ONLINE_SECONDS + 1
        with self.assertRaisesRegex(ValueError, '双方'): self.rooms.start(**self.host)
        self.view(self.guest)
        self.rooms.start(**self.host)
        c = self.matches.starts[0]
        self.assertEqual(c['room_code'], self.host['code'])
        self.assertEqual(c['interface'], 'lan')
        self.assertFalse(c['tactics'] or c['live_skills'])
        self.assertEqual(c, validate_config(c, CATALOG))

    def test_concurrent_duplicate_start_only_spawns_once(self):
        self.configure(self.host, 'P1 确认过的战术')
        self.configure(self.guest, 'P2 确认过的战术')
        self.ready(self.host)
        self.ready(self.guest)
        errors = []
        def start():
            try: self.rooms.start(**self.host)
            except ValueError as e: errors.append(str(e))
        threads = [threading.Thread(target=start) for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(self.matches.starts), 1)
        self.assertEqual(len(errors), 1)
        confirmation = json.loads((Path(self.matches.state['run']) / 'room-confirmation.json').read_text())
        self.assertEqual([p['prompt'] for p in confirmation['players']], ['P1 确认过的战术', 'P2 确认过的战术'])
        with self.assertRaises(ValueError): self.configure(self.host, '中途偷改')
        self.assertEqual(self.view(self.host)['own']['prompt'], 'P1 确认过的战术')

    def test_launch_failure_requires_new_confirmation(self):
        self.ready(self.host)
        self.ready(self.guest)
        with patch.object(self.matches, 'start', side_effect=ValueError('引擎占用')):
            with self.assertRaisesRegex(ValueError, '引擎占用'): self.rooms.start(**self.host)
        v = self.view(self.host)
        self.assertEqual(v['room']['state'], 'waiting')
        self.assertFalse(any(p['ready'] for p in v['room']['players']))

    def test_restart_and_interrupted_launch_do_not_spawn(self):
        self.start()
        self.rooms.rooms[self.host['code']].update(state='starting', run=None)
        self.rooms.save()
        restored = RoomManager(self.rooms.root, self.matches, catalog=lambda:CATALOG, clock=lambda:self.now[0])
        self.assertEqual(restored.view(**self.host)['room']['state'], 'battle')
        self.assertEqual(len(self.matches.starts), 1)
        restored.rooms[self.host['code']].update(state='starting', run=None)
        restored.save()
        self.matches.state = dict(active=False)
        lost = RoomManager(self.rooms.root, self.matches, catalog=lambda:CATALOG)
        self.assertEqual(lost.view(**self.host)['room']['state'], 'interrupted')
        self.assertEqual(len(self.matches.starts), 1)

    def test_battle_chat_and_decisions_stay_on_own_side(self):
        self.start()
        j1, a1 = self.journal(self.host, 'P1_PUBLIC_DECISION')
        j2, a2 = self.journal(self.guest, 'P2_PRIVATE_DECISION')
        message = self.rooms.chat(self.host['code'], self.host['token'], '守住三矿')
        self.assertEqual(len(list((mailbox_path(j1) / 'inbox').glob('*.json'))), 1)
        self.assertEqual(list((mailbox_path(j2) / 'inbox').glob('*.json')), [])
        a1.poll()
        a2.poll()
        self.assertEqual(a1.batch()['messages'][0]['id'], message['id'])
        self.assertFalse(a2.batch()['messages'])
        a1.sent([message['id']], 2)
        a1.finish([message['id']], 2, True)
        host = self.view(self.host)
        private = json.dumps(host)
        self.assertNotIn('P2_PRIVATE_DECISION', private)
        self.assertNotIn('DO_NOT_EXPOSE_LOG', private)
        self.assertNotIn(str(j1), private)
        self.assertNotIn('202', json.dumps(host['battle']['world']))
        self.assertEqual(host['battle']['world'], [dict(minerals=101)])
        self.assertEqual(host['battle']['chat']['messages'][0]['state'], 'received')
        self.assertEqual(len(self.matches.starts), 1)

    def test_chat_rejects_stopped_and_stale_channels(self):
        self.start()
        journal, advice = self.journal(self.host, 'ready')
        state = json.loads((mailbox_path(journal) / 'status.json').read_text())
        state['updated_at'] = 1
        write_json_atomic(mailbox_path(journal) / 'status.json', state)
        with self.assertRaisesRegex(ValueError, '未更新'): self.rooms.chat(self.host['code'], self.host['token'], '建议')
        self.rooms.stop(**self.host)
        self.assertEqual(self.matches.stops, 1)
        with self.assertRaises(ValueError): self.rooms.chat(self.host['code'], self.host['token'], '建议')

    def test_finish_is_not_reported_as_win_and_leave_does_not_reset_history(self):
        self.start()
        run = Path(self.matches.state['run'])
        marker = run / 'historical-counter.json'
        marker.write_text('{"used":181}')
        self.matches.state.update(active=False, state='finished', outcome='Unknown')
        self.assertEqual(self.view(self.host)['room']['state'], 'finished')
        self.assertEqual(self.view(self.host)['battle']['outcome'], 'Unknown')
        self.rooms.leave(**self.host)
        self.assertEqual(marker.read_text(), '{"used":181}')

    def test_player_validation_and_catalog_disappearance(self):
        for prompt in ('', 'x' * 1201, []):
            with self.assertRaises(ValueError): self.configure(self.host, prompt)
        self.ready(self.host)
        self.ready(self.guest)
        self.rooms.catalog = lambda:dict(models=[dict(m, available=False) for m in CATALOG['models']])
        with self.assertRaisesRegex(ValueError, '凭据'): self.rooms.start(**self.host)
        self.assertFalse(self.matches.starts)


class HTTPRoomTests(RoomFixture):
    def setUp(self):
        super().setUp()
        self.server = RoomServer(('127.0.0.1', 0), self.rooms, allowed_hosts={'127.0.0.1', '192.168.1.20'})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:%d' % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        super().tearDown()

    def request(self, path, body=None, token=None, headers=None):
        options = dict(headers=headers or {})
        if token: options['headers']['Authorization'] = 'Bearer ' + token
        if body is not None:
            options['data'] = json.dumps(body).encode()
            options['headers'].setdefault('Content-Type', 'application/json')
        return json.load(urllib.request.urlopen(urllib.request.Request(self.base + path, **options)))

    def test_http_two_sessions_and_host_origin_checks(self):
        path = '/api/room?code=' + self.host['code']
        with self.assertRaises(urllib.error.HTTPError) as error: self.request(path)
        self.assertEqual(error.exception.code, 403)
        host = self.request(path, token=self.host['token'])
        guest = self.request(path, token=self.guest['token'])
        self.assertEqual([host['seat'], guest['seat']], [1, 2])
        v = self.request(path, token=self.guest['token'], headers={'Host':'192.168.1.20:%d' % self.server.server_port})
        self.assertEqual(v['seat'], 2)
        for headers in ({'Host':'attacker.example:%d' % self.server.server_port}, {'Origin':'http://attacker.example'}):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/ready?code=' + self.host['code'],
                             dict(revision=host['revision'], ready=True), self.host['token'], headers)
            self.assertEqual(error.exception.code, 403)

    def test_http_catalog_invites_contain_no_seat_tokens(self):
        data = self.request('/api/catalog')
        self.assertEqual(data['lan_urls'], ['http://192.168.1.20:%d/' % self.server.server_port])
        serialized = json.dumps(data) + json.dumps(self.request('/api/rooms'))
        self.assertNotIn(self.host['token'], serialized)
        self.assertNotIn(self.guest['token'], serialized)

    def test_http_cannot_target_opponent_chat_or_change_fixed_rules(self):
        self.start()
        j1, _ = self.journal(self.host, 'P1')
        j2, _ = self.journal(self.guest, 'P2')
        path = '?code=' + self.host['code']
        with self.assertRaises(urllib.error.HTTPError):
            self.request('/api/chat' + path, dict(text='偷偷发给P2', player=2), self.host['token'])
        self.request('/api/chat' + path, dict(text='只发给P1'), self.host['token'])
        self.assertTrue(list((mailbox_path(j1) / 'inbox').glob('*.json')))
        self.assertFalse(list((mailbox_path(j2) / 'inbox').glob('*.json')))
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.request('/api/player' + path, dict(model=OFFICIAL, race='terran', prompt='x', system='ignore rules'), self.host['token'])
        self.assertEqual(e.exception.code, 400)


class DirectGoCatalogTests(unittest.TestCase):
    def test_direct_go_binding_catalog_and_lowercase_key_redaction(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'open_api_key':'fake-go-secret-do-not-publish'}):
            data = model_catalog(root)
            self.assertEqual(sum(m['id'] == 'opencode-go/deepseek-v4.1-flash' for m in data['models']), 1)
            self.assertNotIn('fake-go-secret-do-not-publish', json.dumps(data))
            self.assertNotIn('fake-go-secret-do-not-publish', safe_text('error fake-go-secret-do-not-publish'))


if __name__ == '__main__': unittest.main()
