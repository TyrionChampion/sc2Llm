"""Shared request epochs and observation-based steps for paused native matches.

Only scheduling is shared: each planner receives its own player's observation.
No request waits for another model's answer before entering the same batch.
"""
import math
import threading
from arena.raw_engine import LOOPS_PER_SECOND, ATTACK, MOVE
from arena.production_lifecycle import PRODUCTION_INTERMEDIATES


class PlannerBatcher:
    def __init__(self,commanders,quantum_game_s=2):
        if not math.isfinite(quantum_game_s) or quantum_game_s<0:raise ValueError('batch interval must be finite and nonnegative')
        self.commanders=commanders;self.quantum=quantum_game_s
        self.batches=0;self.requests=0;self.largest_batch=0;self.last_batch=None
        for c in commanders:
            c.defer_planner_requests=True
            if callable(getattr(c.tactics,'request_plan',None)):c.tactics.defer_planner_requests=True
            c.control_policy['non_realtime_scheduler']=dict(batch_game_s=self.quantum,
                ordinary_max_alignment_delay_game_s=self.quantum+1/LOOPS_PER_SECOND if self.quantum else 0,
                urgent='Base/combat alerts keep their original deadlines',
                synchronization='All due prompts prepared from player-local observations, then workers released together')

    def _aligned(self,due):
        if not math.isfinite(due) or not self.quantum:return due
        due_loop=math.ceil(due*LOOPS_PER_SECOND-1e-9)
        epoch=math.floor(due/self.quantum)
        loop=math.ceil(epoch*self.quantum*LOOPS_PER_SECOND-1e-9)
        if loop<due_loop:loop=math.ceil((epoch+1)*self.quantum*LOOPS_PER_SECOND-1e-9)
        return loop/LOOPS_PER_SECOND

    def _jobs(self,worlds):
        common_s=min(w.seconds for w in worlds)
        for c,w in zip(self.commanders,worlds):
            if c.surrender or c.client_fatal is not None or getattr(c.backend,'match_control_pending',False):continue
            offset=w.seconds-common_s
            raw=c.request_deadline()-offset
            due=min(self._aligned(raw),getattr(c,'urgent_request_game_s',float('inf'))-offset)
            yield c,w,due,'main'
            tactical=c.tactics
            if callable(getattr(tactical,'request_deadline',None)):
                raw=tactical.request_deadline()-offset
                due=raw if tactical.alerts or tactical.combat_alerts else self._aligned(raw)
                yield tactical,w,due,'battle'

    def dispatch(self,worlds):
        if len(worlds)!=len(self.commanders):raise ValueError('one observation per commander required')
        common_s=min(w.seconds for w in worlds)
        jobs=[job for job in self._jobs(worlds) if common_s+1e-9>=job[2]]
        if not jobs:return
        gate=threading.Event();sent=[]
        batch=dict(id=self.batches+1,game_loop=min(w.game_loop for w in worlds))
        try:
            for planner,w,due,role in jobs:
                planner.request_gate=gate;planner.request_batch=batch
                if planner.request_plan(w,due_at=due+(w.seconds-common_s)):sent.append(role)
        finally:
            gate.set()  # Also release already queued workers if later prompt preparation fails.
        if sent:
            self.batches+=1;self.requests+=len(sent);self.largest_batch=max(self.largest_batch,len(sent))
            self.last_batch=dict(batch,requests=len(sent),roles=sent)

    def next_deadline(self,worlds):
        now=min(w.seconds for w in worlds)
        # Already-due but unavailable clients must not trap the engine in 1-loop steps.
        return min((due for _,_,due,_ in self._jobs(worlds) if due>now+1e-9),default=float('inf'))

    def public(self):
        return dict(batch_game_s=self.quantum,batches=self.batches,requests=self.requests,
                    largest_batch=self.largest_batch,last_batch=self.last_batch)


class AdaptiveSteps:
    def __init__(self,combat_loops=8,max_loops=64):
        if any(type(n) is not int or not 1<=n<=224 for n in (combat_loops,max_loops)) or max_loops<combat_loops:
            raise ValueError('steps must satisfy 1 <= combat <= maximum <= 224')
        self.combat_loops=combat_loops;self.max_loops=max_loops
        self.previous={};self.last_observed={};self.contact_until=float('-inf')
        self.last_choice=None

    def choose(self,worlds,next_deadline=float('inf')):
        if not worlds:raise ValueError('at least one player observation required')
        now=min(w.seconds for w in worlds);contact=False;approach=False
        for player,w in enumerate(worlds):
            own=w.my_units;enemies=w.enemy_units
            armed=[u for u in own if u.ground_dps>0 or u.air_dps>0]
            approach|=bool(enemies or any(u.orders and not u.is_building and
                            any(order in (ATTACK,MOVE) for order in u.orders) for u in armed))
            contact|=bool(w.effects)
            contact|=any((u.can_attack(e) or e.can_attack(u)) and
                         u.distance(e)<=max(16,u.attack_range+e.attack_range+4)
                         for u in own for e in enemies)
            if w.game_loop>self.last_observed.get(player,-1):
                previous=self.previous.get(player,{})
                contact|=any(u.health<previous.get(u.tag,u.health) for u in own)
                contact|=any(tag in previous for tag in w.dead_units)
                self.previous[player]={u.tag:u.health for u in own if u.type_id not in PRODUCTION_INTERMEDIATES}
                self.last_observed[player]=w.game_loop
        if contact:self.contact_until=now+2
        mode='combat' if now<self.contact_until else 'approach' if approach else 'quiet'
        count=self.combat_loops if mode=='combat' else min(self.max_loops,max(self.combat_loops,32)) if mode=='approach' else self.max_loops
        if math.isfinite(next_deadline) and next_deadline>now:
            count=min(count,max(1,math.ceil((next_deadline-now)*LOOPS_PER_SECOND-1e-9)))
        self.last_choice=dict(mode=mode,step_loops=count,game_s=now)
        return count

    def public(self):
        return dict(combat_loops=self.combat_loops,max_loops=self.max_loops,
                    contact_hold_game_s=2,last_choice=self.last_choice)
