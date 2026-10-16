"""One explicit model-ordered Queen injection; no automatic cycle or larva inference."""
from arena.raw_engine import command

ABILITY=251
INJECT_BUFF=11

def inject_cast_in_range(queen,w,tree):
    """Protect a cast near its target, while allowing explicit travel retargets."""
    for n,ability in enumerate(queen.orders):
        if tree.canonical_ability(ability)!=tree.canonical_ability(ABILITY):continue
        target=w.by_tag(queen.order_targets[n]) if n<len(queen.order_targets) else None
        if target is None or target.alliance!=1:return True  # Unknown prior target is not assumed interruptible.
        reach=tree.abilities[ABILITY].cast_range+queen.radius+target.radius+1
        if queen.distance(target)<=reach:return True
    return False

def specification(tree,race,args):
    if race!='zerg':raise ValueError('inject_larva requires Zerg')
    if set(args)-{'base','timeout_s'}:raise ValueError('inject_larva accepts only base and timeout_s')
    timeout=args.get('timeout_s',90)
    if isinstance(timeout,bool) or not isinstance(timeout,int) or not 1<=timeout<=300:
        raise ValueError('inject_larva timeout_s must be an integer in 1..300')
    base=args.get('base','home')
    if not isinstance(base,str) or base not in ('home','expansion') and not base.startswith('site_'):
        raise ValueError('inject_larva base must be home, expansion or an observed site_N')
    if ABILITY not in tree.abilities or tree.abilities[ABILITY].link_name!='SpawnLarva':
        raise ValueError('native inject ability identity mismatch')
    return dict(kind='inject_larva',base=base,ability=ABILITY,timeout_s=timeout)

def report(w,q):
    protected=q.protected()
    return dict(task=dict(skill='inject_larva',count=1,args=dict(base='home|expansion|observed site_N',timeout_s='optional integer 1..300, default 90')),
        rule='Zerg only. One explicit cast, 25 Queen energy. Free owned Queen, own completed base. Native cast may move Queen to that base; units stay reserved while observed approach progresses. For distant travel explicitly increase timeout_s. Confirm actual energy spending + buff, NOT larvae already born. Existing buff prevents recast. No automatic cycle. Total request deadline defaults to 90 game seconds; missing orders or stalled approach fail earlier.',
        queens=[dict(tag=u.tag,pos=u.pos,energy=round(u.energy,1),allocated=u.tag in protected,
            orders=list(u.orders),order_targets=list(u.order_targets),order_positions=list(u.order_positions))
            for u in w.my(126) if not u.hallucination],
        bases=[dict(tag=u.tag,pos=u.pos,completed=u.done,inject_active=INJECT_BUFF in u.buffs)
            for u in w.my_units if u.type_id in (86,100,101)])

class InjectLarvaRun:
    def __init__(self,spec,args):
        self.spec=spec;self.args=dict(args);self.tags=set();self.pending=None;self.next_at=0
        self.created_at=None;self.last_game_s=None
    def close(self):self.tags.clear()
    def summary(self):
        deadline=self.created_at+self.spec['timeout_s'] if self.created_at is not None else None
        return dict(kind='inject_larva',args=self.args,timeout_s=self.spec['timeout_s'],
            request_created_game_s=self.created_at,deadline_game_s=deadline,
            remaining_game_s=round(max(0,deadline-self.last_game_s),3) if deadline is not None else None,
            unit_tags=sorted(self.tags),pending=self.pending)
    def tick(self,w,q,t,used,bank,out):
        self.created_at=t.created;self.last_game_s=w.seconds
        try:self.advance(w,q,t,used,out)
        except Exception as e:
            reason=type(e).__name__+': '+str(e)
            def observed(tag):
                actor=w.by_tag(tag)
                if actor is None or actor.alliance!=1:return dict(tag=tag,status='not currently observed/owned')
                return dict(tag=tag,status='observed own',type_id=actor.type_id,pos=list(actor.pos),
                    energy=actor.energy,completed=actor.done,orders=list(actor.orders),
                    order_targets=list(actor.order_targets),order_positions=list(actor.order_positions),buffs=list(actor.buffs))
            p=self.pending or {};queen=w.by_tag(p.get('queen'))
            protected=q.protected()
            evidence=dict(game_s=w.seconds,phase='after_dispatch' if self.pending else 'before_dispatch',
                last_wait_reason=t.reason,
                queen=observed(p['queen']) if p.get('queen') else None,
                base=observed(p['base']) if p.get('base') else None,
                own_queens=[dict(observed(u.tag),allocated=u.tag in protected) for u in w.my(126)],
                own_bases=[observed(u.tag) for u in w.my_units if u.type_id in (86,100,101)],
                own_nearby_buildings=[dict(tag=u.tag,type_id=u.type_id,pos=list(u.pos),radius=u.radius)
                    for u in w.my_units if queen is not None and u.is_building and u.distance(queen)<=12],
                observed_dead_tags=list(w.dead_units),
                scope='Current own observations and last waiting condition. Null bound queen/base means no dispatch binding, not proof of base destruction. Nearby buildings are context, not proven blockers.')
            q.event('inject_larva_failed',task_id=t.id,error=reason,operation=self.summary(),observation_evidence=evidence)
            q.update(t,'failed',reason)
        return bank
    def advance(self,w,q,t,used,out):
        if self.pending:
            p=self.pending;queen=w.by_tag(p['queen']);base=w.by_tag(p['base'])
            if any(tag==p['queen'] and q.tech_tree.canonical_ability(a)==q.tech_tree.canonical_ability(ABILITY)
                   for tag,a,_ in w.errors):raise RuntimeError('native inject rejected: '+str(w.errors))
            if queen is None or base is None or queen.alliance!=1 or base.alliance!=1:
                raise RuntimeError('owned queen/base lost before injection confirmation')
            # Travel regenerates energy. Compare consecutive observations, not
            # the send-time balance, or a successful distant cast looks unspent.
            if queen.energy<=p['last_energy']-20:
                p.update(energy_spend_observed=True,energy_before_spend=p['last_energy'],energy_after_spend=queen.energy)
            p['last_energy']=queen.energy
            if p.get('energy_spend_observed') and INJECT_BUFF in base.buffs:
                q.event('inject_larva_confirmed',task_id=t.id,queen_tag=queen.tag,base_tag=base.tag,
                    energy_at_send=p['energy'],energy_before=p['energy_before_spend'],
                    energy_now=p['energy_after_spend'],energy_current=queen.energy,buff_id=INJECT_BUFF,larva_spawn_confirmed=False)
                q.update(t,'completed','observed Queen energy spent and base inject buff; larvae not yet confirmed');return
            native_order=any(q.tech_tree.canonical_ability(a)==q.tech_tree.canonical_ability(ABILITY)
                and n<len(queen.order_targets) and queen.order_targets[n]==base.tag
                for n,a in enumerate(queen.orders))
            p['native_order_observed']=native_order
            if queen.distance(p['last_pos'])>=.25 and (native_order or queen.distance(base)<p['last_distance']-.25):
                p.update(last_pos=queen.pos,last_distance=queen.distance(base),progress_at=w.seconds)
            if w.seconds-t.created>self.spec['timeout_s']:
                raise TimeoutError('inject energy/buff not confirmed before '+str(self.spec['timeout_s'])+'-second request deadline; no recast')
            if w.seconds-p['sent']>8 and not native_order and w.seconds-p['progress_at']>8:
                raise TimeoutError('inject order/approach not observed for 8 game seconds; no recast')
            if w.seconds-p['progress_at']>20:raise TimeoutError('inject approach stalled for 20 game seconds; no recast')
            return
        if w.seconds-t.created>self.spec['timeout_s']:raise TimeoutError('inject request expired; fresh model decision required')
        if w.seconds<self.next_at:return
        self.next_at=w.seconds+.5;home=q.backend.homes[q.index];name=self.spec['base']
        bases=[u for u in w.my_units if u.type_id in (86,100,101) and u.done and not u.hallucination]
        if name=='home':bases=[u for u in bases if u.distance(home)<15]
        elif name=='expansion':bases=[u for u in bases if u.distance(home)>=15]
        else:
            from arena.spatial import resource_sites
            point=resource_sites(w).get(name)
            bases=[u for u in bases if point is not None and u.distance(point)<15]
        if not bases:q.update(t,'waiting','requested own completed base unavailable; no fallback');return
        base=min(bases,key=lambda u:u.distance(home))
        if INJECT_BUFF in base.buffs:
            q.event('inject_larva_already_active',task_id=t.id,base_tag=base.tag,energy_spent=0)
            q.update(t,'completed','observed existing injection buff; no extra cast or larva completion assumed');return
        queens=[u for u in w.my(126) if u.done and not u.hallucination and u.energy>=25
            and u.tag not in used and u.tag not in q.protected()
            and not inject_cast_in_range(u,w,q.tech_tree)]
        if not queens:q.update(t,'waiting','need free own Queen with 25 energy and no active native injection');return
        available=q.backend.available_abilities(q.index,[u.tag for u in queens])
        queens=[u for u in queens if q.tech_tree.ability_available(ABILITY,available.get(u.tag,set()))]
        if not queens:q.update(t,'waiting','inject ability unavailable in native query');return
        queen=min(queens,key=lambda u:u.distance(base))
        if not q.issue(t,command(ABILITY,[queen.tag],target=base.tag),out):return
        self.tags={queen.tag};used.add(queen.tag)
        self.pending=dict(queen=queen.tag,base=base.tag,energy=queen.energy,last_energy=queen.energy,sent=w.seconds,
            last_pos=queen.pos,last_distance=queen.distance(base),progress_at=w.seconds)
        q.event('inject_larva_order_issued',task_id=t.id,queen_tag=queen.tag,base_tag=base.tag,
            ability_id=ABILITY,energy_before=queen.energy,execution='issued; waiting for energy/buff evidence')
        q.update(t,'running','inject order issued; awaiting observed energy and base buff')
