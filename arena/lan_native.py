"""One local rendered SC2 process, one controller, one player's own observation."""
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

from absl import flags
from pysc2.lib import remote_controller
from s2clientprotocol import sc2api_pb2 as pb, common_pb2 as common, error_pb2
import portpicker

from arena.game_speed import LOOPS_PER_SECOND
from arena.raw_engine import RawArena, GameClock


def create_request(map_path, names):
    request = pb.RequestCreateGame(realtime=True, disable_fog=False, random_seed=7,
                                   local_map=pb.LocalMap(map_path=map_path))
    for name in names: request.player_setup.add(type=pb.Participant, player_name=name)
    return request


def join_request(manifest, race, name):
    ports = manifest['ports']
    if len(ports) != 4 or len(set(ports)) != 4 or any(type(p) is not int or not 1024 <= p <= 65535 for p in ports):
        raise ValueError('双人对局端口配置无效')
    request = pb.RequestJoinGame(race={'terran':common.Terran, 'zerg':common.Zerg, 'protoss':common.Protoss}[race],
        player_name=name, host_ip=manifest['host_ip'], shared_port=0,
        options=pb.InterfaceOptions(raw=True, score=True, raw_affects_selection=False))
    request.server_ports.game_port, request.server_ports.base_port = ports[:2]
    request.client_ports.add(game_port=ports[2], base_port=ports[3])
    return request


class LocalSC2:
    def __init__(self, run, engine='auto'):
        self.run, self.engine = Path(run), engine
        self.process = self.output = self.controller = self.sc_process = None
        self.port = None
        self.closed = False

    @property
    def running(self):
        return self.sc_process.running if self.sc_process else self.process is not None and self.process.poll() is None

    def start(self):
        if not flags.FLAGS.is_parsed(): flags.FLAGS(['arena_client'])
        engine = self.engine
        if engine == 'auto': engine = 'windows' if platform.system() == 'Windows' else 'proton'
        clean = {k:v for k,v in os.environ.items() if 'api_key' not in k.lower() and not k.upper().endswith('_TOKEN')}
        if engine in ('windows', 'linux-headless'):
            from pysc2 import run_configs
            if engine == 'windows' and platform.system() != 'Windows': raise ValueError('Windows 引擎只能在 Windows 上启动')
            config = run_configs.get()
            config.env = clean
            self.sc_process = config.start(want_rgb=engine == 'windows', host='127.0.0.1',
                window_size=(1600, 900), timeout_seconds=150)
            self.controller = self.sc_process.controller
        elif engine == 'proton':
            if platform.system() != 'Linux': raise ValueError('Proton 启动器仅支持 Linux')
            from arena.native_runtime import prepare, GAME, ROOT
            prepare()
            versions = [p for p in (GAME / 'Versions').glob('Base*') if p.name[4:].isdigit() and (p / 'SC2_x64.exe').is_file()]
            if not versions: raise ValueError('Proton 游戏目录没有可用的 SC2_x64.exe')
            exe = max(versions, key=lambda p:int(p.name[4:])) / 'SC2_x64.exe'
            self.port = portpicker.pick_unused_port()
            win = lambda p:'Z:' + str(p).replace('/', '\\')
            temp = self.run / 'sc2-temp'
            temp.mkdir()
            self.output = (self.run / 'sc2-process.log').open('w', encoding='utf-8')
            command = [sys.executable, str(ROOT / 'arena/native_runtime.py'), win(exe),
                '-listen', '127.0.0.1', '-port', str(self.port), '-dataDir', win(GAME) + '\\',
                '-tempDir', win(temp) + '\\', '-displayMode', '0', '-windowwidth', '1600', '-windowheight', '900']
            self.process = subprocess.Popen(command, env=clean, stdout=self.output, stderr=subprocess.STDOUT)
            self.controller = remote_controller.RemoteController('127.0.0.1', self.port, self, timeout_seconds=150)
        else: raise ValueError('未知本机引擎启动方式')
        ping = self.controller.ping()
        return dict(base_build=ping.base_build, data_version=ping.data_version, game_version=ping.game_version)

    def save_map(self, data, manifest):
        if len(data) != manifest['map_bytes'] or hashlib.sha256(data).hexdigest() != manifest['map_sha256']:
            raise ValueError('下载地图大小或 SHA256 不符，拒绝建房/加入')
        self.map_path = 'Maps/Arena/LAN-' + manifest['map_sha256'][:16] + '.SC2Map'
        self.controller.save_map(self.map_path, data)

    def create(self):
        self.controller.create_game(create_request(self.map_path, ('P1', 'P2')))

    def join(self, manifest, race, name):
        response = self.controller.join_game(join_request(manifest, race, name))
        return response.player_id

    def terminate(self):
        # Only the game process owned by this client, never another participant.
        if self.sc_process:
            proc = getattr(self.sc_process, '_proc', None)
            if proc is not None and proc.poll() is None: proc.terminate()
        elif self.process is not None and self.process.poll() is None:
            self.process.terminate()

    def close(self):
        if self.closed: return
        self.closed = True
        if self.sc_process:
            self.sc_process.close()
        else:
            if self.controller:
                try: self.controller.quit()
                except Exception: pass
                self.controller.close()
            if self.process:
                try: self.process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try: self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired: self.process.kill()
        if self.output: self.output.close()
        if self.port is not None: portpicker.return_port(self.port)


class LocalPlayerArena(RawArena):
    """Backend index0 is always LOCAL, including the room's P2 and engine player2."""
    def __init__(self, controller, race, game_speed, player_id):
        self.controllers = [controller]
        self.races = [race]
        self.game_speed, self.player_id = game_speed, player_id
        self._terminal_result = self._pending_surrender = self._surrender_replay = None
        self.surrender_report = None
        self.action_errors = 0
        self.last = [controller.observe()]
        self.info = controller.game_info()
        self.size = (self.info.start_raw.map_size.x, self.info.start_raw.map_size.y)
        self.configure_tech_tree(controller.data_raw(), controller.ping().game_version)
        deadline = time.monotonic() + 45
        while self.last[0].observation.game_loop < 112 and not self.last[0].player_result:
            if time.monotonic() > deadline: raise RuntimeError('原生 realtime 仿真未启动')
            time.sleep(.1)
            self.last = [controller.observe()]
        if self.last[0].observation.player_common.player_id != player_id:
            raise RuntimeError('本方观察的引擎玩家 ID 与加入结果不一致')
        self.initial = self.snapshots()
        self.homes = [next(u.pos for u in self.initial[0].my_units if u.type_id in (18, 132, 130, 59, 86, 100, 101))]
        self.configure_start_locations([self.info])
        self.clock = GameClock(self.initial[0].game_loop, LOOPS_PER_SECOND[game_speed])

    def proto(self, index):
        if index != 0: raise ValueError('本地客户端不能读取另一名玩家')
        return self.last[0]

    @property
    def ended(self): return self._terminal_result is not None or bool(self.last[0].player_result)

    def step(self, batches):
        if len(batches) != 1: raise ValueError('本地客户端只接受本方动作')
        if self.ended: raise ValueError('对局已结束')
        if self.match_control_pending: return self._finish_local_surrender()
        before = self.snapshots()
        failures = []
        sent = time.time()
        if batches[0]:
            response = self.controllers[0].actions(pb.RequestAction(actions=batches[0]))
            if response is not None:
                if len(response.result) != len(batches[0]): raise RuntimeError('原生动作回执数量不匹配')
                failures = [(i, code) for i, code in enumerate(response.result) if code != error_pb2.Success]
        time.sleep(.1)
        self.last = [self.controllers[0].observe()]
        world = self.snapshots()[0]
        errors = list(world.errors)
        for n, code in failures:
            raw = batches[0][n].action_raw.unit_command
            for tag in raw.unit_tags:
                e = (tag, raw.ability_id, code)
                if e not in errors: errors.append(e)
        world = replace(world, errors=tuple(errors))
        self.action_errors += len(errors)
        self.clock.update(world.game_loop)
        return self.attach_action_feedback(before, [world], batches, [failures], [sent])

    def _finish_local_surrender(self):
        entry = dict(self._pending_surrender, player=self.player_id)
        world = self.snapshots()[0]
        try:
            replay = self.controllers[0].save_replay()
            if not replay: raise RuntimeError('投降前回放为空')
        except Exception as error:
            self._pending_surrender = None
            return [replace(world, feedback=(dict(kind='surrender_failed', player=1,
                engine_player_id=self.player_id, error=str(error), leave_sent=False),))]
        self._surrender_replay = replay
        response = self.controllers[0].leave()
        if response is None: raise RuntimeError('投降结果不确定，不编造引擎胜负')
        self._terminal_result = {self.player_id:'Defeat'}
        self._pending_surrender = None
        self.surrender_report = dict(entry, result_source='acknowledged_concession_rule',
            state='completed', engine_results=None, replay_scope='before_leave')
        return [replace(world, feedback=(dict(kind='surrender_completed', **self.surrender_report),))]
