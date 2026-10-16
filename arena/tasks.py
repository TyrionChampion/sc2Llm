"""Persistent, bounded high-level task queue; observe results, don't spam orders."""
from dataclasses import dataclass,field
from collections import deque
import math
from copy import deepcopy
import json
from arena.macro import MACROS,MacroRun,specification,intent_key
from arena.orbital import OrbitalRun, ABILITIES as ORBITAL_ABILITIES
from arena.rally import RallyOrder, RALLY_NAMES, named_rally_point, parse_producer_selector, producer_inventory
from arena.tech_tree import WORKERS,TOWNHALLS
from arena.economy import Economy, validate_plan
from arena.spatial import validate_args as spatial_args, SpatialMission
from arena.composite_skills import PlanValidationError, SkillRun, validate_definitions
from arena.raw_engine import (SCV, MARINE, CC, DEPOT, BARRACKS,
    TRAIN_SCV, TRAIN_MARINE, BUILD_DEPOT, BUILD_BARRACKS, ATTACK, command)

SPECS={
 'train_worker':(SCV,TRAIN_SCV,50,CC),
 'train_unit':(MARINE,TRAIN_MARINE,50,BARRACKS),
 'supply_depot':(DEPOT,BUILD_DEPOT,100,None),
 'barracks':(BARRACKS,BUILD_BARRACKS,150,None),
 'expand':(CC,318,400,None),
}
ALLOWED=set(SPECS)|MACROS|{'attack_nearest','move_out','gather','no_op','macro_plan','cancel_expansion','rally_units','scout','order_units','cancel_unit_order','defend_base','cancel_task','stop_production','use_ability','cancel_native_building','cancel_native_queue','cancel_native_morph'}
TERMINAL={'completed','failed','cancelled'}

@dataclass
class Task:
    id: int
    skill: str
    count: int=1
    source: str="unknown"
    request_id: int|None=None
    status: str='queued'
    reason: str=''
    created: float=0
    started: float|None=None
    baseline: set|None=None
    born: set=field(default_factory=set)
    worker: int|None=None
    target: int|None=None
    spot: tuple|None=None
    excluded: set=field(default_factory=set)
    sent: float=-999
    retry_at: float=0
    attempts: int=0
    issued: int=0
    program: object=None
    args: dict=field(default_factory=dict)
    intent: object=None
    budget: dict=field(default_factory=dict)
    @property
    def active(self): return self.status not in TERMINAL

class TaskQueue:
    def __init__(self,backend,index,emit=print,feedback=None,python_catalog=None):
        self.backend=backend; self.index=index; self.emit=emit
        self.tasks=[]; self.next_id=1; self.clock=0; self.library={}
        self.production_append_receipts=deque(maxlen=128)
        self.last_larva_purchase_task=0
        self.larva_unacknowledged_at_tick_start=False
        self.native_ability_receipts=deque(maxlen=128)
        self.feedback=feedback; self.action_contexts=[]
        self.defense_tags=set(); self.defense_paused={}; self.defense_blocked=set()
        self.python_catalog=python_catalog; self.pending_stops=[];self.python_blocked=set()
        self.tech_tree=getattr(backend,'tech_tree',None)
        self.race=backend.races[index] if hasattr(backend,'races') else 'terran'
        self.gas_workers_per_refinery=None;self.gas_assignments={};self.gas_worker_tags=set();self.gas_next_at=0;self.gas_release_pending=set()
        self.gas_bank=0;self.supply_bank=0
        self.economy=Economy(self.tech_tree,self.race,self.event)
        if self.tech_tree is None:self.emit('[capability-profile] legacy Terran adapter without engine tech_tree; new three-race macros unavailable')
    def _validate_rally_args(self,args):
        """Validate rally_units args. Returns a normalized copy; raises ValueError otherwise."""
        if not isinstance(args,dict):
            raise ValueError('an object')
        allowed={'producers','target','duration_s','reason'}
        for key in sorted(set(args)-allowed):
            raise ValueError('remove unsupported parameter '+key+'; allowed: '+', '.join(sorted(allowed)))
        producers=args.get('producers')
        if producers=='all':
            selectors='all'
        elif not isinstance(producers,list) or not producers:
            raise ValueError('producers must be "all" or a non-empty list like ["barracks#1","hatchery#2"]')
        else:
            selectors=[]
            for item in producers:
                kind,ordinal=parse_producer_selector(item)
                selectors.append(kind+'#'+str(ordinal))
            if len(set(selectors))!=len(selectors):
                raise ValueError('duplicate producer selector in '+repr(producers))
        producers=selectors
        target=args.get('target')
        coordinate=None
        if isinstance(target,str):
            if target not in RALLY_NAMES:
                raise ValueError('target must be one of '+', '.join(sorted(RALLY_NAMES))+' or [x, y]')
        elif isinstance(target,(list,tuple)) and len(target)==2 and all(
                isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in target):
            coordinate=[float(target[0]),float(target[1])]
        else:
            raise ValueError('target must be a named place or a finite [x, y] coordinate pair')
        duration=args.get('duration_s')
        if duration is not None and (type(duration) is not int or not 1<=duration<=3600):
            raise ValueError('duration_s must be an integer 1..3600 when given; omit for a persistent order')
        reason=args.get('reason','')
        if not isinstance(reason,str) or len(reason)>200:
            raise ValueError('reason must be a string of at most 200 characters')
        clean={'producers':selectors,'target':target if isinstance(target,str) else coordinate,
               'duration_s':duration,'reason':reason}
        if isinstance(target,str):
            # Names already accepted elsewhere (morph_unit, python skills) resolve through the
            # shared resolver, so an unknown or unwinnable name fails here rather than in battle.
            clean['target_name']=target
        return clean

    def _rally_spec(self,w,skill,args,grid=None):
        inventory,labels=producer_inventory(self.backend,w,self.index)
        by_label={record['label']:record for record in inventory}
        requested=list(by_label) if args['producers']=='all' else list(args['producers'])
        if not requested:
            raise ValueError('no own completed production building to address; rally_units needs at least one')
        missing=[s for s in requested if s not in by_label]
        if missing:
            raise ValueError('no own completed production building matches '+', '.join(missing)+
                             '; observed: '+(', '.join(sorted(by_label)) or 'none'))
        tags=sorted(by_label[s]['tag'] for s in requested)
        spec={'tags':tags,'target':args['target'],'duration_s':args.get('duration_s'),
              'labels':{by_label[s]['tag']:s for s in requested},'reason':args.get('reason','')}
        target=args['target']
        if isinstance(target,str):
            # Resolve once here so an unknown or currently impossible name fails at enqueue
            # time with a concrete reason, instead of silently doing nothing in battle.
            named_rally_point(grid,self.backend,self.index,target,w,
                              allow_threatened=True,allow_construction=True)
        return spec

    def rally_orders(self):
        return [t for t in self.tasks if t.active and isinstance(t.program,RallyOrder)]

    def rally_owned_tags(self):
        return {tag for t in self.rally_orders() for tag in t.program.tags}

    def _known_own_names(self):
        """Names the commander can legitimately use for defend_base units.

        Must use the same resolver the report uses (defense_orders.unit_name), otherwise the
        commander reads a name from defense_report.own_nearby and is then refused when ordering it.
        """
        from arena.defense_orders import unit_names
        owned=set()
        for type_id in getattr(self.backend,'types',{}) or {}:
            for name in unit_names(type_id,self.backend):
                if name and not name[0].isdigit():
                    owned.add(name)
        return owned

    def _validate_defend_args(self,args):
        """Explicit standing defence order: which base, which kinds, how many, what stance.

        The commander names the target on purpose; the script must not guess it. Leaving a base
        unordered is how the commander gives it up, so `base` and `units` are required.
        """
        if not isinstance(args,dict):
            raise ValueError('an object')
        allowed={'base','units','count','stance','timeout_s','reason'}
        for key in sorted(set(args)-allowed):
            raise ValueError('remove unsupported parameter '+key+'; allowed: '+', '.join(sorted(allowed)))
        base=args.get('base')
        if isinstance(base,str):
            from arena.rally import RALLY_NAMES
            named = (base in RALLY_NAMES
                     or (base.startswith('site_') and base[5:].isdigit())
                     or (base.startswith('base_') and base[5:].isdigit()))
            if not named:
                raise ValueError('base must be home, an observed site_N/base_N from defense_report, or [x,y]')
        elif not (isinstance(base,(list,tuple)) and len(base)==2
                  and all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in base)):
            raise ValueError('base must be home, an observed site_N, or a finite [x,y] pair')
        units=args.get('units')
        if units!='all':
            if not isinstance(units,list) or not units or any(not isinstance(v,str) for v in units):
                raise ValueError('units must be "all" or a non-empty list of unit names from world.own')
            # Fail on a misspelled kind instead of silently defending with nothing: the names must
            # be exactly what the model was shown in world.own / defense_report.
            if self.tech_tree is not None:
                known=set(self._known_own_names())
                requested={str(u).strip().lower() for u in units}
                owned={k.lower() for k in known}
                unknown=sorted(requested-owned)
                if unknown:
                    raise ValueError('unknown unit kind(s) '+', '.join(unknown)+
                                     '; usable names include: '+', '.join(sorted(known)[:10]))
        count=args.get('count')
        if count is not None and (type(count) is not int or not 1<=count<=32):
            raise ValueError('count must be an integer 1..32 when given')
        stance=args.get('stance','hold')
        if stance not in ('hold','trade','screen'):
            raise ValueError('stance must be hold, trade or screen')
        timeout=args.get('timeout_s',300)
        if type(timeout) not in (int,float) or not 30<=timeout<=900:
            raise ValueError('timeout_s must be 30..900 game seconds')
        reason=args.get('reason','')
        if not isinstance(reason,str) or not 1<=len(reason.strip())<=200:
            raise ValueError('reason requires 1..200 characters')
        return {'base':base,'units':units,'count':count,'stance':stance,
                'timeout_s':timeout,'reason':reason.strip()}

    def _rally_grid(self):
        """Static ground map for coordinate clamping/named entrances, or None if absent."""
        ready=getattr(self.backend,'grid',None)
        if ready is not None:return ready
        infos=getattr(self.backend,'map_info_by_player',None)
        if infos is None:return None
        try:
            from arena.rally import GroundMap
            return GroundMap(infos[self.index].start_raw)
        except (ValueError,AttributeError,TypeError,IndexError):
            return None

    def update(self,t,status,reason):
        if (t.status,t.reason)!=(status,reason):
            self.emit('[task %d] %s -> %s: %s' % (t.id,t.skill,status,reason))
            previous=t.status
            t.status,t.reason=status,reason
            if status in TERMINAL and t.program and hasattr(t.program,"close"):t.program.close()
            self.event('task_status',task_id=t.id,skill=t.skill,previous_state=previous,
                       state=status,reason=reason,source=t.source,request_id=t.request_id)
    def event(self,kind,**details):
        if self.feedback: self.feedback(kind,**details)
    def issue(self,t,action,output):
        tags=set(action.action_raw.unit_command.unit_tags)
        if tags & self.defense_tags:
            key=t.id if t else None
            if key not in self.defense_blocked:
                self.defense_blocked.add(key)
                self.event('defense_order_blocked',task_id=key,unit_tags=sorted(tags & self.defense_tags),
                           handling='Conflicting command suppressed; defender owns these units.')
            return False
        output.append(action)
        self.action_contexts.append(dict(source='task_queue' if t else 'gas_allocation',task_id=t.id if t else None,skill=t.skill if t else 'gather_gas',
                                        request_id=t.request_id if t else None))
        return True
    @staticmethod
    def unissued_macro(t):
        run=t.program
        return (isinstance(run,MacroRun) and run.spec.get('kind') in ('production','research','form')
                and run.issued==0 and not run.pending and not run.relocation)
    @staticmethod
    def stoppable_production(t):
        return bool(t and isinstance(t.program,MacroRun) and t.program.spec.get('kind')=='production'
            and not t.program.spec['entry'].get('structure')
            and t.program.spec['entry']['name'] not in ('nuke','interceptor'))
    def enqueue(self,items,now,source='LLM',request_id=None,skills=None):
        issues=[]
        def invalid(field,received,expected):
            issues.append(dict(field=field,received=received,expected=expected))
        if not isinstance(items,list) or len(items)>16:
            raise PlanValidationError([dict(field='tasks',received=items,
                expected='a list of 0..16 high-level commands')])
        python_names=set(self.python_catalog.entries) if self.python_catalog else set()
        proposed=validate_definitions([] if skills is None else skills,self.library,ALLOWED|{'surrender','cancel_python_task'}|python_names,
                                      {t.skill for t in self.tasks if t.active})
        allowed_skills=ALLOWED|set(proposed)|python_names|({'cancel_python_task'} if python_names else set())
        if not items and not skills:
            self.emit('[queue] %s explicit wait; keeping %d active tasks' %
                      (source,sum(t.active for t in self.tasks)))
            self.event('plan_wait',request_id=request_id,active_tasks=sum(t.active for t in self.tasks))
            return
        clean=[]; cancellations=set(); supersedes=[];native_cancel_records={};production_stops=set()
        if sum(isinstance(i,dict) and i.get('skill')=='macro_plan' for i in items)>1:
            invalid('tasks',items,'at most one macro_plan per reply')
        for index,item in enumerate(items):
            field='tasks[%d]' % index
            if not isinstance(item,dict) or not isinstance(item.get('skill'),str) or item.get('skill') not in allowed_skills:
                invalid(field,item,'task with skill in '+', '.join(sorted(allowed_skills)));continue
            for extra in item.keys()-{'skill','args','count'}:
                invalid(field+'.'+extra,item[extra],'remove unsupported task field')
            skill=item['skill'];args=item.get('args',{})
            if not isinstance(args,dict): invalid(field+'.args',args,'an object')
            elif skill=='cancel_expansion':
                task_id=args.get('task_id')
                target=next((t for t in self.tasks if t.id==task_id),None)
                if (set(args)!={'task_id'} or type(task_id) is not int or not target or
                    not target.active or target.skill!='expand' or not isinstance(target.program,MacroRun) or target.program.issued):
                    invalid(field+'.args',args,'task_id of an active unissued expansion; native construction cannot be cancelled here')
                else:cancellations.add(task_id)
            elif skill=='stop_production':
                target=next((t for t in self.tasks if t.id==args.get('task_id')),None)
                if set(args)!={'task_id'} or type(args.get('task_id')) is not int or not self.stoppable_production(target):
                    invalid(field+'.args',args,'task_id of an owned worker/unit production goal; no buildings, research, forms or ammo')
                else:production_stops.add(target.id)
            elif skill=='macro_plan':
                try:args=validate_plan(args,self.tech_tree,self.race)
                except (ValueError,KeyError,TypeError) as e:invalid(field+'.args',args,str(e))
            elif skill=='use_ability':
                from arena.native_abilities import validate_args
                try:args=validate_args(self.tech_tree,args,self.backend.size)
                except (ValueError,KeyError,TypeError) as e:invalid(field+'.args',args,str(e))
            elif skill=='cancel_native_building':
                from arena.native_building_cancel import supported,capture
                task_id=args.get('task_id');target=next((t for t in self.tasks if t.id==task_id),None)
                if set(args)!={'task_id'} or type(task_id) is not int or target is None or not supported(target):
                    invalid(field+'.args',args,'task_id of an owned worker-built building macro; not unit queues, research or townhall upgrades')
                else:
                    native_cancel_records[task_id]=capture(target)
                    if target.active:cancellations.add(task_id)
            elif skill=='cancel_native_queue':
                from arena.native_queue_cancel import supported,capture
                task_id=args.get('task_id');target=next((t for t in self.tasks if t.id==task_id),None)
                if set(args)!={'task_id'} or type(task_id) is not int or target is None or not supported(target,self.tech_tree):
                    invalid(field+'.args',args,'task_id of an owned unit/research macro with building producers; not eggs, morphs or construction')
                else:
                    native_cancel_records[task_id]=dict(capture(target,self.tech_tree),cancel_tool=skill)
                    if target.active:cancellations.add(task_id)
            elif skill=='cancel_native_morph':
                from arena.native_morph_cancel import supported,capture
                task_id=args.get('task_id');target=next((t for t in self.tasks if t.id==task_id),None)
                if set(args)!={'task_id'} or type(task_id) is not int or target is None or not supported(target,self.tech_tree):
                    invalid(field+'.args',args,'task_id of an owned paid building-upgrade macro; not units, research or construction')
                else:
                    native_cancel_records[task_id]=dict(capture(target),cancel_tool=skill)
                    if target.active:cancellations.add(task_id)
            elif skill in python_names:
                if skill in self.python_blocked:invalid(field+'.skill',skill,'skill is blocked pending repair; inspect skill_repairs/live_skill_development')
                args=self.python_catalog.validate_args(skill,args,field+'.args')
            elif skill=='cancel_python_task':
                task_id=args.get('task_id')
                target=next((t for t in self.tasks if t.id==task_id),None)
                if set(args)!={'task_id'} or type(task_id) is not int or not target or not target.active or target.skill not in python_names:
                    invalid(field+'.args',args,'task_id of an active Python task owned by this player')
                else:cancellations.add(task_id)
            elif skill in MACROS and self.tech_tree is not None:
                try:specification(self.tech_tree,self.race,skill,args)
                except (ValueError,KeyError,TypeError) as e:invalid(field+'.args',args,str(e))
            elif skill=='cancel_unit_order':
                task_id=args.get('task_id')
                target=next((t for t in self.tasks if t.id==task_id),None)
                if set(args)!={'task_id'} or type(task_id) is not int or not target or target.skill not in ('scout','order_units'):
                    invalid(field+'.args',args,'task_id of a known scout/order_units mission owned by this player')
                else:cancellations.add(task_id)
            elif skill=='defend_base':
                try:args=self._validate_defend_args(args)
                except (ValueError,KeyError,TypeError) as e:invalid(field+'.args',args,str(e))
                else:
                    # A new order for the SAME base replaces the old one (the commander would
                    # otherwise have to cancel first just to re-split its troops). Which base it
                    # resolves to is only known once a snapshot exists, so bind it on first tick.
                    same=sorted(t.id for t in self.tasks if t.active and t.skill=='defend_base'
                                and t.program is not None and t.program.base_selector==args['base'])
                    if same:
                        supersedes.extend(same)
                        for task_id in same:
                            cancellations.add(task_id)
            elif skill=='cancel_task':
                task_id=args.get('task_id')
                target=next((t for t in self.tasks if t.id==task_id),None)
                if set(args)!={'task_id'} or type(task_id) is not int or not target \
                        or not (target.skill in ('defend_base','scout','order_units','rally_units')
                                or self.unissued_macro(target)):
                    invalid(field+'.args',args,
                            'task_id of an owned defend_base/scout/order_units/rally_units mission or wholly unissued macro; issued native orders/relocations cannot be cancelled here')
                else:cancellations.add(task_id)
            elif skill in ('scout','order_units'):
                try:args=spatial_args(skill,args,self.backend.size)
                except (ValueError,KeyError,TypeError) as e:invalid(field+'.args',args,str(e))
            elif skill=='rally_units':
                try:args=self._validate_rally_args(args)
                except (ValueError,KeyError,TypeError) as e:invalid(field+'.args',args,str(e))
            elif skill in MACROS and skill not in SPECS:
                invalid(field+'.skill',skill,'backend with an engine-bound three-race tech_tree')
            else:
                allowed=({'unit','queue_mode'} if skill=='train_unit' else
                         {'queue_mode'} if skill=='train_worker' else set())
                for arg in sorted(set(args)-allowed):
                    invalid(field+'.args.'+arg,args[arg],
                            'remove this parameter; allowed args for '+skill+': '+(', '.join(sorted(allowed)) or 'none'))
                if skill=='train_unit' and args.get('unit','marine')!='marine':
                    invalid(field+'.args.unit',args.get('unit'),'marine (currently supported unit)')
                if skill in ('train_worker','train_unit') and args.get('queue_mode','coalesce') not in ('coalesce','append'):
                    invalid(field+'.args.queue_mode',args.get('queue_mode'),'coalesce or append')
            count=item.get('count',1)
            if type(count) is not int or not 1<=count<=200:
                invalid(field+'.count',count,'integer 1..200')
            elif (skill not in ('train_worker','train_unit','morph_unit') and
                  not (self.tech_tree is not None and skill in ('build_supply','build_structure','expand','supply_depot','barracks')) and count!=1):
                invalid(field+'.count',count,'1; quantities >1 require training, forms or engine-bound construction')
            if skill=='train_unit' and isinstance(args,dict) and args.get('unit') in ('nuke','interceptor') and count!=1:
                invalid(field+'.count',count,'1 for ammo restocking; raw API does not expose an exact stored-ammo count')
            clean.append((skill,count,args))
        if issues: raise PlanValidationError(issues)
        casters=[args['caster'] for skill,count,args in clean if skill=='use_ability']
        if len(set(casters))!=len(casters):
            raise PlanValidationError([dict(field='tasks',received=casters,
                expected='at most one explicit ability order per caster in a reply')])
        def key(skill,args):
            if skill in ('cancel_native_building','cancel_native_queue','cancel_native_morph'):return (skill,args['task_id'])
            if skill=='use_ability':return (skill,json.dumps(args,sort_keys=True))
            if skill=='defend_base':
                # Distinct bases/kind-splits are distinct intents: two simultaneous defences must
                # coexist. reason is deliberately excluded, like scout/order_units.
                units=args.get('units')
                return (skill,json.dumps(dict(base=args.get('base'),
                    units='all' if units=='all' else sorted(units or ()),
                    count=args.get('count'),stance=args.get('stance','hold')),
                    sort_keys=True,ensure_ascii=False))
            if skill in ('scout','order_units'):
                return (skill,json.dumps({k:v for k,v in args.items() if k!='reason'},sort_keys=True,ensure_ascii=False))
            if skill=='rally_units':
                requested=args.get('producers')
                return (skill,str(args.get('target')),'all' if requested=='all' else tuple(sorted(requested or ())))
            if self.tech_tree is not None and skill in MACROS:return intent_key(specification(self.tech_tree,self.race,skill,args))
            return skill
        def receipt(skill,args):
            return (source,request_id,key(skill,args)) if request_id is not None else None
        inactive_after_stop={t.id for t in self.tasks if t.id in production_stops and not t.program.issued}
        inactive_after_plan=cancellations|inactive_after_stop
        append_keys=set()
        for index,(skill,count,args) in enumerate(clean):
            if args.get('queue_mode')!='append':continue
            intent=key(skill,args)
            product=(specification(self.tech_tree,self.race,skill,args)['entry']['id']
                     if self.tech_tree is not None and skill in ('train_worker','train_unit') else intent)
            if product in append_keys:
                invalid('tasks[%d]'%index,args,'at most one append per product in a plan')
            append_keys.add(product)
            if receipt(skill,args) in self.production_append_receipts:continue
            target=next((t for t in self.tasks if t.active and t.id not in inactive_after_plan and t.intent==intent),None)
            remaining=(max(0,target.program.issued-len(target.program.born)) if target and target.id in production_stops
                else self.production_quantity(target)['remaining'] if target else 0)
            if target and remaining+count>200:
                invalid('tasks[%d].count'%index,count,'outstanding production quantity after append must stay <=200')
        if issues:raise PlanValidationError(issues)
        remaining={t.intent if t.intent is not None else t.skill for t in self.tasks if t.active and t.id not in inactive_after_plan}
        incoming={key(skill,args) for skill,count,args in clean if skill not in ('cancel_python_task','cancel_expansion','cancel_unit_order','cancel_task','stop_production','macro_plan')
                  and not (args.get('queue_mode')=='append' and receipt(skill,args) in self.production_append_receipts)
                  and not (skill=='use_ability' and receipt(skill,args) in self.native_ability_receipts)}
        new_count=len(incoming-remaining)
        if sum(t.active and t.id not in inactive_after_plan for t in self.tasks)+new_count>32: raise ValueError('task queue full')
        if len((remaining|incoming)&python_names)>4: raise ValueError('at most 4 active Python tactics per player')
        for t in self.tasks:
            if t.id in production_stops:
                if not t.active:
                    self.event('production_stop_noop',task_id=t.id,state=t.status,request_id=request_id)
                else:
                    removed=max(0,t.count-t.program.issued)
                    if removed:
                        t.program.cancelled_unissued=getattr(t.program,'cancelled_unissued',0)+removed
                        t.count=t.program.issued
                    self.event('production_goal_stopped',task_id=t.id,stopped_unissued=removed,
                        quantity=self.production_quantity(t),native_orders_cancelled=False,refund_confirmed=False,request_id=request_id)
                    if not t.count:self.update(t,'cancelled','Unissued production goal stopped; no native order cancelled or refund inferred')
            if t.id in cancellations:
                if t.id in supersedes:setattr(t,'superseded',True)
                if not t.active:
                    self.event('task_cancel_noop',task_id=t.id,skill=t.skill,state=t.status,
                               reason='Owned mission already ended; no stop order or state rewrite',
                               request_id=request_id)
                    continue
                if t.id in native_cancel_records:
                    cancel_kind={'cancel_native_queue':'queue','cancel_native_morph':'morph'}.get(native_cancel_records[t.id].get('cancel_tool'),'building')
                    self.event('native_'+cancel_kind+'_cancel_requested',original_task_id=t.id,
                        request_id=request_id,original_request_id=t.request_id,
                        native_cancellation_complete=False,refund_confirmed=False)
                    self.update(t,'cancelled','Explicit macro goal stopped; linked native '+cancel_kind+' cancellation still pending')
                elif t.skill in python_names or t.skill in ('scout','order_units','defend_base'):
                    self.pending_stops.append(t)
                    self.update(t,'cancelled',
                        'superseded by a new order for the same base; its units are inherited'
                        if getattr(t,'superseded',False)
                        else 'LLM cancelled allocated mission; stop orders pending')
                else:
                    self.update(t,'cancelled','LLM cancelled unissued macro; no native order cancelled or spent resources refunded')
        self.tasks=[t for t in self.tasks if t.active]+[t for t in self.tasks if not t.active][-48:]
        for name,definition in proposed.items():
            if self.library.get(name)!=definition:
                self.emit('[skill] %s defined by %s: %d steps' % (name,source,len(definition['steps'])))
                self.event('skill_defined',skill=name,definition=deepcopy(definition),request_id=request_id,source=source)
        self.library=proposed
        for skill,count,args in clean:
            if skill in ('cancel_python_task','cancel_expansion','cancel_unit_order','cancel_task','stop_production'):continue
            if skill=='use_ability' and receipt(skill,args) in self.native_ability_receipts:
                self.event('native_ability_request_replayed',request_id=request_id,
                           reason='same explicit request already accepted; no extra cast')
                continue
            append=args.get('queue_mode')=='append'
            record=receipt(skill,args) if append else None
            if record is not None and record in self.production_append_receipts:
                self.event('production_append_replayed',skill=skill,request_id=request_id,
                           added_quantity=0,reason='this explicit append request already applied')
                continue
            existing=next((t for t in self.tasks if t.active and
                (t.intent if t.intent is not None else t.skill)==key(skill,args)),None)
            if existing and append:
                previous=existing.count;existing.count+=count
                if record is not None:self.production_append_receipts.append(record)
                self.event('production_appended',task_id=existing.id,skill=skill,
                    request_id=request_id,previous_count=previous,added_quantity=count,
                    production_quantity=self.production_quantity(existing),
                    reason='explicit finite replenishment; in-flight orders and deadline preserved')
                continue
            if existing:
                self.emit('[queue] %s duplicate active intent %s coalesced' % (source,skill))
                self.event('task_coalesced',skill=skill,task_id=existing.id,request_id=request_id,
                    requested_count=count,added_quantity=0,
                    **({'production_quantity':self.production_quantity(existing)}
                       if skill in ('train_worker','train_unit') else {}),
                    reason=('same intent already active; no quantity added; use queue_mode=append for explicit replenishment'
                            if skill in ('train_worker','train_unit') else 'same intent already active; existing task preserved'))
                continue
            t=Task(self.next_id,skill,count,source=source,request_id=request_id,created=now,args=deepcopy(args),intent=key(skill,args))
            if skill=='macro_plan':
                self.economy.set_plan(args,now,request_id)
                self.update(t,'completed','macro goal recorded; explicit production tasks still required')
            if self.tech_tree is not None and skill in MACROS:
                run_type=OrbitalRun if skill in ORBITAL_ABILITIES else MacroRun
                if skill=='inject_larva':
                    from arena.queen import InjectLarvaRun
                    run_type=InjectLarvaRun
                t.program=run_type(specification(self.tech_tree,self.race,skill,args),args)
            elif skill=='rally_units':
                grid=self._rally_grid()
                t.program=RallyOrder(self.backend,self.index,self.event,grid=grid,
                                     build_spec=lambda world, args=args, grid=grid, skill=skill: self._rally_spec(world,skill,args,grid))
            elif skill in ('scout','order_units'):t.program=SpatialMission(skill,args)
            elif skill=='use_ability':
                from arena.native_abilities import NativeAbilityRun
                t.program=NativeAbilityRun(args)
            elif skill=='cancel_native_building':
                from arena.native_building_cancel import NativeBuildingCancelRun
                t.program=NativeBuildingCancelRun(native_cancel_records[args['task_id']])
            elif skill=='cancel_native_queue':
                from arena.native_queue_cancel import NativeQueueCancelRun
                t.program=NativeQueueCancelRun(native_cancel_records[args['task_id']])
            elif skill=='cancel_native_morph':
                from arena.native_morph_cancel import NativeMorphCancelRun
                t.program=NativeMorphCancelRun(native_cancel_records[args['task_id']])
            elif skill=='defend_base':
                from arena.defense_orders import DefendBase
                inherited=[x for x in (next((y for y in self.tasks if y.id==task_id),None)
                                       for task_id in supersedes) if x is not None]
                t.program=DefendBase(args,supersedes=inherited)
            elif skill in self.library:t.program=SkillRun(self.library[skill])
            elif skill in python_names:
                from arena.python_skills import PythonSkillRun
                t.program=PythonSkillRun(self.python_catalog.entries[skill],args)
            self.next_id+=1; self.tasks.append(t)
            if skill=='use_ability' and request_id is not None:self.native_ability_receipts.append(receipt(skill,args))
            if record is not None:self.production_append_receipts.append(record)
            self.emit('[queue] %s enqueued #%d %s count=%d' % (source,t.id,skill,count))
            self.event('task_enqueued',task_id=t.id,skill=skill,count=count,request_id=request_id,source=source)
    @staticmethod
    def production_quantity(t):
        issued=t.program.issued if isinstance(t.program,MacroRun) else t.issued
        completed=len(t.program.born) if isinstance(t.program,MacroRun) else len(t.born)
        stopped=getattr(t.program,'cancelled_unissued',0)
        return dict(requested=t.count+stopped,issued=issued,observed_completed=completed,
                    unissued=max(0,t.count-issued),awaiting_completion=max(0,issued-completed),
                    remaining=max(0,t.count-completed),**({'cancelled_unissued':stopped} if stopped else {}))
    def summary(self):
        return [{'id':t.id,'skill':t.skill,'count':t.count,'state':t.status,'reason':t.reason,
                 **({'production_quantity':self.production_quantity(t)}
                    if t.skill in ('train_worker','train_unit','build_supply','build_structure','expand','supply_depot','barracks') else {}),
                 **({'workflow':self.workflow_summary(t)} if t.program else {}),
                 **({'budget':t.budget} if t.budget else {})}
                for t in self.tasks if t.active]
    def workflow_summary(self,t):
        result=t.program.summary()
        if isinstance(t.program,MacroRun) and t.program.spec['kind']=='form':
            result['form_sources']=sorted({self.tech_tree.names[r['producer']]
                for r in t.program.spec['entry']['recipes']})
            result['form_meaning']='Free transformation from existing source actors, not purchase of the named product.'
        return result
    def pending_purchase_reservations(self):
        """Reserve uncertain in-flight purchases against potentially lagging realtime stock."""
        result=dict(minerals=0,gas=0,supply=0,orders=0)
        for t in self.tasks:
            if not t.active:continue
            purchases=(t.program.pending if isinstance(t.program,MacroRun)
                       else getattr(t.program,'pending_purchases',()))
            for p in purchases:
                if p.get('seen'):continue
                for key in ('minerals','gas','supply'):result[key]+=p.get(key,0)
                result['orders']+=1
        return result
    def protected(self):
        return {t.worker for t in self.tasks if t.active and t.worker}|{
            tag for t in self.tasks if t.active and t.program and getattr(t,'skill',None)!='rally_units'
            for tag in t.program.tags}|self.gas_worker_tags|set(self.gas_assignments)|self.defense_tags
    def take_defense(self,tags,now):
        from arena.python_skills import PythonSkillRun
        self.defense_tags=set(tags)
        for t in self.tasks:
            if t.active and isinstance(t.program,PythonSkillRun) and t.program.tags & self.defense_tags and t.id not in self.defense_paused:
                self.defense_paused[t.id]=now
                t.program.close()
                t.program.worker=None
                t.program.sent_loop=None
                self.update(t,'paused','Defense temporarily owns squad; old worker reply discarded')
                self.event('defense_task_paused',task_id=t.id,unit_tags=sorted(t.program.tags))
    def release_defense(self,w):
        from arena.python_skills import IsolatedWorker
        self.defense_tags.clear();self.defense_blocked.clear()
        for t in self.tasks:
            if t.id not in self.defense_paused:continue
            since=self.defense_paused.pop(t.id)
            if not t.active:continue
            embarked={tag for u in w.my_units for tag in u.passengers}
            if any((w.by_tag(tag) is None and tag not in embarked) or (w.by_tag(tag) is not None and w.by_tag(tag).alliance!=1) for tag in t.program.tags):
                self.update(t,'cancelled','Squad changed during defense; commander must redeploy survivors explicitly')
                self.event('defense_task_needs_redispatch',task_id=t.id,reason='allocated member absent after defense; not assumed killed')
                continue
            duration=w.seconds-since;t.created+=duration
            if t.program.mission:t.program.mission['deadline_game_s']+=duration
            t.program.worker=IsolatedWorker(t.program.entry)
            t.program.last.clear();t.program.sent_loop=None;t.program.next_at=w.seconds
            self.update(t,'running','Defense released squad; retained script state, fresh observation and fresh worker')
            self.event('defense_task_resumed',task_id=t.id,paused_game_s=duration)
    def candidates(self,w,t):
        base=self.backend.homes[self.index]
        if t.skill=='expand':
            patches=[u for u in w.units if u.mineral and u.distance(base)>14]
            if not patches: return []
            base=min(patches,key=lambda m:m.distance(base)).pos
        odd=t.skill in ('barracks','expand')
        result=[]
        for r in (7,10,13,16):
            for dx,dy in ((r,0),(-r,0),(0,r),(0,-r),(r,r),(-r,-r),(r,-r),(-r,r)):
                p=(math.floor(base[0])+dx+(.5 if odd else 0),
                   math.floor(base[1])+dy+(.5 if odd else 0))
                if p in t.excluded: continue
                # Placement queries cannot see other orders from THIS batch yet.
                # Reserve sites until the corresponding building is observed.
                if any(other is not t and other.active and other.spot is not None
                       and math.dist(p,other.spot)<5 for other in self.tasks): continue
                if not(3<p[0]<self.backend.size[0]-3 and 3<p[1]<self.backend.size[1]-3): continue
                # Keep space for Barracks add-ons and avoid mineral lines.
                if any(u.is_building and u.distance(p)<(6 if u.type_id==CC else 4) for u in w.my_units): continue
                if any(u.mineral and u.distance(p)<4 for u in w.units): continue
                result.append(p)
        return result
    def tick(self,w,reserved):
        self.clock=w.seconds; now=w.seconds; output=[]; used=set(reserved)
        self.defense_tags_snapshot=None   # first defence order this tick fills it in
        self.economy.observe(w)
        self.economy.refresh(w,self.tasks)
        # Rally owns only the rally setting; it must not reserve the production queue.
        # rally_owned_tags() separately protects the setting from automatic rallies.
        used.update(tag for t in self.tasks if t.active and t.program and getattr(t,'skill',None)!='rally_units'
                    for tag in t.program.tags)
        self.action_contexts=[]
        # A cancelled defence order still holds its defense_tags reservation until stop_orders
        # runs below. Mark only those tags used, so a same-reply follow-up order does not dispatch
        # into a blocked reservation and get silently dropped while being marked completed.
        # Scoped to defenses on purpose: Python/JSON cancellations must stay atomically
        # reassignable in the same reply.
        # Superseded orders are cancelled during enqueue; their units are rebound to the new
        # order by tag, so mark them used this tick to avoid a second order grabbing the same unit.
        # Cancelled defences still hold their reservation until stop_orders runs below; reserve
        # those tags for this tick so a same-reply follow-up order is not dispatched into a blocked
        # reservation. Active defences are handled inside DefendBase itself (it subtracts its own
        # previous tags), so they must NOT be added here.
        self.reserved_for_cancel = {tag for t in self.tasks if not t.active and t.program
                                    and getattr(t.program, 'holds_defense_reservation', False)
                                    for tag in getattr(t.program, 'tags', ())}
        used.update(self.reserved_for_cancel)
        used.update(tag for cancelled in self.pending_stops if cancelled.program
                    and getattr(cancelled.program, 'holds_defense_reservation', False)
                    for tag in getattr(cancelled.program, 'tags', ()))
        for cancelled in self.pending_stops:
            cancelled.program.stop_orders(w,self,cancelled,output)
            self.event('spatial_cancel_stop_requested' if cancelled.skill in ('scout','order_units','defend_base') else
                       'python_skill_cancel_stop_requested',task_id=cancelled.id,confirmed=False)
        self.pending_stops.clear()
        pending=self.pending_purchase_reservations()
        self.larva_unacknowledged_at_tick_start=any(
            p.get('larva_purchase') and not p.get('seen')
            for task in self.tasks if task.active and isinstance(task.program,MacroRun)
            for p in task.program.pending)
        bank=max(0,w.minerals-pending['minerals'])
        self.gas_bank=max(0,w.vespene-pending['gas'])
        self.supply_bank=w.supply_cap-w.supply_used-pending['supply']
        supply=self.supply_bank
        if self.tech_tree is not None:self.balance_gas(w,used,output)

        ordered=sorted(self.tasks,key=lambda t:t.id!=self.economy.budget_task)
        # One native larva purchase may consume a different larva. Rotate
        # requested products so the confirmation gate does not starve later goals.
        slots=[i for i,t in enumerate(ordered) if t.active and isinstance(t.program,MacroRun)
            and t.id!=self.economy.budget_task and t.program.issued<t.count
            and t.program.spec.get('kind')=='production'
            and any(r['producer']==151 for r in t.program.spec['entry']['recipes'])]
        rotated=sorted((ordered[i] for i in slots),key=lambda t:(t.id<=self.last_larva_purchase_task,t.id))
        for i,t in zip(slots,rotated):ordered[i]=t
        for t in ordered:
            if not t.active: continue
            if t.id in self.defense_paused:continue
            if t.program:
                self.economy.refresh(w,self.tasks)
                held_m,held_g=self.economy.holdback(w,t,bank,self.gas_bank)
                before_m,before_g=bank,self.gas_bank
                self.gas_bank-=held_g
                try:
                    bank=t.program.tick(w,self,t,used,bank-held_m,output)+held_m
                finally:
                    self.gas_bank+=held_g
                self.economy.spent(t,before_m-bank,before_g-self.gas_bank,now)
                t.budget=(dict(reserved_for_expansion_task=self.economy.budget_task,
                               withheld_minerals=held_m,withheld_gas=held_g,
                               note='Task received only unreserved resources; inspect reason for other blockers.')
                          if held_m or held_g else {})
                if pending['orders']:
                    t.budget['unconfirmed_purchases_reserved_at_tick_start']=dict(pending)
                    t.budget['unconfirmed_purchase_meaning']='Not a spend/completion claim; hold until observed order/product or rejection, preventing duplicate use of lagging stock.'
                continue
            deadline=max(300,180+t.count*30)
            if now-t.created>deadline:
                self.update(t,'failed','%d game-second deadline exceeded' % deadline); continue
            if t.skill in ('gather','no_op'):
                if t.skill=='gather' and self.tech_tree is not None:self.rebalance_minerals(w,used,output,t)
                self.update(t,'completed','mineral allocation dispatched; idle-worker gathering maintained'); continue
            if t.skill in ('attack_nearest','move_out'):
                if getattr(self,'army_tactics',None) is not None:
                    self.army_tactics.mission(t.skill)
                    self.update(t,'completed','persistent tactical mission accepted; execution and outcome require observation')
                    continue
                army=[u for u in w.my_units if u.done and not u.hallucination
                      and not u.is_building and u.type_id not in WORKERS
                      and (u.ground_dps>0 or u.air_dps>0 or u.type_id in (54,56,81,82,129,126,499,111,77,75))]
                if not army:
                    self.update(t,'waiting','no combat units'); continue
                tags=[u.tag for u in army if u.tag not in used]
                if not tags: continue
                enemies=w.enemy_units
                if t.skill=='attack_nearest' and enemies:
                    target=min(enemies,key=lambda e:army[0].distance(e)).pos
                else: target=self.backend.enemy_start(self.index)
                self.issue(t,command(ATTACK,tags,pos=target),output); used.update(tags)
                self.update(t,'completed','attack-move handed to action pipeline; dispatch/effect require later evidence'); continue
            type_id,ability,cost,producer_type=SPECS[t.skill]
            if t.baseline is None: t.baseline={u.tag for u in w.my(type_id)}
            fresh=[u for u in w.my(type_id) if u.tag not in t.baseline]
            t.born.update(u.tag for u in fresh if u.done)
            if len(t.born)>=t.count:
                self.update(t,'completed','observed %d newly completed unit(s)' % len(t.born)); continue
            if producer_type is not None:
                if t.issued>=t.count:
                    self.update(t,'running','all requested orders issued; awaiting observed completion');continue
                producers=[u for u in w.my(producer_type) if u.done and not u.orders and u.tag not in used]
                if not producers:
                    self.update(t,'waiting','production building busy or missing'); continue
                # A command may not appear in the next observation immediately.
                if now<t.sent+1: continue
                if bank<cost or supply<1:
                    self.update(t,'waiting','resources or supply'); continue
                p=producers[0]
                self.issue(t,command(ability,[p.tag]),output); used.add(p.tag)
                t.issued+=1
                bank-=cost; supply-=1; t.sent=now
                self.update(t,'running','training; awaiting observed completion'); continue
            # Building state is matched by new unit tag + intended location.
            if t.target is None and t.spot:
                matches=[u for u in fresh if u.distance(t.spot)<2]
                if matches: t.target=matches[0].tag
            building=w.by_tag(t.target) if t.target else None
            if building:
                self.update(t,'running','construction started; awaiting completion')
                worker=w.by_tag(t.worker)
                if worker is None:
                    choices=[u for u in w.my(SCV) if u.tag not in used and u.tag not in self.protected()]
                    if choices: worker=min(choices,key=lambda u:u.distance(building)); t.worker=worker.tag
                if worker and worker.tag not in used and not worker.orders and now>=t.sent+2:
                    self.issue(t,command(1,[worker.tag],target=building.tag),output)
                    used.add(worker.tag); t.sent=now
                continue
            if t.target is not None:
                self.update(t,'failed','construction destroyed'); continue
            error=any(tag==t.worker and a==ability for tag,a,code in w.errors)
            if t.spot is not None:
                if not error and now<t.sent+12: continue
                t.excluded.add(t.spot); t.spot=None; t.worker=None
                self.update(t,'waiting','build rejected/timed out; trying a different location')
                if t.attempts>=5:
                    self.update(t,'failed','five unconfirmed build attempts'); continue
            if now<t.retry_at: continue
            if t.skill=='barracks' and not any(u.done for u in w.my(DEPOT)):
                self.update(t,'waiting','SupplyDepot must finish first'); continue
            if bank<cost:
                self.update(t,'waiting','insufficient minerals'); continue
            workers=[u for u in w.my(SCV) if u.tag not in used and u.tag not in self.protected()]
            if not workers:
                self.update(t,'waiting','workers reserved by safety/construction'); continue
            candidates=self.candidates(w,t)
            if not candidates:
                self.update(t,'failed','no candidate locations'); continue
            worker=min(workers,key=lambda u:u.distance(candidates[0]))
            spot=self.backend.placement(self.index,ability,worker.tag,candidates)
            t.retry_at=now+2
            if spot is None:
                self.update(t,'waiting','placement query rejected all candidates'); continue
            self.issue(t,command(ability,[worker.tag],pos=spot),output); used.add(worker.tag)
            bank-=cost; t.spot=spot; t.worker=worker.tag; t.sent=now; t.attempts+=1
            self.update(t,'issued','raw build order sent; awaiting a new building tag')
        self.economy.refresh(w,self.tasks)
        return output

    def balance_gas(self,w,used,out,t=None):
        if self.gas_workers_per_refinery is None:return
        refs={u.tag:u for u in w.my_units if u.gas_building and u.done and u.vespene_contents>0}
        workers={u.tag:u for u in w.my_units if u.type_id in WORKERS and u.done}
        for u in workers.values():
            targets=[tag for tag in u.order_targets if tag in refs]
            if targets:self.gas_assignments.setdefault(u.tag,targets[0])
        old=set(self.gas_assignments)
        self.gas_assignments={tag:ref for tag,ref in self.gas_assignments.items()
            if tag not in w.dead_units and ref in refs and (w.by_tag(tag) is None or tag in workers)}
        released=(old-set(self.gas_assignments))|self.gas_release_pending
        # Hidden mining workers keep their claims; an observed building or
        # other non-worker must neither hold a slot nor receive a gather order.
        released={tag for tag in released if w.by_tag(tag) is None or tag in workers}
        target=self.gas_workers_per_refinery
        for ref in refs:
            assigned=sorted(tag for tag,r in self.gas_assignments.items() if r==ref)
            for tag in assigned[target:]:self.gas_assignments.pop(tag);released.add(tag)
        self.gas_release_pending=set(released)
        if w.seconds>=self.gas_next_at:
            for tag in released:
                if tag in w.dead_units:self.gas_release_pending.discard(tag);continue
                if tag not in workers:continue
                patches=[m for m in w.units if m.mineral]
                if patches:
                    patch=min(patches,key=workers[tag].distance);self.issue(t,command(3666,[tag],target=patch.tag),out);used.add(tag);self.gas_release_pending.discard(tag)
            busy={tag for task in self.tasks if task.active and task.program for tag in task.program.tags}
            for ref,building in refs.items():
                assigned=[tag for tag,r in self.gas_assignments.items() if r==ref]
                candidates=[u for u in workers.values() if u.tag not in self.gas_assignments and u.tag not in used and u.tag not in busy]
                candidates.sort(key=building.distance)
                for u in candidates[:max(0,target-len(assigned))]:
                    self.gas_assignments[u.tag]=ref;self.issue(t,command(3666,[u.tag],target=ref),out);used.add(u.tag)
                for tag in assigned:
                    if tag in workers and not workers[tag].orders:self.issue(t,command(3666,[tag],target=ref),out);used.add(tag)
            self.gas_next_at=w.seconds+1
        self.gas_worker_tags=set(self.gas_assignments)|self.gas_release_pending;used.update(self.gas_worker_tags)

    def rebalance_minerals(self,w,used,out,t):
        bases=[u for u in w.my_units if u.type_id in TOWNHALLS and u.done and not u.flying]
        workers=[u for u in w.my_units if u.type_id in WORKERS and u.tag not in self.protected() and u.tag not in used]
        destinations=[]
        for base in bases:
            deficit=max(0,base.ideal_harvesters-base.assigned_harvesters)
            if deficit:destinations.append((base,deficit))
        for base,deficit in destinations:
            patches=[m for m in w.units if m.mineral and m.distance(base)<12]
            if not patches:continue
            choices=[u for u in workers if u.distance(base)>14 and any(b.assigned_harvesters>b.ideal_harvesters and u.distance(b)<14 for b in bases)]
            choices.sort(key=base.distance)
            for u in choices[:deficit]:
                patch=min(patches,key=u.distance);self.issue(t,command(3666,[u.tag],target=patch.tag),out);used.add(u.tag);workers.remove(u)

    def close(self):
        for t in self.tasks:
            if t.program and hasattr(t.program,"close"):t.program.close()
