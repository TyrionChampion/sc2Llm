"""Explicit official V4.1 Flash battle client; only public output is retained."""
import json
import time
from arena.llm import PROVIDERS, LLMError
from arena.player_config import LivePlanner

class DeepSeekFlashPlanner(LivePlanner):
    compact_battle = True
    soft_timeout = 15

    def __init__(self, *, defense=False, developer=False, battle=False, reasoning_effort=None):
        # 8000: high reasoning was hitting the former 6000 ceiling with finish_reason=length
        # (reasoning_tokens == completion_tokens), i.e. the visible answer never started.
        super().__init__(PROVIDERS['deepseek'], model='deepseek-flash', max_tokens=8000, timeout=40)
        if sum(bool(v) for v in (defense,developer,battle))>1:raise ValueError('choose defense, developer or battle, not multiple roles')
        self.defense = defense
        self.battle = battle
        self.developer = developer
        if reasoning_effort not in (None,'low','high'):raise ValueError('reasoning_effort must be low or high')
        self.reasoning_effort = reasoning_effort or ('high' if developer else 'low')
        if developer:
            self.max_tokens=12000; self.timeout=90; self.soft_timeout=30
        if defense:
            self.max_tokens=384; self.timeout=8; self.soft_timeout=3
        if battle:
            self.max_tokens=1200;self.timeout=10;self.soft_timeout=3
        self.last_metadata = None
        self.started = None
        self.state = "idle"
        self.last_timing = None

    def request_status(self):
        elapsed=time.monotonic()-self.started if self.started else 0
        state=('slow' if elapsed>self.soft_timeout else 'waiting') if self.state=='waiting' else self.state
        return dict(state=state,elapsed_s=round(elapsed,2),soft_timeout_s=self.soft_timeout,
                    hard_timeout_s=self.timeout,last_timing=self.last_timing)

    def chat(self, system, user):
        self.started=time.monotonic();self.state='waiting';self.last_metadata=None;self.last_usage=None
        try:
            result=super().chat(system,user)
            self.state='received'
            return result
        except Exception:
            self.state='failed'
            raise
        finally:
            self.last_timing=dict(response_s=round(time.monotonic()-self.started,3),
                                  input_chars=len(system)+len(user),metadata=self.last_metadata)

    def _call_openai(self, system, user):
        result = self._post(self.p.base_url + '/chat/completions', {
            'model':self.model,
            'messages':[{'role':'system','content':system},{'role':'user','content':user}],
            'max_tokens':self.max_tokens,
            **({'thinking':{'type':'disabled'}} if self.defense or self.battle else
               {'reasoning_effort':self.reasoning_effort,'thinking':{'type':'enabled'}}),
            'response_format':{'type':'json_object'},
        }, {'Authorization':'Bearer ' + self.p.api_key})
        self._record_usage(result)
        choice = result['choices'][0]
        self.last_metadata = dict(requested_model=self.model, returned_model=result.get('model'),
                                  finish_reason=choice.get('finish_reason'),usage=result.get('usage'))
        if result.get('model') != self.model:
            raise LLMError('Official model identity mismatch: ' + str(self.last_metadata))
        if choice.get('finish_reason') != 'stop':
            raise LLMError('Incomplete DeepSeek reply: ' + json.dumps(self.last_metadata))
        content = choice['message'].get('content')
        if not isinstance(content,str) or not content.strip():
            raise LLMError('DeepSeek returned no public answer: ' + json.dumps(self.last_metadata))
        # reasoning_content is neither logged nor exposed to the spectator.
        return content
