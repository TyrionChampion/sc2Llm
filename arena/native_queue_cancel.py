"""Stop a linked macro goal and cancel its observed building-owned native queues."""
from copy import deepcopy
from arena.macro import MacroRun
from arena.raw_engine import command


def supported(task,tree):
    if tree is None or not isinstance(task.program,MacroRun):return False
    spec=task.program.spec
    buildings={e['id'] for e in tree.units.values() if e.get('structure')}
    return (spec['kind'] in ('production','research') and not spec['entry'].get('structure')
        and spec['entry']['name'] not in ('nuke','interceptor') and any(
        r['producer'] in buildings for r in spec['entry']['recipes'])
        )


def capture(task,tree):
    run=task.program;entry=run.spec['entry']
    buildings={e['id'] for e in tree.units.values() if e.get('structure')}
    return dict(task_id=task.id,product=entry['name'],product_id=entry['id'],
        kind=run.spec['kind'],structure=bool(entry.get('structure')),
        producers=[r['producer'] for r in entry['recipes'] if r['producer'] in buildings],
        original_state=task.status,issued=run.issued,baseline=list(run.baseline or ()),
        pending=deepcopy(run.pending),unit_tags=list(run.tags),
        cancellation_completed=getattr(task,'native_queue_cancellation_completed',False))


def report(q):
    return dict(enabled=True,task='cancel_native_queue args={task_id:owned macro ID}; outer count=1',
        rule='Stops its goal and cancels only observed linked building queues for units or research. '
             'Other queue owners/abilities are rejected; completed products are not undone. '
             'Queue disappearance is acknowledgement, not proof of refund or causal cancellation. '
             'Not townhall upgrades, worker construction, eggs, mobile-unit morphs or ammo.',
        supported_tasks=[dict(task_id=t.id,product=t.program.spec['entry']['name'],state=t.status,issued=t.program.issued)
            for t in q.tasks if supported(t,q.tech_tree) and (t.active or t.program.pending
                and not getattr(t,'native_queue_cancellation_completed',False))])


class NativeQueueCancelRun:
    def __init__(self,record):
        self.record=deepcopy(record)
        self.tags=set(record['unit_tags'])|{tag for p in record['pending'] for tag in p['tags']}
        self.pending_purchases=deepcopy(record['pending'])
        self.awaiting={};self.remaining={};self.resolved=set();self.next_at={};self.dispatches=0
    def close(self):self.tags.clear();self.pending_purchases.clear()
    def summary(self):
        return dict(kind='native_queue_cancel',original_task_id=self.record['task_id'],
            product=self.record['product'],unit_tags=sorted(self.tags),awaiting=deepcopy(self.awaiting),
            dispatches=self.dispatches,refund_confirmed=False,causal_cancellation_confirmed=False)
    def done(self,q,t,reason):
        original=next((old for old in q.tasks if old.id==self.record['task_id']),None)
        if original is not None:original.native_queue_cancellation_completed=True
        q.event('native_queue_cancel_observed',task_id=t.id,original_task_id=self.record['task_id'],reason=reason,
            dispatches=self.dispatches,macro_goal_stopped=True,refund_confirmed=False,causal_cancellation_confirmed=False)
        q.update(t,'completed',reason+'; refund/causal cancellation not inferred')
    def tick(self,w,q,t,used,bank,out):
        try:self.advance(w,q,t,used,out)
        except Exception as error:
            q.event('native_queue_cancel_failed',task_id=t.id,original_task_id=self.record['task_id'],
                error=str(error),macro_goal_stopped=True,native_cancellation_complete=False,refund_confirmed=False)
            q.update(t,'failed',str(error))
        return bank
    def advance(self,w,q,t,used,out):
        tree=q.tech_tree
        if any(error_tag==tag and tree.canonical_ability(a)==tree.canonical_ability(receipt['ability'])
               for tag,receipt in self.awaiting.items() for error_tag,a,_ in w.errors):
            raise RuntimeError('native queue cancellation rejected')
        if self.record['original_state']=='completed' or self.record['cancellation_completed'] or not self.record['issued']:
            self.done(q,t,'goal ended/unissued; no native queue cancellation needed');return
        if self.record['kind']=='research' and self.record['product_id'] in w.upgrades:
            self.done(q,t,'upgrade already observed completed; cannot undo it');return
        if w.seconds-t.created>=30:raise TimeoutError('native queue cancellation not acknowledged within 30 seconds')
        pending=self.record['pending']
        if not pending:
            self.done(q,t,'goal has no retained native queue binding; no cancellation sent');return
        bindings={tag:[p for p in pending if tag in p['tags']] for tag in self.tags}
        bindings={tag:ps for tag,ps in bindings.items() if ps}
        # Another task's ownership is never subtracted just because the goal was stopped.
        if any(other.active and other.skill!='rally_units' and other.id not in (t.id,self.record['task_id']) and other.program
               and set(getattr(other.program,'tags',()))&set(bindings) for other in q.tasks):
            raise ValueError('linked producer now belongs to another active task; no cancellation sent')
        for tag,ps in bindings.items():
            if tag in self.resolved:continue
            actor=w.by_tag(tag)
            if actor is None or actor.alliance!=1 or actor.hallucination:
                raise ValueError('linked producer no longer observed/owned; cancellation not confirmed')
            if not actor.is_building:raise ValueError('mobile/egg cancellation is not supported by this tool')
            if actor.type_id not in self.record['producers']:
                if self.record['structure'] and actor.type_id==self.record['product_id']:
                    self.resolved.add(tag);continue
                raise ValueError('producer form changed; no unrelated cancellation sent')
            abilities={tree.canonical_ability(p['ability']) for p in ps}
            if any(tree.canonical_ability(a) not in abilities for a in actor.orders):
                raise ValueError('observed queue includes an unrelated order; no cancellation sent')
            count=len(actor.orders);bound=sum(p.get('quantity',1) for p in ps)
            if count>bound:raise ValueError('observed queue exceeds linked purchases; ownership ambiguous')
            receipt=self.awaiting.get(tag)
            if receipt:
                if any(error_tag==tag and tree.canonical_ability(a)==tree.canonical_ability(receipt['ability'])
                       for error_tag,a,_ in w.errors):raise RuntimeError('native queue cancellation rejected')
                if count>receipt['before']:raise ValueError('queue grew during cancellation; fresh decision required')
                if count==receipt['before']:continue
                q.event('native_queue_change_observed',task_id=t.id,producer_tag=tag,
                    before=receipt['before'],after=count,refund_confirmed=False,causal_cancellation_confirmed=False)
                del self.awaiting[tag];self.remaining[tag]=count
            if not count:
                if not all(p.get('seen') for p in ps) and w.seconds-max(p.get('sent',t.created) for p in ps)<8:
                    q.update(t,'waiting','linked purchase not yet observed; waiting within acknowledgement grace');continue
                self.resolved.add(tag);continue
            if tag in self.remaining and count>self.remaining[tag]:raise ValueError('queue grew after observation; fresh decision required')
            self.remaining[tag]=count
            for p in self.pending_purchases:
                if tag in p['tags']:p['seen']=True
            if w.seconds<self.next_at.get(tag,0) or tag in used-self.tags or tag in q.defense_tags:continue
            available=q.backend.available_abilities(q.index,[tag])
            canonical={3671,3659} if self.record['kind']=='research' else {3671}
            choices=[a for a in available.get(tag,set()) if a in tree.abilities
                     and tree.abilities[a].target==1 and tree.canonical_ability(a) in canonical]
            if not choices:
                q.update(t,'waiting','linked native queue cancel unavailable; no fallback command');continue
            ability=min(choices)
            if not q.issue(t,command(ability,[tag]),out):continue
            used.add(tag);self.awaiting[tag]=dict(ability=ability,before=count,loop=w.game_loop)
            self.dispatches+=1;self.next_at[tag]=w.seconds+.5
            q.event('native_queue_cancel_order_issued',task_id=t.id,original_task_id=self.record['task_id'],
                    producer_tag=tag,ability_id=ability,queue_before=count,refund_confirmed=False)
            q.update(t,'running','native cancellation sent; awaiting observed queue change')
        if set(bindings)<=self.resolved:
            self.done(q,t,'linked orders no longer observed; completed products retained')
