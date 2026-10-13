"""Observed, race-aware macro execution. LLM chooses products/tech/economy intent."""
from copy import deepcopy
from arena.orbital import ABILITIES as ORBITAL_ABILITIES, specification as orbital_specification
import math
from arena.raw_engine import command,GATHER,ATTACK,MOVE
from arena.tech_tree import WORKERS,TOWNHALLS,GAS_BUILDINGS,RACE_INFO,ADDONS

MACROS={'train_worker','train_unit','build_structure','research','morph_unit','build_supply',
        'gather_gas','supply_depot','barracks','expand'} | set(ORBITAL_ABILITIES)

def specification(tree,race,skill,args):
    if not isinstance(args,dict):raise ValueError('macro args must be an object')
    if skill in ORBITAL_ABILITIES:return orbital_specification(tree,race,skill,args)
    allowed={'train_unit':{'unit'},'build_structure':{'structure','base'},'research':{'upgrade'},
             'morph_unit':{'unit','target'},'gather_gas':{'workers_per_refinery'},'expand':{'base'}}.get(skill,set())
    if set(args)-allowed:raise ValueError(skill+' allowed args: '+str(sorted(allowed)))
    if skill=='gather_gas':
        count=args.get('workers_per_refinery')
        if type(count) is not int or not 0<=count<=3:raise ValueError('gather_gas requires workers_per_refinery integer 0..3')
        return dict(kind='gas',per_refinery=count)
    if skill=='research':
        name=args.get('upgrade');e=tree.research.get(name) if isinstance(name,str) else None
        if not e or e['race']!=race:raise ValueError('unsupported '+race+' research: '+str(name))
        return dict(kind='research',entry=e)
    if skill=='morph_unit':
        name=args.get('unit');rs=tree.forms.get(name) if isinstance(name,str) else None
        if not rs or tree.types[rs[0]['target']].race!={'terran':1,'zerg':2,'protoss':3}[race]:raise ValueError('unsupported '+race+' unit form: '+str(name)+'; morph_unit only free forms listed in tech_tree.forms. Paid unit morph uses train_unit; lair/hive/orbital_command use build_structure; hatchery uses expand/build_structure.')
        target=args.get('target','home')
        if target not in ('home','rally','enemy_start','nearest_enemy'):raise ValueError('unknown morph target landmark')
        return dict(kind='form',entry=dict(id=rs[0]['target'],name=name,recipes=rs),target=target)
    if skill=='train_worker':name=tree.names[RACE_INFO[race]['worker']]
    elif skill=='build_supply':name=tree.names[RACE_INFO[race]['supply']]
    elif skill=='expand':name=tree.names[RACE_INFO[race]['townhall']]
    elif skill in ('supply_depot','barracks'):name=skill
    elif skill=='train_unit':name=args.get('unit','marine')
    else:name=args.get('structure')
    e=tree.units.get(name) if isinstance(name,str) else None
    if not e or e['race']!=race:raise ValueError('unsupported '+race+' product: '+str(name))
    if skill=='train_unit' and e['structure']:raise ValueError('use build_structure for '+name)
    if skill=='build_structure' and not e['structure']:raise ValueError('use train_unit for '+name)
    base=args.get('base','expansion' if skill=='expand' else 'home')
    if not isinstance(base,str) or base not in ('home','expansion') and not base.startswith('site_'):raise ValueError('base must be home, expansion, or an observed site_N')
    if skill=='expand' and base=='home':raise ValueError('expand needs expansion or a known site_N, not home')
    return dict(kind='production',entry=e,base=base)

def intent_key(spec):
    def hashable(target):
        # Scan accepts an explicit [x, y]; the dedup key must stay hashable/comparable.
        return tuple(target) if isinstance(target,(list,tuple)) else target
    if spec['kind']=='orbital':return ('orbital',spec['name'],hashable(spec['target']))
    if spec['kind']=='gas':return ('gas',spec['per_refinery'])
    return (spec['kind'],spec['entry']['id'],spec.get('base','home'),spec.get('target','home'))

def mineral_groups(w):
    patches=[u for u in w.units if u.mineral]
    groups=[];remaining=set(u.tag for u in patches);bytag={u.tag:u for u in patches}
    while remaining:
        seed=min(remaining);remaining.remove(seed);group=[bytag[seed]];front=[seed]
        while front:
            tag=front.pop();neighbors=[t for t in remaining if bytag[t].distance(bytag[tag])<8]
            for t in neighbors:remaining.remove(t);front.append(t);group.append(bytag[t])
        if group:groups.append(group)
    groups.sort(key=lambda group:(round(sum(u.x for u in group)/len(group),1),round(sum(u.y for u in group)/len(group),1)))
    return groups

def remember_resource_sites(w,history=()):
    """Keep player-observed site identities across neutral tag changes/depletion."""
    records=[(name,set(points)) for name,points in history]
    for group in mineral_groups(w):
        positions={u.pos for u in group}
        match=next((points for _,points in records if points & positions),None)
        if match is not None:match.update(positions)
        elif len(group)>=4:records.append(('site_'+str(min(u.tag for u in group)),positions))
    return tuple((name,tuple(sorted(points))) for name,points in records)

def expansion_clusters(w):
    history=getattr(w,'resource_site_memory',())
    result=[]
    for group in mineral_groups(w):
        positions={u.pos for u in group}
        name=next((name for name,points in history if positions.intersection(points)),None)
        if name is not None or len(group)>=4:
            result.append((name or 'site_'+str(min(u.tag for u in group)),group))
    return result

def site_summary(w,home):
    result=[]
    for name,group in expansion_clusters(w):
        center=(sum(u.x for u in group)/len(group),sum(u.y for u in group)/len(group))
        bases=[u for u in w.my_units if u.type_id in TOWNHALLS and u.distance(center)<15]
        result.append(dict(id=name,distance_home=round(math.dist(center,home),1),own_base=bool(bases),
            mineral_patches=len(group),nearby_visible_enemies=sum(u.distance(center)<18 for u in w.enemy_units)))
    return result

class MacroRun:
    def __init__(self,spec,args):
        self.spec=deepcopy(spec);self.args=deepcopy(args);self.tags=set();self.pending=[]
        self.issued=0;self.born=set();self.baseline=None;self.next_at=0;self.last_issue=0
        self.gas_before=None;self.gas_tags=set();self.research_unconfirmed_since=None;self.failure=None;self.retries=0;self.relocation=None;self.relocations=0
        self.placement_feedback=None
    def close(self):self.tags.clear()
    def summary(self):
        result=dict(kind='macro',operation=self.spec['kind'],product=self.spec.get('entry',{}).get('name'),
                    args=self.args,unit_tags=sorted(self.tags),issued_quantity=self.issued,observed_completed=len(self.born),relocation=self.relocation)
        if self.placement_feedback:result['placement_feedback']=self.placement_feedback
        return result
    def fail(self,q,t,reason,w=None):
        evidence={}
        if w is not None:
            evidence=dict(game_s=w.seconds,pending_orders=deepcopy(self.pending),
                observed_new_products=[dict(tag=u.tag,position=list(u.pos),progress=u.progress)
                    for u in w.my(self.spec.get('entry',{}).get('id'))
                    if self.spec['kind']!='research' and u.tag not in (self.baseline or set())],
                observed_dead_tags=list(w.dead_units),
                source='current own observation; no unseen spawn or completion inferred')
        q.event('macro_failed',task_id=t.id,skill=t.skill,error=reason,operation=self.summary(),
                observation_evidence=evidence)
        q.update(t,'failed',reason)
    def _landmark(self,w,q,name):
        home=q.backend.homes[q.index]
        if name=='home':return home
        if name=='enemy_start':return q.backend.enemy_start(q.index)
        if name=='rally':
            enemy=q.backend.enemy_start(q.index);d=max(1,math.dist(home,enemy));return tuple(home[i]+(enemy[i]-home[i])*12/d for i in (0,1))
        if not w.enemy_units:raise ValueError('nearest_enemy has no visible target; no fallback')
        return min(w.enemy_units,key=lambda u:u.distance(home)).pos
    def _positions(self,w,q,t,actor,recipe):
        tree=q.backend.tech_tree;product=self.spec['entry']['id'];home=q.backend.homes[q.index]
        if self.spec['kind']=='form':anchors=[self._landmark(w,q,self.spec['target'])]
        elif self.spec.get('base')=='expansion' or self.spec.get('base','').startswith('site_'):
            groups=expansion_clusters(w)
            if self.spec['base'].startswith('site_'):groups=[g for g in groups if g[0]==self.spec['base']]
            if product in TOWNHALLS:
                groups=[g for g in groups if not any(u.type_id in TOWNHALLS and any(u.distance(m)<12 for m in g[1]) for u in w.my_units)]
            elif self.spec['base']=='expansion':
                groups=[g for g in groups if any(u.type_id in TOWNHALLS and u.done and u.distance(home)>15 and any(u.distance(m)<12 for m in g[1]) for u in w.my_units)]
            groups.sort(key=lambda g:min(m.distance(home) for m in g[1]))
            anchors=[(sum(m.x for m in g)/len(g),sum(m.y for m in g)/len(g)) for _,g in groups if not any(any(e.distance(m)<12 for m in g) for e in w.enemy_units)]
        else:
            anchors=[u.pos for u in w.my_units if u.type_id in TOWNHALLS and u.done]
            anchors.sort(key=lambda p:math.dist(p,home))
            if not anchors:anchors=[home]
            if self.spec['entry'].get('race')=='protoss' and product not in (59,60,61):
                anchors=[u.pos for u in w.my_units if u.type_id in (60,136) and u.done]
        result=[]
        for anchor in anchors:
            radii=(0,2,3,4,5,6) if recipe.get('producer')==133 or self.spec['entry'].get('race')=='protoss' and product not in (59,60,61) else ((6,7,8,9,10,11,12) if product in TOWNHALLS else (5,7,9,11,14,17,21))
            for radius in radii:
                for n in range(24):
                    theta=2*math.pi*n/24;size=tree.abilities[recipe['ability']].footprint_radius
                    offset=.5 if round(size*2)%2 else 0
                    point=(math.floor(anchor[0]+radius*math.cos(theta))+offset,math.floor(anchor[1]+radius*math.sin(theta))+offset)
                    if not all(2<=point[k]<q.backend.size[k]-2 for k in (0,1)):continue
                    if any(u.is_building and u.distance(point)<max(2,size+u.radius+.75) for u in w.units):continue
                    if recipe.get('producer')==133:
                        # Placement queries permit colliding warp points. Each pending
                        # warp needs its own footprint, including this task's prior casts.
                        if any(u.distance(point)<size+u.radius+.25 for u in w.units if not u.is_building):continue
                        if any(p.get('warp_in') and p.get('pos') and math.dist(point,p['pos'])<2
                               for p in self.pending):continue
                    if any(u.mineral and u.distance(point)<(6 if product in TOWNHALLS else size+1.5) for u in w.units):continue
                    if any(other is not t and other.active and other.spot and math.dist(point,other.spot)<6 for other in q.tasks):continue
                    if point in t.excluded:continue
                    if product==60 and any(u.type_id==60 and u.distance(point)<7 for u in w.my_units):continue
                    if product in (21,27,28) and any(u.is_building and u.distance((point[0]+2.5,point[1]-.5))<u.radius+1.8 for u in w.units):continue
                    if point not in result:result.append(point)
        if product in TOWNHALLS:
            minerals=[u for u in w.units if u.mineral]
            result.sort(key=lambda p:sum(sorted(u.distance(p) for u in minerals)[:8]))
        return result[:512]
    def _gas(self,w,q,t,used,bank,out):
        desired=self.spec['per_refinery'];q.gas_workers_per_refinery=desired
        refineries=[u for u in w.my_units if u.gas_building and u.done and u.vespene_contents>0]
        if desired and not refineries:q.update(t,'waiting','need completed gas building on a geyser');return bank
        q.balance_gas(w,used,out,t)
        if self.gas_before is None:self.gas_before=w.vespene
        if desired==0 and not q.gas_worker_tags and all(u.assigned_harvesters==0 for u in refineries):
            q.update(t,'completed','gas allocation disabled; workers released to mineral mining')
        elif refineries and all(u.assigned_harvesters>=desired for u in refineries):
            q.event('gas_allocation_confirmed',task_id=t.id,workers_per_refinery=desired,vespene=w.vespene,resource_increased=w.vespene>self.gas_before)
            q.update(t,'completed','observed gas workers at requested saturation; allocation will be maintained')
        else:q.update(t,'running','assigning gas workers; awaiting observed refinery saturation')
        return bank
    def tick(self,w,q,t,used,bank,out):
        try:return self._tick(w,q,t,used,bank,out)
        except Exception as e:self.fail(q,t,type(e).__name__+': '+str(e),w);return bank
    def _tick(self,w,q,t,used,bank,out):
        if w.seconds-t.created>900:raise TimeoutError('macro 900 game-second deadline exceeded')
        if self.spec['kind']=='gas':return self._gas(w,q,t,used,bank,out)
        tree=q.backend.tech_tree;entry=self.spec['entry'];kind=self.spec['kind'];product=entry['id']
        if self.relocation:
            move=self.relocation;actor=w.by_tag(move['tag'])
            if any(tag==move['tag'] for tag,_,_ in w.errors):raise RuntimeError('engine rejected addon relocation: '+str(w.errors))
            if actor is None:raise RuntimeError('production building lost during addon relocation')
            if w.seconds-move['started']>45:raise TimeoutError('addon relocation exceeded 45 game seconds')
            self.tags={actor.tag};used.add(actor.tag)
            if not actor.flying:
                if move['phase']=='landing':
                    q.event('addon_relocation_confirmed',task_id=t.id,producer_tag=actor.tag,pos=actor.pos)
                    self.relocation=None;self.tags.clear();self.next_at=w.seconds+.2
                else:q.update(t,'running','waiting for observed lift-off to clear addon footprint')
                return bank
            if w.seconds<self.next_at:return bank
            self.next_at=w.seconds+1
            ability=move['land'];available=q.backend.available_abilities(q.index,[actor.tag])
            if not tree.ability_available(ability,available.get(actor.tag,set())):return bank
            candidates=self._positions(w,q,t,actor,{'producer':actor.type_id,'ability':ability})
            pos=q.backend.placement_with_addon(q.index,ability,actor.tag,candidates)
            if pos is None:q.update(t,'waiting','relocating addon producer: no legal combined landing/2x2 addon site');return bank
            if move['phase']=='landing' and actor.orders:return bank
            q.issue(t,command(ability,[actor.tag],pos=pos),out);move['phase']='landing'
            q.event('addon_relocation_land',task_id=t.id,producer_tag=actor.tag,pos=pos)
            q.update(t,'running','landing at engine-checked production and addon footprints');return bank
        if product in (58,85) and kind=='production':
            actors=[u for u in w.my(50 if product==58 else 79) if u.done and not u.hallucination]
            eligible=[u for u in actors if product==58 or not u.orders and not u.buffs]
            abilities=q.backend.available_abilities(q.index,[u.tag for u in eligible],ignore_resources=True) if eligible else {}
            stocked=[u for u in eligible if (tree.ability_available(1622,abilities.get(u.tag,set())) if product==58 else
                tree.ability_available(16,abilities.get(u.tag,set())) and not tree.ability_available(1042,abilities.get(u.tag,set())))]
            if stocked:
                q.event('ammo_stock_confirmed',task_id=t.id,product=entry['name'],producer_tags=[u.tag for u in stocked],
                    evidence='Ghost nuclear strike available' if product==58 else 'idle non-disabled real carrier has no build-interceptor capacity with resource checks disabled',
                    exact_quantity_observable=False,hangar_capacity_inference=product==85)
                q.update(t,'completed','nuclear strike capability confirms armed ammo' if product==58 else 'full carrier hangar inferred from capacity query; exact ammo count not exposed');return bank
        if kind=='research' and product in w.upgrades:
            q.event('research_confirmed',task_id=t.id,upgrade=entry['name'],upgrade_id=product)
            q.update(t,'completed','research present in observed upgrade_ids');return bank
        if kind!='research':
            if self.baseline is None:self.baseline={u.tag for u in w.my(product)}
            fresh=[u for u in w.my(product) if u.tag not in self.baseline]
            self.born.update(u.tag for u in fresh if u.done)
            if len(self.born)>=t.count:
                if product in GAS_BUILDINGS and self.pending:
                    for pending in self.pending:
                        for tag in pending['tags']:
                            if pending.get('consumes_actor'):continue
                            q.gas_assignments.setdefault(tag,next(iter(self.born)))
                q.event('production_confirmed',task_id=t.id,product=entry['name'],unit_tags=sorted(self.born),requested=t.count,observed=len(self.born))
                q.update(t,'completed','observed %d completed %s'%(len(self.born),entry['name']));return bank
        errors=[(tag,ability,code) for tag,ability,code in w.errors if any(tag in p['tags'] and tree.canonical_ability(ability)==tree.canonical_ability(p['ability']) for p in self.pending)]
        if errors:
            from s2clientprotocol import error_pb2
            affected=[p for p in self.pending if any(tag in p['tags'] and tree.canonical_ability(a)==tree.canonical_ability(p['ability']) for tag,a,_ in errors)]
            if len(affected)==1 and not affected[0]['seen'] and self.retries<5 and all(code in {9,10,13,40,79,*range(41,52)} for _,_,code in errors):
                p=affected[0];self.pending.remove(p);self.issued-=p.get('quantity',1);self.tags={tag for item in self.pending for tag in item['tags']};self.retries+=1
                if any(41<=code<=51 for _,_,code in errors) and p.get('pos'):t.excluded.add(p['pos'])
                t.spot=None;self.next_at=w.seconds+1
                q.event('macro_retry',task_id=t.id,attempt=self.retries,errors=[{'unit_tag':tag,'ability_id':a,'result':error_pb2.ActionResult.Name(code)} for tag,a,code in errors],handling='Rejected order not counted; wait/reselect legal placement. No completion assumed.')
                q.update(t,'waiting','engine rejection recorded; bounded retry %d/5'%self.retries);return bank
            raise RuntimeError('engine rejected macro action: '+str([(tag,a,error_pb2.ActionResult.Name(code)) for tag,a,code in errors]))
        # Confirm order appearance. Release each producer once its accepted order finishes.
        keep=[]
        for p in self.pending:
            actors=[w.by_tag(tag) for tag in p['tags']]
            order=any(a and any(tree.canonical_ability(x)==tree.canonical_ability(p['ability']) for x in a.orders) for a in actors)
            transformed=any(a and a.type_id==product for a in actors) if kind!='research' else False
            building=next((u for u in w.my(product) if p.get('pos') and u.distance(p['pos'])<2 and u.tag not in (self.baseline or set())),None) if kind!='research' else None
            if order or transformed or building:p['seen']=True
            grace=p.get('ack_timeout_s',8)
            if not p['seen'] and w.seconds-p['sent']>grace:
                raise RuntimeError('macro order was not observed within %s seconds; no success assumed'%grace)
            if p['seen'] and not order and not (building and not building.done and p.get('scv_build')):
                if not transformed and any(a is None for a in actors) and not p.get('consumes_actor'):raise RuntimeError('macro producer died before observed completion')
                if kind=='research':self.research_unconfirmed_since=w.seconds
                continue
            keep.append(p)
        self.pending=keep;self.tags={tag for p in keep for tag in p['tags']}
        # A native SCV Build order targets a POSITION, usually target_unit_tag=0.
        # Keep one owner until confirmed death. Interrupted living owners resume themselves.
        if kind=='production' and entry.get('structure') and entry['race']=='terran' and t.spot and any(r['producer']==45 for r in entry['recipes']):
            building=next((u for u in w.my(product) if u.distance(t.spot)<2 and not u.done),None)
            pending=next((p for p in self.pending if p.get('scv_build')),None)
            if building and pending:
                owner_tag=pending['tags'][0];owner=w.by_tag(owner_tag)
                if building.progress>pending.get('construction_progress',-1)+.00001:
                    pending['construction_progress']=building.progress
                    pending['progress_at']=w.seconds
                stalled=w.seconds-pending.get('progress_at',w.seconds)
                build_order=owner and any(
                    (tree.canonical_ability(a)==tree.canonical_ability(pending['ability']) and
                     n<len(owner.order_positions) and owner.order_positions[n] is not None and
                     math.dist(owner.order_positions[n],t.spot)<2) or
                    (n<len(owner.order_targets) and owner.order_targets[n]==building.tag)
                    for n,a in enumerate(owner.orders))
                if owner_tag in w.dead_units:
                    pending['owner_dead']=True
                if pending.get('owner_dead'):
                    gas={u.tag for u in w.my_units if u.gas_building}
                    workers=[u for u in w.my(45) if u.tag not in used and u.tag not in q.protected()
                             and not any(tag in gas for tag in u.order_targets)
                             and (not u.orders or all(a in (295,296,298,299,3666,3667) for a in u.orders))]
                    actor=min(workers,key=lambda u:u.distance(building)) if workers else None
                    reason='previous builder death confirmed'
                elif owner is None:
                    q.update(t,'waiting','construction owner temporarily unobserved; no unconfirmed replacement')
                    return bank
                elif stalled<(6 if build_order else 1.5):
                    q.update(t,'running','single SCV construction; owner/order/progress observed')
                    return bank
                else:
                    actor=owner;reason='same builder interrupted or stalled'
                if actor is not None and w.seconds>=self.next_at:
                    q.issue(t,command(1,[actor.tag],target=building.tag),out);used.add(actor.tag)
                    pending['tags']=[actor.tag];pending.pop('owner_dead',None)
                    pending['progress_at']=w.seconds
                    self.tags={tag for p in self.pending for tag in p['tags']}
                    q.event('construction_owner_resume',task_id=t.id,building_tag=building.tag,
                            previous_owner=owner_tag,owner=actor.tag,reason=reason)
                    self.next_at=w.seconds+3
                q.update(t,'running' if actor else 'waiting','single-owner construction resume: '+reason)
                return bank
        if kind=='research' and self.issued and not self.pending and self.research_unconfirmed_since is not None and w.seconds-self.research_unconfirmed_since>8:
            raise RuntimeError('research order disappeared for over 8 game seconds but upgrade is absent; cancelled/interrupted or confirmation missing, not completed')
        if self.issued>=t.count:
            q.update(t,'running','ammo order finished/queued; awaiting an owned Ghost with nuclear strike capability' if product==58 and kind=='production' else 'awaiting observed '+('upgrade' if kind=='research' else 'product')+' completion');return bank
        if w.seconds<self.next_at:return bank
        self.next_at=w.seconds+.5
        recipes=entry['recipes'];hints=[]
        for r in recipes:
            producer=r['producer'];actor_types=r.get('actor_types',[producer]);required=r.get('required_upgrade')
            if required and required not in w.upgrades:hints.append('need upgrade '+tree.upgrade_names[required]);continue
            actors=[u for u in w.my_units if u.type_id in actor_types and u.done and u.tag not in used and u.tag not in (q.protected()-self.tags)]
            actors=[u for u in actors if u.type_id in WORKERS or not u.orders]
            if producer in WORKERS:
                gas_tags={u.tag for u in w.my_units if u.gas_building}
                actors=[u for u in actors if not gas_tags.intersection(u.order_targets)]
            if r.get('addon'):actors=[u for u in actors if not u.addon_tag]
            if len(actors)<r.get('actor_count',1):hints.append('need idle '+tree.names[producer]);continue
            minerals=entry.get('minerals',r.get('minerals',0));gas=entry.get('gas',r.get('gas',0));supply=r.get('supply',0)
            if bank<minerals or q.gas_bank<gas or (supply>0 and q.supply_bank<supply):
                deficits=[('%s required %g, available %g'%(name,need,have))
                    for name,need,have in (('minerals',minerals,bank),('gas',gas,q.gas_bank),
                                          ('free supply',supply,q.supply_bank)) if need>0 and have<need]
                hints.append('resource shortage: '+'; '.join(deficits));continue
            available=q.backend.available_abilities(q.index,[u.tag for u in actors])
            legal=[u for u in actors if tree.ability_available(r['ability'],available.get(u.tag,set()))]
            if kind=='production' and not entry.get('structure') and not r.get('requires_techlab'):
                # Assign interchangeable production away from a ready specialist slot.
                # Only already-requested, currently feasible products reserve that slot.
                held={}
                for actor in legal:
                    addon=w.by_tag(actor.addon_tag) if actor.addon_tag else None
                    for other in q.tasks:
                        program=other.program
                        if other is t or not other.active or not isinstance(program,MacroRun):continue
                        spec=program.spec;special=spec.get('entry',{})
                        if spec.get('kind')!='production' or program.issued>=other.count:continue
                        for recipe in special.get('recipes',[]):
                            if recipe['producer']!=producer:continue
                            needs_lab=(recipe.get('requires_techlab') and addon and addon.type_id in (37,39,41))
                            needs_addon=(special.get('structure') and recipe.get('addon') and not actor.addon_tag)
                            if not (needs_lab or needs_addon):continue
                            need_m=special.get('minerals',recipe.get('minerals',0))
                            need_g=special.get('gas',recipe.get('gas',0));need_s=recipe.get('supply',0)
                            if (bank>=minerals+need_m and q.gas_bank>=gas+need_g
                                    and q.supply_bank>=supply+need_s
                                    and tree.ability_available(recipe['ability'],available.get(actor.tag,set()))):
                                held[actor.tag]=special['name']
                legal=[actor for actor in legal if actor.tag not in held]
                legal.sort(key=lambda actor:bool(actor.addon_tag and w.by_tag(actor.addon_tag)
                    and w.by_tag(actor.addon_tag).type_id in (37,39,41)))
                if held and len(legal)<r.get('actor_count',1):
                    hints.append('producer capacity reserved for existing '+', '.join(sorted(set(held.values())))+' request')
                    continue
            if product==141 and r.get('actor_count')==2:
                # Real native discriminator: per-unit queries omit the multi-selection merge,
                # while a single raw order with both templars successfully creates an Archon.
                legal=[u for u in actors if not u.hallucination]

            if len(legal)<r.get('actor_count',1):
                req=r.get('required_building');hints.append('engine ability unavailable for '+tree.names[producer]+('; prerequisite '+tree.names[req] if req else '')+('; attached tech lab needed' if r.get('requires_techlab') else '')+'; check power, cooldown, tech and placement');continue
            chosen=legal[:r.get('actor_count',1)];actor=chosen[0];pos=None;target=None
            if kind=='production' and product in GAS_BUILDINGS:
                base=self.spec.get('base','home');home=q.backend.homes[q.index]
                base_points=[u.pos for u in w.my_units if u.type_id in TOWNHALLS and u.done]
                if base=='home':base_points=[p for p in base_points if math.dist(p,home)<15]
                elif base=='expansion':base_points=[p for p in base_points if math.dist(p,home)>15]
                else:base_points=[(sum(m.x for m in g)/len(g),sum(m.y for m in g)/len(g)) for n,g in expansion_clusters(w) if n==base]
                geysers=[u for u in w.units if u.geyser and not any(b.gas_building and b.distance(u)<1 for b in w.my_units)
                    and any(u.distance(p)<15 for p in base_points)
                    and not any(other is not t and other.active and other.spot and math.dist(other.spot,u.pos)<2 for other in q.tasks)]
                if not geysers:hints.append('no unoccupied geyser near owned base; expand or scout');continue
                geyser=min(geysers,key=lambda u:u.distance(q.backend.homes[q.index]));target=geyser.tag;pos=geyser.pos
                chosen=[min(legal,key=lambda u:u.distance(geyser))];actor=chosen[0]
            elif r.get('addon'):
                clear=[u for u in legal if q.backend.placement(q.index,319,0,[(u.x+2.5,u.y-.5)]) is not None]
                if not clear:
                    if self.relocations>=3:raise RuntimeError('addon footprint remained blocked after three visible relocations')
                    actor=legal[0];lift,land={21:(452,554),27:(485,520),28:(518,522)}[actor.type_id]
                    if not tree.ability_available(lift,available.get(actor.tag,set())):
                        hints.append('addon footprint blocked and producer cannot currently lift');continue
                    q.issue(t,command(lift,[actor.tag]),out);used.add(actor.tag);self.tags={actor.tag};self.relocations+=1
                    self.relocation={'tag':actor.tag,'land':land,'phase':'lifting','started':w.seconds};self.next_at=w.seconds+.5
                    q.event('addon_relocation_started',task_id=t.id,producer_tag=actor.tag,attempt=self.relocations,reason='engine rejected 2x2 addon footprint')
                    q.update(t,'running','lifting production building to clear a blocked addon footprint');return bank
                actor=clear[0];chosen=[actor];pos=(actor.x+2.5,actor.y-.5)
            elif r.get('placement'):
                candidates=self._positions(w,q,t,actor,r)
                if not candidates:hints.append('no candidate sites; need vision, power/creep or another expansion');continue
                pos=(q.backend.placement_with_addon(q.index,r['ability'],actor.tag,candidates) if product in (21,27,28) else q.backend.placement(q.index,r['ability'],actor.tag,candidates))
                if pos is None:
                    evidence=getattr(q.backend,'placement_evidence',None)
                    self.placement_feedback=(evidence(q.index,r['ability'])
                        if callable(evidence) and product not in (21,27,28) else None)
                    if self.placement_feedback and self.placement_feedback.get('failure_counts'):
                        hints.append('engine rejected placement: '+', '.join(self.placement_feedback['failure_counts']))
                    else:hints.append('engine rejected placement candidates; check creep/power/terrain/visibility')
                    continue
                self.placement_feedback=None
                if producer in WORKERS:chosen=[min(legal,key=lambda u:u.distance(pos))];actor=chosen[0]
            if r.get('actor_count',1)==2:
                q.event('multi_actor_ability_check',task_id=t.id,product=entry['name'],method='eligible owned pair; individual query cannot represent merge; raw engine result and new unit will confirm')
                chosen=sorted(legal,key=lambda u:u.distance(actor))[:2]
            tags=[u.tag for u in chosen]
            action=command(r['ability'],tags,pos=None if target or r.get('addon') else pos,target=target)
            q.issue(t,action,out);used.update(tags);self.tags.update(tags)
            amount=r.get('quantity',1);self.issued+=amount;self.last_issue=w.seconds
            self.pending.append(dict(tags=tags,ability=r['ability'],sent=w.seconds,seen=False,pos=pos,quantity=amount,
                # A slow-power warp may take >8s and expose no order on the gate.
                # Retain the reservation until a real spawned unit/order is observed.
                ack_timeout_s=30 if producer==133 else 8,
                warp_in=producer==133,
                consumes_actor=r.get('consumes_actor',False),scv_build=producer==45 and entry.get('structure',False)))
            bank-=minerals;q.gas_bank-=gas;q.supply_bank-=supply;t.spot=pos;t.sent=w.seconds
            q.event('macro_order_issued',task_id=t.id,skill=t.skill,product=entry['name'],producer_tags=tags,
                ability_id=r['ability'],target_pos=pos,target_tag=target,minerals=minerals,gas=gas,supply=supply,quantity=amount,
                execution='issued; completion awaits observation')
            addon=w.by_tag(actor.addon_tag) if actor.addon_tag else None
            if kind=='production' and not entry.get('structure') and addon and addon.type_id in (38,40,42) and self.issued<t.count and bank>=minerals and q.gas_bank>=gas and (supply<=0 or q.supply_bank>=supply):
                q.issue(t,command(r['ability'],tags,queued=True),out)
                self.issued+=amount;bank-=minerals;q.gas_bank-=gas;q.supply_bank-=supply
                self.pending.append(dict(tags=tags,ability=r['ability'],sent=w.seconds,seen=False,consumes_actor=False,scv_build=False))
                q.event('reactor_second_order_issued',task_id=t.id,product=entry['name'],producer_tags=tags,execution='queued; completion awaits observation')
            q.update(t,'running','order issued for '+entry['name']+'; awaiting observed completion')
            return bank
        q.update(t,'waiting',' | '.join(dict.fromkeys(hints))[:1400] or 'no legal producer recipe')
        return bank
