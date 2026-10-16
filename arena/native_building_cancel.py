"""Cancel one explicit macro building goal and its linked native construction."""
from copy import deepcopy
import math
from arena.raw_engine import command
from arena.macro import MacroRun
from arena.tech_tree import WORKERS


def supported(task):
    return (isinstance(task.program,MacroRun) and task.program.spec.get('kind')=='production'
        and task.program.spec['entry'].get('structure')
        and any(r['producer'] in WORKERS for r in task.program.spec['entry']['recipes']))


def capture(task):
    run=task.program
    return dict(task_id=task.id,product=run.spec['entry']['name'],product_id=run.spec['entry']['id'],
        baseline=list(run.baseline or ()),pending=deepcopy(run.pending),
        original_state=task.status,issued=run.issued,unit_tags=list(run.tags),
        cancellation_completed=getattr(task,'native_cancellation_completed',False))


def report(q):
    return dict(enabled=True,task='cancel_native_building args={task_id:owned macro ID}; outer count=1',
        rule='Stops the macro and cancels its linked unfinished worker-built building/approach once. '
             'Completed/gone goals are explicit no-ops; no destruction/refund assumed. Not queues, research or townhall upgrades.',
        supported_tasks=[dict(task_id=t.id,product=t.program.spec['entry']['name'],state=t.status,
            issued=t.program.issued) for t in q.tasks if supported(t)
            and (t.active or t.program.pending and not getattr(t,'native_cancellation_completed',False))])


class NativeBuildingCancelRun:
    def __init__(self,record):
        self.record=deepcopy(record);self.tags=set(record['unit_tags'])|{tag for p in record['pending'] for tag in p['tags']};self.sent=None;self.next_at=0
        self.pending_purchases=deepcopy(record['pending'])
    def close(self):self.tags.clear();self.pending_purchases.clear()
    def summary(self):return dict(kind='native_building_cancel',original_task_id=self.record['task_id'],
        product=self.record['product'],unit_tags=sorted(self.tags),sent=self.sent,refund_confirmed=False)
    def tick(self,w,q,t,used,bank,out):
        try:self.advance(w,q,t,used,out)
        except Exception as error:
            q.event('native_building_cancel_failed',task_id=t.id,original_task_id=self.record['task_id'],
                error=str(error),macro_goal_stopped=True,native_cancellation_complete=False,refund_confirmed=False)
            q.update(t,'failed',str(error))
        return bank
    def done(self,q,t,reason,native=False):
        original=next((old for old in q.tasks if old.id==self.record['task_id']),None)
        if original is not None:original.native_cancellation_completed=True
        q.event('native_building_cancel_observed',task_id=t.id,original_task_id=self.record['task_id'],
            reason=reason,native_order_dispatched=native,macro_goal_stopped=True,refund_confirmed=False)
        q.update(t,'completed',reason+'; refund not inferred')
    def advance(self,w,q,t,used,out):
        tree=q.tech_tree
        if self.sent:
            p=self.sent
            if any(tag==p['tag'] and tree.canonical_ability(a)==tree.canonical_ability(p['ability'])
                   for tag,a,_ in w.errors):raise RuntimeError('native cancellation rejected: '+str(w.errors))
            actor=w.by_tag(p['tag'])
            if actor is not None and actor.alliance!=1:raise ValueError('cancellation target no longer owned')
            if p['mode']=='building' and (actor is None or actor.type_id in WORKERS):
                self.done(q,t,'linked unfinished building no longer observed after cancel order',True);return
            if p['mode']=='builder':
                appeared=[u for u in w.my(self.record['product_id']) if u.tag not in self.record['baseline']
                    and any(order.get('pos') and u.distance(order['pos'])<=2 for order in self.record['pending'])]
                if any(not u.done for u in appeared):
                    raise RuntimeError('construction appeared despite builder stop; fresh native cancellation required')
            if p['mode']=='builder' and (actor is None or not any(
                    tree.canonical_ability(a)==tree.canonical_ability(p['build_ability']) for a in actor.orders)):
                self.done(q,t,'linked builder approach no longer observed after stop order',True);return
            if w.seconds-t.created>=30:raise TimeoutError('native cancellation not confirmed within 30 seconds; no recast')
            return
        if self.record['original_state']=='completed' or self.record['cancellation_completed']:
            self.done(q,t,'original macro already ended; no native cancellation attempted');return
        if not self.record['issued']:
            self.done(q,t,'unissued macro stopped; no native construction or refund');return
        if w.seconds-t.created>=30:raise TimeoutError('linked native construction/order not found before deadline')
        if w.seconds<self.next_at:return
        self.next_at=w.seconds+.5
        pending=self.record['pending']
        products=w.my(self.record['product_id'])+(w.my(133) if self.record['product_id']==62 else [])
        candidates=[u for u in products
            if u.tag not in self.record['baseline'] and not u.hallucination
            and any(p.get('pos') and u.distance(p['pos'])<=2
                and (p.get('building_tag') is None or u.tag==p['building_tag']) for p in pending)]
        if len(candidates)>1:raise ValueError('ambiguous linked building; no cancellation sent')
        if candidates:
            building=candidates[0]
            if self.record['product_id']==62 and building.type_id==133:
                self.done(q,t,'linked Gateway construction completed and converted; cannot undo construction');return
            if building.done:
                self.done(q,t,'linked building already completed; cannot undo construction');return
            if building.tag in used-self.tags or building.tag in q.defense_tags:
                q.update(t,'waiting','linked building has another owner this tick');return
            available=q.backend.available_abilities(q.index,[building.tag])
            choices=[a for a in available.get(building.tag,set())
                if a in tree.abilities and tree.canonical_ability(a)==tree.canonical_ability(3659)]
            if not choices:
                q.update(t,'waiting','native construction cancel unavailable; no order sent');return
            ability=min(choices)
            if not q.issue(t,command(ability,[building.tag]),out):return
            self.tags.add(building.tag);used.add(building.tag);self.pending_purchases.clear()
            self.sent=dict(mode='building',tag=building.tag,ability=ability,loop=w.game_loop)
        else:
            approaches=[(u,p) for p in pending if p.get('building_tag') is None for tag in p['tags'] for u in [w.by_tag(tag)]
                if u is not None and u.alliance==1 and u.type_id in WORKERS
                and any(tree.canonical_ability(a)==tree.canonical_ability(p['ability'])
                    and p.get('pos') and n<len(u.order_positions) and u.order_positions[n] is not None
                    and math.dist(u.order_positions[n],p['pos'])<=2 for n,a in enumerate(u.orders))]
            if not approaches:
                q.update(t,'waiting','waiting for linked unfinished building or observed builder approach');return
            if len(approaches)!=1:raise ValueError('ambiguous builder approach; no cancellation sent')
            worker,p=approaches[0]
            if worker.tag in q.defense_tags:return
            if not q.issue(t,command(4,[worker.tag]),out):return
            self.tags.add(worker.tag);used.add(worker.tag)
            self.sent=dict(mode='builder',tag=worker.tag,ability=4,build_ability=p['ability'],loop=w.game_loop)
        q.event('native_building_cancel_order_issued',task_id=t.id,original_task_id=self.record['task_id'],
            operation=dict(self.sent),refund_confirmed=False)
        q.update(t,'running','one native cancellation/stop sent; awaiting linked building/order disappearance')
