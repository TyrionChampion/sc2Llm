"""Bounded background repair. Only the game thread installs an immutable version."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from arena.lab_prepare import develop

class SkillRepair:
    def __init__(self,developer,lab,root,budget=300,library=None,library_version=None):
        self.developer=developer;self.lab=lab;self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.library=library;self.library_version=library_version
        self.budget=budget;self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='skill-repair')
        self.future=None;self.current=None;self.seen=set();self.count=0;self.closed=False;self.status={};self.commander=None
    def bind(self,commander):self.commander=commander
    def on_failure(self,details):
        c=self.commander;name=details['skill'];digest=details['sha256'];error=details['error']
        # Unit losses/deadlines are mission outcomes; editing code cannot restore the game state.
        if any(s in error for s in ('allocated unit lost','mission game-time deadline','engine rejected an allocated')):
            c.record_feedback('python_repair_skipped',skill=name,error=error,reason='mission/engine outcome, not a demonstrated source defect');return
        if self.closed:return
        if self.future is not None:
            c.record_feedback('python_repair_skipped',skill=name,reason='another repair is already in progress');return
        if digest in self.seen or self.count>=3:
            c.tasks.python_blocked.add(name)
            self.status[name]=dict(state='failed',error='repair budget exhausted for this source/match',redeploy='Skill blocked; use another command.')
            c.record_feedback('python_repair_skipped',skill=name,reason='bounded repair budget exhausted or this source was already attempted');return
        entry=c.tasks.python_catalog.entries[name]
        if entry['sha256']!=digest:
            c.record_feedback('python_repair_skipped',skill=name,reason='failure came from an older frozen version; current catalog is newer');return
        self.seen.add(digest);self.count+=1
        context=deepcopy(details);context['map_size']=list(c.backend.size)
        self.current=dict(skill=name,old_sha256=digest,entry=deepcopy(entry),failure=context,library_version=self.library_version,catalog_before=c.tasks.python_catalog.public())
        c.tasks.python_blocked.add(name)
        self.status[name]=dict(state='repairing',old_sha256=digest,error=error,redeploy='wait for a new version; other tasks continue')
        c.emit('[python-repair] '+name+' queued in background; gameplay continues')
        c.record_feedback('python_repair_started',skill=name,sha256=digest,error=error)
        self.future=self.pool.submit(self._develop,deepcopy(self.current),self.count)
    def _develop(self,job,number):
        # No controller, World or task queue access from this worker.
        entry=job['entry'];m=entry['manifest'];folder=self.root/('revision-%d'%number)
        folder.mkdir()
        (folder/'failure.json').write_text(json.dumps(job['failure'],ensure_ascii=False,indent=2))
        (self.lab.root/'fixtures/failure.json').write_text(json.dumps(job['failure'],ensure_ascii=False,indent=2))
        self.lab.write('skills/'+m['file'],entry['source'].decode())
        manifests=[p for p in (self.lab.root/'skills').glob('*.json') if json.loads(p.read_text()).get('name')==m['name']]
        if len(manifests)!=1:raise ValueError('repair lab needs exactly one matching manifest')
        self.lab.write('skills/'+manifests[0].name,json.dumps(m))
        catalog,report=develop(self.developer,self.lab,folder/'accepted',self.budget,repair=job)
        before={e['name']:e for e in job.get('catalog_before',[])}
        after={e['name']:e for e in catalog.public()}
        if before and (set(before)!=set(after) or any(after[n]!=e for n,e in before.items() if n!=job['skill'])):
            raise ValueError('repair changed unrelated skills; refusing multi-skill drift')
        if self.library:
            report['persistent']=self.library.publish(self.developer.model,self.lab,catalog,report,expected=job['library_version'],
                origin={'kind':'live_repair','revision':str(folder),'skill':job['skill'],'old_sha256':job['old_sha256']})
            accepted=Path(report['frozen_directory'])
            (accepted.parent/(accepted.name+'-report.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2))
        return catalog,report
    def poll(self):
        if self.future is None or not self.future.done() or self.closed:return
        future=self.future;job=self.current;self.future=None;self.current=None
        c=self.commander;name=job['skill']
        try:
            catalog,report=future.result();new=catalog.entries[name]
            old=c.tasks.python_catalog.entries[name]
            if old['sha256']!=job['old_sha256']:raise RuntimeError('catalog changed while repair ran; revision not installed')
            # Existing missions hold their own immutable entry. New calls receive the new version.
            if self.library:
                if report.get('persistent',{}).get('state')!='published':raise RuntimeError('Repair has no durable accepted version; refusing install')
                self.library_version=report['persistent']['version']
            c.tasks.python_catalog.entries[name]=new;c.tasks.python_blocked.discard(name)
            self.status[name]=dict(state='ready',old_sha256=old['sha256'],new_sha256=new['sha256'],
                redeploy='New version installed. Decide whether/when to issue a NEW task with all required args; no automatic redeployment.')
            if self.library:self.status[name]['persistent']=report['persistent']
            c.record_feedback('python_skill_revision_ready',skill=name,**self.status[name],validation=report['validation']['scope'])
            c.emit('[python-repair] '+name+' new version ready; awaiting LLM redeployment')
        except Exception as e:
            self.status[name]=dict(state='failed',error=type(e).__name__+': '+str(e),redeploy='This skill remains blocked; use another skill or macro commands.')
            c.record_feedback('python_repair_failed',skill=name,**self.status[name])
            c.emit('[python-repair-error] '+name+': '+str(e))
    def public(self):return deepcopy(self.status)
    def close(self):
        if self.closed:return
        self.closed=True
        if self.future is not None:self.developer.close()
        self.pool.shutdown(wait=False,cancel_futures=True)
