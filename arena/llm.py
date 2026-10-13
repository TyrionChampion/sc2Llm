"""LLM adapter layer: pluggable providers, each reading its own API key.

Design
------
Every provider is described by a small record (env var, base url, model) so
adding a vendor is a data change, not a code change. All of them speak one of
two wire formats:
  - "openai"    : POST {base}/chat/completions  (OpenAI, DeepSeek, Moonshot,
                  DashScope-compat, Zhipu, Groq, local vLLM/Ollama, ...)
  - "anthropic" : POST {base}/v1/messages

A "mock" provider is always available so the whole pipeline (state -> prompt ->
decision -> skill execution) can be verified with no key and no network.
"""
import json
import os
import time
import urllib.error
import urllib.request

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


class Provider:
    def __init__(self, name, kind, key_env, base_url, model,
                 extra_headers=None, key_required=True):
        self.name = name
        self.kind = kind
        self.key_env = key_env
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.extra_headers = extra_headers or {}
        self.key_required = key_required

    @property
    def api_key(self):
        return os.environ.get(self.key_env, "").strip()

    @property
    def configured(self):
        return bool(self.api_key) or not self.key_required

    def __repr__(self):
        return "Provider(%s, model=%s, key=%s)" % (
            self.name, self.model,
            "set" if self.api_key else ("not-needed" if not self.key_required
                                        else "MISSING"))


# --- the catalogue -----------------------------------------------------------
# Add a vendor here and it works everywhere else. `model` can be overridden at
# runtime with e.g. ARENA_MODEL_DEEPSEEK=deepseek-reasoner.
PROVIDERS = {
    "openai": Provider("openai", "openai", "OPENAI_API_KEY",
                       "https://api.openai.com/v1", "gpt-4o-mini"),
    "anthropic": Provider("anthropic", "anthropic", "ANTHROPIC_API_KEY",
                          "https://api.anthropic.com", "claude-3-5-sonnet-latest"),
    "deepseek": Provider("deepseek", "openai", "DEEPSEEK_API_KEY",
                         "https://api.deepseek.com/v1", "deepseek-chat"),
    "opencode_go": Provider("opencode_go", "openai", "open_api_key",
                            "https://opencode.ai/zen/go/v1", "deepseek-v4.1-flash"),
    "moonshot": Provider("moonshot", "openai", "MOONSHOT_API_KEY",
                         "https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    "dashscope": Provider("dashscope", "openai", "DASHSCOPE_API_KEY",
                          "https://dashscope.aliyuncs.com/compatible-mode/v1",
                          "qwen-plus"),
    "zhipu": Provider("zhipu", "openai", "ZHIPU_API_KEY",
                      "https://open.bigmodel.cn/api/paas/v4", "glm-4-flash"),
    "groq": Provider("groq", "openai", "GROQ_API_KEY",
                     "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "ollama": Provider("ollama", "openai", "OLLAMA_API_KEY",
                       "http://127.0.0.1:11434/v1", "qwen2.5:7b",
                       key_required=False),
    # always usable, needs nothing
    "mock": Provider("mock", "mock", "MOCK_API_KEY", "", "mock-policy",
                     key_required=False),
}


def resolve_model(provider, default):
    """Allow per-provider model override from the environment."""
    env = "ARENA_MODEL_%s" % provider.name.upper()
    return os.environ.get(env, "").strip() or default


class LLMError(Exception):
    pass


# Account-level refusals: retrying cannot fix them, and continuing to poll burns the match
# while the AI silently stops deciding. 401 unauthorized, 402 insufficient balance and
# 403 forbidden mean "stop and tell the operator", not "try again shortly".
FATAL_HTTP_CODES = (401, 402, 403)


def http_error(code, body):
    """Build the error for a non-2xx response, marking account-level refusals as fatal."""
    error = LLMError("HTTP %s: %s" % (code, str(body)[:400]))
    error.fatal = code in FATAL_HTTP_CODES
    error.status_code = code
    return error


class LLMClient:
    """One client per provider. Sequential by design (LLM latency dominates)."""

    def __init__(self, provider, model=None, temperature=0.7, max_tokens=400,
                 timeout=60):
        if isinstance(provider, str):
            if provider not in PROVIDERS:
                raise LLMError("unknown provider %r; known: %s"
                               % (provider, ", ".join(sorted(PROVIDERS))))
            provider = PROVIDERS[provider]
        self.p = provider
        self.model = model or resolve_model(provider, provider.model)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.calls = 0
        self.errors = 0
        self.total_latency = 0.0
        self.last_raw = None
        # Token accounting, including prompt-cache hits. DeepSeek reports
        # prompt_cache_hit_tokens / prompt_cache_miss_tokens; OpenAI reports
        # usage.prompt_tokens_details.cached_tokens. We read whichever exists.
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0,
                      "cached_tokens": 0, "calls": 0, "cache_measured_prompt_tokens": 0,
                      "cache_unavailable_calls": 0, "cache_measured_cached_tokens": 0}
        self.last_usage = None
        self.usage_history = []

    def __repr__(self):
        return "LLMClient(%s, %s)" % (self.p.name, self.model)

    # -- token / cache accounting -------------------------------------------
    def _record_usage(self, payload):
        """Record server counts; unsupported/missing metrics remain unavailable."""
        u = (payload or {}).get("usage") or {}
        if not isinstance(u, dict) or not u:
            self.last_usage = None
            return
        def count(value):
            try:
                return max(0, int(value)) if value is not None else None
            except (ValueError, TypeError, OverflowError):
                return None
        cached = u.get("prompt_cache_hit_tokens")
        if cached is None:
            cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens")
        rec = {"prompt_tokens": count(u.get("prompt_tokens")),
               "completion_tokens": count(u.get("completion_tokens")),
               "cached_tokens": count(cached),
               "cache_miss_tokens": count(u.get("prompt_cache_miss_tokens"))}
        measured = (rec["prompt_tokens"] is not None and rec["cached_tokens"] is not None)
        rec["hit_rate"] = (rec["cached_tokens"] / rec["prompt_tokens"]
                           if measured and rec["prompt_tokens"] else None)
        self.last_usage = rec
        self.usage_history.append(rec)
        for k in ("prompt_tokens", "completion_tokens", "cached_tokens"):
            if rec[k] is not None:
                self.usage[k] += rec[k]
        self.usage["calls"] += 1
        if measured:
            self.usage["cache_measured_prompt_tokens"] += rec["prompt_tokens"]
            self.usage["cache_measured_cached_tokens"] += rec["cached_tokens"]
        else:
            self.usage["cache_unavailable_calls"] += 1

    @property
    def cache_hit_rate(self):
        """Hit fraction for calls with reported cache metrics, else None."""
        p = self.usage["cache_measured_prompt_tokens"]
        return (self.usage["cache_measured_cached_tokens"] / p) if p else None

    def usage_summary(self):
        u = self.usage
        rate = self.cache_hit_rate
        label = "unavailable" if rate is None else "%.1f%%" % (100 * rate)
        return ("calls=%d prompt=%d cached=%d (%s; unavailable_calls=%d) completion=%d"
                % (u["calls"], u["prompt_tokens"], u["cached_tokens"], label,
                   u["cache_unavailable_calls"], u["completion_tokens"]))

    # -- public -------------------------------------------------------------
    def chat(self, system, user, retries=2):
        """Return the assistant text. Raises LLMError on hard failure."""
        if self.p.kind == "mock":
            return self._mock(system, user)
        if not self.p.configured:
            raise LLMError(
                "provider %s has no API key: set %s"
                % (self.p.name, self.p.key_env))

        last = None
        for attempt in range(retries + 1):
            t0 = time.time()
            try:
                self.last_usage = None
                if self.p.kind == "anthropic":
                    out = self._call_anthropic(system, user)
                else:
                    out = self._call_openai(system, user)
                self.calls += 1
                self.total_latency += time.time() - t0
                self.last_raw = out
                return out
            except Exception as e:  # noqa: BLE001 - report, don't hide
                last = e
                self.errors += 1
                if getattr(e, "fatal", False):
                    # Account-level refusal: no retry, keep the reason inspectable upstream.
                    fatal = LLMError("%s call failed: %s" % (self.p.name, e))
                    fatal.fatal = True
                    fatal.status_code = getattr(e, "status_code", None)
                    raise fatal from None
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
        raise LLMError("%s call failed: %s" % (self.p.name, last))

    @property
    def avg_latency(self):
        return self.total_latency / self.calls if self.calls else 0.0

    # -- wire formats -------------------------------------------------------
    def _post(self, url, payload, headers):
        headers = dict(headers)
        headers.setdefault("Content-Type", "application/json")
        data = json.dumps(payload).encode("utf-8")
        if requests is not None:
            r = requests.post(url, data=data, headers=headers,
                              timeout=self.timeout)
            if r.status_code >= 400:
                raise http_error(r.status_code, r.text)
            return r.json()
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise http_error(e.code, e.read()[:400]) from None

    def _call_openai(self, system, user):
        url = "%s/chat/completions" % self.p.base_url
        headers = {"Authorization": "Bearer %s" % self.p.api_key}
        headers.update(self.p.extra_headers)
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        j = self._post(url, payload, headers)
        self._record_usage(j)
        try:
            return j["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise LLMError("unexpected response shape: %s" % str(j)[:300])

    def _call_anthropic(self, system, user):
        url = "%s/v1/messages" % self.p.base_url
        headers = {"x-api-key": self.p.api_key,
                   "anthropic-version": "2023-06-01"}
        headers.update(self.p.extra_headers)
        payload = {
            "model": self.model,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        j = self._post(url, payload, headers)
        self._record_usage(j)
        try:
            return "".join(b.get("text", "") for b in j["content"])
        except (KeyError, TypeError):
            raise LLMError("unexpected response shape: %s" % str(j)[:300])

    # -- mock ---------------------------------------------------------------
    def _mock(self, system, user):
        """Deterministic commander so a match is watchable with no API key.

        Reads back the state line the policy layer builds, so the pipeline stays
        honest: the mock only sees what a real LLM would see.

        Order of intent matters: supply headroom first, then keep the worker
        count up, then tech to Barracks, then army.
        """
        self.calls += 1
        self.last_raw = "(mock)"
        # ARENA_DELAY lets us rehearse a slow network model without spending
        # anyone's API budget -- used to prove the async policy never blocks.
        _d = os.environ.get("ARENA_DELAY", "").strip()
        if _d:
            try:
                time.sleep(float(_d))
            except ValueError:
                pass
        mins = supply_used = supply_cap = workers = marines = depots = 0
        barracks = 0
        try:
            user=user.split("CURRENT STATE\n",1)[-1].splitlines()[0]
            if "mins=" in user:
                mins = int(user.split("mins=")[1].split()[0])
            if "supply=" in user:
                a, b = user.split("supply=")[1].split()[0].split("/")
                supply_used, supply_cap = int(a), int(b)
            seg = user.split("|", 1)[1] if "|" in user else ""
            seg=seg.split(" | ENEMY_ON_SCREEN:",1)[0]
            for part in seg.split(","):
                part = part.strip()
                if " " not in part:
                    continue
                name, count = part.rsplit(" x", 1)
                try:
                    n = int(count)
                except ValueError:
                    continue
                name = name.strip()
                if name == "SCV":
                    workers = n
                elif name == "Marine":
                    marines = n
                elif name == "SupplyDepot":
                    depots = n
                elif name == "Barracks":
                    barracks = n
        except Exception:
            pass

        def say(skill, reason, **args):
            d = {"skill": skill, "reason": reason}
            if args:
                d["args"] = args
            return json.dumps(d)

        # 1) never get supply-blocked
        if supply_cap and supply_used >= supply_cap - 1:
            return say("supply_depot", "supply %d/%d, need depot now"
                       % (supply_used, supply_cap))
        # 2) keep the economy growing
        if workers < 16 and (not supply_cap or supply_used < supply_cap - 1):
            return say("train_worker", "workers=%d, grow economy" % workers)
        # 3) tech up before army
        if barracks == 0 and mins >= 150:
            return say("barracks", "no barracks, teching up")
        # 4) build an army
        if barracks and marines < 8:
            return say("train_unit", "marines=%d, need army" % marines,
                       unit="marine")
        # 5) otherwise keep mining
        return say("gather", "keep mining")


def available_providers():
    """Name -> 'ready' / 'no key' summary for the UI."""
    out = {}
    for name, p in PROVIDERS.items():
        out[name] = "ready" if p.configured else "no key (%s)" % p.key_env
    return out
