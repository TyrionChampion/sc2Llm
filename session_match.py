"""Current interactive Codex session versus the official paid DeepSeek API.

All commands: ./run_sc2.sh .venv311/bin/python session_match.py ...
start --run session-runtime/<unique-name> [--seconds 5400]
observe --run <same-dir> [--wait 15] [--full]
submit --run <same-dir> --request <id>  (plan JSON on stdin)
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from arena.external_planner import ExternalPlanner, atomic_json, submit


def observe(root, wait, full):
    deadline = time.monotonic() + min(55, max(0, wait))
    requested = False
    while True:
        status_path = root / 'run.json'
        status = json.loads(status_path.read_text()) if status_path.exists() else {'status':'not started'}
        if status['status'] not in ('starting', 'running'):
            print(json.dumps(status, ensure_ascii=False)); return
        current = root / 'p1-mailbox' / 'current.json'
        if current.exists():
            r = json.loads(current.read_text())
            answered = (current.parent / (r['request_id'] + '.response.json')).exists()
            if not answered and time.time() < r['deadline_at'] - 15:
                p = r['payload']
                if not full:
                    p = {k:v for k,v in p.items() if k not in ('game_knowledge','tech_tree','prepared_strategy','context_policy')}
                print(json.dumps(dict(request_id=r['request_id'],remaining_s=round(r['deadline_at']-time.time(),1),payload=p),ensure_ascii=False));return
        if not requested and current.parent.exists():
            atomic_json(current.parent/'observe.request.json',dict(requested_at=time.time()))
            requested=True
        if time.monotonic() >= deadline:
            print(json.dumps(dict(status='waiting for fresh P1 observation',run_status=status['status']),ensure_ascii=False));return
        time.sleep(.1)


def start(args):
    from arena.llm import PROVIDERS
    from arena.deepseek_planner import DeepSeekFlashPlanner
    import native_llm
    root = args.run.resolve()
    root.mkdir(parents=True, exist_ok=False)
    local = ExternalPlanner(root / 'p1-mailbox', on_demand=True)
    # Explicit official key; never ARENA_P2_API_KEY or an OpenCode login.
    official = DeepSeekFlashPlanner()
    if not official.p.configured:
        raise RuntimeError('DEEPSEEK_API_KEY is missing; no provider fallback')
    official.compact_battle = True
    official.soft_timeout = 15
    official.prepared_strategy = '你是 DeepSeek 官方 API 选手，对手是当前 Codex 会话。依据己方观察自主决定经济、采气、开矿、科技与战斗。双方拥有相同宏指令和可定义JSON技能；本场无预装Python技能或脚本开局。'
    identity = dict(side='P2', provider='deepseek-official', model=official.model, key_source='DEEPSEEK_API_KEY')
    metadata = dict(status='starting', pid=os.getpid(), identities=[local.identity(),identity],
                    realtime=True, endpoint=official.p.base_url, opening=False,
                    python_tactics='none for either side; macro and JSON workflows available',
                    controller='active current Codex session; no OpenAI API call',
                    disconnected_stop_s=120)
    atomic_json(root / 'run.json', metadata)
    viewer = None
    done = threading.Event()
    def watchdog():
        while not done.wait(1):
            if local.last_contact is not None and time.monotonic()-local.last_contact > 120:
                atomic_json(root / 'disconnect.json', dict(error='Current session has not replied for 120s; stopping match explicitly'))
                print('[controller disconnected] no reply for 120s; stopping match',flush=True)
                os.kill(os.getpid(),signal.SIGINT)
                return
    threading.Thread(target=watchdog,daemon=True).start()
    try:
        if not args.no_output_window:
            with (root / 'output-window.log').open('w') as log:
                viewer=subprocess.Popen([sys.executable,'llm_output_window.py','--run',str(root)],stdout=log,stderr=subprocess.STDOUT)
            metadata['output_window_pid']=viewer.pid
        metadata['status']='running';atomic_json(root / 'run.json',metadata)
        native_llm.main(players_override=[(local,local.identity()),(official,identity)],
                        opening=False, argv=['--seconds',str(args.seconds),'--map',args.map,'--game-speed',args.game_speed,'--output',str(root/'match.json')])
        metadata['status']='finished'
    except BaseException as error:
        metadata['status']='interrupted' if isinstance(error,KeyboardInterrupt) else 'error'
        metadata['error']=type(error).__name__+': '+str(error)
        raise
    finally:
        done.set();local.close()
        if (root/'match.json').exists():
            report=json.loads((root/'match.json').read_text())
            metadata.update(outcome=report.get('outcome'),timing=report.get('timing'),replay=report.get('replay'))
        atomic_json(root/'run.json',metadata)
        # Leave the public response window available for the spectator's review.
        # It only reads files, and cannot call either model.


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['start','observe','submit'])
    parser.add_argument('--run',required=True,type=Path)
    parser.add_argument('--seconds',type=float,default=5400,help='maximum match wall seconds after startup (default: 90 minutes)')
    parser.add_argument('--map',default='Acropolis')
    parser.add_argument('--game-speed',choices=['slower','normal','faster'],default='normal',help='real simulation speed; Slower/Normal currently require Acropolis')
    parser.add_argument('--no-output-window',action='store_true')
    parser.add_argument('--wait',type=float,default=5)
    parser.add_argument('--full',action='store_true')
    parser.add_argument('--request')
    args=parser.parse_args()
    if args.command=='start':
        if args.seconds<=0:parser.error('--seconds must be positive')
        start(args)
    elif args.command=='observe':observe(args.run,args.wait,args.full)
    else:
        if not args.request:parser.error('submit needs --request')
        print(json.dumps(submit(args.run/'p1-mailbox',args.request,json.load(sys.stdin)),ensure_ascii=False))

if __name__=='__main__':main()
