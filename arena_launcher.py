"""Loopback-only SC2 match launcher. ./run_sc2.sh .venv311/bin/python arena_launcher.py"""
import argparse
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

from arena.launch_config import ROOT, model_catalog, validate_config, safe_text
from arena.lifecycle import write_json_atomic, read_match_status, match_status_label

RUNTIME = ROOT / 'launcher-runtime'


def process_birth(pid):
    try:
        text = Path('/proc/%d/stat' % pid).read_text()
        fields = text[text.rindex(')') + 2:].split()
        if fields[0] == 'Z': return None
        return fields[19]
    except (OSError, ValueError, IndexError):
        return None


def native_busy():
    lock = ROOT / 'native-runtime/native-match.lock'
    lock.parent.mkdir(exist_ok=True)
    with lock.open('a') as stream:
        try: fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return '已有原生对局占用引擎；请先结束那一局。'
    # An orphaned native process can survive its controller and release the lock.
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit(): continue
        try: command = (proc / 'cmdline').read_bytes()
        except OSError: continue
        if any(arg.endswith(b'SC2_x64.exe') for arg in command.split(b'\0')) and str(ROOT / 'native-runtime').encode() in command:
            return '发现本项目遗留的 SC2 游戏进程 %s，请先关闭该窗口。' % proc.name
    return None


class MatchManager:
    def __init__(self, root=RUNTIME, spawn=None, busy=native_busy):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
        self.mutex = threading.RLock()
        self.spawn = spawn or subprocess.Popen
        self.busy = busy
        self.child = None
        self.current = None
        pointer = self.root / 'current.json'
        if pointer.exists():
            self.current = json.loads(pointer.read_text())
            run = Path(self.current['run']).resolve()
            if run.parent != self.root.resolve(): raise ValueError('当前对局路径不在启动器目录')

    def alive(self):
        if not self.current: return False
        if self.child is not None and self.child.poll() is not None: return False
        return process_birth(self.current['pid']) == self.current.get('birth') and self.current.get('birth') is not None

    def start(self, value):
        with self.mutex:
            if self.alive(): raise ValueError('当前对局尚未停止，不能重复开局')
            problem = self.busy()
            if problem: raise ValueError(problem)
            config = validate_config(value)
            run = Path(tempfile.mkdtemp(prefix='match-', dir=self.root))
            write_json_atomic(run / 'config.json', config)
            write_json_atomic(run / 'run.json', dict(status='queued', config=config))
            command = [str(ROOT / 'run_sc2.sh'), sys.executable,
                       '-u', '-m', 'arena.launch_match', '--run', str(run)]
            child = None
            try:
                with (run / 'launcher.log').open('w') as stream:
                    child = self.spawn(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                        stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
                self.child = child
                self.current = dict(run=str(run), pid=child.pid, birth=process_birth(child.pid),
                                    started_at=time.time(), config=config, stop_requested=False)
                write_json_atomic(self.root / 'current.json', self.current)
            except Exception as error:
                if child is not None and child.poll() is None:
                    child.send_signal(signal.SIGINT)
                write_json_atomic(run / 'run.json', dict(status='failed', error=str(error), config=config))
                raise
            return self.status()

    def stop(self):
        with self.mutex:
            if not self.alive(): raise ValueError('没有本界面启动的活动对局')
            if not self.current.get('stop_requested'):
                # Signal only our worker, not unrelated Proton/Battle.net processes.
                os.kill(self.current['pid'], signal.SIGINT)
                self.current['stop_requested'] = True
                write_json_atomic(self.root / 'current.json', self.current)
            return self.status()

    def status(self):
        with self.mutex:
            if not self.current: return dict(active=False, state='idle', label='等待开局', log='')
            run = Path(self.current['run'])
            alive = self.alive()
            meta = json.loads((run / 'run.json').read_text())
            state = meta.get('status', 'unknown')
            labels = dict(queued='正在启动控制进程', preparing='正在验收已有技能',
                starting_model_service='正在启动 OpenCode 模型服务', prepared='技能准备完成',
                starting_native_game='正在打开原生游戏', finished='对局结束',
                failed='启动 / 对局失败', interrupted='对局已停止')
            result = dict(active=alive, state=state, label=labels.get(state, state),
                run=str(run), config=self.current['config'], skills=meta.get('skills', []),
                error=safe_text(meta.get('error', '')), stop_requested=self.current.get('stop_requested', False))
            if (run / 'match.json').exists():
                data, notice = read_match_status(run)
                result.update(label=match_status_label(data.get('outcome')), notice=safe_text(notice),
                    outcome=data.get('outcome'), replay=data.get('replay'), cleanup=data.get('cleanup_status'))
                sample = (data.get('samples') or [{}])[-1]
                result['worlds'] = sample.get('worlds', data.get('worlds', []))
                result['timing'] = sample.get('timing', data.get('timing', {}))
                result['players'] = [dict(decisions=c.get('decisions'), errors=c.get('errors'),
                    thinking=c.get('thinking'), engine_errors=c.get('engine_errors'),
                    model_decisions_available=c.get('model_decisions_available', True),
                    model_credentials_failed=c.get('model_credentials_failed'),
                    model_stopped_reason=('账户级失败：余额/密钥/权限，已停止请求该模型；之后不会再有新决策'
                                          if c.get('model_credentials_failed') else None))
                    for c in sample.get('commanders', []) if c]
            if not alive and state not in ('finished', 'failed', 'interrupted'):
                result.update(state='lost', label='控制进程已退出，状态未完成收尾',
                    error='请查看下方日志；不能据旧状态认为游戏仍在运行。')
            if alive and self.current.get('stop_requested'): result['label'] = '正在停止并保存录像…'
            log = run / 'launcher.log'
            if log.exists():
                with log.open('rb') as stream:
                    stream.seek(max(0, log.stat().st_size - 16000))
                    result['log'] = safe_text(stream.read().decode('utf-8', 'replace'))
            else: result['log'] = ''
            return result


class LauncherServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, manager):
        super().__init__(address, Handler)
        self.manager = manager
        self.token = secrets.token_urlsafe(32)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def reply(self, code, value, content_type='application/json; charset=utf-8'):
        data = value.encode() if isinstance(value, str) else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers(); self.wfile.write(data)

    def valid_host(self):
        port = self.server.server_port
        return self.headers.get('Host') in ('127.0.0.1:%d' % port, 'localhost:%d' % port)

    def do_GET(self):
        if not self.valid_host(): return self.reply(403, {'error': '仅允许本机地址'})
        path = urlsplit(self.path).path
        try:
            if path == '/':
                page = (ROOT / 'arena/launcher.html').read_text().replace('__TOKEN__', self.server.token)
                return self.reply(200, page, 'text/html; charset=utf-8')
            if path == '/api/catalog': return self.reply(200, model_catalog())
            if path == '/api/status': return self.reply(200, self.server.manager.status())
            return self.reply(404, {'error': '未找到页面'})
        except Exception as error: return self.reply(500, {'error': safe_text(type(error).__name__ + ': ' + str(error))})

    def do_POST(self):
        origin = self.headers.get('Origin')
        allowed = ('http://127.0.0.1:%d' % self.server.server_port, 'http://localhost:%d' % self.server.server_port)
        if not self.valid_host() or (origin is not None and origin not in allowed) or self.headers.get('X-Arena-Token') != self.server.token:
            return self.reply(403, {'error': '本机启动令牌或来源无效，请刷新页面'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 8192: raise ValueError('请求长度不合法')
            body = json.loads(self.rfile.read(length))
            path = urlsplit(self.path).path
            if path == '/api/start': result = self.server.manager.start(body)
            elif path == '/api/stop' and body == {}: result = self.server.manager.stop()
            else: return self.reply(404, {'error': '未知操作'})
            return self.reply(200, result)
        except (ValueError, TypeError, KeyError) as error: return self.reply(400, {'error': safe_text(str(error))})
        except Exception as error: return self.reply(500, {'error': safe_text(type(error).__name__ + ': ' + str(error))})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--open', action='store_true', help='open the local interface in the default browser')
    args = parser.parse_args()
    RUNTIME.mkdir(exist_ok=True)
    with (RUNTIME / 'ui.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            url = json.loads((RUNTIME / 'ui.json').read_text())['url']
            print('启动界面已运行：' + url, flush=True)
            if args.open:
                import webbrowser
                if not webbrowser.open(url): print('浏览器未自动打开，请手动访问上述地址。', flush=True)
            return
        server = LauncherServer(('127.0.0.1', args.port), MatchManager())
        url = 'http://127.0.0.1:%d/' % server.server_port
        write_json_atomic(RUNTIME / 'ui.json', dict(url=url, pid=os.getpid()))
        print('星际 AI 对战启动界面：' + url, flush=True)
        if args.open:
            import webbrowser
            if not webbrowser.open(url): print('浏览器未自动打开，请手动访问上述地址。', flush=True)
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally: server.server_close()


if __name__ == '__main__': main()
