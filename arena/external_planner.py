"""Player-local file mailbox for an actively connected external commander.

This is not an LLM API. The current assistant must read and reply to every
request; no response ever invents a plan or switches to a scripted player.
"""
import json
import os
from pathlib import Path
import threading
import time
import uuid


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class ExternalPlanner:
    compact_battle = True
    model = 'Codex-current-session'

    def __init__(self, directory, timeout=25, on_demand=False):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=False)
        self.session = uuid.uuid4().hex
        self.timeout = timeout
        self.on_demand = on_demand
        self.last_timing = None
        self.soft_timeout = min(15, timeout / 2)
        self.calls = 0
        self.last_raw = None
        self.closed = threading.Event()
        self.started = None
        self.last_contact = time.monotonic() if on_demand else None
        self.sequence = 0
        self.state = 'idle'
        self.prepared_strategy = '当前 Codex 会话逐次读取己方观察并决策；没有离线自动战略代理。'
        atomic_json(self.directory / 'status.json', self.request_status())

    def ready_for_request(self):
        if not self.on_demand:
            return True
        path = self.directory / "observe.request.json"
        if not path.exists():
            return False
        return time.time() - json.loads(path.read_text())["requested_at"] < 55

    def identity(self):
        return dict(side='P1', provider='external-session', model=self.model, key_source='none')

    def request_status(self):
        elapsed = max(0, time.monotonic() - self.started) if self.started else 0
        state = ('slow' if elapsed > self.soft_timeout else 'waiting') if self.state == 'waiting' else self.state
        return dict(state=state, elapsed_s=round(elapsed, 2), hard_timeout_s=self.timeout,
                    session=self.session, sequence=self.sequence, on_demand=self.on_demand,
                    last_timing=self.last_timing)

    def chat(self, system, user):
        if self.closed.is_set():
            raise RuntimeError('External commander is closed; no automatic fallback')
        if self.on_demand:
            (self.directory / "observe.request.json").unlink(missing_ok=True)
        payload = json.loads(user)
        self.sequence += 1
        request_id = self.session + '-' + str(self.sequence)
        self.started = time.monotonic()
        if self.last_contact is None:
            self.last_contact = self.started
        self.state = 'waiting'
        request = dict(request_id=request_id, created_at=time.time(),
                       deadline_at=time.time() + self.timeout, system=system, payload=payload)
        atomic_json(self.directory / (request_id + '.request.json'), request)
        atomic_json(self.directory / 'current.json', request)
        atomic_json(self.directory / 'status.json', self.request_status())
        response_path = self.directory / (request_id + '.response.json')
        try:
            while not self.closed.wait(.05):
                if time.monotonic() - self.started >= self.timeout:
                    raise TimeoutError('Current Codex session did not reply within %.1fs; existing tasks continue, no fallback' % self.timeout)
                if response_path.exists():
                    response = json.loads(response_path.read_text())
                    if response.get('request_id') != request_id:
                        raise ValueError('External response request_id mismatch; rejected')
                    if not isinstance(response.get('plan'), dict):
                        raise ValueError('External response needs a plan object')
                    self.last_raw = json.dumps(response['plan'], ensure_ascii=False)
                    self.last_timing = dict(response_s=round(time.monotonic()-self.started,3),
                        snapshot_game_s=payload.get('world',{}).get('game_s'),
                        submitted_at=response.get('submitted_at'),
                        policy='fresh observation on reader demand' if self.on_demand else 'periodic')
                    self.calls += 1
                    self.last_contact = time.monotonic()
                    self.state = 'received'
                    return self.last_raw
            raise RuntimeError('External commander closed during request')
        except Exception as error:
            self.state = 'closed' if self.closed.is_set() else 'error'
            atomic_json(self.directory / (request_id + '.error.json'),
                        dict(error=type(error).__name__ + ': ' + str(error), request_id=request_id))
            raise
        finally:
            atomic_json(self.directory / 'status.json', self.request_status())

    def close(self):
        self.closed.set()
        self.state = 'closed'
        atomic_json(self.directory / 'status.json', self.request_status())


def submit(directory, request_id, plan):
    directory = Path(directory)
    request = json.loads((directory / 'current.json').read_text())
    if request_id != request['request_id']:
        raise ValueError('Request is no longer current; read a fresh observation')
    status = json.loads((directory / 'status.json').read_text())
    if status['state'] not in ('waiting', 'slow') or time.time() >= request['deadline_at']:
        raise ValueError('Request expired or closed; read a fresh observation')
    if not isinstance(plan, dict) or not isinstance(plan.get('tasks'), list):
        raise ValueError('Plan must be an object with a tasks list; executor validates commands')
    # Serialize competing submissions with an exclusive lock file.
    lock = directory / (request_id + '.submit.lock')
    with lock.open('x'):
        path = directory / (request_id + '.response.json')
        if path.exists():
            raise ValueError('A response was already submitted')
        atomic_json(path, dict(request_id=request_id, plan=plan, submitted_at=time.time()))
    return dict(submitted=request_id, executor_acceptance='pending; inspect next feedback')
