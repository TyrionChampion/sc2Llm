"""Own-player post-match evidence for an explicitly requested model review."""
import json
from pathlib import Path

REVIEW_NOTE = """The user explicitly asks YOU to inspect and improve your own Python skills before a rematch.
You ARE authorized and able to edit skills/*.py and tests/test_*.py via lab_write_file, then run lab_run_tests and lab_validate. Do not merely describe a fix. Read fixtures/last_match_review.json, the referenced error files, CONTRACT.md and your existing source/tests. Keep your own skill, fix demonstrated code issues and add targeted regressions; don't rewrite working code just to change its hash.
Distinguish dispatch/JSON errors from Python bugs: missing required args must be fixed in your battle plan, not by changing the Python signature. nearest_enemy requires a CURRENTLY visible enemy before Python starts; otherwise choose enemy_start/home/rally deliberately. allocated unit lost terminates the mission: the commander must reassign surviving troops. JSON parse errors require complete valid JSON, no markdown, no unfinished strings. Do not blame the validator while required fields are absent.
A Python dispatch requires OUTER count=1 and ALL args fields: count (integer 1..32), target, retreat_target (home/rally), timeout_s (1..300), params (object, {} allowed). Keep strategy concise and explicitly remind your battle self of correct skill use and that background Python repair is enabled. During battle submit tasks, not source; background development may edit Python after a reported script failure. When revision state becomes ready, YOU decide whether to dispatch a NEW task.
COACHING FROM THE HOST AFTER THE LAST FULL MATCH:
1. Battle replies: select at most THREE useful high-level tasks, keep reason under 100 characters. Avoid long explanations, markdown and redundant queued orders. A 60s MODEL timeout is not a Python tick bug; do not edit the tactic pretending to fix model latency.
2. Python has a 50ms wall/IPC budget, not a guarantee of 50ms CPU execution. Test a 32-unit squad and a large visible crowd; avoid quadratic per-unit target sorting and reissuing identical orders every tick. Do not claim the precise timeout cause without measurements. Meaningful order/state checks matter more than speculative rewrites.
3. Enemy tags may disappear BETWEEN tick input and execution, EVEN INSIDE ATTACK RANGE. Being nearby is NOT a guarantee of continued visibility. Test an enemy that was in range in input but absent at execution; consider attack_move to the existing commander objective when stable execution matters. Read fixtures/execution_race.json: the host NOW rejects old in-range attack_unit implementations on this exact before/after race. Fix your own Python and regression tests if this fails. Do not silently invent coordinates (0,0) for a missing mission target; fail explicitly with no actions.
4. A mission deadline or loss of an allocated unit requires a new commander decision; Python cannot replace troops or extend host deadlines. Keep strategic deployment counts achievable with current free units, and redeploy surviving troops after failure.
5. You may FIX YOUR OWN Python using lab_write_file; add regressions for actual root causes. Preserve a working implementation when evidence does not justify changing it. Do not weaken existing tests to hide failures.
Run both real tools after your final edit and report only ready/strategy JSON. Include the exact callable skill name and all five argument names in your strategy so the stateless battle commander can use it correctly."""


def load_review(source,index,model,lab):
    source=Path(source).resolve();run=json.loads((source/'run.json').read_text())
    if run['models'][index]!=model:raise ValueError('review source author/model mismatch')
    match=json.loads((source/'match.json').read_text())
    if match['outcome']=='running':raise ValueError('review source match must be stopped first')
    old=source/('p%d'%(index+1))/'lab'
    for folder in ('skills','tests'):
        for p in (old/folder).iterdir():
            if not p.is_file() or p.is_symlink():raise ValueError('review files must be regular files')
            lab.write(folder+'/'+p.name,p.read_text())
    for p in (old/'fixtures').glob('*.json'):
        if p.is_symlink():raise ValueError('symlink fixture forbidden')
        (lab.root/'fixtures'/p.name).write_bytes(p.read_bytes())
    commander=match['commanders'][index]
    journal=Path(commander['feedback']['journal'])
    review={'player':index+1,'model':model,'source_match':str(source),'scope':'Only this player own feedback and own source. Enemy observations are player-local, not opponent private plans.',
            'can_edit_python':True,'instructions':REVIEW_NOTE,'events':[]}
    selected={'plan_rejected','model_response_invalid','model_request_failed','plan_expired','python_skill_failed','python_skill_revision_ready'}
    for line in journal.read_text().splitlines():
        row=json.loads(line)
        if row.get('record')!='feedback_event':continue
        e=row['event']
        if e['kind'] not in selected:continue
        name='prior_event_%d.json'%e['id'];data=json.dumps(e,ensure_ascii=False)
        if len(data.encode())>128000:raise ValueError('review event exceeds lab read limit; not truncated: '+name)
        (lab.root/'fixtures'/name).write_text(data)
        review['events'].append({'id':e['id'],'kind':e['kind'],'game_s':round(e['game_loop']/22.4,2),
                                 'error':e.get('error'),'file':'fixtures/'+name})
    (lab.root/'fixtures/last_match_review.json').write_text(json.dumps(review,ensure_ascii=False,indent=2))
    print('[review P%d] loaded own source/tests and %d complete error/revision records; Python editing explicitly authorized'%(index+1,len(review['events'])),flush=True)
    return review


def require_review_read(lab):
    rows=[json.loads(x) for x in (lab.root/'audit.jsonl').read_text().splitlines()]
    if not any(r.get('tool')=='read_file' and r.get('args',{}).get('path')=='fixtures/last_match_review.json' for r in rows):
        raise ValueError('review was not accepted: model did not actually read its last-match review with the lab tool')


def rehearse_dispatch(client,catalog,strategy):
    """Actual battle LLM produces a callable Python task; schema check, no game actions."""
    from arena.battle_plans import SYSTEM,bounded_note
    from arena.tasks import TaskQueue
    client.phase='battle';client.timeout=60
    names=list(catalog.entries)
    payload={'world':{'game_s':30,'minerals':500,'supply':[20,31],
        'own':{'CommandCenter':1,'SCV':12,'Barracks':1,'Marine':8},'visible_enemy':{},'stimpack_researched':False},
        'active_tasks':[],'python_skill_library':catalog.public(),'skill_library':[],
        'prepared_strategy':bounded_note(strategy,1200),'skill_repairs':{},
        'control_policy':{'combat_reflex_enabled':False,'surrender_available':False,'python_tactics_enabled':True},
        'rehearsal':'SYNTHETIC dispatch check BEFORE game. Return exactly one task using your listed Python skill, args.count=4, target=enemy_start, retreat_target=home, timeout_s=120 and params={}. Include all five args fields. No skills definitions. This checks real battle JSON, not game execution.'}
    attempts=[]
    for attempt in range(1,4):
        raw=None
        try:
            raw=client.chat(SYSTEM+'\nThis is a pregame interface exercise, NOT a live strategic decision. Follow payload.rehearsal exactly, which overrides prepared strategy and world-based tactical choices for this call only. Read rehearsal_feedback and correct the rejected response. Do not add production/economy tasks during this exercise.',json.dumps(payload,ensure_ascii=False));plan=json.loads(raw)
            if not isinstance(plan,dict) or set(plan)-{'tasks','reason'}:raise ValueError('rehearsal expects tasks/reason only')
            tasks=plan.get('tasks')
            if not isinstance(tasks,list) or len(tasks)!=1 or not isinstance(tasks[0],dict) or tasks[0].get('skill') not in names:
                raise ValueError('rehearsal must invoke exactly one registered Python skill')
            q=TaskQueue(None,client.index,lambda message:None,python_catalog=catalog)
            q.enqueue(tasks,30,'dispatch rehearsal')
            args=tasks[0]['args']
            if args!={'count':4,'target':'enemy_start','retreat_target':'home','timeout_s':120,'params':{}}:
                raise ValueError('use the exact synthetic rehearsal arguments requested')
            result={'passed':True,'attempt':attempt,'plan':plan,'previous_errors':attempts,
                    'scope':'actual model response accepted by production task validator; no engine execution'}
            client.audit(dict(record='dispatch_rehearsal',**result))
            print('[review P%d] real battle Python dispatch JSON PASSED'%(client.index+1),flush=True)
            return result
        except Exception as error:
            message=type(error).__name__+': '+str(error)
            attempts.append({'attempt':attempt,'error':message,'response':raw})
            client.audit(dict(record='dispatch_rehearsal_failed',**attempts[-1]))
            print('[review P%d] dispatch rehearsal REJECTED: %s'%(client.index+1,message),flush=True)
            payload['rehearsal_feedback']=attempts[-1]
            if client.closed:raise
    raise RuntimeError('Python dispatch rehearsal failed after 3 attempts: '+json.dumps(attempts,ensure_ascii=False))


def qualify_battle(client,catalog,strategy):
    """Five real decisions over varied synthetic states, never an engine/win-rate claim."""
    import time
    from arena.battle_plans import SYSTEM,compact_payload
    from arena.tasks import TaskQueue
    name=next(iter(catalog.entries))
    normal_args={'count':4,'target':'enemy_start','retreat_target':'home','timeout_s':120,'params':{}}
    cases=[
        ('opening',{'game_s':5,'minerals':500,'supply':[8,13],'own':{'CommandCenter':1,'SCV':8},'visible_enemy':{}},[],[]),
        ('army_available',{'game_s':120,'minerals':500,'supply':[20,31],'own':{'CommandCenter':1,'SCV':12,'Barracks':1,'Marine':8},'visible_enemy':{}},[],[]),
        ('bad_args_feedback',{'game_s':130,'minerals':400,'supply':[20,31],'own':{'CommandCenter':1,'SCV':12,'Barracks':1,'Marine':8},'visible_enemy':{'Marine':2}},[],
            [{'id':1,'kind':'plan_rejected','error':'Python args must contain count, target, retreat_target, timeout_s, params; count <=32. Existing tasks continue.'}]),
        ('busy_python_task',{'game_s':145,'minerals':300,'supply':[20,31],'own':{'CommandCenter':1,'SCV':12,'Barracks':1,'Marine':8},'visible_enemy':{}},
            [{'id':7,'skill':name,'count':1,'state':'running','reason':'owns 4 Marines','workflow':{'kind':'python','args':normal_args}}],[]),
        ('lost_squad',{'game_s':160,'minerals':800,'supply':[15,31],'own':{'CommandCenter':1,'SCV':12,'Barracks':1,'Marine':3},'visible_enemy':{'Marine':8}},[],
            [{'id':2,'kind':'python_skill_failed','skill':name,'error':'allocated unit lost; mission ended; remaining units can be assigned by a new decision'}])]
    from arena.knowledge import cached_tree,ECONOMY,RACES
    from types import SimpleNamespace
    race=getattr(client,'race','terran');tree=cached_tree()
    names={'terran':{},'zerg':{'CommandCenter':'Hatchery','SCV':'Drone','Barracks':'SpawningPool','Marine':'Roach'},'protoss':{'CommandCenter':'Nexus','SCV':'Probe','Barracks':'Gateway','Marine':'Stalker'}}[race]
    for _,world,_,_ in cases:
        world['race']=race;world['vespene']=150;world['upgrades']=[];world['own']={names.get(k,k):v for k,v in world['own'].items()}
    previous=None;results=[];client.phase='battle';client.timeout=60
    for label,world,active,events in cases:
        payload=compact_payload({'world':world,'prepared_strategy':strategy,'active_tasks':active,
            'control_policy':{'combat_reflex_enabled':False,'idle_worker_gathering':True,'surrender_available':False,'python_tactics_enabled':True},
            'feedback':{'events':events,'pending_total':len(events),'deferred_count':0},
            'skill_repairs':{},'python_skill_library':catalog.public(),'skill_library':[]},previous)
        payload.update(tech_tree=tree.public(race),game_knowledge={'economy':ECONOMY,'race_notes':RACES[race]})
        if getattr(client,'qualification_feedback',None):
            payload['qualification_feedback']=client.qualification_feedback
            payload['coach']='Correct the exact rejected fields. Only use skill names and argument shapes in the output tool schema. Do not invent build_ aliases or put target on a macro command.'
        started=time.monotonic();raw=None
        try:
            raw=client.chat(SYSTEM,json.dumps(payload,ensure_ascii=False,separators=(',',':')))
            elapsed=time.monotonic()-started;plan=json.loads(raw)
            if not isinstance(plan,dict) or set(plan)-{'tasks','skills','reason'}:raise ValueError('unsupported plan fields')
            q=TaskQueue(SimpleNamespace(tech_tree=tree,races=[race,race]),client.index,lambda message:None,python_catalog=catalog)
            # Reconstruct the active Python mission for cancellation/coalescing schema checks.
            if active:
                q.next_id=7;q.enqueue([{'skill':name,'args':normal_args}],world['game_s'])
            q.enqueue(plan['tasks'],world['game_s'],skills=plan.get('skills'))
            if label=='opening' and not any(t.get('skill') in ('train_worker','build_supply','build_structure','supply_depot','barracks','expand') for t in plan['tasks']):
                raise ValueError('opening produced no economy/construction task')
            if elapsed>22:raise TimeoutError('qualification response exceeded 22s responsiveness gate')
            from arena.battle_plans import bounded_note
            previous=bounded_note(plan.get('reason',''),600)
            row={'case':label,'passed':True,'latency_s':elapsed,'plan':plan}
            results.append(row);client.audit(dict(record='decision_qualification',**row))
            print('[qualify P%d] %s PASSED %.2fs'%(client.index+1,label,elapsed),flush=True)
        except Exception as error:
            row={'case':label,'passed':False,'error':type(error).__name__+': '+str(error),'response':raw}
            client.audit(dict(record='decision_qualification',**row))
            print('[qualify P%d] REJECTED %s'%(client.index+1,row['error']),flush=True)
            raise RuntimeError(json.dumps(row,ensure_ascii=False)) from error
    return {'passed':True,'consecutive_decisions':5,'scope':'real model + production plan validator on synthetic observations, not a live engine guarantee','results':results}


def qualify_with_coaching(client,catalog,strategy):
    errors=[]
    for round_number in range(1,4):
        client.qualification_feedback=errors
        try:
            report=qualify_battle(client,catalog,strategy)
            report.update(round=round_number,previous_failures=errors)
            return report
        except RuntimeError as error:
            errors.append(str(error))
            print('[qualify P%d] feedback delivered for a fresh full five-round check: %s'%(client.index+1,error),flush=True)
            if client.closed:raise
    raise RuntimeError('Did not pass five consecutive decisions after coaching: '+json.dumps(errors,ensure_ascii=False))
