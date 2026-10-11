"""Model-authored pregame files with explicit, bounded fixture validation."""
import json
from pathlib import Path
import time
from types import SimpleNamespace
from arena.python_skills import PythonCatalog, PythonSkillRun, IsolatedWorker, KINDS
from arena.raw_engine import Entity, Snapshot
from arena.tasks import ALLOWED

PREP_SYSTEM="""You are preparing your OWN Terran player for a realtime StarCraft II match.
You will continue making strategic decisions during the match; Python tactics handle squad details.
You have no file/shell tools. AUTHOR actual standalone Python source in a JSON files response.
Return ONLY {"files":[{"name":"unique_tactic.json","content":"JSON manifest text"},
{"name":"unique_tactic.py","content":"Python source text"}],"strategy":"brief strategy"}.
Author ONE compact useful tactic (roughly 40..80 lines), manifest+single Python file, obeying the interface.
Keep analysis brief; a correct small tactic is sufficient. Do not add optional features.
Do not copy a supplied example: design your own tactic. Prefer useful Marine micro; normal game
starts with SCVs and a CommandCenter. You must build a Barracks and train Marines with macro tasks.
Production, research, gas gathering and expansion are available through race-aware macros; explicitly build their prerequisites.
Choose sensible local retreat hysteresis; do not take over macroeconomy. Use only documented fields.
Missing tech/health/cost observations must be handled visibly. Return finite JSON; do not use markdown fences.
The engine has NOT started yet. You may correct failures within the preparation deadline.
"""

def write_bundle(raw,folder):
    value=json.loads(raw)
    if not isinstance(value,dict) or set(value)!={"files","strategy"}:raise ValueError("preparation must return exactly files,strategy")
    if not isinstance(value["strategy"],str) or len(value["strategy"])>4000:raise ValueError("strategy must be text <=4000 characters")
    files=value["files"]
    if not isinstance(files,list) or not 2<=len(files)<=6:raise ValueError("need 1..3 manifest/Python pairs (2..6 files)")
    names=set()
    for item in files:
        if not isinstance(item,dict) or set(item)!={"name","content"}:raise ValueError("each file needs name/content")
        name=item["name"];content=item["content"]
        if not isinstance(name,str) or Path(name).name!=name or Path(name).suffix not in (".py",".json") or name in names:raise ValueError("unsafe or duplicate skill filename")
        if not isinstance(content,str) or len(content.encode())>65536:raise ValueError("file content must be text <=64 KiB")
        names.add(name)
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=False)
    for item in files:(folder/item["name"]).write_text(item["content"])
    catalog=PythonCatalog(folder,reserved=ALLOWED)
    expected={e["manifest"]["file"] for e in catalog.entries.values()}
    if expected!={name for name in names if name.endswith(".py")}:raise ValueError("every Python file must have a manifest")
    return catalog,value["strategy"]

def smoke_catalog(catalog,race="terran"):
    """Synthetic states + real worker. Explicitly not engine/strategy validation."""
    results=[]
    for name,entry in catalog.entries.items():
        from arena.skill_lab import representative
        from arena.knowledge import cached_tree
        kind=representative(entry["manifest"]["unit"],race)
        backend=SimpleNamespace(homes=[(10,10)],size=(200,200),enemy_start=lambda i:(160,160),
            tech_tree=cached_tree(),types={kind:SimpleNamespace(mineral_cost=50,vespene_cost=0),48:SimpleNamespace(mineral_cost=50,vespene_cost=0)},
            available_abilities=lambda i,tags:{t:{380,388,390} for t in tags})
        q=SimpleNamespace(backend=backend,index=0)
        run=PythonSkillRun(entry,dict(count=1,target="enemy_start",retreat_target="home",timeout_s=90,params={}))
        run.tags={101};run.mission=dict(objective=[160,160],retreat=[10,10],deadline_game_s=90)
        worker=IsolatedWorker(entry);state={}
        try:
            for case in ("quiet","enemy_near","low_health"):
                own=Entity(101,kind,1,30,30,8 if case=="low_health" else 45,45,ground_dps=10,attack_range=5)
                units=[own]
                if case!="quiet":units.extend(Entity(200+j,48,4,35+j*.1,30,45,45,ground_dps=10,attack_range=5) for j in range(4))
                w=Snapshot(224,500,12,15,tuple(units),(30,30),upgrades=(15,))
                worker.submit(run.context(w,q),state);until=time.monotonic()+3
                while True:
                    reply=worker.poll()
                    if reply is not None:break
                    if time.monotonic()>until:raise TimeoutError("smoke worker did not finish")
                    time.sleep(.002)
                if reply.get("error"):raise ValueError(reply["error"]+"\n"+reply.get("traceback",""))
                value=reply.get("result");run.validate_result(value,w,q)
                if value["status"]=="failed":raise ValueError("fixture script failure: "+value["reason"])
                state=value["state"]
                results.append(dict(skill=name,fixture=case,status=value["status"],latency_ms=reply["elapsed_ms"],diagnostics=reply.get("diagnostics","")))
        finally:worker.close()
    return results

def prepare_player(planner,folder,seconds):
    start=time.monotonic();deadline=start+seconds;folder=Path(folder);attempt=0;errors=[]
    manual=(Path(__file__).resolve().parents[1]/"AI_SKILL_MANUAL.md").read_text()
    contract=manual.split("## 0 Python",1)[1].split("## 1 分工",1)[0]
    prompt="Interface contract:\n## 0 Python"+contract+"\nWrite your initial skill bundle now."
    while time.monotonic()<deadline and attempt<4:
        attempt+=1;remaining=deadline-time.monotonic();planner.timeout=min(remaining,100)
        print("[prepare P%d] authoring attempt %d; remaining %.0fs"%(planner.index+1,attempt,remaining),flush=True)
        raw=planner.chat(PREP_SYSTEM,prompt)
        try:
            catalog,strategy=write_bundle(raw,folder/("attempt-%d"%attempt))
            results=smoke_catalog(catalog)
            if time.monotonic()>deadline:raise TimeoutError("preparation deadline exceeded during validation")
            report=dict(status="prepared",model=planner.model,session_id=planner.session_id,wall_s=time.monotonic()-start,
                attempts=attempt,validation="synthetic contexts in real isolated Python; not native gameplay",smoke=results,
                skills=catalog.public(),strategy=strategy,previous_errors=errors)
            (folder/"preparation.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
            planner.audit(dict(record="preparation_finished",**report))
            print("[prepare P%d] ready: %s"%(planner.index+1,", ".join(catalog.entries)),flush=True)
            planner.timeout=60;planner.calls=0;planner.last_raw=None;planner.phase="battle"
            return catalog,report
        except (ValueError,SyntaxError,RuntimeError,TimeoutError) as error:
            message=type(error).__name__+": "+str(error);errors.append(message)
            planner.audit(dict(record="preparation_validation_failed",attempt=attempt,error=message))
            print("[prepare P%d] REJECTED: %s"%(planner.index+1,message),flush=True)
            prompt="Your previous bundle was rejected. No game started. Error:\n"+message+"\nReturn a COMPLETE corrected files/strategy JSON bundle."
    raise RuntimeError("P%d preparation failed within deadline/4 attempts: %s"%(planner.index+1,errors))
