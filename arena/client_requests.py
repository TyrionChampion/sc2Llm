"""Durable per-match attempts, including failed calls, independent of provider counters."""
from pathlib import Path
import threading
from arena.lifecycle import write_json_atomic


class AccountedPlanner:
    def __init__(self, client, path):
        self.client, self.path = client, Path(path)
        if self.path.exists(): raise ValueError('请求账本已存在，拒绝重置历史计数')
        self.lock = threading.RLock()
        self.counts = dict(attempted=0, successful=0, failed=0)
        write_json_atomic(self.path, self.counts)

    def __getattr__(self, name): return getattr(self.client, name)

    @property
    def prepared_strategy(self): return self.client.prepared_strategy

    @prepared_strategy.setter
    def prepared_strategy(self, value): self.client.prepared_strategy = value

    @property
    def response_schema(self): return getattr(self.client, 'response_schema', None)

    @response_schema.setter
    def response_schema(self, value): self.client.response_schema = value

    def snapshot(self):
        with self.lock: return dict(self.counts)

    def chat(self, system, user):
        with self.lock:
            self.counts['attempted'] += 1
            write_json_atomic(self.path, self.counts)  # Persist BEFORE contacting the model.
        success = False
        try:
            answer = self.client.chat(system, user)
            success = True
            return answer
        finally:
            with self.lock:
                self.counts['successful' if success else 'failed'] += 1
                write_json_atomic(self.path, self.counts)
