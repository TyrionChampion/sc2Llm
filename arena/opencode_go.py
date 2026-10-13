"""DeepSeek through the OpenCode Go subscription, with no provider fallback."""
import json
import time
import uuid

from arena.llm import LLMError, PROVIDERS, resolve_model
from arena.player_config import LivePlanner


class OpenCodeGoPlanner(LivePlanner):
    compact_battle = True
    soft_timeout = 15

    def __init__(self, *, provider=None, model=None, battle=False, session_id=None):
        provider = provider or PROVIDERS['opencode_go']
        model = model or resolve_model(provider, provider.model)
        if model not in ('deepseek-v4.1-flash', 'deepseek-v4-flash', 'deepseek-v4-pro'):
            raise ValueError('OpenCode Go battle requires an explicit supported DeepSeek model')
        super().__init__(provider, model=model, max_tokens=1200 if battle else 4000,
                         timeout=10 if battle else 40)
        self.battle = battle
        self.soft_timeout = 3 if battle else 15
        self.session_id = session_id or 'sc2-' + uuid.uuid4().hex
        self.started = None
        self.state = 'idle'
        self.last_timing = None
        self.last_metadata = None
        self.fatal_status = None

    def request_status(self):
        elapsed = time.monotonic() - self.started if self.state == 'waiting' else 0
        state = 'slow' if self.state == 'waiting' and elapsed > self.soft_timeout else self.state
        return dict(state=state, elapsed_s=round(elapsed, 2), soft_timeout_s=self.soft_timeout,
                    hard_timeout_s=self.timeout, last_timing=self.last_timing)

    def chat(self, system, user):
        if self.fatal_status:
            error = LLMError('OpenCode Go session stopped after HTTP %s' % self.fatal_status)
            error.fatal = True
            error.status_code = self.fatal_status
            raise error
        self.started = time.monotonic()
        self.state = 'waiting'
        self.last_metadata = None
        try:
            result = super().chat(system, user)
            self.state = 'received'
            return result
        except Exception as error:
            self.state = 'failed'
            if getattr(error, 'fatal', False):
                self.fatal_status = error.status_code
            raise
        finally:
            self.last_timing = dict(response_s=round(time.monotonic() - self.started, 3),
                                    input_chars=len(system) + len(user), metadata=self.last_metadata)

    def _call_openai(self, system, user):
        result = self._post(self.p.base_url + '/chat/completions', {
            'model': self.model,
            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            'max_tokens': self.max_tokens,
            'thinking': {'type': 'disabled'},
            'response_format': {'type': 'json_object'},
        }, {'Authorization': 'Bearer ' + self.p.api_key,
            'User-Agent': 'opencode/1.18.32', 'x-opencode-session': self.session_id})
        self._record_usage(result)
        choices = result.get('choices') or []
        choice = choices[0] if choices else {}
        self.last_metadata = dict(provider=self.p.name, requested_model=self.model,
                                  returned_model=result.get('model'),
                                  finish_reason=choice.get('finish_reason'), usage=result.get('usage'))
        # The Go response uses the advertised Go ID. Never silently switch to Zen/official API.
        if result.get('model') != self.model or choice.get('finish_reason') != 'stop':
            raise LLMError('Incomplete or mismatched OpenCode Go reply: ' + json.dumps(self.last_metadata))
        content = choice.get('message', {}).get('content')
        if not isinstance(content, str) or not content.strip():
            raise LLMError('OpenCode Go returned no public answer: ' + json.dumps(self.last_metadata))
        # Validate public JSON only. reasoning_content is never returned or logged.
        try:
            if not isinstance(json.loads(content), dict):
                raise ValueError('expected an object')
        except (ValueError, TypeError) as error:
            raise LLMError('Invalid OpenCode Go JSON: ' + str(error)) from None
        return content
