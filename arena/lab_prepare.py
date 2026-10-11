"""Real OpenCode tool-driven authoring, tests, and source snapshots."""
import json,time
from pathlib import Path
from arena.skill_lab import SkillLab,LAB_GUIDE,ROOT
from arena.battle_plans import LANGUAGE_RULES


def lab_mcp(root):
    return {'lab':dict(type='local',enabled=True,command=[str(ROOT/'run_sc2.sh'),str(ROOT/'.venv311/bin/python'),str(ROOT/'arena/skill_lab_mcp.py'),str(Path(root).resolve())])}


def ready_reply(raw,planner):
    try:return json.loads(raw)
    except json.JSONDecodeError:
        # Developer summaries sometimes include prose. This is NOT the live action parser.
        decoder=json.JSONDecoder();found=[]
        for i,ch in enumerate(raw):
            if ch!='{':continue
            try:
                value,end=decoder.raw_decode(raw[i:])
                if isinstance(value,dict) and set(value)=={'ready','strategy'}:found.append(value)
            except json.JSONDecodeError:pass
        if len(found)!=1:raise ValueError('need exactly one ready/strategy JSON object in developer completion')
        planner.audit(dict(record='developer_summary_format_normalized',handling='Accepted one unambiguous ready/strategy object; actual files/tests independently checked.'))
        print('[lab P%d] developer summary prose detected; extracting unique ready/strategy object; all checks still required'%(planner.index+1),flush=True)
        return found[0]


def develop(planner,lab,destination,seconds=600,repair=None):
    if not planner.lab_tools:raise ValueError('development requires a tool-enabled OpenCode session')
    deadline=time.monotonic()+seconds;errors=[]
    system='You are the Python tactics developer for your own '+lab.race+' player. Use actual lab tools to author, test, inspect errors and fix code. Keep preparation compact (roughly 80 lines of skill, 5-8 focused tests); spend time on running/correcting tests. '+LAB_GUIDE+'\n'+LANGUAGE_RULES
    if repair:system+=' This is a REPAIR, not a redesign. Make the smallest fix and regression test that addresses the recorded failure. Do not add new tactical features or unrelated test suites.'
    prompt=('Repair your existing skill. Read fixtures/failure.json, CONTRACT.md and all current source/tests. Add a regression test, fix the recorded failure, run tests and validate. Preserve manifest public interface. '
            if repair else 'Read README.md, CONTRACT.md, GAME_KNOWLEDGE.md and fixtures/player.json. Create your OWN compact useful tactic for your assigned race, manifest and meaningful unittest tests. Run tests and validate. Fix failures. ')
    if not repair and list((lab.root/'skills').glob('*.py')):
        prompt='Draft files/tests are already in this private lab. Read them, run tests, fix any failures, validate and finish. Preserve working supplied code; no need to rewrite from scratch.'
    if not repair:
        prompt+=' Read GAME_KNOWLEDGE.md opening and macro_plan guidance even when reusing tactics. Include a concise conditional macro opening in strategy, separately from Python micro instructions. Live observations/costs override cached build timings; combat skills must not take over macro decisions.'
    if not repair and (lab.root/'REFERENCE_GUIDE.md').exists():
        prompt+=' The user explicitly supplied prior accepted source and tests as shared references. Read REFERENCE_GUIDE.md and its listed JSON files before designing your final skills. You may learn from/adapt them, retain attribution, and must run your own tests plus current host validation. They are not preaccepted under your identity. Use GAME_KNOWLEDGE.md for the expanded unit/tech/gas/expansion capabilities; old Marine-only examples are not the whole interface.'
    if not repair and (lab.root/'fixtures/shared_skill_origin.json').exists():
        prompt+=' These are USER-SUPPLIED SHARED SKILLS, not necessarily authored by you. Read fixtures/shared_skill_origin.json for the exact original author/version and keep attribution. Both contestants start from the same code. Inspect the actual source and tests, use lab_run_tests and lab_validate, and change only demonstrated issues. Strategy must name the skill and all required dispatch arguments; high-level strategy remains your responsibility.'
    review_note=getattr(planner,'review_note',None) if not repair else None
    if review_note:
        system+='\n'+review_note
        prompt+=' Read fixtures/last_match_review.json and your referenced error evidence first; you may modify Python and tests.'
    for attempt in range(1,5):
        remaining=deadline-time.monotonic()
        if remaining<=0:break
        planner.timeout=min(remaining,300)
        print('[lab P%d] %s tool session round %d, %.0fs left'%(planner.index+1,'repair' if repair else 'prepare',attempt,remaining),flush=True)
        try:
            raw=planner.chat(system,prompt);result=ready_reply(raw,planner)
            if result.get('ready') is not True or not isinstance(result.get('strategy'),str):raise ValueError('finish with ready=true and strategy text JSON')
            if time.monotonic()>deadline:raise TimeoutError('development deadline exceeded')
            if review_note:
                from arena.match_review import require_review_read
                require_review_read(lab)
            catalog,validation=lab.accept(destination)
            if repair:
                old=repair['entry'];new=catalog.entries.get(old['manifest']['name'])
                if new is None or new['manifest']!=old['manifest']:raise ValueError('repair must preserve manifest public interface exactly')
                if new['sha256']==old['sha256']:raise ValueError('repair did not change failed source hash')
            report=dict(status='prepared',model=planner.model,session_id=planner.session_id,attempts=attempt,
                frozen_directory=str(Path(destination).resolve()),wall_s=seconds-max(0,deadline-time.monotonic()),strategy=result['strategy'],skills=catalog.public(),**validation,errors=errors)
            (Path(destination).parent/(Path(destination).name+'-report.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2))
            planner.audit(dict(record='lab_accepted',**report));print('[lab P%d] PASSED self-tests + host checks'% (planner.index+1),flush=True)
            return catalog,report
        except Exception as e:
            message=type(e).__name__+': '+str(e);errors.append(message)
            planner.audit(dict(record='lab_development_failed',error=message));print('[lab P%d] REJECTED %s'%(planner.index+1,message),flush=True)
            if planner.closed:raise
            # Never overwrite a frozen accepted snapshot when a later semantic check fails.
            if Path(destination).exists():destination=Path(str(destination)+'-retry')
            prompt='Previous preparation/repair was NOT accepted: '+message+'\nInspect your lab, correct the issue and rerun run_tests AND validate for the final files. Return ready/strategy JSON only after actual tool use.'
    raise RuntimeError('lab development failed within deadline/4 rounds: '+str(errors))


def copy_lab_fixtures(old,lab):
    for p in (old.root/'fixtures').glob('*.json'):
        if p.is_symlink() or not p.is_file():raise ValueError('fixture must be a regular file')
        if p.name in ('execution_race.json','player.json','basic.json'):continue  # Current host fixture is authoritative, not an old archived copy.
        (lab.root/'fixtures'/p.name).write_text(old.read('fixtures/'+p.name))
    print('[lab] copied archived test fixtures; current host execution-race fixture retained',flush=True)


def restore_prepared(source,index,model,lab,destination):
    source=Path(source).resolve();record=json.loads((source/'run.json').read_text())
    if record.get('races',['terran','terran'])[index]!=lab.race:raise ValueError('prepared player race mismatch')
    prepared=record['preparation'][index]
    if prepared['status']!='prepared' or prepared['model']!=model:raise ValueError('prepared player/model mismatch')
    old=SkillLab(source/('p%d'%(index+1))/'lab')
    if old.digest()!=prepared['digest']:raise ValueError('prepared files/tests changed since acceptance')
    for folder in ('skills','tests'):
        for p in (old.root/folder).iterdir():lab.write(folder+'/'+p.name,p.read_text())
    copy_lab_fixtures(old,lab)
    tested=lab.run_tests();validated=lab.validate()
    if not tested['passed'] or not validated['passed']:raise ValueError('restored preparation no longer passes checks')
    catalog,checks=lab.accept(destination)
    if catalog.public()!=prepared['skills']:raise ValueError('restored skill hashes/manifest mismatch')
    report={**prepared,**checks,'frozen_directory':str(Path(destination).resolve()),'restored_from':str(source),
            'restoration':'Exact model-authored source AND tests; self-tests and host checks rerun; original developer tool audit retained at source.'}
    (Path(destination).parent/(Path(destination).name+'-report.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('[lab P%d] restored exact tested model-authored files; checks passed'%(index+1),flush=True)
    return catalog,report
