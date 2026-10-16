"""Persistent two-seat rooms. Every private operation resolves a player token first."""
import copy
import hashlib
import json
from pathlib import Path
import secrets
import threading
import time

from arena import battle_plans
from arena.autonomous_bot import INITIAL_STRATEGY
from arena.launch_config import MAX_PLAYER_PROMPT, RACES, model_catalog, validate_config
from arena.lifecycle import write_json_atomic
from arena.match_control import SURRENDER_PROTOCOL
from arena.operator_advice import PROTOCOL, mailbox_path, read_status, submit_advice


ONLINE_SECONDS = 20


def prompt_rules(combat_micro=False):
    # Room clients are compact planners with no Python catalogue and no opening script.
    return dict(system=battle_plans.SYSTEM + SURRENDER_PROTOCOL +
                (battle_plans.COMBAT_MICRO_PROTOCOL if combat_micro else ''),
                chat=PROTOCOL, default_prompt=INITIAL_STRATEGY, max_prompt=MAX_PLAYER_PROMPT)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def name_text(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 32:
        raise ValueError('昵称须为 1～32 字符')
    return value.strip()


def room_settings(value):
    if not isinstance(value, dict) or set(value) != {'speed', 'minutes', 'combat_micro'}:
        raise ValueError('房间设置需要速度、时限和任务内微操选项')
    if value['speed'] not in ('slower', 'normal', 'faster'):
        raise ValueError('未知游戏速度')
    if type(value['minutes']) is not int or not 1 <= value['minutes'] <= 180:
        raise ValueError('时限须为 1～180 分钟')
    if type(value['combat_micro']) is not bool:
        raise ValueError('微操选项须为布尔值')
    return copy.deepcopy(value)


class RoomManager:
    def __init__(self, root, matches, catalog=model_catalog, clock=time.time):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.matches, self.catalog, self.clock = matches, catalog, clock
        self.lock = threading.RLock()
        self.path = self.root / 'rooms.json'
        self.rooms = json.loads(self.path.read_text()) if self.path.exists() else {}
        for room in self.rooms.values():
            if room['state'] == 'starting':
                status = self.matches.status()
                if status.get('config', {}).get('room_code') == room['code'] and status.get('run'):
                    room.update(state='battle', run=status['run'])
                else:
                    # Ambiguous launch is never retried automatically after a server crash.
                    room['state'] = 'interrupted'
                self.save()

    def save(self):
        write_json_atomic(self.path, self.rooms)
        self.path.chmod(0o600)

    def new_player(self, name):
        available = [m for m in self.catalog()['models'] if m['available']]
        return dict(name=name_text(name), token=secrets.token_urlsafe(32),
                    model=available[0]['id'] if available else '', race='terran',
                    prompt=INITIAL_STRATEGY, ready_revision=None, seen=self.clock())

    def resolve(self, code, token):
        if not isinstance(code, str) or code not in self.rooms:
            raise ValueError('房间不存在')
        if not isinstance(token, str) or not token:
            raise PermissionError('请先加入房间')
        room = self.rooms[code]
        for i, player in enumerate(room['players']):
            if player and secrets.compare_digest(player['token'], token):
                player['seen'] = self.clock()
                return room, i
        raise PermissionError('玩家身份无效，请使用加入房间时的浏览器')

    def revision(self, room, index):
        p = room['players'][index]
        return digest(dict(model=p['model'], race=p['race'], prompt=p['prompt'],
                           settings=room['settings'], rules=prompt_rules(room['settings']['combat_micro'])))

    def unready(self, room):
        for p in room['players']:
            if p: p['ready_revision'] = None

    def waiting(self, room):
        if room['state'] != 'waiting':
            raise ValueError('对局已启动或房间已关闭，不能修改准备配置')

    def summary(self, room):
        return dict(code=room['code'], state=room['state'], settings=room['settings'],
            players=[None if not p else dict(name=p['name'], model=p['model'], race=p['race'],
                online=self.clock() - p['seen'] <= ONLINE_SECONDS,
                ready=p['ready_revision'] == self.revision(room, i))
                for i, p in enumerate(room['players'])])

    def list_rooms(self):
        with self.lock:
            return [self.summary(r) for r in self.rooms.values() if r['state'] == 'waiting']

    def create(self, name, settings):
        with self.lock:
            if sum(r['state'] == 'waiting' for r in self.rooms.values()) >= 32:
                raise ValueError('等待中的房间已满，请使用已有房间')
            settings = room_settings(settings)
            player = self.new_player(name)
            code = ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(6))
            while code in self.rooms:
                code = ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(6))
            self.rooms[code] = dict(code=code, state='waiting', settings=settings,
                                    players=[player, None], created_at=self.clock(), run=None)
            self.save()
            return dict(code=code, token=player['token'])

    def join(self, code, name):
        with self.lock:
            if not isinstance(code, str): raise ValueError('请输入房间码')
            code = code.strip().upper()
            room = self.rooms.get(code)
            if room is None: raise ValueError('房间不存在')
            self.waiting(room)
            if room['players'][1] is not None: raise ValueError('房间已满')
            player = self.new_player(name)
            room['players'][1] = player
            self.unready(room)
            self.save()
            return dict(code=code, token=player['token'])

    def configure(self, code, token, value):
        with self.lock:
            room, index = self.resolve(code, token)
            self.waiting(room)
            if not isinstance(value, dict) or set(value) != {'model', 'race', 'prompt'}:
                raise ValueError('玩家配置需要模型、种族和战术提示词')
            catalog = {m['id']: m for m in self.player_catalog(room, index)['models']}
            if not isinstance(value['model'], str) or value['model'] not in catalog or not catalog[value['model']]['available']:
                raise ValueError('请选择主机已配置的模型')
            if value['race'] not in RACES: raise ValueError('未知种族')
            if not isinstance(value['prompt'], str) or not value['prompt'].strip() or len(value['prompt']) > MAX_PLAYER_PROMPT:
                raise ValueError('战术提示词须为 1～%d 字符' % MAX_PLAYER_PROMPT)
            room['players'][index].update(copy.deepcopy(value))
            self.unready(room)
            self.save()

    def settings(self, code, token, value):
        with self.lock:
            room, index = self.resolve(code, token)
            self.waiting(room)
            if index != 0: raise PermissionError('只有房主可修改房间设置')
            room['settings'] = room_settings(value)
            self.unready(room)
            self.save()

    def player_catalog(self, room, index):
        return self.catalog()

    def ready(self, code, token, revision, ready):
        with self.lock:
            room, index = self.resolve(code, token)
            self.waiting(room)
            if type(ready) is not bool: raise ValueError('准备状态无效')
            if revision != self.revision(room, index):
                raise ValueError('配置已变化，请重新检查提示词和房间设置')
            p = room['players'][index]
            if ready:
                model = next((m for m in self.player_catalog(room, index)['models'] if m['id'] == p['model']), None)
                if not model or not model['available']: raise ValueError('模型尚未配置')
            p['ready_revision'] = revision if ready else None
            self.save()

    def launch_config(self, room):
        return validate_config(dict(interface='lan', room_code=room['code'],
            players=[{k:p[k] for k in ('model', 'race', 'prompt')} for p in room['players']],
            **room['settings'], reasoning='high', tactics=False, live_skills=False), self.catalog())

    def start(self, code, token):
        with self.lock:
            room, index = self.resolve(code, token)
            self.waiting(room)
            if index != 0: raise PermissionError('只有房主可以开始对局')
            if not all(p and self.clock() - p['seen'] <= ONLINE_SECONDS and
                       p['ready_revision'] == self.revision(room, i) for i, p in enumerate(room['players'])):
                raise ValueError('双方须在线并确认最新配置后准备')
            config = self.launch_config(room)
            room['state'] = 'starting'
            room['confirmed'] = [dict(player=i + 1, revision=self.revision(room, i),
                                      prompt=p['prompt'], model=p['model'], race=p['race'])
                                  for i, p in enumerate(room['players'])]
            # Persist launch intent before spawning: a restart cannot launch it twice.
            self.save()
            confirmation = dict(code=code, players=room['confirmed'], settings=room['settings'],
                                rules=prompt_rules(room['settings']['combat_micro']))
            write_json_atomic(self.root / ('confirmation-' + code + '.json'), confirmation)
            try:
                result = self.matches.start(config)
            except Exception:
                room['state'] = 'waiting'
                self.unready(room)
                self.save()
                raise
            room.update(state='battle', run=result['run'])
            self.save()
            write_json_atomic(Path(result['run']) / 'room-confirmation.json', confirmation)

    def match_state(self, room):
        if not room['run']: return None
        status = self.matches.status()
        if status.get('run') != room['run']:
            return dict(active=False, state='archived', label='对局已归档')
        if not status['active'] and room['state'] == 'battle':
            room['state'] = 'finished'
            self.save()
        return status

    def journal(self, room, index):
        if not room['run']: return None
        journals = sorted((Path(room['run']) / 'feedback').glob('p%d-*.jsonl' % (index + 1)))
        if len(journals) > 1: raise ValueError('该玩家存在多个日志，停止投递以避免错发')
        return journals[0] if journals else None

    def battle_view(self, room, index, status):
        if status is None: return None
        result = {k:status[k] for k in ('active', 'state', 'label', 'error', 'outcome', 'stop_requested') if k in status}
        result['world'] = (status.get('worlds') or [])[index:index + 1]
        result['commander'] = (status.get('players') or [])[index:index + 1]
        result.update(decisions=[], chat=dict(accepting=False, reason='等待本方指挥通道', messages=[]))
        journal = self.journal(room, index)
        if journal:
            try:
                chat = read_status(mailbox_path(journal))
                result['chat'] = {k:chat[k] for k in ('accepting', 'reason', 'messages', 'updated_at')}
                if not status['active'] or status.get('stop_requested') or self.clock() - chat['updated_at'] > 15:
                    result['chat'].update(accepting=False, reason='对局未运行或本方指挥通道已失联')
            except FileNotFoundError: pass
            # Read only this player's public answers. Never export queued prompts or raw logs.
            with journal.open('rb') as stream:
                offset = max(0, journal.stat().st_size - 256000)
                stream.seek(offset)
                if offset: stream.readline()
                for line in stream:
                    try: row = json.loads(line)
                    except (ValueError, UnicodeDecodeError): continue
                    if row.get('record') != 'request_finished': continue
                    try: plan = json.loads(row.get('response') or '{}')
                    except (ValueError, TypeError): plan = {}
                    if not isinstance(plan, dict): plan = {}
                    result['decisions'].append(dict(request_id=row.get('request_id'),
                        reason=plan.get('reason', ''), tasks=plan.get('tasks', []),
                        error=row.get('error'), latency_s=row.get('latency_s')))
            result['decisions'] = result['decisions'][-30:]
        return result

    def view(self, code, token):
        with self.lock:
            room, index = self.resolve(code, token)
            status = self.match_state(room)
            p = room['players'][index]
            return dict(room=self.summary(room), seat=index + 1, own={k:p[k] for k in ('model', 'race', 'prompt')},
                        revision=self.revision(room, index), rules=prompt_rules(room['settings']['combat_micro']),
                        battle=self.battle_view(room, index, status))

    def chat(self, code, token, text):
        with self.lock:
            room, index = self.resolve(code, token)
            status = self.match_state(room)
            if room['state'] != 'battle' or not status or not status['active'] or status.get('stop_requested'):
                raise ValueError('当前没有可接收建议的活动对局')
            journal = self.journal(room, index)
            if journal is None: raise ValueError('本方指挥通道尚未就绪')
            try:
                return submit_advice(mailbox_path(journal), text, player=index + 1)
            except FileNotFoundError:
                raise ValueError('本方指挥通道尚未就绪') from None

    def stop(self, code, token):
        with self.lock:
            room, index = self.resolve(code, token)
            if index != 0: raise PermissionError('只有房主可结束整场对局')
            status = self.match_state(room)
            if not status or not status['active']: raise ValueError('没有本房间的活动对局')
            self.matches.stop()

    def leave(self, code, token):
        with self.lock:
            room, index = self.resolve(code, token)
            if room['state'] in ('starting', 'battle'):
                raise ValueError('对战中关闭页面不会停止游戏；需要停止整场时由房主结束对局')
            if index == 0: room['state'] = 'closed'
            else: room['players'][index] = None
            self.unready(room)
            self.save()
