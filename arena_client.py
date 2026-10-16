"""Run this on each player's PC after obtaining a one-use pairing code in the room."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import platform
import shlex
import socket
from urllib.parse import urlsplit
import uuid

import requests
from arena.launch_config import ROOT, model_catalog
from arena.lan_client import LocalClient
from arena.lifecycle import write_json_atomic
from arena.local_lock import local_lock


def load_environment(path):
    if not path.exists(): return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line.startswith('export '): line = line[7:]
        if not line or line.startswith('#') or '=' not in line: continue
        name, value = line.split('=', 1)
        name = name.strip()
        if not name.replace('_', '').isalnum() or name[0].isdigit(): continue
        if name in os.environ: continue
        lexer = shlex.shlex(value, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = '#'
        os.environ[name] = ' '.join(lexer)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', required=True, help='http://房间服务器LAN-IP:8768')
    parser.add_argument('--room', required=True)
    parser.add_argument('--pair', required=True, help='网页本方席位的一次性配对码')
    parser.add_argument('--lan-ip', help='多网卡时指定本机可被对方访问的 IPv4')
    parser.add_argument('--engine', choices=('auto', 'windows', 'proton', 'linux-headless'), default='auto')
    parser.add_argument('--sc2-path', help='Windows 原生 SC2 安装目录；Linux headless 验证也可用')
    args = parser.parse_args(argv)
    url = urlsplit(args.server)
    if url.scheme != 'http' or not url.hostname or url.path not in ('', '/') or url.username or url.password or url.query or url.fragment:
        parser.error('--server 必须是无凭据的局域网 HTTP 地址')
    server_ip = socket.gethostbyname(url.hostname)
    address = ipaddress.ip_address(server_ip)
    if not (address.is_private or address.is_loopback) or address.is_unspecified or address.is_multicast:
        parser.error('此客户端仅连接局域网房间')
    if args.lan_ip:
        lan_ip = args.lan_ip
    else:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((server_ip, url.port or 80))
            lan_ip = sock.getsockname()[0]
    if platform.system() not in ('Windows', 'Linux'): parser.error('仅支持 Windows 或 Linux')
    os.environ['PYTHONUTF8'] = '1'
    load_environment(ROOT / '.env')
    if args.sc2_path: os.environ['SC2PATH'] = str(Path(args.sc2_path).expanduser().resolve())
    models = [{k:m[k] for k in ('id', 'name', 'available', 'notice')} for m in model_catalog()['models']]
    if not any(m['available'] for m in models): parser.error('本机没有已配置模型，请先设置 .env 或 OpenCode 登录')
    root = ROOT / 'client-runtime'
    root.mkdir(exist_ok=True)
    try:
        with local_lock(root / 'client.lock'):
            response = requests.post(args.server.rstrip('/') + '/api/client/pair', json=dict(code=args.room.strip().upper(),
                pairing_code=args.pair, client_id=uuid.uuid4().hex, lan_ip=lan_ip,
                platform=platform.system(), models=models), timeout=10)
            if response.status_code >= 400: raise ValueError(response.json().get('error', '配对失败'))
            identity = response.json()
            session_path = root / ('player-' + uuid.uuid4().hex + '.json')
            write_json_atomic(session_path, identity)
            session_path.chmod(0o600)
            print('已配对 P%d。本机 %s / %s；请回网页选择模型、确认提示词并准备。' % (identity['seat'], platform.system(), lan_ip), flush=True)
            client = LocalClient(args.server, identity['code'], identity['agent_token'], root / 'matches', engine=args.engine)
            try: client.run()
            except KeyboardInterrupt: client.close()
    except (OSError, ValueError) as error: parser.exit(1, str(error) + '\n')


if __name__ == '__main__': main()
