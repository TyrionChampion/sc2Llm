"""Bounded official-API skill authoring, isolated acceptance, game-thread installation.

Only JSON/source bytes cross into the game process; generated code runs under bwrap.
The developer subprocess never receives a game controller or another player's observation.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from arena.python_skills import PythonCatalog, PythonSkillRun, KINDS
from arena.skill_lab import SkillLab, replay_check, fixture_context, ROOT
from arena.persistent_skills import PersistentSkills
from arena.lifecycle import write_json_atomic

IDENTITY = 'deepseek/deepseek-flash'
PROFILES = ('local_tactic', 'queen_inject')
PROTOCOL = """
live_skill_development.enabled permits a separate top-level develop_skill request:
{"tasks":[],"reason":"中文简述需求","develop_skill":{"name":"queen_cycle","unit":"queen",
"goal":"能量足够时给没有注卵计时buff的己方基地注卵，等待确认再重复",
"verification":"queen_inject"}}.
Fields name/unit/goal/verification are required. No skills definitions or game tasks in that
reply: existing tasks continue. Request only genuinely missing local Python abilities,
not macro production/research or already callable commands. For Queen injection use
verification=queen_inject; other tactics use local_tactic. No Python source in battle replies.
Development runs in a separate official DeepSeek request, at most 3 jobs/player/match and
2 API attempts/job, one job at a time. Inspect live_skill_development.jobs. Do not repeat
developing or ready requests. Failed jobs retain full diagnostics. Existing source defects
also trigger a bounded repair; mission loss is not repaired. A ready skill appears in
python_skill_library: YOU must issue a NEW task with complete args. No automatic deployment.
Tests are isolated synthetic/local-observation checks, not proof of native game effects.
"""
DEVELOPER_SYSTEM = """
You implement ONE local Python SC2 tactic for the given race and request. Return one JSON
object ONLY: {"files":{"skills/NAME.py":"complete source","skills/NAME.json":"JSON manifest",
"tests/test_NAME.py":"unittest source"},"strategy":"简体中文使用说明"}.
Use exactly those three filenames, actual NAME from request, no extra files. Return all files
every round. tick(ctx,state) must follow CONTRACT.md. Only the host runs tests; never claim
native SC2 success. No access to credentials, OS, network or game controllers from your skill.
Implement local details, never macroeconomy or global strategy. Select only requested unit.
Default params must work. For repair preserve the entire manifest/interface.
Tests must assert behavior, including empty units, missing targets, unavailable abilities and
state transitions. Use /lab/fixtures/live.json for recorded local observations if useful.
On test failure use returned diagnostics to fix your source AND rerun via returning all files.
For queen_inject: use effect_inject_larva, own Hatchery/Lair/Hive only, sufficient Queen energy,
avoid an existing QueenSpawnLarvaTimer buff and pending injection orders; record pending state
to avoid duplicate spending. An issued action is not confirmation; observe energy/buff change.
Stay running when waiting; one missing/occupied base isn't permanent mission completion.
Host verification fixtures specify ability and buff IDs from the installed engine metadata.
Public explanations/descriptions/reasons must be 简体中文. Do not expose private reasoning.
"""

def request_schema():
    return dict(type='object', additionalProperties=False,
        required=['name','unit','goal','verification'], properties={
        'name':dict(type='string',pattern='^[a-z][a-z0-9_]{0,39}$'),
        'unit':dict(type='string',enum=sorted(KINDS)),
        'goal':dict(type='string',minLength=1,maxLength=800),
        'verification':dict(type='string',enum=list(PROFILES))})

def queen_cases():
    from pysc2.lib.buffs import Buffs
    ctx=fixture_context(False,126)
    ctx['units'][0].update(energy=50,health=175,health_max=175)
    base=deepcopy(ctx['units'][0])
    base.update(tag=301,type_id=86,pos=[32,30],energy=0,is_building=True,health=1500,health_max=1500)
    ctx['allies']=[base]
    ctx['available_spells']={'101':[dict(name='effect_inject_larva',ability_id=251,target='unit')]}
    cases={'ready':ctx}
    c=deepcopy(ctx);c['units'][0]['energy']=0;c['available_spells']={'101':[]};cases['no_energy']=c
    c=deepcopy(ctx);c['allies'][0]['buffs']=[int(Buffs.QueenSpawnLarvaTimer)];cases['already_injected']=c
    c=deepcopy(ctx);c['allies']=[];cases['no_base']=c
    c=deepcopy(ctx);c['units']=[];c['available_spells']={};cases['no_queen']=c
    return cases

def verify_profile(entry, profile):
    if profile=='local_tactic':
        return dict(passed=True,scope='generic SkillLab legality checks; tactical effectiveness unproven')
    reports={};cases=queen_cases()
    for name,ctx in cases.items():
        check=replay_check(entry,dict(context=ctx))
        actions=check['result']['actions']
        casts=[a for a in actions if a.get('action')=='cast' and a.get('ability')=='effect_inject_larva']
        if name=='ready':
            if len(casts)!=1 or casts[0].get('target')!=301 or casts[0].get('unit_tags')!=[101]:
                raise ValueError('queen_inject ready case must inject the owned local base with Queen 101')
            if check['result']['status']!='running':raise ValueError('injection service must remain running')
        elif casts:raise ValueError('queen_inject must not cast in '+name)
        reports[name]=check
    # Repeat immediately with same energy/no buff: unacknowledged orders must not be spammed.
    state=reports['ready']['result']['state']
    c=deepcopy(cases['ready']);c['game_loop']+=1;c['game_s']+=1/22.4
    check=replay_check(entry,dict(context=c,state=state))
    if any(a.get('ability')=='effect_inject_larva' for a in check['result']['actions']):
        raise ValueError('queen_inject repeated an unconfirmed order on the next tick')
    reports['pending_no_duplicate']=check
    # Confirmed timer appears, then expires: service must resume, not stick after first cast.
    c=deepcopy(cases['already_injected']);c['game_loop']+=224;c['game_s']+=10
    ack=replay_check(entry,dict(context=c,state=state))
    c=deepcopy(cases['ready']);c['game_loop']+=2240;c['game_s']+=100
    again=replay_check(entry,dict(context=c,state=ack['result']['state']))
    if not any(a.get('ability')=='effect_inject_larva' and a.get('target')==301 for a in again['result']['actions']):
        raise ValueError('queen_inject did not resume after observed buff expiry')
    reports['confirmed_then_reinject']=again
    return dict(passed=True,scope='synthetic Queen injection cycle, not native effect confirmation',cases=reports)

def store_for(root,race,name):
    return PersistentSkills(Path(root)/race/name)

def restore_live(root,race,destination,catalog=None):
    """Race + skill namespaces preserve independently authored skills and exact provider identity."""
    folder=Path(root)/race
    receipts=[]
    if not folder.exists():return catalog,receipts
    for path in sorted(folder.iterdir()):
        if path.is_symlink() or not path.is_dir() or not re.fullmatch(r'[a-z][a-z0-9_]{0,39}',path.name):
            raise ValueError('unsafe live library directory: '+str(path))
        library=store_for(root,race,path.name);current=library.latest(IDENTITY)
        if current is None:
            # Failed/unfinished publication may leave an empty namespace, visibly not callable.
            print('[live-skill] no published version for '+race+'/'+path.name+'; namespace not loaded',flush=True)
            receipts.append(dict(name=path.name,state='unpublished'));continue
        work=SkillLab(Path(destination)/path.name/'lab',race=race);work.setup()
        new,report=library.restore(IDENTITY,work,work.root.parent/'accepted',current['version'])
        if set(new.entries)!={path.name}:raise ValueError('live bundle must contain its one named skill')
        profile=current['manifest']['origin'].get('verification')
        if profile not in PROFILES:raise ValueError('live bundle missing verification profile')
        verify_profile(new.entries[path.name],profile)
        if catalog is None:catalog=PythonCatalog.empty()
        if path.name not in catalog.entries and len(catalog.entries)>=16:raise ValueError('live catalog limit exceeded')
        catalog.entries[path.name]=new.entries[path.name]
        receipts.append(dict(name=path.name,state='loaded',version=current['version'],verification=profile))
    return catalog,receipts

def develop_live(job, folder, developer=None):
    """Called outside the game process. At most two paid calls; no implicit retries."""
    if developer is None:
        from arena.deepseek_planner import DeepSeekFlashPlanner
        developer=DeepSeekFlashPlanner(developer=True)
    folder=Path(folder);req=job['request'];name=req['name']
    lab=SkillLab(folder/'lab',race=job['race']);lab.setup()
    (lab.root/'fixtures/live.json').write_text(json.dumps(job['observation'],ensure_ascii=False))
    if job.get('failure'):
        (lab.root/'fixtures/failure.json').write_text(json.dumps(job['failure'],ensure_ascii=False))
    cases=queen_cases() if req['verification']=='queen_inject' else {}
    (lab.root/'fixtures/verification.json').write_text(json.dumps(cases))
    old=job.get('old_entry');error=None;previous=None
    required={'skills/'+name+'.py','skills/'+name+'.json','tests/test_'+name+'.py'}
    for attempt in range(1,3):
        prompt=dict(request=req,race=job['race'],contract=(lab.root/'CONTRACT.md').read_text(),
            observation=job['observation'],verification_fixtures=cases,old_entry=old,
            failure=job.get('failure'),previous_reply=previous,previous_diagnostics=error)
        raw=None
        try:
            raw=developer.chat(DEVELOPER_SYSTEM,json.dumps(prompt,ensure_ascii=False))
            (folder/('reply-%d.json'%attempt)).write_text(raw)
            if len(raw.encode())>220000:raise ValueError('developer response exceeds 220KB')
            previous=raw
            reply=json.loads(raw);previous=reply
            if not isinstance(reply,dict) or set(reply)!={'files','strategy'}:raise ValueError('return exact files/strategy fields')
            files=reply['files']
            if not isinstance(files,dict) or set(files)!=required:raise ValueError('return exactly '+str(sorted(required)))
            if not isinstance(reply['strategy'],str) or not 1<=len(reply['strategy'])<=1200:raise ValueError('strategy requires 1..1200 chars')
            manifest=json.loads(files['skills/'+name+'.json'])
            if manifest.get('name')!=name or manifest.get('unit')!=req['unit'] or manifest.get('file')!=name+'.py':
                raise ValueError('manifest must match requested name/unit and filename')
            if old and manifest!=old['manifest']:raise ValueError('repair cannot change the public manifest')
            for path,content in files.items():lab.write(path,content)
            catalog=PythonCatalog(lab.root/'skills');entry=catalog.entries[name]
            if old and entry['sha256']==old['sha256']:raise ValueError('repair must change failed source')
            tested=lab.run_tests();checked=lab.validate()
            if not tested['passed'] or not checked['passed']:
                raise ValueError(json.dumps(dict(self_tests=tested,host_validation=checked),ensure_ascii=False))
            profile=verify_profile(entry,req['verification'])
            local=job['observation'].get('local_context')
            if local and local['units']:
                replay_check(entry,dict(context=local,map_size=job['map_size']))
            catalog,proof=lab.accept(folder/'accepted')
            report=dict(status='prepared',model=IDENTITY,strategy=reply['strategy'],skills=catalog.public(),
                frozen_directory=str((folder/'accepted').resolve()),profile=profile,**proof)
            if job.get('store_root'):
                library=store_for(job['store_root'],job['race'],name)
                report['persistent']=library.publish(IDENTITY,lab,catalog,report,expected=job['persistent_before'],
                    origin=dict(kind='live_authoring',verification=req['verification'],request=req,run=str(folder)))
            return report
        except Exception as exc:
            error=type(exc).__name__+': '+str(exc)
            write_json_atomic(folder/('failure-%d.json'%attempt),dict(error=error,attempt=attempt))
            # Do not spend a second call retrying transport/quota failures or a post-acceptance disk failure.
            if raw is None or (folder/'accepted').exists():raise
        finally:
            with (folder/'developer-usage.jsonl').open('a') as out:
                out.write(json.dumps(dict(attempt=attempt,usage=getattr(developer,'last_usage',None)),ensure_ascii=False)+'\n')
    raise ValueError('two development attempts failed: '+str(error))

class LiveSkillDevelopment:
    def __init__(self,root,store_root=None):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.store_root=str(Path(store_root).resolve()) if store_root else None
        self.commander=None;self.process=None;self.current=None;self.count=0;self.status={}
        self.seen_failures=set();self.closed=False;self.started=None;self.logfile=None
    def bind(self,commander):self.commander=commander
    def public(self):
        return dict(enabled=not self.closed,jobs=deepcopy(self.status),remaining_jobs=max(0,3-self.count),
            max_api_attempts_per_job=2,job_wall_timeout_s=240,
            developer_model=IDENTITY,developer_reasoning='high',validation='isolated checks; native effect unproven')
    def validate(self,request):
        c=self.commander
        if self.closed:raise ValueError('skill development closed')
        if self.process is not None:raise ValueError('a skill development job is already running')
        if self.count>=3:raise ValueError('skill development budget exhausted (3 jobs/player/match)')
        if not isinstance(request,dict) or set(request)!={'name','unit','goal','verification'}:
            raise ValueError('develop_skill requires exact name/unit/goal/verification fields')
        name=request['name'];unit=request['unit']
        if not isinstance(name,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,39}',name):raise ValueError('invalid skill name')
        if not isinstance(unit,str) or unit not in KINDS:raise ValueError('invalid unit selector')
        from arena.tasks import ALLOWED
        if name in ALLOWED|{'surrender','cancel_python_task'} or name in c.tasks.library:raise ValueError('reserved skill name')
        if not isinstance(request['goal'],str) or not 1<=len(request['goal'].strip())<=800:raise ValueError('goal must have 1..800 characters')
        if request['verification'] not in PROFILES:raise ValueError('unknown verification profile')
        if request['verification']=='queen_inject' and unit!='queen':raise ValueError('queen_inject requires queen selector')
        tree=c.tasks.tech_tree
        if tree is None:raise ValueError('live development requires engine-bound tech_tree')
        race_id={'terran':1,'zerg':2,'protoss':3}[c.tasks.race]
        if not any(i in tree.types and tree.types[i].race==race_id for i in KINDS[unit]):
            raise ValueError('selector incompatible with player race')
        entries=c.tasks.python_catalog.entries if c.tasks.python_catalog else {}
        if name in entries and self.status.get(name,{}).get('verification','local_tactic')=='queen_inject' and request['verification']!='queen_inject':
            raise ValueError('queen_inject verification may not be downgraded during repair')
        if name in entries and entries[name]['manifest']['unit']!=unit:raise ValueError('repair cannot change unit selector')
        if name not in entries and len(entries)>=16:raise ValueError('catalog limit 16')
        if self.status.get(name,{}).get('state')=='ready':raise ValueError('skill already ready; dispatch it instead')
    def request(self,request,w,failure=None):
        self.validate(request);c=self.commander;name=request['name']
        old=c.tasks.python_catalog.entries.get(name) if c.tasks.python_catalog else None
        if old and old['manifest']['file']!=name+'.py':
            raise ValueError('live repair requires original filename matching skill name; no silent rename')
        args=dict(count=1,target='home',retreat_target='home',timeout_s=120,params={})
        entry=old or dict(manifest=dict(actions=['cast'],unit=request['unit']))
        run=PythonSkillRun(entry,args)
        own=[u for u in w.my_units if u.type_id in KINDS[request['unit']]]
        run.tags={u.tag for u in own[:1]}
        run.mission=dict(objective=list(c.backend.homes[c.index]),retreat=list(c.backend.homes[c.index]),deadline_game_s=w.seconds+120)
        observation=dict(world=w.summary(),local_context=run.context(w,c.tasks))
        old_json=({**deepcopy(old),'source':old['source'].decode()} if old else None)
        self.count+=1;folder=self.root/('job-%d'%self.count);folder.mkdir()
        current=(store_for(self.store_root,c.tasks.race,name).latest(IDENTITY) if self.store_root else None)
        job=dict(request=deepcopy(request),race=c.tasks.race,old_entry=old_json,failure=deepcopy(failure),
            observation=observation,map_size=list(c.backend.size),store_root=self.store_root,
            persistent_before=current['version'] if current else None)
        write_json_atomic(folder/'job.json',job)
        self.current=dict(name=name,old_sha256=old['sha256'] if old else None,folder=folder,request=deepcopy(request))
        self.started=time.monotonic()
        self.logfile=(folder/'worker.log').open('w')
        try:
            self.process=subprocess.Popen([sys.executable,'-m','arena.live_skills','--job',str(folder/'job.json')],
                cwd=ROOT,stdin=subprocess.DEVNULL,stdout=self.logfile,stderr=subprocess.STDOUT,start_new_session=True)
        except Exception:
            self.logfile.close();self.logfile=None;self.current=None;raise
        if old:c.tasks.python_blocked.add(name)
        self.status[name]=dict(state='developing',goal=request['goal'],verification=request['verification'],
            old_sha256=self.current['old_sha256'],job=str(folder))
        c.record_feedback('python_development_started',skill=name,**self.status[name])
        c.emit('[live-skill] '+name+' developing in background; gameplay continues')
    def on_failure(self,details):
        c=self.commander;name=details['skill'];digest=details['sha256']
        if digest in self.seen_failures:return
        self.seen_failures.add(digest)
        error=details['error']
        if any(s in error for s in ('allocated unit lost','mission game-time deadline','engine rejected an allocated')):
            c.record_feedback('python_development_skipped',skill=name,reason='mission/engine outcome; source defect unproven');return
        entry=c.tasks.python_catalog.entries.get(name) if c.tasks.python_catalog else None
        if not entry or entry['sha256']!=digest:
            c.record_feedback('python_development_skipped',skill=name,reason='failure from older source version');return
        c.tasks.python_blocked.add(name)
        previous=self.status.get(name,{})
        req=dict(name=name,unit=entry['manifest']['unit'],goal='修复实际错误，保持公开接口和原行为：'+error[:700],
            verification=previous.get('verification','local_tactic'))
        self.status[name]=dict(state='failed',error=error,verification=req['verification'])
        try:
            # Failure callbacks occur while tasks execute, after ingest_feedback stored this exact world.
            self.request(req,c._seen_feedback_world,failure={**details,'map_size':list(c.backend.size)})
        except Exception as exc:
            c.record_feedback('python_development_skipped',skill=name,error=str(exc),reason='bounded developer unavailable; skill remains blocked')
    def _finish(self):
        if self.logfile:self.logfile.close()
        self.logfile=None;self.process=None;self.current=None
    def poll(self):
        if self.closed or self.process is None:return
        c=self.commander;job=self.current;name=job['name']
        expired=time.monotonic()-self.started>240
        if not expired and self.process.poll() is None:return
        try:
            if expired:
                try:os.killpg(self.process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                self.process.wait(timeout=2)
                raise TimeoutError('development exceeded 240 wall seconds; subprocess terminated')
            result=json.loads((job['folder']/'result.json').read_text())
            if self.process.returncode!=0 or not result.get('passed'):raise ValueError(result.get('error','developer subprocess failed'))
            report=result['report'];new=PythonCatalog(report['frozen_directory'])
            if set(new.entries)!={name} or new.public()!=report['skills']:raise ValueError('accepted source/catalog mismatch')
            entries=c.tasks.python_catalog.entries if c.tasks.python_catalog else {}
            old=entries.get(name)
            if (old['sha256'] if old else None)!=job['old_sha256']:raise ValueError('catalog changed during development')
            if name in c.tasks.library:raise ValueError('name now occupied by JSON skill')
            if name not in entries and len(entries)>=16:raise ValueError('catalog now full')
            if self.store_root and report.get('persistent',{}).get('state')!='published':raise ValueError('durable version missing')
            if c.tasks.python_catalog is None:c.tasks.python_catalog=PythonCatalog.empty()
            c.tasks.python_catalog.entries[name]=new.entries[name];c.tasks.python_blocked.discard(name)
            c.control_policy.update(python_tactics_enabled=True,python_tactic_local_retreat=True)
            self.status[name]=dict(state='ready',sha256=new.entries[name]['sha256'],verification=job['request']['verification'],
                persistent=report.get('persistent'),validation=report['profile']['scope'],
                handling='已通过隔离验收，等待主模型新任务派遣；尚未证明原生游戏效果。')
            c.record_feedback('python_development_ready',skill=name,**self.status[name])
            c.last_request=float('-inf')
            c.emit('[live-skill] '+name+' ready; awaiting NEW model dispatch')
        except Exception as exc:
            self.status[name]=dict(state='failed',error=type(exc).__name__+': '+str(exc),job=str(job['folder']),
                verification=job['request']['verification'])
            c.record_feedback('python_development_failed',skill=name,**self.status[name])
            c.emit('[live-skill-error] '+name+': '+str(exc))
        finally:self._finish()
    def close(self):
        if self.closed:return
        self.closed=True
        if self.process is not None:
            name=self.current['name']
            if self.process.poll() is None:
                try:os.killpg(self.process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                self.process.wait(timeout=2)
            self.status[name]=dict(state='cancelled',reason='match stopped; no late installation')
            self.commander.record_feedback('python_development_skipped',skill=name,reason='match stopped; developer terminated')
            self._finish()

def main():
    import argparse,traceback
    p=argparse.ArgumentParser();p.add_argument('--job',required=True);args=p.parse_args()
    path=Path(args.job);folder=path.parent
    try:
        report=develop_live(json.loads(path.read_text()),folder)
        write_json_atomic(folder/'result.json',dict(passed=True,report=report))
    except Exception as exc:
        write_json_atomic(folder/'result.json',dict(passed=False,error=type(exc).__name__+': '+str(exc),
            traceback=traceback.format_exc()))
        raise

if __name__=='__main__':main()
