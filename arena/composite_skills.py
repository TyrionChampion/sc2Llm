"""LLM-authored finite workflows. Never evaluates model code or changes game speed."""
from copy import deepcopy
import math
import re
from types import SimpleNamespace
from arena.raw_engine import SCV, MARINE, DEPOT, BARRACKS, CC, MOVE, ATTACK, GATHER, command

class PlanValidationError(ValueError):
    def __init__(self, issues):
        self.issues=issues
        super().__init__('; '.join('%s: %s (received %r)' %
            (i['field'],i['expected'],i['received']) for i in issues))

SELECTORS={'scv':(SCV,), 'marine':(MARINE,), 'siege_tank':(33,32)}
BUILDINGS={'supply_depot':(DEPOT,319,100), 'barracks':(BARRACKS,321,150), 'expand':(CC,318,400)}
TARGETS={'home','rally','enemy_start','nearest_enemy'}
FIELDS={'build':{'structure'},'gather':{'target'},'move':{'target'},
        'attack_move':{'target'},'stim':set(),'siege':set(),'unsiege':set(),
        'wait':{'condition','radius'}}

PROTOCOL = """
You may AUTHOR reusable skills, not just call built-in tasks. Add a skills array:
{"skills":[{"name":"my_depot_cycle","select":{"unit":"scv","count":1},
 "steps":[{"action":"build","structure":"supply_depot"},
          {"action":"gather","target":"nearest_mineral","queue":true}],"timeout_s":180}],
 "tasks":[{"skill":"my_depot_cycle"}],"reason":"..."}.
Each definition selects and reserves the same units for the whole workflow. Names: ASCII
letter then letters/digits/underscore, <=40 chars, cannot override built-ins. Up to 8 new
or revised definitions per reply, 24 stored per player. Redefining an active skill with a
different definition is rejected. skill_library contains stored definitions for reuse.
select.unit: scv / marine / siege_tank; select.count: 1..32 (SCV exactly 1).
steps: 1..12 finite sequential operations. Each step has action and optional timeout_s
(1..180 game seconds, default 60). Definition timeout_s: 1..300, default 180.
Operations and EXACT extra fields:
- build: structure=supply_depot|barracks|expand. SCV only. Wait for completed building.
- gather: target=nearest_mineral. SCV only. Confirm CURRENT gathering order.
- move / attack_move: target=home|rally|enemy_start|nearest_enemy. move confirms arrival
  within 3 units; attack_move confirms matching attack order or arrival, NOT victory.
- stim: marine only, no extra fields. Requires researched Stimpack, health>10; confirm buff.
- siege / unsiege: siege_tank only, no extra fields. Confirm resulting unit mode.
- wait: condition=enemy_near, optional radius 1..30 (default 12); checks visible enemies.
Optional queue=true ONLY for gather immediately after build, or siege immediately after
move. These pairs use native Shift queues. All other steps wait for observed completion
before dispatch. No arbitrary Python, loops, raw API ids, coordinates or tags accepted.
No other step fields. Missing units/tech/resources wait with visible feedback until timeout;
unit loss/action rejection fails visibly. Skills are invoked via tasks, count=1, no args.
Definitions-only replies use tasks=[]. Stored skills do not run until invoked.
Tank production and Stimpack research are NOT automated by current primitives. Do not assume
those prerequisites exist; check world. No automatic combat retreat/defense is added.
"""

def validate_definitions(definitions, library, builtin, active):
    issues=[]; proposed=deepcopy(library)
    def bad(path,value,expected): issues.append(dict(field=path,received=value,expected=expected))
    def fields(obj,allowed,path):
        for k in obj.keys()-allowed: bad(path+'.'+k,obj[k],'remove unsupported field')
    def number(value,lo,hi): return type(value) in (int,float) and math.isfinite(value) and lo<=value<=hi
    if not isinstance(definitions,list) or len(definitions)>8:
        raise PlanValidationError([dict(field='skills',received=definitions,expected='list of 0..8 definitions')])
    seen=set()
    for n,d in enumerate(definitions):
        path='skills[%d]' % n
        if not isinstance(d,dict): bad(path,d,'skill definition object');continue
        fields(d,{'name','select','steps','timeout_s'},path)
        name=d.get('name')
        if not isinstance(name,str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,39}',name):
            bad(path+'.name',name,'ASCII identifier, 1..40 characters');continue
        if name in builtin or name in seen: bad(path+'.name',name,'unique non-built-in name')
        seen.add(name)
        selector=d.get('select');kind=None
        if not isinstance(selector,dict): bad(path+'.select',selector,'object with unit and count')
        else:
            fields(selector,{'unit','count'},path+'.select');kind=selector.get('unit');count=selector.get('count',1)
            if not isinstance(kind,str) or kind not in SELECTORS: bad(path+'.select.unit',kind,'scv, marine, siege_tank')
            if type(count) is not int or not 1<=count<=32 or (kind=='scv' and count!=1):
                bad(path+'.select.count',count,'integer 1..32; scv requires 1')
        if not number(d.get('timeout_s',180),1,300): bad(path+'.timeout_s',d.get('timeout_s'),'number 1..300')
        steps=d.get('steps')
        if not isinstance(steps,list) or not 1<=len(steps)<=12:
            bad(path+'.steps',steps,'list of 1..12 steps');continue
        for j,s in enumerate(steps):
            p=path+'.steps[%d]' % j
            if not isinstance(s,dict): bad(p,s,'step object');continue
            op=s.get('action')
            if not isinstance(op,str) or op not in FIELDS: bad(p+'.action',op,', '.join(FIELDS));continue
            fields(s,{'action','queue','timeout_s'}|FIELDS[op],p)
            if not number(s.get('timeout_s',60),1,180): bad(p+'.timeout_s',s.get('timeout_s'),'number 1..180')
            queued=s.get('queue',False)
            if type(queued) is not bool: bad(p+'.queue',queued,'boolean')
            prev=steps[j-1].get('action') if j and isinstance(steps[j-1],dict) else None
            if queued and (prev,op) not in {('build','gather'),('move','siege')}:
                bad(p+'.queue',queued,'true only for build -> gather or move -> siege pairs')
            expected_kind={'build':'scv','gather':'scv','stim':'marine','siege':'siege_tank','unsiege':'siege_tank'}.get(op)
            if expected_kind and kind!=expected_kind: bad(p+'.action',op,'requires selector '+expected_kind)
            if op=='build' and s.get('structure') not in tuple(BUILDINGS): bad(p+'.structure',s.get('structure'),', '.join(BUILDINGS))
            if op=='gather' and s.get('target')!='nearest_mineral': bad(p+'.target',s.get('target'),'nearest_mineral')
            if op in ('move','attack_move') and s.get('target') not in tuple(TARGETS): bad(p+'.target',s.get('target'),', '.join(sorted(TARGETS)))
            if op=='wait':
                if s.get('condition')!='enemy_near': bad(p+'.condition',s.get('condition'),'enemy_near')
                if not number(s.get('radius',12),1,30): bad(p+'.radius',s.get('radius'),'number 1..30')
        if name in active and name in library and library[name]!=d:
            bad(path+'.name',name,'cannot revise a skill while it is active')
        proposed[name]=deepcopy(d)
    if len(proposed)>24: bad('skills',len(proposed),'at most 24 stored definitions')
    if issues: raise PlanValidationError(issues)
    return proposed

class SkillRun:
    def __init__(self,definition):
        self.definition=deepcopy(definition);self.tags=set();self.index=0
        self.states=[{} for _ in definition['steps']];self.step_started=None;self.retry_at=0
    def summary(self): return dict(step=self.index+1,steps=len(self.states),unit_tags=sorted(self.tags))
    def target(self,label,w,q,units):
        if label=='home': return q.backend.homes[q.index]
        if label=='enemy_start': return q.backend.enemy_start(q.index)
        if label=='nearest_enemy':
            if not w.enemy_units: return None
            return min(w.enemy_units,key=units[0].distance).pos
        home=q.backend.homes[q.index];enemy=q.backend.enemy_start(q.index)
        length=max(1,math.dist(home,enemy))
        return (home[0]+(enemy[0]-home[0])*10/length,home[1]+(enemy[1]-home[1])*10/length)
    def complete(self,step,state,w,units):
        op=step['action']
        if op=='build':
            found=[u for u in w.my(state['type']) if u.tag not in state['baseline'] and u.distance(state['pos'])<2]
            if found: state['building']=found[0].tag
            return bool(found and found[0].done)
        if op=='gather': return all(u.orders and u.orders[0] in (295,GATHER) and u.order_targets and u.order_targets[0]==state['target'] for u in units)
        if op=='move': return all(u.distance(state['pos'])<=3 for u in units)
        if op=='stim': return all(27 in u.buffs for u in units)
        if op in ('siege','unsiege'):
            return all(u.type_id==(32 if op=='siege' else 33) and not any(a in (388,390) for a in u.orders) for u in units)
        if op=='attack_move':
            return all(u.distance(state['pos'])<=3 or (u.orders and u.orders[0] in (23,ATTACK)
                and u.order_positions and u.order_positions[0] is not None
                and math.dist(u.order_positions[0],state['pos'])<.5) for u in units)
        return any(u.distance(e)<=step.get('radius',12) for u in units for e in w.enemy_units)
    def prepare(self,step,w,q,t,units,bank):
        op=step['action'];state={};action=None;cost=0
        if op=='wait': return state,None,0,None
        if op=='build':
            kind,ability,cost=BUILDINGS[step['structure']]
            if bank<cost:return None,None,0,'insufficient minerals: need %d' % cost
            if kind==BARRACKS and not any(u.done for u in w.my(DEPOT)):return None,None,0,'requires completed SupplyDepot'
            proxy=SimpleNamespace(skill=step['structure'],excluded=set())
            points=q.candidates(w,proxy)
            if not points:return None,None,0,'no candidate building positions'
            pos=q.backend.placement(q.index,ability,units[0].tag,points)
            if pos is None:return None,None,0,'placement query rejected all candidates'
            state=dict(type=kind,baseline={u.tag for u in w.my(kind)},pos=pos)
            action=command(ability,sorted(self.tags),pos=pos)
        elif op=='gather':
            patches=[u for u in w.units if u.mineral]
            if not patches:return None,None,0,'no visible minerals; no fallback target'
            target=min(patches,key=units[0].distance).tag;state['target']=target
            action=command(GATHER,sorted(self.tags),target=target)
        elif op in ('move','attack_move'):
            if any(u.type_id==32 for u in units):return None,None,0,'tank is sieged; define an unsiege step first'
            pos=self.target(step['target'],w,q,units)
            if pos is None:return None,None,0,'no visible enemy; no fallback target'
            state['pos']=pos;action=command(MOVE if op=='move' else ATTACK,sorted(self.tags),pos=pos)
        elif op=='stim':
            if 15 not in w.upgrades:return None,None,0,'Stimpack research missing; research is not automated'
            pending=[u for u in units if 27 not in u.buffs]
            if any(u.health<=10 for u in pending):return None,None,0,'Stimpack requires Marine health >10'
            if pending:action=command(3675,[u.tag for u in pending])
        elif op in ('siege','unsiege'):
            pending=[u for u in units if u.type_id!=(32 if op=='siege' else 33)]
            if pending:action=command(388 if op=='siege' else 390,[u.tag for u in pending])
        if action is not None:
            c=action.action_raw.unit_command;c.queue_command=step.get('queue',False)
            state['ability']=c.ability_id
            # Only query special abilities; movement/build/gather have specific prerequisite/placement checks.
            if op in ('stim','siege','unsiege'):
                abilities=q.backend.available_abilities(q.index,list(c.unit_tags))
                alternatives={3675:{3675,380},388:{388},390:{390}}[c.ability_id]
                if any(not (abilities.get(tag,set())&alternatives) for tag in c.unit_tags):
                    return None,None,0,'ability unavailable for selected unit(s): %d' % c.ability_id
        return state,action,cost,None
    def evidence(self,step,state,w,units):
        result=dict(units=[dict(tag=u.tag,type_id=u.type_id,position=u.pos,health=u.health,
            buffs=u.buffs,orders=u.orders,order_targets=u.order_targets,engaged_target=u.engaged_target,order_positions=u.order_positions) for u in units])
        if 'pos' in state:result['target_position']=state['pos']
        if 'target' in state:result['target_tag']=state['target']
        if state.get('building'):
            building=w.by_tag(state['building'])
            result['building']=dict(tag=building.tag,type_id=building.type_id,progress=building.progress,position=building.pos)
        if step['action']=='wait':
            result['nearby_visible_enemies']=[e.tag for e in w.enemy_units if any(u.distance(e)<=step.get('radius',12) for u in units)]
        if step['action']=='attack_move':result['scope']='matching attack order or arrival; not victory'
        return result
    def fail(self,w,q,t,output,reason):
        q.update(t,'failed',reason)
        alive=[tag for tag in sorted(self.tags) if w.by_tag(tag) and w.by_tag(tag).alliance==1]
        if alive and any('ability' in s for s in self.states):
            output.append(command(4,alive))
            q.action_contexts.append(dict(source='composite_skill_cancel',task_id=t.id,skill=t.skill,request_id=t.request_id))
            q.event('skill_cancel_requested',task_id=t.id,skill=t.skill,unit_tags=alive,
                    reason='STOP requested to clear remaining native orders; acceptance is not yet confirmed',request_id=t.request_id)
    def tick(self,w,q,t,used,bank,output):
        now=w.seconds;steps=self.definition['steps']
        if now-t.created>self.definition.get('timeout_s',180):
            self.fail(w,q,t,output,'skill game-time deadline exceeded; last state: '+t.reason);return bank
        if self.step_started is None:self.step_started=now
        if now-self.step_started>steps[self.index].get('timeout_s',60):
            self.fail(w,q,t,output,'step %d timed out; last state: %s' % (self.index+1,t.reason));return bank
        if not self.tags:
            selector=self.definition['select'];count=selector.get('count',1)
            choices=[u for u in w.my_units if u.type_id in SELECTORS[selector['unit']] and u.done and u.tag not in used and u.tag not in q.protected()]
            choices.sort(key=lambda u:(bool(u.orders),u.distance(q.backend.homes[q.index]),u.tag))
            if len(choices)<count:
                q.update(t,'waiting','step 1: need %d unreserved %s; available %d' % (count,selector['unit'],len(choices)));return bank
            self.tags={u.tag for u in choices[:count]}
            q.event('skill_units_reserved',task_id=t.id,skill=t.skill,unit_tags=sorted(self.tags),request_id=t.request_id)
        units=[w.by_tag(tag) for tag in sorted(self.tags)]
        if any(u is None or u.alliance!=1 for u in units):
            self.fail(w,q,t,output,'selected unit lost; workflow will not silently switch units');return bank
        used.update(self.tags)
        for state in self.states:
            if state.get('sent') is not None and not state.get('complete'):
                errors=[e for e in w.errors if e[0] in self.tags and e[1]==state.get('ability')]
                if errors:self.fail(w,q,t,output,'engine rejected workflow action %r; cause unknown, see engine feedback' % errors);return bank
                if state.get('building') and w.by_tag(state['building']) is None:
                    self.fail(w,q,t,output,'observed construction was destroyed');return bank
        i=self.index;step=steps[i];state=self.states[i]
        if 'sent' in state:
            if not self.complete(step,state,w,units):return bank
            state['complete']=True
            q.event('skill_step_completed',task_id=t.id,skill=t.skill,step=i+1,action=step['action'],request_id=t.request_id,
                    evidence=self.evidence(step,state,w,units))
            self.index+=1;self.step_started=now;self.retry_at=now;t.spot=None
            if self.index==len(steps):
                q.update(t,'completed','all %d workflow steps observed complete' % len(steps));return bank
            i=self.index;step=steps[i];state=self.states[i]
            q.update(t,'running','step %d/%d: %s' % (i+1,len(steps),step['action']))
            if 'sent' in state:return bank
        if now<self.retry_at:return bank
        self.retry_at=now+1
        group=[i]
        if i+1<len(steps) and steps[i+1].get('queue',False):group.append(i+1)
        prepared=[];remaining=bank
        try:
            for j in group:
                st,action,cost,error=self.prepare(steps[j],w,q,t,units,remaining)
                if error:
                    q.update(t,'waiting','step %d: %s' % (j+1,error));return bank
                prepared.append((j,st,action));remaining-=cost
        except Exception as e:
            self.fail(w,q,t,output,'skill preparation failed: %s: %s' % (type(e).__name__,e));return bank
        for j,st,action in prepared:
            st['sent']=now;self.states[j]=st
            if steps[j]['action']=='build':t.spot=st['pos']
            if action is not None:
                output.append(action)
                q.action_contexts.append(dict(source='composite_skill',task_id=t.id,skill=t.skill,step=j+1,request_id=t.request_id))
            q.event('skill_step_started',task_id=t.id,skill=t.skill,step=j+1,action=steps[j]['action'],
                    queued=steps[j].get('queue',False),command_sent=action is not None,request_id=t.request_id)
        q.update(t,'running','step %d/%d: %s; awaiting observation' % (i+1,len(steps),step['action']))
        return remaining
