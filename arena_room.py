"""LAN room server: two browsers, independent model selection and next-turn chat."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import socket
from urllib.parse import parse_qs, urlsplit

from arena.launch_config import ROOT, safe_text
from arena.lan_room import RoomManager
from arena.local_lock import local_lock


def local_hosts():
    hosts = {'localhost', '127.0.0.1'}
    try:
        hosts.update(row[4][0] for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except socket.gaierror: pass
    # Enumerate assigned IPv4 addresses without making an external connection.
    import os
    if os.name != 'nt':
        import fcntl
        import struct
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for _, name in socket.if_nameindex():
                try:
                    hosts.add(socket.inet_ntoa(fcntl.ioctl(sock.fileno(), 0x8915,
                        struct.pack('256s', name[:15].encode()))[20:24]))
                except OSError: pass
    return hosts


class RoomServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, rooms, allowed_hosts=None, advertise_host=None):
        self.rooms = rooms
        self.allowed_hosts = set(allowed_hosts or local_hosts())
        self.advertise_host = advertise_host
        super().__init__(address, RoomHandler)


class RoomHandler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass  # Never log player tokens or chat text.

    def reply(self, code, value, html=False):
        binary = isinstance(value, bytes)
        body = value if binary else (value if html else json.dumps(value, ensure_ascii=False)).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/octet-stream' if binary else 'text/html; charset=utf-8' if html else 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; connect-src 'self'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        try:
            if len(self.headers.get_all('Host', [])) != 1: return False
            host = urlsplit('http://' + self.headers['Host'])
            return (host.hostname in self.server.allowed_hosts and host.port == self.server.server_port
                    and not host.username and not host.password and not host.path)
        except ValueError: return False

    def identity(self):
        code = parse_qs(urlsplit(self.path).query).get('code', [''])[0]
        auth = self.headers.get('Authorization', '')
        return code, auth[7:] if auth.startswith('Bearer ') else ''

    def do_GET(self):
        if not self.valid_host(): return self.reply(403, dict(error='请使用主机显示的局域网地址'))
        path = urlsplit(self.path).path
        try:
            if path == '/': return self.reply(200, (ROOT / 'arena/room.html').read_text(), html=True)
            if path == '/api/catalog':
                catalog = self.server.rooms.catalog()
                hosts = ([self.server.advertise_host] if self.server.advertise_host else
                         sorted(h for h in self.server.allowed_hosts if h not in ('localhost', '127.0.0.1')))
                return self.reply(200, dict(**catalog, lan_urls=['http://%s:%d/' % (h, self.server.server_port) for h in hosts]))
            if path == '/api/rooms': return self.reply(200, dict(rooms=self.server.rooms.list_rooms()))
            if path == '/api/room': return self.reply(200, self.server.rooms.view(*self.identity()))
            if path == '/api/client/map' and hasattr(self.server.rooms, 'map_data'):
                return self.reply(200, self.server.rooms.map_data(*self.identity()))
            return self.reply(404, dict(error='未找到页面'))
        except PermissionError as error: self.reply(403, dict(error=str(error)))
        except ValueError as error: self.reply(400, dict(error=str(error)))
        except Exception as error: self.reply(500, dict(error=safe_text(str(error))))

    def do_POST(self):
        origin = self.headers.get('Origin')
        if not self.valid_host() or (origin is not None and origin != 'http://' + self.headers.get('Host', '')):
            return self.reply(403, dict(error='请求来源无效'))
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            return self.reply(415, dict(error='请求必须是 JSON'))
        try:
            path = urlsplit(self.path).path
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= (1048576 if path == '/api/client/poll' else 128000 if path == '/api/client/pair' else 16000):
                raise ValueError('请求长度无效')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict): raise ValueError('请求必须是对象')
            manager = self.server.rooms
            code, token = self.identity()
            if path == '/api/client/pair' and set(body) == {'code', 'pairing_code', 'client_id', 'lan_ip', 'platform', 'models'} and hasattr(manager, 'pair'):
                return self.reply(200, manager.pair(body['code'], body['pairing_code'], body['client_id'], body['lan_ip'], body['platform'], body['models']))
            if path == '/api/client/poll' and hasattr(manager, 'agent_poll'):
                return self.reply(200, manager.agent_poll(code, token, body))
            if path == '/api/client-ticket' and not body and hasattr(manager, 'ticket'):
                return self.reply(200, manager.ticket(code, token))
            if path == '/api/create' and set(body) == {'name', 'settings'}:
                return self.reply(200, manager.create(body['name'], body['settings']))
            if path == '/api/join' and set(body) == {'code', 'name'}:
                return self.reply(200, manager.join(body['code'], body['name']))
            if path == '/api/player': manager.configure(code, token, body)
            elif path == '/api/settings': manager.settings(code, token, body)
            elif path == '/api/ready' and set(body) == {'revision', 'ready'}:
                manager.ready(code, token, body['revision'], body['ready'])
            elif path == '/api/start' and not body: manager.start(code, token)
            elif path == '/api/stop' and not body: manager.stop(code, token)
            elif path == '/api/chat' and set(body) == {'text'}:
                return self.reply(200, manager.chat(code, token, body['text']))
            elif path == '/api/leave' and not body:
                manager.leave(code, token)
                return self.reply(200, dict(left=True))
            else: return self.reply(404, dict(error='未知操作或参数'))
            return self.reply(200, manager.view(code, token))
        except PermissionError as error: self.reply(403, dict(error=str(error)))
        except (ValueError, TypeError, KeyError, UnicodeDecodeError) as error: self.reply(400, dict(error=str(error)))
        except Exception as error: self.reply(500, dict(error=safe_text(str(error))))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8768)
    parser.add_argument('--advertise-host', help='本机局域网 IPv4 地址，用于显示分享链接')
    parser.add_argument('--open', action='store_true')
    parser.add_argument('--central', action='store_true', help='兼容旧版：两方游戏均在服务器运行')
    parser.add_argument('--game-port-base', type=int, default=15000, help='双方 SC2 游戏网络使用的连续四个端口')
    args = parser.parse_args(argv)
    if not 1024 <= args.game_port_base <= 65532: parser.error('游戏端口起点须为 1024～65532')
    hosts = local_hosts()
    if args.advertise_host:
        try: address = ipaddress.ip_address(args.advertise_host)
        except ValueError: parser.error('--advertise-host 必须为本机 IPv4 地址')
        if address.version != 4 or str(address) not in hosts:
            parser.error('--advertise-host 必须是已分配给本机的 IPv4 地址')
    if args.host not in hosts | {'0.0.0.0'}: parser.error('--host 必须为本机 IPv4 地址或 0.0.0.0')
    root = ROOT / 'launcher-runtime/lan'
    root.mkdir(parents=True, exist_ok=True)
    with local_lock(root / 'server.lock'):
        if args.central:
            from arena_launcher import MatchManager
            rooms = RoomManager(root / 'rooms', MatchManager(root / 'matches'))
        else:
            from arena.distributed_room import DistributedRoomManager
            rooms = DistributedRoomManager(root / 'native-rooms', port_base=args.game_port_base)
        server = RoomServer((args.host, args.port), rooms, hosts, args.advertise_host)
        candidates = [args.advertise_host] if args.advertise_host else sorted(h for h in hosts if h != 'localhost' and not h.startswith('127.'))
        print('局域网 AI 房间（%s）：' % ('兼容集中式模式' if args.central else '双方各自启动本机原生游戏与 AI'), flush=True)
        for host in candidates: print('  http://%s:%d/' % (host, server.server_port), flush=True)
        url = 'http://127.0.0.1:%d/' % server.server_port
        print('本机访问：' + url, flush=True)
        if args.open:
            import webbrowser
            webbrowser.open(url if args.host in ('0.0.0.0', '127.0.0.1', 'localhost') else 'http://%s:%d/' % (args.host, server.server_port))
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally: server.server_close()


if __name__ == '__main__': main()
