"""Per-player local files, isolated Python tests, and independently enforced acceptance."""
from copy import deepcopy
import hashlib,json,os,re,selectors,signal,subprocess,sys,tempfile,time,traceback
from pathlib import Path
from types import SimpleNamespace
from arena.python_skills import PythonCatalog,PythonSkillRun,IsolatedWorker,KINDS
from arena.opencode_prepare import smoke_catalog
from arena.raw_engine import Entity,Snapshot
ROOT=Path(__file__).resolve().parents[1]


def representative(selector,race='terran'):
    if selector=='army':return {'terran':48,'zerg':110,'protoss':74}[race]
    if selector=='worker':return {'terran':45,'zerg':104,'protoss':84}[race]
    return min(KINDS[selector]-{32}) if KINDS[selector]-{32} else 32

def fixture_context(enemies=True,kind=48):
    def unit(tag,x,hp=45):
        return dict(tag=tag,type_id=kind,pos=[x,30],health=hp,health_max=45,flying=False,is_building=False,
            ground_dps=10,air_dps=10,attack_range=5,energy=0,energy_max=200,cargo_taken=0,cargo_max=0,passengers=[],buffs=[],orders=[],order_targets=[],order_positions=[],mineral_cost=50,gas_cost=0)
    return dict(game_loop=224,game_s=10,mission=dict(objective=[160,160],retreat=[10,10],deadline_game_s=100),
        params={},available_spells={},units=[unit(101,30)],allies=[],enemies=[unit(201,34)] if enemies else [],minerals=[],stim_researched=False,local_radius=20)


def disappearing_in_range_case(kind=48):
    before=fixture_context(kind=kind);before['enemies'][0]['pos']=[31,30]
    before['units']=[{**deepcopy(before['units'][0]),'tag':101+i} for i in range(3)]
    after=deepcopy(before);after['game_loop']+=1;after['enemies']=[]
    return dict(context=before,execution_context=after,state={},map_size=[200,200])


def replay_check(entry,case):
    """Execute frozen source against recorded player-local input and newer execution observation."""
    before=case['context'];after=case.get('execution_context') or before
    run=PythonSkillRun(entry,dict(count=max(1,len(before['units'])),target='enemy_start',retreat_target='home',timeout_s=90,params=before['params']))
    run.tags={u['tag'] for u in before['units']};run.mission=before['mission']
    units=[];types={}
    for label,alliance in (('units',1),('allies',1),('enemies',4),('minerals',3)):
        for u in after.get(label,[]):
            types[u['type_id']]=SimpleNamespace(mineral_cost=u.get('mineral_cost'),vespene_cost=u.get('gas_cost'))
            units.append(Entity(u['tag'],u['type_id'],alliance,*u['pos'],u['health'],u['health_max'],
                flying=u.get('flying',False),is_building=u.get('is_building',False),ground_dps=u.get('ground_dps',0),
                air_dps=u.get('air_dps',0),energy=u.get('energy',0),energy_max=u.get('energy_max',0),cargo_taken=u.get('cargo_taken',0),cargo_max=u.get('cargo_max',0),passengers=tuple(u.get('passengers',[])),order_targets=tuple(u.get('order_targets',[])),order_positions=tuple(u.get('order_positions',[])),attack_range=u.get('attack_range',0),buffs=tuple(u.get('buffs',[])),orders=tuple(u.get('orders',[]))))
    w=Snapshot(after['game_loop'],500,12,15,tuple(units),(30,30),upgrades=(15,) if after.get('stim_researched') else ())
    from arena.knowledge import cached_tree
    tree=cached_tree()
    q=SimpleNamespace(index=0,backend=SimpleNamespace(homes=[(10,10)],size=tuple(case.get('map_size',[200,200])),
        types=types,tech_tree=tree,enemy_start=lambda i:(160,160),available_abilities=lambda i,tags:{t:({x['ability_id'] for x in after.get('available_spells',{}).get(str(t),[])}|{380,388,390}) for t in tags}))
    worker=IsolatedWorker(entry)
    try:
        worker.submit(before,case.get('state',{}));deadline=time.monotonic()+3
        while True:
            reply=worker.poll()
            if reply is not None:break
            if time.monotonic()>deadline:raise TimeoutError('replay worker deadline')
            time.sleep(.002)
        if 'error' in reply:raise ValueError(reply['error']+'\n'+reply.get('traceback',''))
        result=reply['result'];run.validate_result(result,w,q)
        if result['status']=='failed':raise ValueError('script reported failure: '+result['reason'])
        return dict(status='passed',result=result,latency_ms=reply['elapsed_ms'])
    finally:worker.close()


class SkillLab:
    def __init__(self,root,race=None):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        config=self.root/'fixtures/player.json'
        self.race=race or (json.loads(config.read_text())['race'] if config.exists() else 'terran')
        if self.race not in ('terran','zerg','protoss'):raise ValueError('unknown lab race')
        for n in ('skills','tests','fixtures'):(self.root/n).mkdir(exist_ok=True)
    def setup(self):
        manual=(ROOT/'AI_SKILL_MANUAL.md').read_text()
        (self.root/'CONTRACT.md').write_text('## 0 Python'+manual.split('## 0 Python',1)[1].split('## 1 分工',1)[0])
        (self.root/'fixtures/player.json').write_text(json.dumps({'race':self.race,'catalogue_scope':'last verified native metadata; live engine queries remain authoritative'}))
        (self.root/'GAME_KNOWLEDGE.md').write_text((ROOT/'SC2_GAME_KNOWLEDGE.md').read_text())
        (self.root/'fixtures/basic.json').write_text(json.dumps(fixture_context(kind={'terran':48,'zerg':110,'protoss':74}[self.race]),indent=2))
        (self.root/'fixtures/execution_race.json').write_text(json.dumps(disappearing_in_range_case({'terran':48,'zerg':110,'protoss':74}[self.race]),indent=2))
        (self.root/'README.md').write_text('Your assigned race is '+self.race+'. Read GAME_KNOWLEDGE.md for all units/tech/economy.\n'+LAB_GUIDE)
    def path(self,name,write=False):
        if not isinstance(name,str) or len(name)>200:raise ValueError('invalid filename')
        p=Path(name)
        if p.is_absolute() or '..' in p.parts or any(part.startswith('.') for part in p.parts):raise ValueError('path must stay inside your lab')
        if write and not (len(p.parts)==2 and ((p.parts[0]=='skills' and p.suffix in ('.py','.json')) or (p.parts[0]=='tests' and re.fullmatch(r'test_[a-zA-Z0-9_]+\.py',p.name)))):
            raise ValueError('write only skills/<name>.py/.json or tests/test_<name>.py')
        target=self.root/p
        if not target.resolve().is_relative_to(self.root) or any(a.is_symlink() for a in [target,*target.parents] if a!=self.root.parent):raise ValueError('symlinks are forbidden')
        return target
    def audit(self,tool,args,result):
        with open(self.root/'audit.jsonl','a') as f:f.write(json.dumps(dict(time=time.time(),tool=tool,args=args,result=result),ensure_ascii=False)+'\n')
    def digest(self):
        h=hashlib.sha256()
        for folder in ('skills','tests'):
            for p in sorted((self.root/folder).iterdir()):
                if p.is_symlink() or not p.is_file():raise ValueError('only regular lab files supported')
                h.update(str(p.relative_to(self.root)).encode());h.update(p.read_bytes())
        return h.hexdigest()
    def files(self):return [str(p.relative_to(self.root)) for p in sorted(self.root.rglob('*')) if p.is_file() and p.name not in ('audit.jsonl','tested.json','validated.json')]
    def write(self,name,content):
        if not isinstance(content,str) or len(content.encode())>65536:raise ValueError('file exceeds 64 KiB')
        p=self.path(name,True)
        if not p.exists() and len(list(p.parent.iterdir()))>=16:raise ValueError('maximum 16 files in each directory')
        p.write_text(content)
        return dict(path=name,sha256=hashlib.sha256(content.encode()).hexdigest())
    def read(self,name):
        p=self.path(name)
        if p.stat().st_size>128000:raise ValueError('file too large')
        return p.read_text()
    def run_tests(self):
        digest=self.digest();base=Path(sys.base_prefix).resolve()
        argv=['bwrap','--unshare-all','--die-with-parent','--new-session','--clearenv',
            '--ro-bind',str(base),'/runtime','--ro-bind','/usr/lib','/usr/lib',
            '--symlink','usr/lib','/lib','--symlink','usr/lib','/lib64','--proc','/proc','--dev','/dev','--tmpfs','/tmp',
            '--ro-bind',str(self.root/'skills'),'/lab/skills','--ro-bind',str(self.root/'tests'),'/lab/tests',
            '--ro-bind',str(self.root/'fixtures'),'/lab/fixtures',
            '--ro-bind',str(ROOT/'arena/lab_runner.py'),'/runner.py','--chdir','/tmp',
            '/runtime/bin/'+Path(sys.executable).resolve().name,'-I','-S','-B','/runner.py']
        p=subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env={'PATH':'/usr/bin:/bin'},start_new_session=True)
        output=bytearray();sel=selectors.DefaultSelector();sel.register(p.stdout,selectors.EVENT_READ);deadline=time.monotonic()+8;error=None
        try:
            while sel.get_map():
                if time.monotonic()>deadline:raise TimeoutError('local tests exceeded 8 seconds')
                for key,_ in sel.select(.05):
                    chunk=os.read(key.fd,8192)
                    if not chunk:sel.unregister(key.fileobj);continue
                    output.extend(chunk)
                    if len(output)>65536:raise ValueError('test output exceeded 64 KiB')
            p.wait(timeout=1)
        except Exception as e:error=type(e).__name__+': '+str(e)
        finally:
            try:os.killpg(p.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            p.wait(timeout=2);sel.close();p.stdout.close()
        value=dict(passed=error is None and p.returncode==0,exit_code=p.returncode,error=error,
            output=output[:65536].decode(errors='replace'),digest=digest,scope='model-authored unittest in isolated Python, not native SC2')
        (self.root/'tested.json').write_text(json.dumps(value));return value
    def validate(self):
        report=dict(passed=False,digest=self.digest(),scope='independent synthetic and recorded-context checks; not victory certification')
        try:
            catalog=PythonCatalog(self.root/'skills')
            if not 1<=len(catalog.entries)<=4:raise ValueError('prepare 1..4 compact skills in this lab')
            from arena.knowledge import cached_tree
            tree=cached_tree();race_id={'terran':1,'zerg':2,'protoss':3}[self.race]
            for entry in catalog.entries.values():
                selector=entry['manifest']['unit']
                if not any(uid in tree.types and tree.types[uid].race==race_id for uid in KINDS[selector]):
                    raise ValueError('selector '+selector+' cannot allocate units for assigned race '+self.race)
            report['smoke']=smoke_catalog(catalog,race=self.race);report['edge_cases']=[]
            for name,entry in catalog.entries.items():
                kind=representative(entry['manifest']['unit'],self.race)
                cases=[]
                c=fixture_context(kind=kind);c['enemies'][0]['flying']=True;cases.append(('flying_enemy',c))
                c=fixture_context(kind=kind);c['enemies']=[];cases.append(('target_disappeared_next_tick',c))
                c=fixture_context(kind=kind);c['units'][0]['health']=5;cases.append(('injured_squad',c))
                for label,ctx in cases:
                    report['edge_cases'].append(dict(skill=name,case=label,**replay_check(entry,dict(context=ctx))))
                report['edge_cases'].append(dict(skill=name,case='in_range_target_disappeared_before_execution',
                    **replay_check(entry,disappearing_in_range_case(kind))))
                failure=self.root/'fixtures/failure.json'
                if failure.exists():
                    case=json.loads(failure.read_text())
                    if case['skill']==name and case.get('context'):
                        report['failure_replay']=replay_check(entry,case)
            report.update(passed=True,skills=catalog.public())
        except Exception as e:report.update(error=type(e).__name__+': '+str(e),traceback=traceback.format_exc()[-12000:])
        (self.root/'validated.json').write_text(json.dumps(report));return report
    def accept(self,destination):
        digest=self.digest();tested=json.loads((self.root/'tested.json').read_text());validated=json.loads((self.root/'validated.json').read_text())
        if not all(r.get('passed') and r.get('digest')==digest for r in (tested,validated)):
            raise ValueError('run your own tests AND host validate after the final edit; both must pass for current digest')
        report=self.validate()
        if not report['passed']:raise ValueError(report['error'])
        dst=Path(destination);dst.mkdir(parents=True,exist_ok=False)
        for p in (self.root/'skills').iterdir():(dst/p.name).write_bytes(p.read_bytes())
        catalog=PythonCatalog(dst)
        if catalog.public()!=report['skills'] or self.digest()!=digest:raise RuntimeError('files changed during acceptance')
        return catalog,dict(digest=digest,self_tests=tested,validation=report)


LAB_GUIDE="""You have a private local Python lab. Use lab_list_files, lab_read_file, lab_write_file,
lab_run_tests and lab_validate tools. These are actual local tools, not prose requests.
Read CONTRACT.md, GAME_KNOWLEDGE.md, fixtures/player.json and fixtures/basic.json. Write 1..4 useful compact skills for your own race in skills/name.py and matching
skills/name.json manifests. You may select individual unit types or army/worker. The commander has all three-race macro production/research/gas/expansion APIs. Write your own unittest.TestCase tests in tests/test_name.py.
Import the skill by module name (e.g. import name). Read fixtures at /lab/fixtures/basic.json inside tests.
Your tests may use arbitrary Python standard library in a networkless sandbox. No pip or shell access.
Test state transitions, retreat hysteresis, disappearing targets, flying enemies, empty squads,
invalid/missing cost information. Do not merely check the return type. You choose test inputs/assertions.
run_tests returns exit code/stdout/stderr/tracebacks. validate independently checks manifest, actions,
real isolated tick execution and fixture replay. Fix failures and rerun BOTH tools after your final edit.
Only passing CURRENT source+test hashes can be accepted. Never claim a test passed without running it.
During live repair fixtures/failure.json contains your source-version failure, input state, original
observation and execution observation. Read it and add a regression test before changing your skill.
A target can disappear between observation and execution EVEN INSIDE ATTACK RANGE. fixtures/execution_race.json contains before/after observations; host validate replays that exact race. Merely checking in-range visibility in tick input does not prevent stale attack_unit tags. Consider attack_move to objective for robust orders; add your own regression.
Do not change skill name or public interface during repair. No macroeconomy in Python.
Finish with JSON {"ready":true,"strategy":"..."}; host verifies independently.
"""
