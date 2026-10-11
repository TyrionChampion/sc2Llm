"""Compact battle contract and conservative, player-local late-plan checks."""
import copy
from arena.raw_engine import CC, SCV, MARINE, DEPOT, BARRACKS
from arena.tasks import SPECS

SYSTEM = """Command the race in world.race. Return JSON only: {"tasks":[{"skill":"train_worker","count":2}],"reason":"brief strategy"}. No source/prose. Use only the supplied answer-format tool if present. Decide strategy; scripts select units/coordinates/placement and wait for resources/prerequisites. <=16 tasks. Counts 1..20 only train_worker/train_unit/morph_unit; other outer counts=1. Use tech_tree for EXACT own-race unit/structure/upgrade/form names and prerequisites. train_unit args={"unit":name}; build_structure args={"structure":name,"base":"home|expansion|site_N"}; research args={"upgrade":name}; morph_unit args={"unit":form,"target":"home|rally|enemy_start|nearest_enemy"}; gather_gas args={"workers_per_refinery":0..3}. train_worker/build_supply adapt to your race. expand builds an additional town hall at a mineral cluster; gather redistributes surplus mineral workers. Other macro args={}. Macros: train_worker,train_unit,build_structure,build_supply,research,morph_unit,gather_gas,expand,attack_nearest,move_out,gather,no_op; supply_depot/barracks aliases are Terran only. Macro prerequisites WAIT; you must explicitly order the prerequisites (including tech labs, gas buildings, gas workers and supply). Research must be observed completed before relying on its effects. morph_unit is ONLY free reversible forms in tech_tree.forms (siege/unsiege, gateway/warp_gate, burrow, lift/land). Paid unit morphs (ravager, lurker, brood_lord, overseer) use train_unit; lair/hive/greater_spire/orbital_command use build_structure. Never morph into hatchery. nuke/interceptor are ammo restocking tasks with count=1 only, not observable troop-count batches; a Nuke is confirmed through an owned Ghost nuclear-strike ability, carrier full stock is inferred from an idle capacity query. Building/research outer count MUST equal 1; train_worker/train_unit/morph_unit count may exceed 1. No coordinates/tags in commands. Tasks persist: don't repeat active tasks; tasks=[] waits without clearing them. Use current world, active_tasks, control_policy, feedback and libraries. Grow economy/supply/army and defeat enemy. Return a short reason preserving strategic intent for the next stateless decision. Previous chat is NOT retained; explicit payload is authoritative. Feedback events are complete but paginated; deferred events remain pending. Failure is not recovery; command dispatch/script completion is not victory. Unknown engine cause remains unknown.
Optional skills defines <=8 JSON skills/reply, <=24 total: {"name":"letter_then_alnum_underscore_max40","select":{"unit":"scv|marine|siege_tank","count":1..32},"steps":[...],"timeout_s":1..300}. SCV count=1. <=12 steps; each has action, optional queue:bool and timeout_s:1..180. Actions/fields: build structure=supply_depot|barracks|expand (SCV); gather target=nearest_mineral (SCV); move/attack_move target=home|rally|enemy_start|nearest_enemy; stim (Marine, researched, health>10); siege/unsiege (tank); wait condition=enemy_near,radius=1..30. No extra fields. queue:true only for gather after build or siege after move. Invoke defined name with outer count=1,args={}. Never redefine active skills. JSON workflows retain their listed selectors; Python selectors and macro catalogue support all three races.
python_skill_library lists callable frozen tactics, not JSON definitions. Invoke name with ALL args: {"count":1..32,"target":"home|rally|enemy_start|nearest_enemy","retreat_target":"home|rally","timeout_s":1..300,"params":{documented scalar parameters}}; outer count=1. Allocated squads may retreat locally even with global combat reflex OFF. Cancel via {"skill":"cancel_python_task","args":{"task_id":ID}} then optionally reassign in same plan. Check skill_repairs: repairing/failed names blocked; ready means YOU may issue a new task, never automatic redeployment. Don't submit Python source.
Surrender only if control_policy.surrender_available: sole task {"skill":"surrender","args":{"reason":"1..500 chars"}}, no skills definitions. Never specify opponent. Late plans are checked against changed observations; rejected plans leave existing tasks running. Replan from fresh evidence."""


LANGUAGE_RULES = "所有面向观众的自然语言说明使用简体中文，包括 reason、strategy、修复解释及新技能描述。每个战斗计划包含非空 reason，用100字以内中文简述行动意图，不输出内部推理过程。JSON字段名、技能/API名、参数键、枚举值、代码标识符、文件路径保持协议原样；原始错误保留原文，解释用中文。"
SYSTEM += "\n" + LANGUAGE_RULES
SYSTEM += "\nProduction rallies are automatic by default (main ramp, then an exposed owned expansion entrance) and the automatic path never overwrites an explicit order. You control them per building: rally_units args={\"producers\":[\"barracks#1\",\"hatchery#2\"] or \"all\",\"target\":one name from rally_landmarks or [x,y],\"duration_s\":1..3600 optional,\"reason\":\"short Chinese note\"}; read rally_producers for the exact type#N inventory and rally_orders for current confirmed points. Coordinates are accepted but deliberately given: name the place when a name exists. Read defensive_rally and rally_orders for actual confirmation; rally orders never recall existing squads or set worker rallies. Warp-ins/morphs need your own positioning tasks. Read intelligence on every decision: it retains sightings between model requests, including before scout death. visible means current sight; last_seen/old_unconfirmed are historical and may have moved. Do not treat remembered tags/positions as visible attack targets. Pending alerts are independent of paginated execution feedback."


def bounded_note(value, limit):
    if value is None:return None
    text=str(value)
    return dict(text=text[:limit],truncated=len(text)>limit,original_chars=len(text))


def compact_payload(payload, previous_strategy=None):
    payload=dict(payload)
    payload.pop('recent_events',None)  # Same events remain in structured feedback.
    payload['prepared_strategy']=bounded_note(payload.get('prepared_strategy'),1200)
    payload['previous_strategy']=previous_strategy
    payload['context_policy']={'history':'fresh decision session',
        'strategy_notes':'bounded with explicit truncated/original_chars metadata',
        'feedback':'full events, deferred_count retained; oversized single events sent intact'}
    return payload


def capture(w, tasks):
    return dict(world=w,active={t.id:(t.skill,copy.deepcopy(t.program.summary()) if t.program else None)
                               for t in tasks.tasks if t.active},
                protected=set(tasks.protected()),
                python_versions={k:v['sha256'] for k,v in tasks.python_catalog.entries.items()} if tasks.python_catalog else {},
                library=copy.deepcopy(tasks.library),gas_policy=getattr(tasks,'gas_workers_per_refinery',None))


def revalidate(plan, before, w, tasks, home, age, max_age=70):
    """Return reasons, never mutate/partially enqueue a plan. Not a strategy-quality proof."""
    errors=[]
    if 'develop_skill' in plan:return ['skill development request needs fresh observation']
    if before is None:return ['request observation unavailable; fresh decision required']
    if age>max_age:return ['plan exceeds absolute 70 game-second acceptance limit']
    old=before['world']
    if not isinstance(plan,dict) or not isinstance(plan.get('tasks'),list):return ['malformed plan; tasks list required']
    if plan.get('skills'):return ['late new/redefined JSON workflows require a fresh decision']
    if not plan['tasks']:return []
    old_threat={u.tag for u in old.enemy_units if not u.is_building and u.distance(home)<25}
    new_threat={u.tag for u in w.enemy_units if not u.is_building and u.distance(home)<25}
    for i,item in enumerate(plan['tasks']):
        reason=None
        if not isinstance(item,dict) or not isinstance(item.get('skill'),str):
            errors.append('tasks[%d]: malformed task'%i);continue
        name=item['skill'];args=item.get('args',{})
        if not isinstance(args,dict):errors.append('tasks[%d]: malformed args'%i);continue
        if name=='surrender':reason='late surrender always requires a fresh decision'
        elif name in ('macro_plan','cancel_expansion'):reason='late macro budget/strategy change requires a fresh observation'
        elif name=='no_op':pass
        elif name=='cancel_python_task':
            target=args.get('task_id')
            if type(target) is not int or target not in before['active'] or not any(t.id==target and t.active for t in tasks.tasks):
                reason='cancellation target no longer matches original active task'
        elif new_threat!=old_threat:reason='visible threats near home changed'
        elif getattr(tasks,'tech_tree',None) is not None and name in __import__('arena.macro',fromlist=['MACROS']).MACROS:
            from arena.macro import specification
            from arena.tech_tree import WORKERS,GAS_BUILDINGS,TOWNHALLS
            try:
                spec=specification(tasks.tech_tree,w.race,name,args)
            except (ValueError,KeyError,TypeError) as error:
                errors.append('tasks[%d] %s: %s'%(i,name,error));continue
            old_buildings={u.tag for u in old.my_units if u.is_building}
            buildings={u.tag for u in w.my_units if u.is_building}
            if not old_buildings<=buildings:reason='original structure/tech/base lost'
            elif {u.tag:u.type_id for u in old.enemy_units}!={u.tag:u.type_id for u in w.enemy_units}:
                reason='visible enemy information changed'
            elif any(u.distance(old.by_tag(u.tag))>8 for u in w.enemy_units):
                reason='visible enemy positions changed materially'
            elif spec['kind']=='orbital':reason='late orbital energy/scan decision requires fresh observation'
            elif spec['kind']=='form':reason='late unit transformation requires a fresh tactical decision'
            elif spec['kind']=='gas':
                if {u.tag for u in old.my_units if u.type_id in GAS_BUILDINGS}!={u.tag for u in w.my_units if u.type_id in GAS_BUILDINGS}:
                    reason='gas building population changed'
                elif before.get('gas_policy')!=tasks.gas_workers_per_refinery:reason='gas allocation intent changed'
                elif not any(u.type_id in WORKERS for u in w.my_units):reason='no workers remain'
            else:
                entry=spec['entry'];product=entry['id']
                if spec['kind']=='research':
                    if product in w.upgrades:reason='research already completed'
                elif {(u.tag,u.done) for u in old.my(product)}!={(u.tag,u.done) for u in w.my(product)}:
                    reason='product population/completion changed; fresh quantity required'
                producers={typ for r in entry['recipes'] for typ in r.get('actor_types',[r['producer']])}
                original={u.tag for u in old.my_units if u.type_id in producers}
                current={u.tag for u in w.my_units if u.type_id in producers}
                if original and not original<=current:reason='original production actors lost or transformed'
                if entry.get('structure') and producers & WORKERS and not current:reason='no construction worker remains'
                if name in ('build_supply','supply_depot') and (w.supply_cap>=200 or w.supply_cap-w.supply_used>=8):
                    reason='supply is no longer tight (>=8 free or cap 200)'
                if name=='expand' and {u.tag for u in old.my_units if u.type_id in TOWNHALLS}!={u.tag for u in w.my_units if u.type_id in TOWNHALLS}:
                    reason='base population changed'
            # Queue validation, coalescing and native prerequisites still run at execution.
            # Passing this check is mechanical validity, never strategic-quality proof.
        elif name in SPECS:
            product,_,_,producer=SPECS[name]
            old_products={u.tag for u in old.my(product)};products={u.tag for u in w.my(product)}
            if products!=old_products:reason='production/building population changed; request a fresh quantity'
            elif producer and not any(u.done for u in w.my(producer)):
                # A future producer in the SAME plan is allowed, but a destroyed producer is not.
                if old.my(producer) or not (producer==BARRACKS and any(t.get('skill')=='barracks' for t in plan['tasks'] if isinstance(t,dict))):
                    reason='producer unavailable'
            elif producer and not {u.tag for u in old.my(producer)}.issubset({u.tag for u in w.my(producer)}):reason='original producer lost'
            elif not producer and not any(u.tag in {x.tag for x in old.my(SCV)} for u in w.my(SCV)):reason='original construction workers unavailable'
            if name=='supply_depot' and (w.supply_cap>=200 or w.supply_cap-w.supply_used>=8):reason='supply is no longer tight (>=8 free or cap 200)'
        elif name=='gather':
            if not w.my(SCV):reason='no worker remains'
        elif name=='defend_base':
            # A late defence order must still be mechanically valid: the requested base must exist
            # and the requested kinds must have at least one free unit. Without this branch it fell
            # through to 'unknown late task requires a fresh decision' and the whole plan was
            # rejected, so the commander could never order a defence from a late reply.
            from arena.defense_orders import base_labels, unit_name
            from arena.tech_tree import TOWNHALLS
            halls=[u for u in w.my_units if u.type_id in TOWNHALLS and not u.flying]
            requested=args.get('base')
            if requested=='home':
                point=tuple(tasks.backend.homes[0])
            else:
                point=None
            if requested!='home':
                labels=base_labels(w,tuple(tasks.backend.homes[0]))
                if isinstance(requested,str) and not any(l==requested for l in labels.values()):
                    reason='requested base is no longer owned: '+str(requested)
                elif isinstance(requested,(list,tuple)) and not any(
                        (b.x-requested[0])**2+(b.y-requested[1])**2<=30*30 for b in halls):
                    reason='no owned base near the requested point'
            if not reason and args.get('units')!='all':
                available={unit_name(u.type_id,tasks.backend) for u in w.my_units
                           if not u.is_building and (u.ground_dps>0 or u.air_dps>0)}
                missing=sorted(set(args.get('units') or ())-available)
                if missing:reason='ordered unit kind(s) not present: '+', '.join(missing)
        elif name=='cancel_task':
            if not any(t.active and t.skill=='defend_base' for t in tasks.tasks):
                reason='no active defend_base order to cancel'
        elif name=='rally_units':
            # Revalidate has no backend handle, so it checks the requested type#N selector
            # against the buildings observed in this snapshot. Binding to real tags happens
            # at execution (RallyOrder), which reports a missing building explicitly.
            from arena.rally import PRODUCERS, RALLY_NAMES, parse_producer_selector
            requested=args.get('producers')
            if isinstance(requested,list):
                observed={}
                for unit_ in w.my_units:
                    if unit_.type_id not in PRODUCERS:continue
                    meta=getattr(tasks.backend,'types',{}).get(unit_.type_id)
                    kind=getattr(meta,'name','')
                    if kind:observed.setdefault(''.join(ch for ch in kind if ch.isalnum()).lower(),[]).append(unit_.tag)
                missing=[]
                for selector in requested:
                    try:kind,ordinal=parse_producer_selector(selector)
                    except ValueError:missing.append(str(selector));continue
                    if len(observed.get(kind,()))<ordinal:missing.append(str(selector))
                if missing:reason='requested production building not observed: '+','.join(sorted(missing))
            target=args.get('target')
            if isinstance(target,list) and len(target)==2:
                if not all(isinstance(v,(int,float)) and not isinstance(v,bool) for v in target):reason='coordinate target must be finite [x, y] numbers'
            elif isinstance(target,str):
                if target not in RALLY_NAMES:reason='unknown named rally target '+target
                elif target=='nearest_enemy' and not w.enemy_units:reason='nearest enemy is no longer visible'
        elif name in tasks.library:reason='late JSON workflow requires fresh decision'
        elif name in ('attack_nearest','move_out') or (tasks.python_catalog and name in tasks.python_catalog.entries):
            kinds={MARINE,33,32}
            if name not in ('attack_nearest','move_out'):
                from arena.python_skills import KINDS
                entry=tasks.python_catalog.entries[name];kinds=KINDS[entry['manifest']['unit']]
                if before['python_versions'].get(name)!=entry['sha256'] or name in tasks.python_blocked:reason='Python skill version changed or blocked'
            old_squad={u.tag:u for u in old.my_units if u.type_id in kinds and u.tag not in before['protected']}
            squad={u.tag:u for u in w.my_units if u.type_id in kinds and u.tag not in tasks.protected()}
            if not old_squad or not old_squad.keys()<=squad.keys():reason='original available squad lost or reassigned'
            elif type(args.get('count',1)) is int and len(squad)<args.get('count',1):reason='requested squad size unavailable'
            elif any(squad[tag].health<.8*u.health for tag,u in old_squad.items()):reason='original squad health deteriorated'
            old_enemies={u.tag:u.type_id for u in old.enemy_units};enemies={u.tag:u.type_id for u in w.enemy_units}
            if old_enemies!=enemies:reason='visible enemy composition/targets changed'
            elif any(u.distance(old.by_tag(u.tag).pos)>8 for u in w.enemy_units):reason='visible enemy positions changed materially'
            if args.get('target')=='nearest_enemy' and not w.enemy_units:reason='nearest enemy is no longer visible'
        else:reason='unknown late task requires a fresh decision'
        if reason:errors.append('tasks[%d] %s: %s'%(i,name,reason))
    return errors


# Formatting only: TaskQueue remains the authority for semantic/permission checks.
# Retain composite definitions; their detailed validation is unchanged.
OUTPUT_SCHEMA={
    'type':'object','properties':{
        'tasks':{'type':'array','maxItems':16,'items':{'type':'object',
            'properties':{'skill':{'type':'string'},'count':{'type':'integer','minimum':1,'maximum':20},
                          'args':{'type':'object','additionalProperties':True}},
            'required':['skill'],'additionalProperties':False}},
        'reason':{'type':'string','description':'Short strategic intention, preferably under 100 characters'},
        'skills':{'type':'array','maxItems':8,'items':{'type':'object','additionalProperties':True}}},
    'required':['tasks'],'additionalProperties':False}


def output_schema(payload):
    """Describe the real callable API; the executor still validates all results."""
    import copy
    def obj(properties,required=()):
        return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}
    count={'type':'integer','minimum':1,'maximum':20}
    def task(names,args=None,training=False):
        return obj({'skill':{'type':'string','enum':names},'count':count if training else {'type':'integer','enum':[1]},
                    'args':args or obj({})},['skill'])
    tree=payload.get('tech_tree')
    if tree:
        def named(field,items):return obj({field:{'type':'string','enum':items}},[field])
        variants=[task(['train_worker'],training=True),task(['build_supply','attack_nearest','move_out','gather','no_op']),
            task(['train_unit'],named('unit',[e['name'] for e in tree['units'] if e['name'] not in ('nuke','interceptor')]),training=True),
            task(['build_structure'],obj({'structure':{'type':'string','enum':[e['name'] for e in tree['structures']]},'base':{'type':'string'}},['structure'])),
            task(['research'],named('upgrade',[e['name'] for e in tree['research']])),
            task(['morph_unit'],obj({'unit':{'type':'string','enum':tree['forms']},'target':{'type':'string','enum':['home','rally','enemy_start','nearest_enemy']}},['unit']),training=True),
            task(['gather_gas'],obj({'workers_per_refinery':{'type':'integer','minimum':0,'maximum':3}},['workers_per_refinery'])),
            task(['expand'],obj({'base':{'type':'string'}}))]
        from arena.economy import plan_schema
        macro_branch=task(['macro_plan'],plan_schema([e['name'] for e in tree['units']+tree['structures']]))
        macro_branch['required'].append('args')
        variants.append(macro_branch)
        cancel_branch=task(['cancel_expansion'],obj({'task_id':{'type':'integer','minimum':1}},['task_id']))
        cancel_branch['required'].append('args');variants.append(cancel_branch)
        # rally_units was described in the system prompt but missing from this schema, so the
        # provider's argument validation rejected it before the queue ever saw it.
        producers={'anyOf':[{'type':'string','enum':['all']},
                            {'type':'array','minItems':1,'maxItems':16,'items':
                             {'type':'string','enum':sorted({r.get('label','') for r in payload.get('rally_producers',[])}-{''}) or ['barracks#1']}}]}
        landmark_names=sorted(set(payload.get('rally_landmarks') or []) | {'home','expansion','enemy_start','nearest_enemy'})
        rally_target={'anyOf':[{'type':'string','enum':landmark_names},
                               {'type':'array','minItems':2,'maxItems':2,'items':{'type':'number'}}]}
        variants.append(task(['rally_units'],obj({
            'producers':producers,'target':rally_target,
            'duration_s':{'type':'integer','minimum':1,'maximum':3600},
            'reason':{'type':'string','minLength':1,'maxLength':200}},['producers','target'])))
        ammo=[e['name'] for e in tree['units'] if e['name'] in ('nuke','interceptor')]
        if ammo:variants.append(task(['train_unit'],named('unit',ammo)))
        if tree['race']=='terran':
            variants.append(task(['supply_depot','barracks']))
            variants.append(task(['scan'],obj({'target':{'anyOf':[
                {'type':'string','enum':['home','expansion','enemy_start','nearest_enemy']},
                {'type':'array','minItems':2,'maxItems':2,'items':{'type':'number'}}]}})))
            variants.append(task(['call_mule','supply_drop'],obj({'base':{'type':'string','enum':['home','expansion']}})))
    else:
        variants=[task(['supply_depot','barracks','expand','attack_nearest','move_out','gather','no_op']),
                  task(['train_worker'],training=True),
                  task(['train_unit'],obj({'unit':{'type':'string','enum':['marine']}}),training=True)]
    if payload.get('control_policy',{}).get('spatial_missions_available'):
        from arena.spatial import task_schemas
        variants.extend(task_schemas(payload))
    if payload.get('control_policy',{}).get('defense_orders_available'):
        from arena.defense_orders import task_schemas as defense_schemas
        variants.extend(defense_schemas())
    scalar={'type':['number','boolean','string','null']}
    for entry in payload.get('python_skill_library',[]):
        params=obj({k:{**scalar,'description':v} for k,v in entry['parameters'].items()})
        args=obj({'count':{'type':'integer','minimum':1,'maximum':32},
                  'target':{'type':'string','enum':['home','rally','enemy_start','nearest_enemy']},
                  'retreat_target':{'type':'string','enum':['home','rally']},
                  'timeout_s':{'type':'number','minimum':1,'maximum':300},'params':params},
                 ['count','target','retreat_target','timeout_s','params'])
        branch=task([entry['name']],args);branch['required'].append('args');variants.append(branch)
    if payload.get('python_skill_library'):
        variants.append(task(['cancel_python_task'],obj({'task_id':{'type':'integer','minimum':1}},['task_id'])))
    if payload.get('control_policy',{}).get('surrender_available'):
        variants.append(task(['surrender'],obj({'reason':{'type':'string','minLength':1,'maxLength':500}},['reason'])))
    existing=[d['name'] for d in payload.get('skill_library',[]) if 'name' in d]
    if existing:variants.append(task(existing))
    reason={'type':'string','minLength':1,'maxLength':100,'description':'用简体中文简述行动意图，非空，100字以内。技能标识符保持原样。'}
    known=obj({'tasks':{'type':'array','maxItems':16,'items':{'anyOf':variants}},'reason':reason,
               'skills':{'type':'array','maxItems':0}},['tasks','reason'])
    # Defining and invoking a NEW JSON workflow in one plan remains supported.
    custom=copy.deepcopy(OUTPUT_SCHEMA);custom['required']=['tasks','skills','reason'];custom['properties']['reason']=reason;custom['properties']['skills']['minItems']=1
    reserved=[name for variant in variants for name in variant['properties']['skill']['enum']]
    new_task=task([]);new_task['properties']['skill']={'type':'string','not':{'enum':reserved}}
    custom['properties']['tasks']['items']={'anyOf':variants+[new_task]}
    if payload.get('live_skill_development',{}).get('enabled'):
        from arena.live_skills import request_schema
        development=obj({'tasks':{'type':'array','maxItems':0},'reason':reason,
                         'develop_skill':request_schema()},['tasks','reason','develop_skill'])
        return {'type':'object','anyOf':[known,custom,development]}
    return {'type':'object','anyOf':[known,custom]}

SYSTEM += "\nRead engagement_log for bounded observation windows: enemy_left_vision is not a death, and death events do not prove you killed them. decision_memory stores prior model intentions and periodic milestones, not guaranteed execution. Use it to avoid repeating failed or obsolete strategies; keep current observation authoritative."

SYSTEM += """
Terran command-center choices: build_structure structure=orbital_command upgrades an existing grounded idle CommandCenter after Barracks; it cannot train SCVs while upgrading. Consider upgrading economic bases early, rather than permanently filling every CommandCenter with training tasks. build_structure structure=planetary_fortress requires EngineeringBay and gas, gives static ground defense but NO scans/MULEs. You choose which upgrade; the executor never upgrades automatically.
Orbital abilities are explicit high-level tasks (outer count=1): scan args={"target":"home|expansion|enemy_start|nearest_enemy" or [x,y]}; call_mule args={"base":"home|expansion"}; supply_drop args={"base":"home|expansion"}. Each costs 50 energy from one completed grounded OrbitalCommand; world.bases reports energy and orbital_charges. All three share that energy: reserve for scans if enemy information or cloaked threats matter, otherwise MULEs improve mineral income. Scans temporarily reveal/detect near the chosen place; a concrete [x,y] is accepted when no landmark names it (a remembered fight, a suspected expansion). Coordinates are deliberately given; never grant permanent map knowledge. MULEs mine an observed mineral patch; a spawned MULE is not proof of income. Supply drop boosts one completed unboosted depot by 8, does not replace long-term depot construction, cannot exceed 200. Do not repeat pending casts. Scan requests expire after 30 game seconds, other orbital orders after 120; failures and actual effect confirmations are reported. An unavailable expansion/target never silently falls back to home.
"""

from arena.economy import PROTOCOL as ECONOMY_PROTOCOL
SYSTEM += '\n' + ECONOMY_PROTOCOL

from arena.spatial import PROTOCOL as SPATIAL_PROTOCOL
from arena.defense_orders import PROTOCOL as DEFENSE_PROTOCOL
SYSTEM = SYSTEM.replace('No coordinates/tags in commands.',
    'Coordinates are accepted only by the explicitly documented spatial, scan and rally interfaces; no invented unit tags.')
SYSTEM = SYSTEM.replace('scripts select units/coordinates/placement',
    'scripts allocate units, resolve your spatial goals, and handle placement')
SYSTEM += '\n' + SPATIAL_PROTOCOL
SYSTEM += '\n' + DEFENSE_PROTOCOL
SYSTEM += '''\nDECISION PRIORITY when your own bases are under attack (read defense_report each decision):
1. A base with visible_attackers is a DEFENCE decision, not an economy decision. If urgency is
   critical or urgent, a matching defend_base order goes in THIS reply.
2. Give the order with the reachable unit kinds and counts that can actually trade well; if your
   army is far away and no other defence order exists, the order may recall it -- a distant squad
   that arrives beats a perfect squad that never comes.
3. Never answer an attack with economy-only tasks while seconds_to_loss is dropping: workers lost
   now are worth less than the base you lose next. Rebuild economy AFTER the base holds.
4. Defending, abandoning, or reinforcing one base and giving up another are all legitimate; state
   the choice in reason. Doing nothing and not noticing is not a strategy.
5. Re-check your standing defence orders against defense_report every reply: cancel or replace
   orders whose base is quiet, and keep orders whose base is still contested.'''
