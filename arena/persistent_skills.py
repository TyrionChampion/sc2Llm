"""Validated, content-addressed skill bundles, isolated by exact provider/model."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from arena.skill_lab import SkillLab,ROOT
from arena.python_skills import PythonCatalog


def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
def sha(data):return hashlib.sha256(data).hexdigest()


def payload(root):
    result={}
    for folder in ('skills','tests','fixtures'):
        base=Path(root)/folder
        if base.is_symlink() or not base.is_dir():raise ValueError('Missing/unsafe bundle folder: '+str(base))
        for p in sorted(base.iterdir()):
            if p.is_symlink() or not p.is_file():raise ValueError('Bundle contains non-regular file: '+str(p))
            limit=16*1024*1024 if folder=='fixtures' else 65536
            if p.stat().st_size>limit:raise ValueError('Bundle file exceeds %d bytes; not truncated: %s'%(limit,p))
            result[folder+'/'+p.name]=p.read_bytes()
    if sum(map(len,result.values()))>64*1024*1024:raise ValueError('Bundle exceeds 64 MiB; not truncated')
    return result


def put_files(root,files):
    for name,data in files.items():
        p=Path(root)/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)


def durable_json(path,value):
    path=Path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.head-',delete=False) as f:
        temp=Path(f.name);f.write(encoded(value));f.flush();os.fsync(f.fileno())
    try:os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)
    fd=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


class PersistentSkills:
    def __init__(self,root=None):
        self.root=Path(root or ROOT/'skill-library').resolve();self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
    def folder(self,model):
        if not isinstance(model,str) or '/' not in model or len(model)>240:raise ValueError('Expected exact provider/model identity')
        name=re.sub(r'[^a-zA-Z0-9._-]','_',model)[:80]+'-'+sha(model.encode())[:16]
        path=self.root/name
        if path.is_symlink():raise ValueError('Model library directory cannot be a symlink')
        return path
    @contextmanager
    def locked(self,model):
        folder=self.folder(model);folder.mkdir(exist_ok=True,mode=0o700)
        with (folder/'.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX);yield folder
    def version(self,model,version):
        if not isinstance(version,str) or not re.fullmatch('[0-9a-f]{64}',version):raise ValueError('Invalid persistent version ID')
        folder=self.folder(model)/'versions'/version
        if folder.is_symlink():raise ValueError('Version cannot be a symlink')
        meta=folder/'manifest.json'
        if meta.is_symlink():raise ValueError('Manifest cannot be a symlink')
        record=json.loads(meta.read_text())
        if sha(encoded(record))!=version or record['model']!=model or record['schema']!=1:
            raise ValueError('Persistent manifest hash/model/schema mismatch; no fallback')
        files=payload(folder)
        evidence=folder/'evidence.json'
        if evidence.is_symlink():raise ValueError('Evidence cannot be a symlink')
        files['evidence.json']=evidence.read_bytes()
        if {name:sha(data) for name,data in files.items()}!=record['files']:
            raise ValueError('Persistent source/tests/fixtures/evidence integrity mismatch; no fallback')
        if PythonCatalog(folder/'skills').public()!=record['skills']:raise ValueError('Persistent catalog mismatch')
        return dict(version=version,path=str(folder),manifest=record)
    def latest(self,model):
        p=self.folder(model)/'HEAD.json'
        if p.is_symlink():raise ValueError('HEAD cannot be a symlink')
        if not p.exists():return None
        value=json.loads(p.read_text())
        if value.get('model')!=model:raise ValueError('HEAD model mismatch; no fallback')
        return self.version(model,value['version'])
    def publish(self,model,lab,catalog,report,expected=None,origin=None):
        """Revalidate a frozen copy off the game thread, then compare-and-swap HEAD."""
        if report.get('model')!=model or report.get('status')!='prepared':raise ValueError('Unaccepted/wrong-author report')
        if lab.digest()!=report['digest']:raise ValueError('Draft changed since acceptance; not promoting')
        if catalog.public()!=report['skills']:raise ValueError('Accepted catalog/report mismatch')
        for key in ('self_tests','validation'):
            if not report[key].get('passed') or report[key].get('digest')!=report['digest']:
                raise ValueError('Both self-tests and host checks must pass current source/tests')
        files=payload(lab.root)
        if PythonCatalog(lab.root/'skills').public()!=catalog.public():raise ValueError('Draft is not the accepted source')
        with tempfile.TemporaryDirectory(prefix='.publish-',dir=self.root) as tmp:
            work=SkillLab(Path(tmp)/'work',race=lab.race);work.setup();put_files(work.root,files)
            if work.digest()!=report['digest']:raise ValueError('Snapshot differs from accepted source/tests')
            tested=work.run_tests();checked=work.validate()
            if not tested['passed'] or not checked['passed']:raise ValueError('Persistent snapshot revalidation failed: '+json.dumps({'tests':tested,'host':checked},ensure_ascii=False))
            if payload(work.root)!=files:raise ValueError('Snapshot mutated during validation')
            evidence=dict(model=model,source_report=report,self_tests=tested,validation=checked)
            files['evidence.json']=encoded(evidence)
            record=dict(schema=1,model=model,parent=expected,created_unix=time.time(),origin=origin or {},
                strategy=report['strategy'],skills=catalog.public(),files={k:sha(v) for k,v in files.items()})
            version=sha(encoded(record));stage=Path(tmp)/'bundle';put_files(stage,files)
            (stage/'manifest.json').write_bytes(encoded(record))
            # Flush the complete bundle before making it reachable by HEAD.
            for p in stage.rglob('*'):
                if p.is_file():
                    with p.open('rb') as f:os.fsync(f.fileno())
            for p in [stage/'skills',stage/'tests',stage/'fixtures',stage]:
                fd=os.open(p,os.O_RDONLY)
                try:os.fsync(fd)
                finally:os.close(fd)
            with self.locked(model) as folder:
                current=self.latest(model);actual=current['version'] if current else None
                if actual!=expected:raise ValueError('Library changed concurrently; refusing stale promotion (expected %s, current %s)'%(expected,actual))
                versions=folder/'versions';versions.mkdir(exist_ok=True)
                os.rename(stage,versions/version)
                fd=os.open(versions,os.O_RDONLY)
                try:os.fsync(fd)
                finally:os.close(fd)
                durable_json(folder/'HEAD.json',dict(model=model,version=version))
            result=dict(version=version,path=str(self.folder(model)/'versions'/version),state='published',parent=expected)
            print('[skill-library] %s promoted %s'%(model,version[:12]),flush=True)
            return result
    def restore(self,model,lab,destination,version=None):
        selected=self.version(model,version) if version else self.latest(model)
        if selected is None:raise FileNotFoundError('No persistent version for '+model)
        if any(list((lab.root/f).iterdir()) for f in ('skills','tests')):raise ValueError('Restore requires empty source/test directories')
        source=Path(selected['path']);files=payload(source)
        # Keep new host-provided race fixture, report the upgrade explicitly.
        files.pop('fixtures/execution_race.json',None);put_files(lab.root,files)
        print('[skill-library] %s restoring %s; current host execution-race fixture retained'%(model,selected['version'][:12]),flush=True)
        tested=lab.run_tests();checked=lab.validate()
        if not tested['passed'] or not checked['passed']:raise ValueError('Persistent version failed startup revalidation; no fallback: '+json.dumps({'tests':tested,'host':checked},ensure_ascii=False))
        catalog,checks=lab.accept(destination)
        if catalog.public()!=selected['manifest']['skills']:raise ValueError('Restored catalog changed')
        persistent=dict(version=selected['version'],path=selected['path'],state='loaded',revalidated=True)
        report=dict(status='prepared',model=model,strategy=selected['manifest']['strategy'],skills=catalog.public(),
            frozen_directory=str(Path(destination).resolve()),persistent=persistent,**checks)
        (Path(destination).parent/(Path(destination).name+'-report.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2))
        return catalog,report
