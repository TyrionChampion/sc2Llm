"""Explicit per-player LLM identities and optional separate API keys."""
import os
from arena.llm import LLMClient,PROVIDERS,Provider
from arena.commander import ScriptedPlanner

class LivePlanner(LLMClient):
    def chat(self,system,user):
        # A failed request is surfaced within 20s instead of hidden retry loops.
        return super().chat(system,user,retries=0)


def make_player(index,provider=None,delay=0):
    side='P%d' % (index+1)
    name=provider or os.environ.get('ARENA_%s_PROVIDER' % side) or os.environ.get('ARENA_PROVIDER','deepseek')
    if name not in PROVIDERS: raise ValueError('unknown provider '+name)
    if name=='mock':
        print('[%s config] EXPLICIT MOCK scripted planner; no model API calls' % side,flush=True)
        return ScriptedPlanner(delay),{'side':side,'provider':'mock','model':'scripted','key_source':'none'}
    base=PROVIDERS[name];key_env='ARENA_%s_API_KEY' % side
    if key_env in os.environ:
        if not os.environ[key_env].strip(): raise ValueError(key_env+' was set but is empty; provider-key fallback prohibited')
        selected=Provider(base.name,base.kind,key_env,base.base_url,base.model,base.extra_headers,base.key_required)
    else:
        selected=base;key_env=base.key_env
    if not selected.configured: raise ValueError('%s missing %s; mock fallback prohibited' % (side,key_env))
    model=os.environ.get('ARENA_%s_MODEL' % side) or None
    if name=='opencode_go':
        from arena.opencode_go import OpenCodeGoPlanner
        client=OpenCodeGoPlanner(provider=selected,model=model)
    else:
        client=LivePlanner(selected,model=model,max_tokens=2400,timeout=20)
    identity={'side':side,'provider':name,'model':client.model,'key_source':key_env}
    print('[%s config] %s / %s; key source=%s (value hidden)' % (side,name,client.model,key_env),flush=True)
    return client,identity
