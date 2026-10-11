"""Explicit OpenCode server/session transport. No direct-provider or mock fallback."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import threading
import time
import requests

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_MODELS=("opencode-go/qwen3.8-flash","opencode-go/gpt-6-luna")

def split_model(model):
    if not isinstance(model,str) or "/" not in model:raise ValueError("model must be provider/model")
    provider,name=model.split("/",1)
    if not provider or not name:raise ValueError("empty provider/model")
    return provider,name

def response_text(reply,model,structured=False):
    info=reply.get("info",{})
    if info.get("error"):raise RuntimeError("OpenCode model error: "+json.dumps(info["error"],ensure_ascii=False))
    provider,name=split_model(model)
    if (info.get("providerID"),info.get("modelID"))!=(provider,name):
        raise RuntimeError("OpenCode returned a different model identity; requested "+model)
    if structured:
        value=info.get("structured")
        if not isinstance(value,dict):raise RuntimeError("OpenCode structured battle output missing; no plain-text fallback")
        if info.get("finish") not in ("stop","end_turn","tool-calls"):raise RuntimeError("Structured output did not finish normally: "+str(info.get("finish")))
        return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(",",":"))
    text="".join(p.get("text","") for p in reply.get("parts",[]) if p.get("type")=="text")
    if not text.strip():raise RuntimeError("OpenCode returned no answer text; reasoning is not treated as an answer")
    if info.get("finish") not in ("stop","end_turn"):raise RuntimeError("OpenCode response did not finish normally: "+str(info.get("finish")))
    return text

class OpenCodeServer:
    def __init__(self,root,models,executable=None,mcp=None):
        self.root=Path(root).resolve();self.models=models;self.process=None;self.log=None
        self.mcp=mcp or {}
        self.secret=secrets.token_urlsafe(24)
        self.executable=executable or shutil.which("opencode") or str(Path.home()/".opencode/bin/opencode")
        self.base=None
    def start(self):
        self.root.mkdir(parents=True,exist_ok=False);self.root.chmod(0o700)
        providers={split_model(m)[0] for m in self.models}
        auth_path=Path.home()/".local/share/opencode/auth.json"
        auth=json.loads(auth_path.read_text())
        absent=providers-set(auth)
        if absent:raise RuntimeError("OpenCode missing authenticated provider(s): "+", ".join(sorted(absent)))
        for name in ("data/opencode","cache/opencode","state","config","workspace"):(self.root/name).mkdir(parents=True)
        keyfile=self.root/"data/opencode/auth.json"
        keyfile.write_text(json.dumps({p:auth[p] for p in providers}));keyfile.chmod(0o600)
        cache=Path.home()/".cache/opencode/models.json"
        if not cache.is_file():raise RuntimeError("OpenCode model cache missing; refresh its catalog before starting a match")
        shutil.copyfile(cache,self.root/"cache/opencode/models.json")
        config=self.root/"config/opencode.json"
        model_options={}
        for model in self.models:
            provider,name=split_model(model)
            model_options.setdefault(provider,{"models":{}})["models"][name]={"options":({"effort":"low"} if model=="opencode-go/qwen3.8-max" else {"reasoningEffort":"low"})}
            if model=="opencode-go/qwen3.8-flash":
                model_options[provider]["models"][name]["variants"]={"arena_fast":{"thinking":{"type":"disabled"}}}
        config.write_text(json.dumps({"mcp":self.mcp,"provider":model_options,"$schema":"https://opencode.ai/config.json","permission":{"*":"deny"},
                                     "plugin":[],"autoupdate":False,"share":"disabled"}))
        # The game wrapper's API keys are not passed to this OpenCode process.
        env={k:v for k,v in os.environ.items() if k in ("PATH","HOME","LANG","LC_ALL","HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","NO_PROXY","http_proxy","https_proxy","all_proxy","no_proxy","SSL_CERT_FILE","SSL_CERT_DIR")}
        for kind in ("DATA","CACHE","STATE","CONFIG"):env["XDG_"+kind+"_HOME"]=str(self.root/kind.lower())
        env.update(OPENCODE_CONFIG=str(config),OPENCODE_SERVER_PASSWORD=self.secret,
                   OPENCODE_SERVER_USERNAME="opencode",OPENCODE_DISABLE_AUTOUPDATE="true")
        with socket.socket() as s:s.bind(("127.0.0.1",0));port=s.getsockname()[1]
        self.base="http://127.0.0.1:%d"%port
        self.log=open(self.root/"server.log","w")
        self.process=subprocess.Popen([self.executable,"serve","--hostname","127.0.0.1","--port",str(port)],
            cwd=self.root/"workspace",env=env,stdout=self.log,stderr=subprocess.STDOUT,start_new_session=True)
        until=time.monotonic()+60;next_notice=time.monotonic()+10;last_health_error=None
        while time.monotonic()<until:
            if self.process.poll() is not None:raise RuntimeError("OpenCode server exited; see "+str(self.root/"server.log"))
            try:
                value=self.request("GET","/global/health",timeout=1)
                if value.get("healthy"):break
            except requests.RequestException as health_error:last_health_error=str(health_error)
            if time.monotonic()>=next_notice:
                print("[opencode] waiting for local server startup; last health error: "+str(last_health_error),flush=True);next_notice=time.monotonic()+10
            time.sleep(.1)
        else:raise TimeoutError("OpenCode server did not become healthy within 60s")
        catalog=self.request("GET","/provider",timeout=45)
        connected=set(catalog.get("connected",[]));all_models={p["id"]:p.get("models",{}) for p in catalog.get("all",[])}
        for model in self.models:
            provider,name=split_model(model)
            if provider not in connected or name not in all_models.get(provider,{}):raise RuntimeError("OpenCode model unavailable: "+model)
            if model=="opencode-go/qwen3.8-max":
                variant=all_models[provider][name].get("variants",{}).get("low")
                if not variant or variant.get("effort")!="low":raise RuntimeError("Qwen Max low-effort variant is unavailable; no implicit alternative")
                print("[opencode] qwen3.8-max explicit variant=low, effort=low",flush=True)
        print("[opencode] version=%s; private local server ready; model-specific low effort configured"%value.get("version"),flush=True)
    def request(self,method,path,timeout=10,**kwargs):
        response=requests.request(method,self.base+path,auth=("opencode",self.secret),timeout=timeout,**kwargs)
        if response.status_code>=400:raise RuntimeError("OpenCode HTTP %d: %s"%(response.status_code,response.text[:4000]))
        return response.json()
    def close(self):
        if self.process and self.process.poll() is None:
            os.killpg(self.process.pid,signal.SIGTERM)
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:os.killpg(self.process.pid,signal.SIGKILL);self.process.wait(timeout=3)
        if self.log:self.log.close()
        keyfile=self.root/"data/opencode/auth.json"
        if keyfile.exists():keyfile.unlink()

class OpenCodePlanner:
    def __init__(self,server,model,index,directory,lab_tools=False):
        self.server=server;self.model=model;self.index=index;self.lab_tools=lab_tools
        self.directory=Path(directory).resolve();self.directory.mkdir(parents=True,exist_ok=True)
        self.params={"directory":str(self.directory)};self.calls=0;self.last_raw=None;self.timeout=60;self.soft_timeout=22
        self.closed=False;self.lock=threading.Lock();self.pending=None;self.phase="setup"
        self.structured_battle=True
        self.request_state={"state":"idle"}
        self.audit_path=self.directory/"opencode.jsonl"
        session=server.request("POST","/session",params=self.params,json={"title":"SC2 P%d %s"%(index+1,model),
            "permission":[{"permission":"*","pattern":"*","action":"deny"}]+(
                [{"permission":"lab_*","pattern":"*","action":"allow"}] if lab_tools else [])})
        self.session_id=session["id"]
    def audit(self,record):
        with self.lock,open(self.audit_path,"a") as f:f.write(json.dumps(record,ensure_ascii=False)+"\n")
    def abort(self):
        return self.server.request("POST","/session/"+self.session_id+"/abort",params=self.params,timeout=10)
    @property
    def compact_battle(self):
        return self.phase=="battle" and not self.lab_tools
    def request_status(self):
        state=dict(self.request_state)
        if state.get("state") in ("waiting","slow"):
            state["elapsed_s"]=round(time.monotonic()-state["started"],1)
        state.pop("started",None)
        return state
    def _fresh_battle_session(self):
        previous=self.session_id
        session=self.server.request("POST","/session",params=self.params,timeout=5,
            json={"title":"SC2 P%d battle %d"%(self.index+1,self.calls+1),
                  "permission":[{"permission":"*","pattern":"*","action":"deny"}]+(
                      [{"permission":"StructuredOutput","pattern":"*","action":"allow"}] if self.structured_battle else [])})
        self.session_id=session["id"]
        self.audit(dict(record="battle_context_reset",previous_session=previous,session=self.session_id,
            handling="Fresh decision session; only explicit current payload is supplied. Developer session unchanged."))
    def chat(self,system,user):
        if self.closed:raise RuntimeError("OpenCode planner closed")
        if self.compact_battle:
            try:self._fresh_battle_session()
            except Exception as error:
                self.request_state=dict(state="failed",error="Fresh battle session failed: "+str(error))
                self.audit(dict(record="battle_context_reset_failed",error=str(error),handling="No fallback to previous history."))
                raise
        provider,model=split_model(self.model);path="/session/"+self.session_id
        structured=self.compact_battle and self.structured_battle
        if structured:
            system+="\nUse the actual StructuredOutput tool for the final plan, not text imitating a tool call. Choose at most 3 useful tasks this turn and keep reason under 100 characters."
        variant=("arena_fast" if structured and self.model=="opencode-go/qwen3.8-flash" else "low" if self.model=="opencode-go/qwen3.8-max" else None)
        start=time.monotonic();soft=self.soft_timeout if self.compact_battle else None
        self.request_state=dict(state="waiting",started=start,soft_timeout_s=soft,hard_timeout_s=self.timeout)
        self.audit(dict(record="request",session=self.session_id,model=self.model,system=system,prompt=user,
                        soft_timeout_s=soft,hard_timeout_s=self.timeout,structured=structured,
                        battle_variant=variant if structured else None,variant=variant))
        body=dict(model=dict(providerID=provider,modelID=model),system=system,
                  parts=[dict(type="text",text=user)])
        if structured:
            from arena.battle_plans import output_schema
            body['format']={'type':'json_schema','schema':output_schema(json.loads(user)),'retryCount':0}
        if variant:body['variant']=variant
        def send():
            reply=self.server.request("POST",path+"/message",params=self.params,json=body,timeout=self.timeout+5)
            return time.monotonic(),reply
        pool=ThreadPoolExecutor(max_workers=1)
        future=pool.submit(send);self.pending=future;next_poll=start
        def note_slow(elapsed):
            if soft is not None and elapsed>=soft and self.request_state["state"]!="slow":
                self.request_state={**self.request_state,"state":"slow"}
                self.audit(dict(record="response_slow",elapsed_s=elapsed,soft_timeout_s=soft,
                    hard_timeout_s=self.timeout,handling="Still waiting; existing tasks continue; request NOT aborted."))
                print('[opencode P%d] slow response %.1fs; still waiting (hard deadline %.1fs)'%
                      (self.index+1,elapsed,self.timeout),flush=True)
        try:
            while True:
                if self.closed:raise RuntimeError("OpenCode planner cancelled")
                now=time.monotonic()
                if future.done():
                    completed,reply=future.result()
                    elapsed=completed-start;note_slow(elapsed)
                    if elapsed>self.timeout:raise TimeoutError("OpenCode response exceeded %.1fs; request aborted"%self.timeout)
                    break
                note_slow(now-start)
                remaining=self.timeout-(now-start)
                if remaining<=0:raise TimeoutError("OpenCode response exceeded %.1fs; request aborted"%self.timeout)
                if now>=next_poll:
                    try:
                        status=self.server.request("GET","/session/status",params=self.params,
                            timeout=min(.5,remaining)).get(self.session_id,{})
                    except requests.Timeout as status_error:
                        self.audit(dict(record="status_poll_delayed",error=str(status_error),
                            handling="Original request pending; absolute deadline still enforced."))
                        print('[opencode P%d] local status poll delayed; original request still pending'%(self.index+1),flush=True)
                    else:
                        if status.get("type")=="retry":
                            raise RuntimeError("OpenCode upstream retry rejected by arena: "+status.get("message","unknown cause"))
                    next_poll=time.monotonic()+1
                try:future.result(timeout=max(0,min(.1,self.timeout-(time.monotonic()-start))))
                except FutureTimeout:pass
            text=response_text(reply,self.model,structured=structured)
            self.audit(dict(record="response",session=self.session_id,model=self.model,latency_s=elapsed,
                            response=text,info=reply.get("info")))
            self.calls+=1;self.last_raw=text
            self.request_state=dict(state="completed",elapsed_s=elapsed,was_slow=soft is not None and elapsed>=soft)
            return text
        except Exception as error:
            try:
                aborted=self.abort()
                if aborted is not True:self.closed=True
            except Exception as abort_error:
                self.closed=True
                error=RuntimeError(str(error)+"; abort unconfirmed, planner disabled: "+str(abort_error));aborted=False
            self.request_state=dict(state="failed",elapsed_s=time.monotonic()-start,error=str(error))
            self.audit(dict(record="error",model=self.model,error=str(error),abort_acknowledged=aborted))
            raise error
        finally:
            self.pending=None;pool.shutdown(wait=False)
    def close(self):
        self.closed=True
        if self.pending is not None:self.abort()
    def identity(self):
        return dict(side="P%d"%(self.index+1),provider=split_model(self.model)[0],model=split_model(self.model)[1],opencode_model=self.model,
                    key_source="existing OpenCode login",session_id=self.session_id)
