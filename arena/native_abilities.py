"""Explicit one-shot native orders, using own observations and live ability queries."""
import math

from arena.raw_engine import command


def forbidden_abilities(tree):
    return tree.research_abilities | {r['ability'] for entry in tree.units.values()
        for r in entry['recipes'] if r.get('minerals',0) or r.get('gas',0) or r.get('supply',0)}


def permitted(tree, ability, blocked=None):
    if blocked is None:blocked={tree.canonical_ability(a) for a in forbidden_abilities(tree)}
    name=tree.ability_names.get(ability,'')
    # Native production cancellation needs to close the corresponding macro,
    # track refunds and acknowledge disappearance. Do not expose it as an
    # ordinary spell that would let the still-active macro buy the product again.
    if (name in ('cancel','cancel_last','cancel_slot','cancel_build_in_progress','morph_cancel')
            or name.startswith(('cancel_queue','cancel_slot','cancel_hangar','cancel_morph'))
            or name in ('cancel_barracks_add_on','cancel_factory_add_on','cancel_starport_add_on','cancel_nuke')):
        return False
    return (ability in tree.abilities and tree.abilities[ability].available
            and tree.abilities[ability].target in (1,2,3,4,5)
            and tree.canonical_ability(ability) not in blocked)


def validate_args(tree, args, size):
    if tree is None:raise ValueError('use_ability requires the native engine catalogue')
    if set(args)-{'caster','ability','target','timeout_s'} or not {'caster','ability','target'}<=set(args):
        raise ValueError('use_ability requires caster, ability, target; optional timeout_s')
    if type(args['caster']) is not int or args['caster']<=0:raise ValueError('caster must be an owned unit tag')
    if not isinstance(args['ability'],str):raise ValueError('ability requires an exact native ability name')
    ability=tree.by_ability_name.get(args['ability'])
    if ability is None or not permitted(tree,ability):
        raise ValueError('unknown native ability or purchase/research; use the corresponding macro')
    target=args['target'];kind=tree.abilities[ability].target
    if type(target) is int:
        if target<=0 or kind not in (3,4):raise ValueError('ability does not accept a unit tag')
    elif isinstance(target,list):
        if (kind not in (2,4,5) or len(target)!=2
                or any(type(x) not in (int,float) or not math.isfinite(x) for x in target)
                or not all(0<=target[i]<size[i] for i in (0,1))):
            raise ValueError('ability requires a supported finite point inside the map')
    elif target is not None or kind not in (1,5):
        raise ValueError('target must match the native ability: null, unit tag or [x,y]')
    timeout=args.get('timeout_s',30)
    if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 1<=timeout<=120:
        raise ValueError('timeout_s requires 1..120 game seconds')
    return dict(args,timeout_s=timeout)


def report(w,q):
    tree=q.tech_tree
    geometry=dict(size=list(q.backend.size),pathability_available=False)
    grid=q._rally_grid()
    if grid is not None:
        geometry.update(pathability_available=True,cell_size=8,origin=[0,0],
            walkable_cells=[''.join('1' if grid.pathing[y:y+8,x:x+8].any() else '0'
                for x in range(0,grid.w,8)) for y in range(0,grid.h,8)],
            meaning='Row=floor(y/8), column=floor(x/8); 0=no static walkable ground in cell, 1=some. '
                    'Coarse terrain only; not unit occupancy or confirmed dynamic reachability. Final ground destination uses native path queries.')
    blocked={tree.canonical_ability(a) for a in forbidden_abilities(tree)}
    actors=[u for u in w.my_units if u.done and not u.hallucination]
    query_error=None
    try:available=q.backend.available_abilities(q.index,[u.tag for u in actors]) if actors else {}
    except (RuntimeError,TimeoutError,ConnectionError) as error:
        available={};query_error=type(error).__name__
    catalogue={};rows=[];protected=q.protected()
    for u in actors:
        # Cargo-dependent commands (e.g. unload) may be absent from the static
        # reference. A live native query is authoritative for this owned actor.
        candidates=tree.tactical_abilities(u.type_id)|available.get(u.tag,set())
        candidates={a for a in candidates if permitted(tree,a,blocked)}
        for a in candidates:
            catalogue[str(a)]=dict(name=tree.ability_names[a],target={1:'none',2:'point',3:'unit',
                4:'point_or_unit',5:'point_or_none'}[tree.abilities[a].target])
        ready=(sorted(a for a in candidates if tree.ability_available(a,available.get(u.tag,set())))
               if query_error is None else None)
        rows.append([u.tag,tree.names.get(u.type_id,str(u.type_id)),list(u.pos),round(u.energy,1),
                     sorted(u.buffs),list(u.passengers),u.tag in protected,ready])
    return dict(enabled=True,availability_observed=query_error is None,query_error_type=query_error,
        task='use_ability args={caster:own_tag,ability:exact_name,target:null|unit_tag|[x,y],timeout_s:1..120}; outer count=1',
        rule='One order, no recast. Ready is native availability, not target validity/success; null=unknown. '
             'Release allocated casters, except Nexus chrono with production. Purchases/research and native '
             'build/queue cancel use dedicated tools. Ack is not full effect. Unit targets must be observed.',
        map=geometry,catalogue=catalogue,actors=dict(columns=['tag','unit','pos','energy','buffs','passengers','allocated','ready_ability_ids'],rows=rows),
        neutral_target_scope='Neutral tags within 20 of owned actors; use expansion_sites for remembered resource geography.',
        targets=dict(columns=['tag','alliance','unit','pos'],rows=[[u.tag,u.alliance,
            tree.names.get(u.type_id,str(u.type_id)),list(u.pos)] for u in w.units
            if u.alliance==4 or u.alliance==1 and not u.done
            or u.alliance==3 and any(u.distance(a)<=20 for a in actors)]))


class NativeAbilityRun:
    def __init__(self,args):
        self.args=dict(args);self.tags=set();self.pending=None;self.next_at=0
    def close(self):self.tags.clear()
    def summary(self):return dict(kind='native_ability',args=self.args,unit_tags=sorted(self.tags),pending=self.pending)
    def tick(self,w,q,t,used,bank,out):
        try:self.advance(w,q,t,used,out)
        except Exception as error:
            q.event('native_ability_failed',task_id=t.id,error=str(error),operation=self.summary())
            q.update(t,'failed',str(error))
        return bank
    def advance(self,w,q,t,used,out):
        tree=q.tech_tree;ability=tree.by_ability_name[self.args['ability']]
        caster=w.by_tag(self.args['caster'])
        if caster is None or caster.alliance!=1 or caster.hallucination:
            raise ValueError('caster no longer owned/alive; no fallback caster')
        if any(tag==caster.tag and tree.canonical_ability(a)==tree.canonical_ability(ability)
               for tag,a,_ in w.errors):raise RuntimeError('native engine rejected ability: '+str(w.errors))
        target=self.args['target'];unit_target=w.by_tag(target) if type(target) is int else None
        if self.pending:
            p=self.pending
            evidence=[]
            def matches_target(n):
                if type(target) is int:
                    return n<len(caster.order_targets) and caster.order_targets[n]==target
                if isinstance(target,list):
                    return (n<len(caster.order_positions) and caster.order_positions[n] is not None
                            and math.dist(caster.order_positions[n],target)<=1/64)
                return True
            if any(tree.canonical_ability(a)==tree.canonical_ability(ability) and matches_target(n)
                    for n,a in enumerate(caster.orders)):
                evidence.append('native_order_observed')
            if caster.energy<p['last_energy']-1:evidence.append('energy_spend_observed')
            p['last_energy']=caster.energy
            if set(caster.buffs)!=set(p['buffs']):evidence.append('caster_buffs_changed')
            if caster.passengers!=tuple(p['passengers']):evidence.append('cargo_changed')
            if caster.type_id!=p['type_id']:evidence.append('caster_form_changed')
            if unit_target and set(unit_target.buffs)!=set(p['target_buffs']):evidence.append('target_buffs_changed')
            if not evidence and w.game_loop>p['loop']:
                available=q.backend.available_abilities(q.index,[caster.tag])
                if not tree.ability_available(ability,available.get(caster.tag,set())):
                    evidence.append('native_availability_changed')
            if evidence:
                q.event('native_ability_acknowledged',task_id=t.id,caster=caster.tag,ability=self.args['ability'],
                    evidence=evidence,effect_confirmed=False,meaning='Observed reaction/order only; full tactical effect requires its own observation')
                q.update(t,'completed','native acknowledgement observed; full tactical effect not inferred');return
            if w.seconds-t.created>=self.args['timeout_s']:raise TimeoutError('native ability not acknowledged before deadline; no recast')
            return
        if w.seconds-t.created>=self.args['timeout_s']:raise TimeoutError('native ability request expired before dispatch')
        if w.seconds<self.next_at:return
        self.next_at=w.seconds+.5
        # Chrono boost does not replace the Nexus production order. Other
        # commands respect existing squad/worker ownership and per-frame orders.
        noninterrupting=ability==3755 and caster.type_id==59
        if not noninterrupting and (caster.tag in used or caster.tag in q.protected()):
            q.update(t,'waiting','caster allocated; cancel/release its mission first');return
        if type(target) is int and (unit_target is None or unit_target.alliance not in (1,3,4)):
            raise ValueError('unit target no longer currently observed; no remembered/hidden target')
        if tree.canonical_ability(ability)==tree.canonical_ability(251):
            from arena.queen import inject_cast_in_range
            if inject_cast_in_range(caster,w,tree):
                q.update(t,'waiting','caster still has an in-range native injection; await its observed end before another cast');return
        available=q.backend.available_abilities(q.index,[caster.tag])
        if not tree.ability_available(ability,available.get(caster.tag,set())):
            q.update(t,'waiting','native ability unavailable: check energy, cooldown, tech and caster');return
        if not q.issue(t,command(ability,[caster.tag],pos=target if isinstance(target,list) else None,
                               target=target if type(target) is int else None),out):return
        self.tags={caster.tag};used.add(caster.tag)
        self.pending=dict(loop=w.game_loop,energy=caster.energy,last_energy=caster.energy,
            buffs=list(caster.buffs),passengers=list(caster.passengers),type_id=caster.type_id,
            target_buffs=list(unit_target.buffs) if unit_target else [])
        q.event('native_ability_order_issued',task_id=t.id,caster=caster.tag,ability=self.args['ability'],
                ability_id=ability,target=target,effect_confirmed=False)
        q.update(t,'running','one native ability order issued; awaiting acknowledgement, no automatic recast')
