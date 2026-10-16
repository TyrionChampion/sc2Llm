"""LAN coordinator: no SC2 processes, model credentials or enemy observations here."""
import copy
import hashlib
import ipaddress
from pathlib import Path
import secrets
import time
import uuid

from arena.game_speed import map_for_speed
from arena.lan_room import RoomManager, ONLINE_SECONDS, digest, prompt_rules
from arena.lifecycle import write_json_atomic
from arena.operator_advice import MAX_PENDING, MAX_TEXT

PROTOCOL_VERSION = 2
ACTIVE_PHASES = {'preparing', 'creating', 'joining', 'playing', 'stopping'}


class DistributedRoomManager(RoomManager):
    def __init__(self, root, map_provider=None, port_base=15000, clock=time.time):
        self.map_provider = map_provider or (lambda speed:map_for_speed('Acropolis', speed).read_bytes())
        self.port_base = port_base
        self.matches_root = Path(root).parent / 'native-matches'
        self.matches_root.mkdir(parents=True, exist_ok=True)
        super().__init__(root, matches=None, catalog=lambda:dict(models=[], notices=[
            '请先配对本机客户端；模型与凭据由本方电脑提供。'], mode='distributed'), clock=clock)

    def new_player(self, name):
        return dict(super().new_player(name), model='', agent=None, chat=[])

    def player_catalog(self, room, index):
        agent = room['players'][index].get('agent')
        return dict(models=agent['models'] if agent else [], notices=[])

    def summary(self, room):
        result = super().summary(room)
        for i, p in enumerate(room['players']):
            if not p: continue
            a = p.get('agent') or {}
            e = a.get('engine') or {}
            result['players'][i]['native'] = dict(connected=self.agent_online(a), platform=a.get('platform'),
                game_version=e.get('game_version'), base_build=e.get('base_build'))
        return result

    def agent_online(self, agent):
        return bool(agent and self.clock() - agent['seen'] <= ONLINE_SECONDS)

    def ticket(self, code, token):
        with self.lock:
            room, index = self.resolve(code, token)
            self.waiting(room)
            ticket = 'pair_' + secrets.token_urlsafe(12)
            expires = self.clock() + 120
            room['players'][index]['pairing'] = dict(hash=digest(ticket), expires=expires)
            self.save()
            return dict(pairing_code=ticket, expires_at=expires)

    def pair(self, code, ticket, client_id, lan_ip, platform, models):
        with self.lock:
            room = self.rooms.get(code)
            if room is None: raise ValueError('房间不存在')
            self.waiting(room)
            if not isinstance(ticket, str): raise PermissionError('配对码无效')
            index = next((i for i, p in enumerate(room['players']) if p and
                p.get('pairing', {}).get('expires', 0) >= self.clock() and
                secrets.compare_digest(p['pairing']['hash'], digest(ticket))), None)
            if index is None: raise PermissionError('配对码已使用、过期或无效，请在网页重新获取')
            try: address = ipaddress.ip_address(lan_ip)
            except ValueError: raise ValueError('客户端需要有效的局域网 IPv4 地址') from None
            if address.version != 4 or not (address.is_private or address.is_loopback) or address.is_unspecified or address.is_multicast:
                raise ValueError('客户端必须使用局域网 IPv4 地址')
            if platform not in ('Windows', 'Linux'): raise ValueError('客户端仅支持 Windows/Linux')
            if not isinstance(client_id, str) or len(client_id) != 32 or any(c not in '0123456789abcdef' for c in client_id):
                raise ValueError('客户端 ID 无效')
            if not isinstance(models, list) or not 0 < len(models) <= 300: raise ValueError('客户端模型目录无效')
            clean = []
            for m in models:
                if (not isinstance(m, dict) or set(m) != {'id', 'name', 'available', 'notice'} or
                    not isinstance(m['id'], str) or not 1 <= len(m['id']) <= 200 or '/' not in m['id'] or
                    not isinstance(m['name'], str) or len(m['name']) > 200 or
                    not isinstance(m['notice'], str) or len(m['notice']) > 1000 or type(m['available']) is not bool):
                    raise ValueError('客户端模型目录格式无效；不得包含密钥')
                clean.append(copy.deepcopy(m))
            p = room['players'][index]
            old = p.get('agent')
            if self.agent_online(old) and old['client_id'] != client_id:
                raise ValueError('本方已有在线客户端，请先关闭旧客户端')
            p.pop('pairing', None)
            agent_token = secrets.token_urlsafe(32)
            p['agent'] = dict(token=agent_token, client_id=client_id, lan_ip=str(address),
                platform=platform, models=clean, seen=self.clock(), phase='idle', seq=-1, public={})
            if not any(m['id'] == p['model'] and m['available'] for m in clean):
                p['model'] = next((m['id'] for m in clean if m['available']), '')
            self.unready(room)
            self.save()
            return dict(agent_token=agent_token, seat=index + 1, code=code, protocol=PROTOCOL_VERSION)

    def resolve_agent(self, code, token):
        room = self.rooms.get(code)
        if room is None: raise ValueError('房间不存在')
        for i, p in enumerate(room['players']):
            a = p.get('agent') if p else None
            if a and isinstance(token, str) and token.isascii() and secrets.compare_digest(a['token'], token):
                return room, i, a
        raise PermissionError('本地客户端身份无效，不能使用网页身份替代')

    def ready(self, code, token, revision, ready):
        with self.lock:
            room, index = self.resolve(code, token)
            if ready and not self.agent_online(room['players'][index].get('agent')):
                raise ValueError('请先连接本方本地客户端')
            return super().ready(code, token, revision, ready)

    def start(self, code, token):
        with self.lock:
            room, index = self.resolve(code, token)
            self.waiting(room)
            if index != 0: raise PermissionError('只有房主可以开始对局')
            for previous in self.rooms.values():
                self.advance(previous)
            if any(r.get('phase') in ACTIVE_PHASES for r in self.rooms.values()):
                raise ValueError('协调器已有活动对局，请先结束该场')
            if not all(p and p['ready_revision'] == self.revision(room, i) and
                       self.clock() - p['seen'] <= ONLINE_SECONDS and self.agent_online(p.get('agent'))
                       for i, p in enumerate(room['players'])):
                raise ValueError('双方网页和本地客户端须在线并确认最新配置')
            for i, p in enumerate(room['players']):
                if not any(m['id'] == p['model'] and m['available'] for m in self.player_catalog(room, i)['models']):
                    raise ValueError('所选模型不在本方客户端可用目录中')
            data = self.map_provider(room['settings']['speed'])
            if not isinstance(data, bytes) or not 0 < len(data) <= 32 * 1024 * 1024:
                raise ValueError('地图数据缺失或超过 32 MiB')
            session = uuid.uuid4().hex
            run = self.matches_root / session
            run.mkdir()
            (run / 'map.SC2Map').write_bytes(data)
            room.update(state='battle', phase='preparing', session=session, run=str(run),
                phase_deadline=self.clock() + 180, error='', stop_requested=False,
                manifest=dict(protocol=PROTOCOL_VERSION, session=session, code=code,
                    host_ip=room['players'][0]['agent']['lan_ip'], ports=list(range(self.port_base, self.port_base + 4)),
                    settings=copy.deepcopy(room['settings']), map_sha256=hashlib.sha256(data).hexdigest(),
                    map_bytes=len(data), map_name='Acropolis',
                    rules_sha256=digest(prompt_rules(room['settings']['combat_micro']))))
            for p in room['players']:
                p['agent'].update(phase='idle', engine=None, public={}, seq=-1)
                p['chat'] = []
            confirmation = dict(code=code, manifest=room['manifest'], rules=prompt_rules(room['settings']['combat_micro']),
                players=[{k:p[k] for k in ('name', 'model', 'race', 'prompt')} for p in room['players']])
            write_json_atomic(run / 'room-confirmation.json', confirmation)
            self.save()

    def fail(self, room, message):
        room.update(state='finished', phase='failed', error=message, stop_requested=True)
        for p in room['players']:
            for m in p['chat']:
                if m['state'] in ('queued', 'sent', 'retry'): m['state'] = 'closed'
        self.save()

    def advance(self, room):
        phase = room.get('phase')
        if phase not in ACTIVE_PHASES: return
        agents = [p['agent'] for p in room['players']]
        if any(not self.agent_online(a) for a in agents):
            self.fail(room, '本地客户端心跳已失联；停止协调，不根据失联判胜')
            return
        if phase != 'playing' and self.clock() > room['phase_deadline']:
            self.fail(room, '本地游戏启动/联机超时；请检查版本、LAN 端口和客户端日志')
            return
        if phase == 'preparing' and all(a['phase'] == 'engine_ready' for a in agents):
            engines = [a['engine'] for a in agents]
            if (engines[0]['base_build'], engines[0]['data_version']) != (engines[1]['base_build'], engines[1]['data_version']):
                self.fail(room, '双方 SC2 Base build / data_version 不一致，请安装兼容引擎版本')
                return
            room['manifest']['engine'] = engines[0]
            room.update(phase='creating', phase_deadline=self.clock() + 180)
            self.save()
        elif phase == 'creating' and agents[0]['phase'] == 'created':
            room.update(phase='joining', phase_deadline=self.clock() + 180)
            self.save()
        elif phase == 'joining' and all(a['phase'] == 'joined' for a in agents):
            ids = [a['player_id'] for a in agents]
            if set(ids) != {1, 2}:
                self.fail(room, '双方没有加入同一场双人对局，拒绝开始模型控制')
                return
            room.update(phase='playing', play_started_at=self.clock())
            self.save()
        elif phase == 'playing' and self.clock() - room['play_started_at'] > room['settings']['minutes'] * 60:
            room.update(phase='stopping', stop_requested=True, phase_deadline=self.clock() + 30)
            self.save()
        elif phase in ('playing', 'stopping') and all(a['phase'] in ('finished', 'stopped') for a in agents):
            room.update(state='finished', phase='finished')
            self.save()

    def agent_poll(self, code, token, report):
        with self.lock:
            room, index, agent = self.resolve_agent(code, token)
            if not isinstance(report, dict) or set(report) - {'seq', 'session', 'phase', 'engine', 'player_id', 'public', 'error'}:
                raise ValueError('客户端状态格式无效')
            if type(report.get('seq')) is not int or report['seq'] < 0: raise ValueError('客户端序号无效')
            agent['seen'] = self.clock()
            if room.get('session') and report.get('session') == room['session'] and report['seq'] > agent['seq']:
                phase = report.get('phase', agent['phase'])
                allowed = {'idle', 'engine_ready', 'created', 'joined', 'running', 'finished', 'stopped', 'failed'}
                if phase not in allowed: raise ValueError('未知客户端阶段')
                if phase == 'engine_ready':
                    e = report.get('engine')
                    if (not isinstance(e, dict) or set(e) != {'base_build', 'data_version', 'game_version', 'map_sha256', 'rules_sha256'}
                        or type(e['base_build']) is not int or e['base_build'] <= 0
                        or not all(isinstance(e[k], str) and 0 < len(e[k]) <= 128 for k in e if k != 'base_build')):
                        raise ValueError('引擎版本证明无效')
                    if e['map_sha256'] != room['manifest']['map_sha256'] or e['rules_sha256'] != room['manifest']['rules_sha256']:
                        self.fail(room, '客户端地图或工具契约与双方确认版本不一致')
                    agent['engine'] = e
                if phase == 'created' and index != 0: raise PermissionError('只有 P1 本地游戏可建房')
                if phase == 'joined':
                    if type(report.get('player_id')) is not int or report['player_id'] not in (1, 2): raise ValueError('引擎玩家 ID 无效')
                    agent['player_id'] = report['player_id']
                if phase == 'failed':
                    agent['error'] = str(report.get('error', ''))[:2000]
                    self.fail(room, 'P%d 本地客户端失败，请在本方页面查看详情；未推断胜负' % (index + 1))
                public = report.get('public', agent.get('public', {}))
                if not isinstance(public, dict) or set(public) - {'world', 'commander', 'decisions', 'messages', 'outcome', 'requests', 'error', 'chat_accepting', 'chat_reason'}:
                    raise ValueError('本方公开状态格式无效')
                agent.update(seq=report['seq'], phase=phase, public=copy.deepcopy(public))
                messages = public.get('messages', [])
                if not isinstance(messages, list): raise ValueError('建议状态无效')
                for m in messages:
                    original = next((x for x in room['players'][index]['chat'] if x['id'] == m.get('id')), None)
                    if original and m.get('state') in ('queued', 'sent', 'retry', 'received', 'closed', 'rejected') and original['state'] not in ('received', 'closed', 'rejected'):
                        original.update(state=m['state'], request_id=m.get('request_id'))
                self.save()
            self.advance(room)
            phase = room.get('phase')
            action = 'idle'
            if phase == 'preparing' and agent['phase'] == 'idle': action = 'prepare'
            elif phase == 'creating' and index == 0 and agent['phase'] == 'engine_ready': action = 'create'
            elif phase == 'joining' and agent['phase'] in ('engine_ready', 'created'): action = 'join'
            elif phase == 'playing' and agent['phase'] in ('joined', 'running'): action = 'play'
            elif phase in ('failed', 'stopping', 'finished') or room['state'] == 'closed': action = 'stop'
            result = dict(action=action, seat=index + 1, phase=phase, protocol=PROTOCOL_VERSION,
                          messages=[dict(m) for m in room['players'][index]['chat'] if m['state'] in ('queued', 'sent', 'retry')])
            if room.get('manifest'):
                result.update(manifest=room['manifest'], own={k:room['players'][index][k] for k in ('model', 'race', 'prompt', 'name')})
            return result

    def map_data(self, code, token):
        with self.lock:
            room, _, _ = self.resolve_agent(code, token)
            if not room.get('session'): raise ValueError('对局尚未准备地图')
            data = (Path(room['run']) / 'map.SC2Map').read_bytes()
            if hashlib.sha256(data).hexdigest() != room['manifest']['map_sha256']:
                self.fail(room, '保存的地图文件哈希不符')
                raise ValueError('地图文件已改变')
            return data

    def chat(self, code, token, text):
        with self.lock:
            room, index = self.resolve(code, token)
            self.advance(room)
            if room.get('phase') != 'playing': raise ValueError('等待双方原生游戏和 AI 指挥就绪后再发送建议')
            public = room['players'][index]['agent'].get('public', {})
            if public.get('chat_accepting') is False:
                raise ValueError(public.get('chat_reason') or '本方指挥已停止接收建议')
            if not isinstance(text, str) or not text.strip() or len(text.strip()) > MAX_TEXT:
                raise ValueError('建议须为 1～1000 字符')
            messages = room['players'][index]['chat']
            if sum(m['state'] in ('queued', 'sent', 'retry') for m in messages) >= MAX_PENDING:
                raise ValueError('待处理建议已满')
            m = dict(id=uuid.uuid4().hex, text=text.strip(), submitted_at=self.clock(), state='queued', request_id=None)
            messages.append(m)
            self.save()
            return dict(m)

    def stop(self, code, token):
        with self.lock:
            room, index = self.resolve(code, token)
            if index != 0: raise PermissionError('只有房主能结束整场对局')
            if room.get('phase') not in ACTIVE_PHASES: raise ValueError('没有活动对局')
            if room['phase'] == 'stopping': return
            room.update(phase='stopping', stop_requested=True, phase_deadline=self.clock() + 30)
            self.save()

    def match_state(self, room):
        self.advance(room)
        return None

    def battle_view(self, room, index, status):
        if not room.get('session'): return None
        phase = room['phase']
        agent = room['players'][index]['agent']
        public = agent.get('public', {})
        labels = dict(preparing='正在本机启动原生 SC2 并核对版本', creating='P1 原生游戏正在建房',
            joining='双方原生游戏正在加入同一场对局', playing='本机原生游戏对战中',
            stopping='正在请求双方保存回放并退出', finished='对局结束', failed='联机/客户端失败')
        return dict(active=phase in ACTIVE_PHASES, state=phase, label=labels.get(phase, phase),
            error=agent.get('error') or room.get('error') or public.get('error', ''), stop_requested=room.get('stop_requested', False),
            world=[public['world']] if public.get('world') else [],
            commander=[public['commander']] if public.get('commander') else [],
            decisions=public.get('decisions', []), outcome=public.get('outcome'),
            chat=dict(accepting=phase == 'playing' and self.agent_online(agent) and public.get('chat_accepting') is not False,
                reason=public.get('chat_reason', '') if phase == 'playing' else labels.get(phase, '等待本方游戏'),
                messages=room['players'][index]['chat'][-64:]))

    def view(self, code, token):
        with self.lock:
            result = super().view(code, token)
            room = self.rooms[code]
            agent = room['players'][result['seat'] - 1].get('agent')
            result.update(mode='distributed', catalog=self.player_catalog(room, result['seat'] - 1),
                client=dict(connected=self.agent_online(agent), platform=agent['platform'] if agent else None,
                            phase=agent['phase'] if agent else 'unpaired'))
            return result
