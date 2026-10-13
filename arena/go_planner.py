"""Bounded, cancellable OpenCode Go requests using an injected vault binding."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from arena.chatgpt_planner import ChatGPTSubscriptionPlanner

GO_MODEL = 'opencode-go/deepseek-v4.1-flash'
GO_MODELS = (GO_MODEL, 'opencode-go/deepseek-v4-pro')
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
    # Small compatibility probes succeeded, but real game requests returned 400
    # for this format. Keep the proven JSON mode; no automatic retry/fallback.
    native_schema = False

    def __init__(self, model, directory):
        if model not in GO_MODELS:
            raise ValueError('Unsupported explicitly selected OpenCode Go model')
        if not os.environ.get(KEY_ENV, '').strip():
            raise CredentialBindingMissing('Cloud credential binding missing: open_api_key; no API request sent')
        self.model = model
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.response_kind = 'battle'
        self.max_tokens = 8000
        self.calls = self.attempts = 0
        self.last_raw = self.last_metadata = None
        self.closed = False
        self.pending = None
        self.lock = threading.Lock()
        self.started = None
        self.state = 'idle'
        self.session_id = 'sc2-' + uuid.uuid4().hex
        self.response_schema = None

    def response_schema_for(self, payload):
        if not self.native_schema:return None
        if not isinstance(payload,dict):return None
        if self.response_kind == 'review':
            from arena.chatgpt_planner import review_output_schema
            return review_output_schema()
        if self.response_kind == 'battle' and self.structured_battle and payload.get('tech_tree'):
            from arena.go_schema import battle_schema
            return battle_schema(payload)
        return None

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
                           timeout=self.timeout, session_id=self.session_id, max_tokens=self.max_tokens)
            schema=self.response_schema
            if schema is None:
                try:schema=self.response_schema_for(json.loads(user))
                except (ValueError,TypeError):pass
            if schema is not None:request['response_schema']=schema
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
    if request['requested_model'] not in GO_MODELS:
        raise ValueError('Unsupported model')
    key = os.environ.get(KEY_ENV, '').strip()
    if not key:
        return dict(error='Cloud credential binding missing: open_api_key; no API request sent',
                    error_kind='credential_binding_missing', request_sent=False)
    wire_model = request['requested_model'].split('/', 1)[1]
    format_spec={'type':'json_object'}
    if request.get('response_schema') is not None:
        from arena.go_schema import response_format
        format_spec=response_format(request['response_schema'])
    response = post(GO_ENDPOINT+'/chat/completions',
        headers={'Authorization':'Bearer '+key, 'Content-Type':'application/json',
                 'x-opencode-session':request.get('session_id') or 'sc2-'+uuid.uuid4().hex,
                 'User-Agent':'opencode/1.18.32'},
        json=dict(model=wire_model, messages=[dict(role='system', content=request['system']),
                  dict(role='user', content=request['observation'])],
                  max_tokens=request.get('max_tokens',8000), reasoning_effort='low', response_format=format_spec),
        timeout=request['timeout'])
    if response.status_code >= 400:
        suffix = ' quota exceeded' if response.status_code == 429 else ' request rejected'
        result=dict(error='HTTP %d: OpenCode Go%s' % (response.status_code, suffix))
        # Report only fixed diagnostic labels: provider messages can echo request
        # details/credentials and must never be written to the journal.
        topics=set()
        labels=('schema','max_tokens','quota','credit','session','unsupported','reasoning',
                'messages','coding','secret','binding','credential','authorization','proxy','limit','format')
        def collect_topics(message):
            if isinstance(message,str):
                topics.update('credential' if word=='secret' else word
                              for word in labels if word in message.lower())
        try:
            error=response.json().get('error',{})
            if isinstance(error,dict):
                kind=error.get('type')
                if kind in ('invalid_request_error','invalid_json_schema','authentication_error',
                            'insufficient_quota','rate_limit_error'):
                    result['provider_error_type']=kind
                collect_topics(error.get('message',''))
            elif isinstance(error,str):collect_topics(error)
            collect_topics(response.json().get('message',''))
        except (ValueError,TypeError,AttributeError):pass
        collect_topics(getattr(response,'text',''))
        if topics:result['provider_error_topics']=sorted(topics)
        return result
    data = response.json()
    choice = data['choices'][0]
    raw_usage = data.get('usage') or {}
    usage = dict(raw_usage, input_tokens=raw_usage.get('prompt_tokens',0),
                 output_tokens=raw_usage.get('completion_tokens',0),
                 cached_input_tokens=(raw_usage.get('prompt_tokens_details') or {}).get('cached_tokens',0))
    metadata = dict(requested_model=request['requested_model'], returned_model=data.get('model'),
                    finish_reason=choice.get('finish_reason'), usage=usage, endpoint=GO_ENDPOINT,
                    response_format=format_spec['type'])
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
