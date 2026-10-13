"""Battle decisions through official Codex CLI's ChatGPT subscription login."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time


def review_output_schema():
    def obj(properties):
        return dict(type='object',properties=properties,required=list(properties),additionalProperties=False)
    ids=dict(type='array',items=dict(type='integer',minimum=1),maxItems=12)
    finding=obj(dict(evidence_ids=ids,detail=dict(type='string',minLength=1,maxLength=1500)))
    gap=obj(dict(evidence_ids=dict(ids,minItems=1),description=dict(type='string',minLength=1,maxLength=1500),
                 reproduction=dict(type='string',minLength=1,maxLength=1500)))
    return obj(dict(tasks=dict(type='array',items=dict(type='null'),maxItems=0),
        reason=dict(type='string',maxLength=1800),category=dict(type='string',enum=['strategy','feature_gap','environment','win']),
        strategy=dict(type='string',minLength=1,maxLength=1800),
        findings=dict(type='array',items=finding,maxItems=12),gap=dict(anyOf=[dict(type='null'),gap])))


def battle_output_schema(payload):
    """Constrain representation to the existing autonomous command contracts."""
    from arena.battle_plans import output_schema
    from arena.context_tables import decode_tables
    payload=decode_tables(payload)
    known = output_schema(payload)['anyOf'][0]
    allowed = {'train_worker', 'train_unit', 'build_structure', 'build_supply',
               'research', 'gather_gas', 'expand', 'gather', 'attack_nearest',
               'move_out', 'no_op', 'defend_base', 'cancel_task', 'scout',
               'order_units', 'cancel_unit_order', 'surrender', 'macro_plan',
               'cancel_expansion', 'morph_unit', 'rally_units', 'scan', 'call_mule', 'supply_drop','inject_larva'}
    variants = []
    for branch in known['properties']['tasks']['items']['anyOf']:
        names = [name for name in branch['properties']['skill']['enum'] if name in allowed]
        if not names:
            continue
        branch['properties']['skill']['enum'] = names
        variants.append(branch)
    schema = dict(type='object', additionalProperties=False, properties=dict(
        tasks=dict(type='array', maxItems=3, items=dict(anyOf=variants)),
        reason=known['properties']['reason']))
    def strict(node):
        if isinstance(node, dict):
            node.pop('uniqueItems',None)  # Host still validates uniqueness; provider schema subset omits it.
            if node.get('type') == 'object':
                node['required'] = list(node['properties'])
                node['additionalProperties'] = False
            for value in node.values():
                strict(value)
        elif isinstance(node, list):
            for value in node:
                strict(value)
    strict(schema)
    return schema


class ChatGPTSubscriptionPlanner:
    compact_battle = True
    soft_timeout = 22
    timeout = 60
    structured_battle = False
    reasoning_effort = None

    def __init__(self, model, directory):
        if not model.startswith('chatgpt/') or not model.split('/', 1)[1]:
            raise ValueError('Expected exact chatgpt/model identity')
        self.model = model.split('/', 1)[1]
        self.response_kind = 'battle'
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.binary = shutil.which('codex')
        if not self.binary:
            raise RuntimeError('Official Codex CLI missing; no provider fallback')
        self.env = os.environ.copy()
        self.env.pop('OPENAI_API_KEY', None)
        subscription_home = self.env.get('ARENA_CHATGPT_HOME') or str(
            Path(__file__).resolve().parent.parent / 'session-runtime/chatgpt-subscription')
        self.env['CODEX_HOME'] = subscription_home
        self.calls = self.attempts = 0
        self.last_raw = self.last_metadata = None
        self.request_state = {'state': 'idle'}
        self.closed = False
        self.pending = None
        self.lock = threading.Lock()
        self._check_login()

    def _check_login(self):
        status = subprocess.run([self.binary, 'login', 'status'], env=self.env,
                                capture_output=True, text=True, timeout=15)
        if status.returncode or 'Logged in using ChatGPT' not in status.stdout + status.stderr:
            raise RuntimeError('HTTP 401: ChatGPT subscription login required; run codex login '
                               'with the same CODEX_HOME. API-key authentication is not selected.')

    def request_status(self):
        status = dict(self.request_state)
        started = status.pop('started', None)
        if started is not None:
            status['elapsed_s'] = round(time.monotonic() - started, 2)
            if status['elapsed_s'] > self.soft_timeout:
                status['state'] = 'slow'
        return status

    @staticmethod
    def _stop(process):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()

    def chat(self, system, user):
        started = time.monotonic()
        with self.lock:
            if self.closed:
                raise RuntimeError('ChatGPT planner closed')
            self.attempts += 1
            folder = self.directory / ('request-%04d' % self.attempts)
            folder.mkdir()
            output = folder / 'answer.json'
            command = [self.binary, 'exec', '--ignore-user-config', '--ephemeral',
                       '--skip-git-repo-check', '-C', str(self.directory), '-s', 'read-only',
                       '-c', 'model_provider="openai"', '-c', 'forced_login_method="chatgpt"',
                       '-c', 'features.shell_tool=false', '-c', 'features.unified_exec=false',
                       '-c', 'sqlite_home=' + json.dumps(str(self.directory / 'state')),
                       '-c', 'log_dir=' + json.dumps(str(self.directory / 'state')),
                       '-m', self.model, '--json', '-o', str(output), '-']
            if self.structured_battle and self.response_kind == 'battle':
                schema_path = folder / 'response-schema.json'
                schema_path.write_text(json.dumps(battle_output_schema(json.loads(user)), ensure_ascii=False))
                command[-1:-1] = ['--output-schema', str(schema_path)]
            elif self.response_kind=='review':
                schema_path=folder/'response-schema.json'
                schema_path.write_text(json.dumps(review_output_schema(),ensure_ascii=False))
                command[-1:-1]=['--output-schema',str(schema_path)]
            if self.reasoning_effort:
                command[-1:-1] = ['-c', 'model_reasoning_effort=' + json.dumps(self.reasoning_effort)]
            prompt = ('You are the SC2 battle planner. Do not use tools, inspect files, '
                      'or change the model. Return only the battle plan JSON. '
                      'Choose at most 3 tasks; reason must be under 100 Chinese characters.\n'
                      '<battle_rules>\n' + system + '\n</battle_rules>\n'
                      '<current_observation>\n' + user + '\n</current_observation>')
            if self.response_kind == 'review':
                prompt = ('You are the SC2 post-game reviewer. Do not use tools, inspect files or '
                          'change the model. Return only the review JSON specified below. '
                          'The tasks field must be []; strategic recommendations belong in strategy.\n'
                          '<review_rules>\n'+system+'\n</review_rules>\n'
                          '<battle_evidence>\n'+user+'\n</battle_evidence>')
            (folder / 'request.json').write_text(json.dumps(dict(
                provider='chatgpt', requested_model=self.model, system=system,
                observation=json.loads(user)), ensure_ascii=False), encoding='utf-8')
            process = subprocess.Popen(command, env=self.env, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, start_new_session=True)
            self.pending = process
            self.request_state = dict(state='waiting', started=started,
                                      soft_timeout_s=self.soft_timeout, hard_timeout_s=self.timeout)
        stdout = stderr = None
        try:
            stdout, stderr = process.communicate(prompt, timeout=self.timeout)
            (folder / 'events.jsonl').write_text(stdout, encoding='utf-8')
            (folder / 'stderr.log').write_text(stderr, encoding='utf-8')
            events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
            errors = [event.get('message') or (event.get('error') or {}).get('message', '')
                      for event in events if event.get('type') in ('error', 'turn.failed')]
            if process.returncode or errors:
                detail = '; '.join(errors) or stderr[-2000:] or 'Codex exited without an answer'
                if any(word in detail.lower() for word in ('access token', 'sign in', 'authentication', 'unauthorized')):
                    detail = 'HTTP 401: ' + detail
                raise RuntimeError(detail)
            if not any(event.get('type') == 'turn.completed' for event in events):
                raise RuntimeError('Codex decision did not complete; no partial-answer fallback')
            for event in events:
                item = event.get('item') or {}
                if event.get('type') in ('item.started', 'item.completed') and item.get('type') not in (
                        'agent_message', 'reasoning', None):
                    raise RuntimeError('Unexpected Codex tool activity in battle decision: ' + str(item.get('type')))
            raw = output.read_text(encoding='utf-8')
            plan = json.loads(raw)
            if not isinstance(plan, dict) or not isinstance(plan.get('tasks'), list):
                raise RuntimeError('ChatGPT returned an invalid battle plan')
            if self.response_kind == 'review' and plan['tasks']:
                raise RuntimeError('Post-game review must not dispatch battle tasks')
            self.calls += 1
            self.last_raw = raw
            self.last_metadata = dict(provider='chatgpt', requested_model=self.model,
                                      returned_model=None, authentication='ChatGPT subscription',
                                      structured_output=bool(self.structured_battle and self.response_kind=='battle'),
                                      requested_reasoning_effort=self.reasoning_effort,
                                      usage=next((event.get('usage') for event in events
                                                  if event.get('type') == 'turn.completed'), None))
            (folder / 'metadata.json').write_text(json.dumps(self.last_metadata), encoding='utf-8')
            self.request_state = dict(state='completed', elapsed_s=time.monotonic() - started)
            return raw
        except BaseException as error:
            self._stop(process)
            if stdout is None:
                stdout, stderr = process.communicate()
                (folder / 'events.jsonl').write_text(stdout, encoding='utf-8')
                (folder / 'stderr.log').write_text(stderr, encoding='utf-8')
            self.request_state = dict(state='failed', error=str(error),
                                      elapsed_s=time.monotonic() - started)
            raise
        finally:
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()
            with self.lock:
                self.pending = None

    def close(self):
        with self.lock:
            self.closed = True
            if self.pending is not None:
                self._stop(self.pending)
