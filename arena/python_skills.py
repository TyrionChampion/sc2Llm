"""Opt-in, frozen Python tactics. No user module is imported in the game process."""
import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from arena.composite_skills import PlanValidationError
from arena.raw_engine import command, MOVE, ATTACK, GATHER

from arena.tech_tree import ROLES, UNIT_NAMES, WORKERS, FORMS, REFERENCE, snake
KINDS = {snake(UNIT_NAMES[int(uid)]):{int(uid)} for uid in ROLES if int(uid) in UNIT_NAMES}
for base,form,down in FORMS:
    if base in UNIT_NAMES and snake(UNIT_NAMES[base]) in KINDS:KINDS[snake(UNIT_NAMES[base])].add(form)
KINDS['siege_tank']={33,32}
KINDS['army']=set().union(*(v for k,v in KINDS.items() if k not in ('scv','probe','drone','larva','mule','nuke','interceptor','locust','locust_flying','broodling','changeling','overlord','overlord_transport')))
KINDS['worker']=set(WORKERS)
for uid in {int(k) for k in REFERENCE['training']}|{int(k) for v in REFERENCE['training'].values() for k in v}:
    if uid in UNIT_NAMES:KINDS.setdefault(snake(UNIT_NAMES[uid]),{uid})
OPS = {"move", "attack_move", "attack_unit", "stop", "stim", "siege", "unsiege", "gather", "cast"}
TARGETS = {"home", "rally", "enemy_start", "nearest_enemy"}
MAX_WIRE = 65536
PROTOCOL = """
The JSON definition restrictions above apply to JSON skills only.
Optional python_skill_library lists accepted Python tactics. Each source version is frozen.
You remain the strategic commander: choose when/where to use them and allocate units.
Invoke a listed name with args={"count":N,"target":"home|rally|enemy_start|nearest_enemy",
"retreat_target":"home|rally","timeout_s":1..300,"params":{documented scalar parameters}}.
Each task controls only its allocated squad; scripts may make local retreat decisions.
Global combat reflex OFF does not disable an explicitly assigned Python tactic.
Cancel a Python task with {"skill":"cancel_python_task","args":{"task_id":ID}};
this requests stop orders, releases its squad, and permits a new assignment in the same plan.
Do not invoke unlisted Python names or send source code during battle. Empty tasks keep missions running.
Python script-reported completion is not engine-confirmed victory or objective completion.
"""

def invalid(field, value, expected):
    raise PlanValidationError([dict(field=field, received=value, expected=expected)])

def finite(value):
    try:return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:return False

class PythonCatalog:
    """Explicit directory, single-file stdlib skills, immutable source snapshots."""
    def __init__(self, folder, reserved=()):
        from arena.tasks import ALLOWED
        reserved=set(reserved)|ALLOWED
        root = Path(folder).resolve(strict=True)
        if not root.is_dir(): raise ValueError("Python skill path must be a directory")
        self.entries = {}
        files = sorted(root.glob("*.json"))
        if not 1 <= len(files) <= 16: raise ValueError("Python skill directory needs 1..16 JSON manifests")
        for path in files:
            if path.is_symlink() or path.stat().st_size > 16384: raise ValueError("unsafe/oversized manifest: "+path.name)
            d = json.loads(path.read_text())
            fields = {"name", "description", "file", "unit", "actions", "parameters"}
            if not isinstance(d, dict) or set(d) != fields: raise ValueError("manifest fields must be "+str(sorted(fields)))
            name = d["name"]
            if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", name): raise ValueError("invalid Python skill name")
            if name in set(reserved)|{"cancel_python_task", "surrender"} or name in self.entries: raise ValueError("reserved/duplicate skill: "+name)
            if not isinstance(d["description"], str) or not 1 <= len(d["description"]) <= 1200: raise ValueError("description must have 1..1200 characters")
            if not isinstance(d["unit"], str) or d["unit"] not in KINDS: raise ValueError("unsupported unit selector")
            if not isinstance(d["actions"], list) or not d["actions"] or any(not isinstance(a,str) or a not in OPS for a in d["actions"]): raise ValueError("unsupported actions")
            params = d["parameters"]
            if not isinstance(params, dict) or len(params) > 16: raise ValueError("parameters must be an object of up to 16 descriptions")
            if any(not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", k) or not isinstance(v,str) or len(v)>400 for k,v in params.items()): raise ValueError("invalid parameter documentation")
            filename = d["file"]
            if not isinstance(filename,str) or Path(filename).name != filename or not filename.endswith(".py"): raise ValueError("file must be a local .py basename")
            sourcepath = root / filename
            if sourcepath.is_symlink() or sourcepath.stat().st_size > MAX_WIRE: raise ValueError("unsafe/oversized source")
            source = sourcepath.read_bytes()
            ast.parse(source, filename=filename)  # Syntax check only; never execute submitted code in host.
            digest = hashlib.sha256(source).hexdigest()
            self.entries[name] = dict(manifest=deepcopy(d), source=source, sha256=digest)
        if not shutil.which("bwrap"): raise RuntimeError("bwrap missing; Python skills disabled, no in-process fallback")
    @classmethod
    def empty(cls):
        """Explicit runtime registry; never a fallback for a broken directory."""
        if not shutil.which("bwrap"):raise RuntimeError("bwrap missing; no in-process fallback")
        catalog=cls.__new__(cls);catalog.entries={}
        return catalog

    def public(self):
        return [{**deepcopy(e["manifest"]), "sha256":e["sha256"]} for e in self.entries.values()]
    def validate_args(self, name, args, field):
        expected = {"count", "target", "retreat_target", "timeout_s", "params"}
        if not isinstance(args,dict) or set(args)!=expected: invalid(field,args,"exact fields: "+", ".join(sorted(expected)))
        if type(args["count"]) is not int or not 1<=args["count"]<=32: invalid(field+".count",args["count"],"integer 1..32 allocated units")
        if not isinstance(args["target"],str) or args["target"] not in TARGETS: invalid(field+".target",args["target"],str(sorted(TARGETS)))
        if args["retreat_target"] not in ("home","rally"): invalid(field+".retreat_target",args["retreat_target"],"home or rally")
        if not finite(args["timeout_s"]) or not 1<=args["timeout_s"]<=300: invalid(field+".timeout_s",args["timeout_s"],"finite 1..300 game seconds")
        p=args["params"]; allowed=self.entries[name]["manifest"]["parameters"]
        if not isinstance(p,dict) or set(p)-set(allowed): invalid(field+".params",p,"documented parameters only: "+str(sorted(allowed)))
        for key,value in p.items():
            if not (value is None or type(value) is bool or finite(value) or isinstance(value,str) and len(value)<=256): invalid(field+".params."+key,value,"finite number, boolean, null or string <=256 chars")
        return deepcopy(args)

class IsolatedWorker:
    """All spawn/pipe I/O runs in one background thread; game loop only polls futures."""
    def __init__(self, entry, budget_s=.05, startup_s=2):
        self.entry=entry; self.budget=budget_s; self.startup=startup_s
        self.pool=ThreadPoolExecutor(max_workers=1, thread_name_prefix="python-skill")
        self.future=None; self.proc=None; self.closed=False; self.lock=threading.Lock()
        self.folder=None; self.reader=None; self.pending=b""
    def submit(self, ctx, state):
        if self.closed or self.future is not None: raise RuntimeError("worker closed or busy")
        data=json.dumps(dict(ctx=ctx,state=state),allow_nan=False,separators=(",",":")).encode()+b"\n"
        if len(data)>MAX_WIRE: raise ValueError("Python skill observation exceeds 64 KiB; not truncated")
        self.future=self.pool.submit(self._exchange,data)
    def poll(self):
        if self.future is None or not self.future.done(): return None
        f=self.future; self.future=None
        return f.result()
    def _start(self):
        self.folder=tempfile.TemporaryDirectory(prefix="sc2-skill-")
        root=Path(self.folder.name)
        (root/"tactic.py").write_bytes(self.entry["source"])
        (root/"runner.py").write_bytes(Path(__file__).with_name("python_worker.py").read_bytes())
        base=Path(sys.base_prefix).resolve()
        executable="/runtime/bin/"+Path(sys.executable).resolve().name
        argv=["bwrap","--unshare-all","--die-with-parent","--new-session","--clearenv",
            "--ro-bind",str(base),"/runtime","--ro-bind","/usr/lib","/usr/lib",
            "--symlink","usr/lib","/lib","--ro-bind","/lib64","/lib64",
            "--proc","/proc","--dev","/dev","--tmpfs","/tmp",
            "--ro-bind",str(root),"/skill","--chdir","/tmp",
            executable,"-I","-S","-u","/skill/runner.py"]
        with self.lock:
            if self.closed: raise RuntimeError("worker cancelled before launch")
            self.proc=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                start_new_session=True,env={"PATH":"/usr/bin:/bin"},bufsize=0)
        self.reader=selectors.DefaultSelector()
        for pipe in (self.proc.stdin,self.proc.stdout,self.proc.stderr):os.set_blocking(pipe.fileno(),False)
        self.reader.register(self.proc.stdout,selectors.EVENT_READ,"wire")
        self.reader.register(self.proc.stderr,selectors.EVENT_READ,"diagnostics")
        ready=self._message(b"",self.startup)
        if ready.get("ready") is not True: raise RuntimeError("Python import failed: "+json.dumps(ready))
        return ready.get("diagnostics","")
    def _drain_diagnostics(self,errors,total):
        while True:
            try:chunk=os.read(self.proc.stderr.fileno(),8192)
            except BlockingIOError:break
            if not chunk:break
            total+=len(chunk);errors.extend(chunk)
            if total>MAX_WIRE:raise ValueError("Python output/diagnostics exceeds 64 KiB")
        return total
    def _message(self, data, seconds):
        end=time.monotonic()+seconds; errors=bytearray(); total=0
        if data:self.reader.register(self.proc.stdin,selectors.EVENT_WRITE,"input")
        while True:
            if self.closed: raise RuntimeError("Python worker cancelled")
            if time.monotonic()>=end: raise TimeoutError("Python worker wall-time budget exceeded (%.3fs)"%seconds)
            if not data and b"\n" in self.pending:
                # A response is written after diagnostics; drain that pipe before accepting it.
                total=self._drain_diagnostics(errors,total)
                line,self.pending=self.pending.split(b"\n",1)
                obj=json.loads(line)
                if not isinstance(obj,dict):raise ValueError("worker wire response must be object")
                obj["diagnostics"]=errors.decode(errors="replace")
                return obj
            for key,_ in self.reader.select(max(0,end-time.monotonic())):
                if key.data=="input":
                    n=os.write(key.fileobj.fileno(),data);data=data[n:]
                    if not data:self.reader.unregister(key.fileobj)
                    continue
                chunk=os.read(key.fileobj.fileno(),8192)
                if not chunk:
                    self.reader.unregister(key.fileobj)
                    if key.data=="wire":
                        self._drain_diagnostics(errors,total)
                        raise RuntimeError("Python worker exited without response (code=%s): "%self.proc.poll()+errors.decode(errors="replace"))
                    continue
                total+=len(chunk)
                if total>MAX_WIRE or len(self.pending)+len(chunk)>MAX_WIRE: raise ValueError("Python output/diagnostics exceeds 64 KiB")
                if key.data=="wire":self.pending+=chunk
                else:errors.extend(chunk)
    def _exchange(self,data):
        try:
            startup_logs=self._start() if self.proc is None else ""
            start=time.monotonic();result=self._message(data,self.budget)
            result["elapsed_ms"]=(time.monotonic()-start)*1000
            result["diagnostics"]=startup_logs+result.get("diagnostics","")
            return result
        except Exception:
            self._kill();self._cleanup();raise
    def _kill(self):
        with self.lock:
            p=self.proc
            if p and p.poll() is None:
                try:os.killpg(p.pid,signal.SIGKILL)
                except ProcessLookupError:pass
    def _cleanup(self):
        if self.proc:
            try:self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired: self._kill();self.proc.wait(timeout=2)
            for pipe in (self.proc.stdin,self.proc.stdout,self.proc.stderr):pipe.close()
        if self.reader:self.reader.close()
        if self.folder:self.folder.cleanup()
    def close(self):
        if self.closed:return
        self.closed=True;self._kill()
        # Ordered behind the in-flight exchange; no process.wait on game loop.
        self.pool.submit(self._cleanup)
        self.pool.shutdown(wait=False)

class PythonSkillRun:
    def __init__(self,entry,args):
        self.entry=entry;self.args=deepcopy(args);self.tags=set();self.worker=None
        self.state={};self.sent_loop=None;self.call_id=0;self.last={};self.next_at=0
        self.mission=None;self.calls=0;self.last_ms=None;self.last_context=None
    def summary(self):
        return dict(kind="python",sha256=self.entry["sha256"],unit_tags=sorted(self.tags),
                    mission=self.mission,args=deepcopy(self.args),calls=self.calls,last_tick_ms=self.last_ms)
    def close(self):
        if self.worker:self.worker.close()
    def stop_orders(self,w,q,t,output):
        tags=sorted(u.tag for u in w.my_units if u.tag in self.tags and not u.is_building)
        if tags:
            if not q.issue(t,command(4,tags),output):return
            q.action_contexts[-1].update(source="python_skill_stop",sha256=self.entry["sha256"])
    def fail(self,w,q,t,output,error,**extra):
        self.stop_orders(w,q,t,output)
        q.event("python_skill_failed",task_id=t.id,skill=t.skill,error=str(error),
                sha256=self.entry["sha256"],call_id=self.call_id,
                context=deepcopy(self.last_context),execution_context=self.context(w,q),state=deepcopy(self.state),
                handling="Worker stopped; stop orders requested, not confirmed; no fallback or replacement units.",**extra)
        q.update(t,"failed",str(error))
    def resolve(self,label,w,q):
        home=q.backend.homes[q.index];enemy=q.backend.enemy_start(q.index)
        if label=="home":return list(home)
        if label=="enemy_start":return list(enemy)
        if label=="rally":
            distance=math.dist(home,enemy);scale=min(12/distance,1) if distance else 0
            return [home[i]+(enemy[i]-home[i])*scale for i in (0,1)]
        if not w.enemy_units:raise ValueError("nearest_enemy requested but no currently visible enemy; no fallback")
        return list(min(w.enemy_units,key=lambda u:u.distance(home)).pos)
    def context(self,w,q):
        own=[u for tag in sorted(self.tags) if (u:=w.by_tag(tag)) is not None]
        transports=[u for u in w.my_units if set(u.passengers)&self.tags]
        embarked=[dict(tag=tag,transport_tag=u.tag,pos=list(u.pos)) for u in transports for tag in u.passengers if tag in self.tags]
        local=[u for u in w.units if any(u.distance(a)<=20 for a in own+transports)]
        def encode(u):
            meta=getattr(q.backend,"types",{}).get(u.type_id)
            return dict(tag=u.tag,type_id=u.type_id,pos=list(u.pos),health=u.health,health_max=u.health_max,
                flying=u.flying,is_building=u.is_building,ground_dps=u.ground_dps,air_dps=u.air_dps,
                attack_range=u.attack_range,energy=u.energy,energy_max=u.energy_max,cargo_taken=u.cargo_taken,cargo_max=u.cargo_max,passengers=list(u.passengers),buffs=list(u.buffs),orders=list(u.orders),
                order_targets=list(u.order_targets),order_positions=list(u.order_positions),
                mineral_cost=meta.mineral_cost if meta is not None else None,
                gas_cost=meta.vespene_cost if meta is not None else None)
        spellbook={}
        if 'cast' in self.entry['manifest']['actions']:
            tree=getattr(q.backend,'tech_tree',None)
            if tree is None:raise ValueError('cast requires engine-bound tech_tree; no fabricated ability availability')
            available=q.backend.available_abilities(q.index,[u.tag for u in own])
            for u in own:spellbook[str(u.tag)]=tree.public_spells(u.type_id,available.get(u.tag,set()))
        return dict(available_spells=spellbook,embarked=embarked,game_loop=w.game_loop,game_s=w.seconds,mission=deepcopy(self.mission),
            params=deepcopy(self.args["params"]),units=[encode(u) for u in own],
            allies=[encode(u) for u in local if u.alliance==1 and u.tag not in self.tags],
            enemies=[encode(u) for u in local if u.alliance==4],
            minerals=[encode(u) for u in local if u.mineral],
            stim_researched=15 in w.upgrades,local_radius=20)
    def validate_result(self,result,w,q):
        if not isinstance(result,dict) or set(result)-{"actions","state","status","reason"}:raise ValueError("tick must return actions/state/status/reason only")
        if not {"actions","state","status","reason"}<=set(result):raise ValueError("tick result missing required fields")
        if result["status"] not in ("running","done","failed"):raise ValueError("invalid tick status")
        if not isinstance(result["reason"],str) or len(result["reason"])>1000:raise ValueError("reason must be string <=1000 chars")
        if not isinstance(result["state"],dict) or len(json.dumps(result["state"],allow_nan=False))>8192:raise ValueError("state must be finite JSON object <=8 KiB")
        actions=result["actions"]
        if not isinstance(actions,list) or len(actions)>32:raise ValueError("actions must be list <=32")
        if result["status"]!="running" and actions:raise ValueError("terminal status must have empty actions; host requests stop")
        local=self.context(w,q);enemies={u["tag"] for u in local["enemies"]};minerals={u["tag"] for u in local["minerals"]}
        compiled=[];seen=set()
        for a in actions:
            if not isinstance(a,dict) or set(a)-{"action","unit_tags","target","ability"}:raise ValueError("unknown action fields")
            op=a.get("action");tags=a.get("unit_tags")
            if not isinstance(op,str) or op not in self.entry["manifest"]["actions"]:raise ValueError("action not granted in manifest")
            if not isinstance(tags,list) or not tags or any(type(t) is not int for t in tags) or len(set(tags))!=len(tags):raise ValueError("unit_tags must be distinct integers")
            if not set(tags)<=self.tags or seen.intersection(tags):raise ValueError("unallocated unit or multiple orders for same unit in one tick")
            seen.update(tags);units=[w.by_tag(tag) for tag in tags]
            if any(u is None or u.alliance!=1 for u in units):raise ValueError("allocated unit no longer alive/owned")
            target=a.get("target");pos=None;tag=None
            if op!='cast' and 'ability' in a:raise ValueError('ability is only allowed for cast')
            if op=='cast':
                tree=getattr(q.backend,'tech_tree',None)
                if tree is None:raise ValueError('cast requires engine-bound tech_tree')
                ability=tree.by_ability_name.get(a.get('ability'))
                if ability is None or any(ability not in tree.tactical_abilities(u.type_id) for u in units):raise ValueError('ability not granted for this unit type or is a macroeconomic operation')
                kind=tree.abilities[ability].target
                if type(target) is int:
                    if kind not in (3,4) or not any(v.tag==target for v in w.units if any(v.distance(u)<=20 for u in units)):raise ValueError('cast unit target not visible/local or wrong target kind')
                    tag=target
                elif isinstance(target,list) and len(target)==2 and all(finite(x) for x in target):
                    if kind not in (2,4,5) or any(u.distance(target)>20 for u in units):raise ValueError('cast point target must be local and supported')
                    if not all(0<=target[k]<=q.backend.size[k] for k in (0,1)):raise ValueError('cast point outside map')
                    pos=target
                elif target is not None or kind not in (1,5):raise ValueError('cast requires the engine-specified target')
            elif op in ("move","attack_move"):
                if isinstance(target,str) and target in ("objective","retreat"):pos=self.mission[target]
                elif isinstance(target,list) and len(target)==2 and all(finite(x) for x in target):
                    pos=target
                    if any(u.distance(pos)>20 for u in units):raise ValueError("position exceeds local 20-unit micro radius; use commander objective/retreat")
                else:raise ValueError("position target must be objective/retreat or finite [x,y]")
                if not all(0<=pos[i]<=q.backend.size[i] for i in (0,1)):raise ValueError("target outside map")
                ability=MOVE if op=="move" else ATTACK
                if any(u.type_id==32 for u in units):raise ValueError("sieged tank cannot move; unsiege first")
            elif op in ("attack_unit","gather"):
                if type(target) is not int or target not in (enemies if op=="attack_unit" else minerals):raise ValueError("target not currently visible within allocated squad's local radius")
                tag=target;ability=ATTACK if op=="attack_unit" else GATHER
                if op=="gather" and any(u.type_id not in WORKERS for u in units):raise ValueError("gather requires a worker")
                if op=="attack_unit" and any(not u.can_attack(w.by_tag(tag)) for u in units):raise ValueError("unit cannot attack target's movement layer")
            else:
                if "target" in a:raise ValueError("this action takes no target")
                ability={"stop":4,"stim":3675,"siege":388,"unsiege":390}[op]
                if op=="stim" and (15 not in w.upgrades or any(u.type_id not in (48,51) or u.health<=(10 if u.type_id==48 else 20) or set(u.buffs)&{24,27} for u in units)):raise ValueError("stim requires researched, unstimmed Marine with >10 HP")
                if op in ("siege","unsiege") and any(u.type_id!=({"siege":33,"unsiege":32}[op]) for u in units):raise ValueError("tank is in wrong mode")
            compiled.append((a,ability,tuple(tags),pos,tag))
        # Query only spells. State/selection checks above do not claim engine acceptance.
        spells=[c for c in compiled if c[0]["action"]=="cast" or c[1] in (3675,388,390)]
        if spells:
            abilities=q.backend.available_abilities(q.index,[t for c in spells for t in c[2]])
            for a,ability,tags,_,_ in spells:
                accepted={3675,380,253} if ability==3675 else {ability}
                tree=getattr(q.backend,'tech_tree',None)
                if any(not (tree.ability_available(ability,abilities.get(t,set())) if tree else accepted.intersection(abilities.get(t,set()))) for t in tags):raise ValueError("engine ability query disallows "+a["action"])
        return compiled
    def tick(self,w,q,t,used,bank,output):
        result=None
        try:
            if w.seconds-t.created>=self.args["timeout_s"]:raise TimeoutError("Python mission game-time deadline exceeded")
            if not self.tags:
                candidates=[u for u in w.my_units if u.done and u.type_id in KINDS[self.entry["manifest"]["unit"]] and (self.entry["manifest"]["unit"]!="army" or not u.is_building) and u.tag not in used and u.tag not in q.protected()]
                if len(candidates)<self.args["count"]:
                    q.update(t,"waiting","need %d unreserved %s units"%(self.args["count"],self.entry["manifest"]["unit"]));return bank
                self.mission=dict(objective=self.resolve(self.args["target"],w,q),retreat=self.resolve(self.args["retreat_target"],w,q),deadline_game_s=t.created+self.args["timeout_s"])
                self.tags={u.tag for u in sorted(candidates,key=lambda u:u.tag)[:self.args["count"]]}
                used.update(self.tags);self.worker=IsolatedWorker(self.entry)
                q.event("python_skill_started",task_id=t.id,skill=t.skill,sha256=self.entry["sha256"],unit_tags=sorted(self.tags),mission=self.mission,args=self.args)
                q.update(t,"running","Python tactic owns allocated squad; LLM still owns strategy")
            embarked={tag for u in w.my_units for tag in u.passengers}
            if any((w.by_tag(tag) is None and tag not in embarked) or (w.by_tag(tag) is not None and w.by_tag(tag).alliance!=1) for tag in self.tags):raise ValueError("allocated unit lost; no replacement")
            if any(tag in self.tags for tag,ability,code in w.errors):raise RuntimeError("engine rejected an allocated unit command; see correlated engine feedback for result/cause")
            response=self.worker.poll()
            if response is not None:
                if response.get("diagnostics"):q.event("python_skill_output",task_id=t.id,text=response["diagnostics"])
                if "error" in response:
                    self.fail(w,q,t,output,response["error"],traceback=response.get("traceback"));return bank
                if (w.game_loop-self.sent_loop)/22.4>.75:raise TimeoutError("Python result stale (>0.75 game seconds); discarded")
                result=response.get("result");compiled=self.validate_result(result,w,q)
                self.calls+=1;self.last_ms=response["elapsed_ms"];self.state=result["state"]
                if result["status"]=="failed":raise RuntimeError("script reported failure: "+result["reason"])
                if result["status"]=="done":
                    self.stop_orders(w,q,t,output)
                    q.event("python_skill_reported_done",task_id=t.id,reason=result["reason"],verified_objective=False)
                    q.update(t,"completed","script-reported done (objective unverified): "+result["reason"]);return bank
                evidence_written=False
                for a,ability,tags,pos,tag in compiled:
                    # Suppress unchanged orders per unit, not per caller's group ordering.
                    signature=(ability,tuple(round(x,2) for x in pos) if pos is not None else None,tag)
                    changed=[]
                    for u in tags:
                        previous=self.last.get(u);entity=w.by_tag(u)
                        if previous and previous[0]==signature:
                            age=w.seconds-previous[1]
                            if age<2:continue
                            if ability in (MOVE,ATTACK,GATHER) and (entity.orders or entity.engaged_target):continue
                            if pos is not None and entity.distance(pos)<1.5:continue
                        changed.append(u)
                    if not changed:continue
                    if not q.issue(t,command(ability,changed,pos=pos,target=tag),output):continue
                    q.action_contexts[-1].update(source="python_skill",sha256=self.entry["sha256"],call_id=self.call_id,observed_game_loop=self.sent_loop)
                    for unit_tag in changed:self.last[unit_tag]=(signature,w.seconds)
                    q.event("python_skill_action",task_id=t.id,call_id=self.call_id,action=a,reason=result["reason"],execution="dispatched; not proof of effect",
                            observation=self.last_context if not evidence_written else None,
                            observation_ref_call_id=self.call_id,state=self.state)
                    evidence_written=True
            if self.worker.future is None and w.seconds>=self.next_at:
                self.call_id+=1;self.sent_loop=w.game_loop;self.next_at=w.seconds+.1
                self.last_context=self.context(w,q)
                self.worker.submit(self.last_context,self.state)
        except Exception as error:self.fail(w,q,t,output,type(error).__name__+": "+str(error),
            rejected_result=result,observed_game_loop=self.sent_loop)
        return bank
