"""Bounded, cancellable OpenCode Go requests using an injected vault binding."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from arena.chatgpt_planner import ChatGPTSubscriptionPlanner

GO_MODEL = 'opencode-go/deepseek-v4.1-flash'
GO_ENDPOINT = 'https://opencode.ai/zen/go/v1'
KEY_ENV = 'open_api_key'


class CredentialBindingMissing(RuntimeError):
    pass


class GoPlanner:
    compact_battle = True
    structured_battle = True
    soft_timeout = 15
    timeout = 40
    reasoning_effort = 'low'

    def __init__(self, model, directory):
        if model != GO_MODEL:
            raise ValueError('Unsupported explicitly selected OpenCode Go model')
        if not os.environ.get(KEY_ENV, '').strip():
            raise CredentialBindingMissing('Cloud credential binding missing: open_api_key; no API request sent')
        self.model = model
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.response_kind = 'battle'
        self.calls = self.attempts = 0
        self.last_raw = self.last_metadata = None
        self.closed = False
        self.pending = None
        self.lock = threading.Lock()
        self.started = None
        self.state = 'idle'

    def request_status(self):
        elapsed = time.monotonic()-self.started if self.started else 0
        return dict(state='slow' if self.state == 'waiting' and elapsed > self.soft_timeout else self.state,
                    elapsed_s=round(elapsed, 2), hard_timeout_s=self.timeout)

    def chat(self, system, user):
        with self.lock:
            if self.closed:
                raise RuntimeError('OpenCode Go planner closed')
            self.attempts += 1
            folder = self.directory / ('request-%04d' % self.attempts)
            folder.mkdir()
            request = dict(provider='opencode-go', requested_model=self.model,
                           system=system, observation=user, response_kind=self.response_kind,
                           timeout=self.timeout)
            (folder/'request.json').write_text(json.dumps(request, ensure_ascii=False))
            self.started = time.monotonic()
            self.state = 'waiting'
            process = subprocess.Popen([sys.executable, '-m', 'arena.go_planner', str(folder)],
                cwd=Path(__file__).resolve().parent.parent, start_new_session=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.pending = process
        try:
            process.wait(timeout=self.timeout+3)
            if self.closed:
                raise RuntimeError('OpenCode Go request cancelled')
            result = json.loads((folder/'result.json').read_text())
            self.last_metadata = result.get('metadata')
            if result.get('error'):
                raise RuntimeError(result['error'])
            if process.returncode:
                raise RuntimeError('OpenCode Go worker failed')
            self.last_raw = result['answer']
            self.calls += 1
            self.state = 'received'
            return self.last_raw
        except BaseException:
            self.state = 'failed'
            raise
        finally:
            ChatGPTSubscriptionPlanner._stop(process)
            self.pending = None

    def close(self):
        with self.lock:
            self.closed = True
            if self.pending:
                ChatGPTSubscriptionPlanner._stop(self.pending)
        self.state = 'closed'


def perform_request(request, post):
    """Only the fixed Go destination receives the binding; retain public answer/usage."""
    if request['requested_model'] != GO_MODEL:
        raise ValueError('Unsupported model')
    key = os.environ.get(KEY_ENV, '').strip()
    if not key:
        return dict(error='Cloud credential binding missing: open_api_key; no API request sent',
                    error_kind='credential_binding_missing', request_sent=False)
    wire_model = GO_MODEL.split('/', 1)[1]
    response = post(GO_ENDPOINT+'/chat/completions',
        headers={'Authorization':'Bearer '+key, 'Content-Type':'application/json'},
        json=dict(model=wire_model, messages=[dict(role='system', content=request['system']),
                  dict(role='user', content=request['observation'])],
                  max_tokens=4096, reasoning_effort='low', response_format={'type':'json_object'}),
        timeout=request['timeout'])
    if response.status_code >= 400:
        suffix = ' quota exceeded' if response.status_code == 429 else ' request rejected'
        return dict(error='HTTP %d: OpenCode Go%s' % (response.status_code, suffix))
    data = response.json()
    choice = data['choices'][0]
    metadata = dict(requested_model=GO_MODEL, returned_model=data.get('model'),
                    finish_reason=choice.get('finish_reason'), usage=data.get('usage'), endpoint=GO_ENDPOINT)
    if data.get('model') not in (wire_model, 'deepseek/'+wire_model):
        return dict(error='OpenCode Go model identity mismatch; no fallback', metadata=metadata)
    if choice.get('finish_reason') != 'stop':
        return dict(error='OpenCode Go incomplete response', metadata=metadata)
    answer = choice['message']['content']
    if not isinstance(answer, str):
        return dict(error='OpenCode Go missing public answer', metadata=metadata)
    # Validate the actual public JSON; hidden reasoning is never saved or used as a plan.
    plan = json.loads(answer)
    if not isinstance(plan, dict):
        return dict(error='Invalid battle plan: JSON object required', metadata=metadata)
    return dict(answer=answer, metadata=metadata)


if __name__ == '__main__':
    import requests
    folder = Path(sys.argv[1])
    try:
        result = perform_request(json.loads((folder/'request.json').read_text()), requests.post)
    except Exception as error:
        # Transport exception text can contain request details; retain only its type.
        result = dict(error='OpenCode Go request failed: '+type(error).__name__)
    (folder/'result.json').write_text(json.dumps(result, ensure_ascii=False))
