"""Cancel a bound paid building upgrade, without undoing completed forms."""
from copy import deepcopy
from arena.macro import MacroRun
from arena.raw_engine import command


def supported(task,tree):
    if tree is None or not isinstance(task.program,MacroRun):return False
    spec=task.program.spec;entry=spec.get('entry',{})
    buildings={e['id'] for e in tree.units.values() if e.get('structure')}
    return spec['kind']=='production' and entry.get('structure') and any(
        r['producer'] in buildings and r['producer']!=entry['id'] and r.get('consumes_actor')
        and (r.get('minerals',0)>0 or r.get('gas',0)>0)
        and not r.get('placement') and not r.get('addon') for r in entry['recipes'])


def capture(task):
    run=task.program;entry=run.spec['entry']
    return dict(task_id=task.id,product=entry['name'],product_id=entry['id'],
        producers=sorted({r['producer'] for r in entry['recipes']}),
        pending=deepcopy(run.pending),original_state=task.status,issued=run.issued,
        completed=getattr(task,'native_morph_cancellation_completed',False))


def report(q):
    return dict(enabled=True,task='cancel_native_morph args={task_id:owned paid building-upgrade macro ID}; outer count=1',
        rule='Stops that goal and cancels only bound observed building morphs. Completed forms remain. '
             'No refund/death inference or repurchase. Not ordinary construction, research, eggs or mobile morphs.',
        supported_tasks=[dict(task_id=t.id,product=t.program.spec['entry']['name'],state=t.status)
            for t in q.tasks if supported(t,q.tech_tree) and (t.active or t.program.pending
                and not getattr(t,'native_morph_cancellation_completed',False))])


class NativeMorphCancelRun:
    def __init__(self,record):
        self.record=deepcopy(record);self.tags={tag for p in record['pending'] for tag in p['tags']}
        self.pending_purchases=deepcopy(record['pending']);self.sent={};self.resolved=set();self.dispatches=0
    def close(self):self.tags.clear();self.pending_purchases.clear()
    def summary(self):
        return dict(kind='native_morph_cancel',original_task_id=self.record['task_id'],
            product=self.record['product'],unit_tags=sorted(self.tags),dispatches=self.dispatches,refund_confirmed=False)
    def tick(self,w,q,t,used,bank,out):
        try:self.advance(w,q,t,used,out)
        except Exception as error:
            q.event('native_morph_cancel_failed',task_id=t.id,original_task_id=self.record['task_id'],
                error=str(error),macro_goal_stopped=True,refund_confirmed=False)
            q.update(t,'failed',str(error))
        return bank
    def advance(self,w,q,t,used,out):
        tree=q.tech_tree
        if any(tag==caster and tree.canonical_ability(a)==tree.canonical_ability(ability)
               for caster,ability in self.sent.items() for tag,a,_ in w.errors):
            raise RuntimeError('native building morph cancellation rejected')
        if w.seconds-t.created>=30:raise TimeoutError('building morph cancellation unconfirmed after 30 seconds')
        if self.record['original_state']=='completed' or self.record['completed'] or not self.record['issued']:
            self.finish(q,t,'goal already ended/unissued; no native morph undone');return
        if not self.tags:raise ValueError('issued morph has no retained native binding; cancellation not confirmed')
        bindings={tag:[p for p in self.record['pending'] if tag in p['tags']] for tag in self.tags}
        if any(other.active and other.id not in (t.id,self.record['task_id']) and other.skill!='rally_units'
               and other.program and set(getattr(other.program,'tags',()))&self.tags for other in q.tasks):
            raise ValueError('bound morph actor belongs to another active task')
        actors={}
        for tag,ps in bindings.items():
            if tag in self.resolved:continue
            actor=w.by_tag(tag)
            if actor is None or actor.alliance!=1 or actor.hallucination or not actor.is_building:
                raise ValueError('bound building no longer observed/owned; cancellation not confirmed')
            if actor.type_id not in self.record['producers']+[self.record['product_id']]:
                raise ValueError('bound actor changed into an unrelated form')
            abilities={tree.canonical_ability(p['ability']) for p in ps}
            if any(tree.canonical_ability(a) not in abilities for a in actor.orders):
                raise ValueError('bound building has an unrelated order; no cancellation sent')
            actors[tag]=(actor,ps,bool(actor.orders) or actor.type_id==self.record['product_id'] and not actor.done)
        for tag,(actor,ps,morphing) in actors.items():
            if not morphing:
                if not all(p.get('seen') for p in ps) and w.seconds-max(p.get('sent',t.created) for p in ps)<8:continue
                self.resolved.add(tag);continue
            for p in self.pending_purchases:
                if tag in p['tags']:p['seen']=True
            if tag in self.sent or tag in used-self.tags or tag in q.defense_tags:continue
            available=q.backend.available_abilities(q.index,[tag]).get(tag,set())
            choices=[a for a in available if a in tree.abilities and tree.abilities[a].target==1
                     and tree.canonical_ability(a) in (3659,3671)]
            if not choices:
                q.update(t,'waiting','native morph cancel unavailable; no fallback command');continue
            ability=min(choices)
            if q.issue(t,command(ability,[tag]),out):
                self.sent[tag]=ability;self.dispatches+=1;used.add(tag)
                q.event('native_morph_cancel_order_issued',task_id=t.id,original_task_id=self.record['task_id'],
                        caster=tag,ability_id=ability,refund_confirmed=False)
                q.update(t,'running','native cancel sent; awaiting observed morph end')
        if self.tags<=self.resolved:self.finish(q,t,'bound morph orders/progress no longer observed; completed forms retained')
    def finish(self,q,t,reason):
        original=next((old for old in q.tasks if old.id==self.record['task_id']),None)
        if original is not None:original.native_morph_cancellation_completed=True
        q.event('native_morph_cancel_observed',task_id=t.id,original_task_id=self.record['task_id'],
                reason=reason,dispatches=self.dispatches,macro_goal_stopped=True,refund_confirmed=False)
        q.update(t,'completed',reason+'; refund/causal cancellation not inferred')
