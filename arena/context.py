"""Bounded battle memory and stable-prefix serialization; no extra model calls."""
from collections import Counter, deque
from copy import deepcopy
import json
import threading
from arena.raw_engine import NAMES


def counts(units):
    return dict(sorted(Counter(getattr(u,'name',NAMES.get(u.type_id,str(u.type_id))) for u in units).items()))


def bounded_counts(values,limit=16):
    items=sorted(values.items(),key=lambda item:(-item[1],item[0]))
    result=dict(items[:limit])
    if len(items)>limit:
        result['_other_types']=len(items)-limit
        result['_other_units']=sum(v for _,v in items[limit:])
    return result


def battle_payload(payload):
    """Preserve the top-level API contract, put truly fixed data before observations.

    Never sort all top-level keys: active_tasks would precede the static catalogue.
    Visible enemy roles belong to changing observations, not the fixed rulebook.
    """
    source=deepcopy(payload)
    knowledge=source.get('game_knowledge')
    if knowledge is not None:
        source['visible_enemy_roles']=knowledge.pop('visible_enemy_roles',{})
        if 'technology_report' in knowledge:source['technology_report']=knowledge.pop('technology_report')
    fixed=('tech_tree','game_knowledge','control_policy','prepared_strategy','context_policy')
    versioned=('python_skill_library','skill_library')
    ordered={}
    for key in fixed+versioned:
        if key in source:
            ordered[key]=json.loads(json.dumps(source.pop(key),ensure_ascii=False,sort_keys=True))
    # Chronological memory is bounded, and deliberately in the changing suffix.
    for key in ('world','intelligence','engagement_log','decision_memory','defensive_rally'):
        if key in source:ordered[key]=source.pop(key)
    ordered.update(source)
    return ordered


class DecisionMemory:
    """Recorded intentions, not fabricated strategic reflection or execution success."""
    def __init__(self,recent=8,milestones=6,interval_s=60):
        self.recent=deque(maxlen=recent);self.milestones=deque(maxlen=milestones)
        self.interval=interval_s;self.total=0;self.last_milestone=float('-inf');self.lock=threading.Lock()

    def add(self,game_loop,plan):
        reason=str(plan.get('reason') or '')
        tasks=plan.get('tasks',[plan] if 'skill' in plan else [])
        names=[str(t.get('skill','?'))[:64] for t in tasks if isinstance(t,dict)]
        row=dict(game_s=round(game_loop/22.4,1),reason=reason[:160],reason_truncated=len(reason)>160,
                 skills=names[:8],omitted_skills=max(0,len(names)-8))
        with self.lock:
            self.total+=1;self.recent.append(row)
            if row['game_s']-self.last_milestone>=self.interval:
                self.milestones.append(deepcopy(row));self.last_milestone=row['game_s']

    def public(self):
        with self.lock:
            return dict(total_decisions=self.total,recent=deepcopy(list(self.recent)),
                milestones=deepcopy(list(self.milestones)),recent_limit=self.recent.maxlen,
                milestone_limit=self.milestones.maxlen,milestone_interval_game_s=self.interval,
                meaning='Recorded model intentions; not proof tasks executed or strategy succeeded.')


class EngagementLog:
    """Observation windows, not omniscient kill attribution or spatial battle detection.

    Contact changes, observed damage and death events start/update a window.
    Counts are ALL owned units / currently visible enemies, not a frozen squad.
    Raw deaths are confirmed events, but cancellation/morph consumption may also
    cause events; neither side receives credit for a kill without causal evidence.
    """
    def __init__(self,event,mode='raw',limit=20):
        if mode not in ('raw','screen'):raise ValueError('explicit observation mode required')
        self.event=event;self.mode=mode;self.history=deque(maxlen=limit);self.active=None
        self.total=0;self.closed_total=0;self.evicted=0;self.known={};self.previous=None
        self.last_loop=-1;self.last_activity=0;self.lock=threading.RLock();self.known_evicted=0
        self.deaths_seen=set();self.deaths_order=deque(maxlen=4096)

    def _close(self,why):
        if self.active is None:return
        row=self.active;row['status']='closed';row['closed_by']=why
        if len(self.history)==self.history.maxlen:self.evicted+=1
        self.history.append(row);self.closed_total+=1
        self.event('engagement_event',engagement=deepcopy(row));self.active=None

    def update(self,w):
        with self.lock:
            if w.game_loop<=self.last_loop:return
            self.last_loop=w.game_loop;now=w.game_loop/22.4
            if self.mode=='screen':
                own=w.on_screen(w.my_units);enemy=w.on_screen(w.enemy_units)
                # A camera move changes the sample area. Record it, never compare it as casualties.
                camera=list(w.camera)
                current=dict(our=counts(own),enemy=counts(enemy),camera=camera)
            else:
                own=w.my_units;enemy=w.enemy_units
                current=dict(our=counts(own),enemy=counts(enemy))
            previous=self.previous;self.previous=current
            ours={};theirs={};left={};returned={};damage=False;own_intermediates={};enemy_intermediates={}
            if self.mode=='raw':
                visible={u.tag:u for u in enemy};ours_now={u.tag:u for u in own}
                prior_visible={t:r for t,r in self.known.items() if r['visible'] and r['side']==4}
                dead=set(w.dead_units)
                for tag in dead-self.deaths_seen:
                    known=self.known.pop(tag,None)
                    if known:
                        bucket=ours if known['side']==1 else theirs
                        bucket[known['name']]=bucket.get(known['name'],0)+1
                        from arena.production_lifecycle import PRODUCTION_INTERMEDIATES
                        if known['type'] in PRODUCTION_INTERMEDIATES:
                            subset=own_intermediates if known['side']==1 else enemy_intermediates
                            subset[known['name']]=subset.get(known['name'],0)+1
                    if len(self.deaths_order)==self.deaths_order.maxlen:self.deaths_seen.discard(self.deaths_order[0])
                    self.deaths_order.append(tag);self.deaths_seen.add(tag)
                for tag,row in prior_visible.items():
                    if tag not in visible and tag not in dead:left[row['name']]=left.get(row['name'],0)+1
                for u in list(own)+list(enemy):
                    old=self.known.get(u.tag);name=NAMES.get(u.type_id,str(u.type_id))
                    if old and old['type']==u.type_id and old['health']>u.health:damage=True
                    if u.alliance==4 and (old is None or not old['visible']):returned[name]=returned.get(name,0)+1
                    self.known[u.tag]=dict(name=name,type=u.type_id,side=u.alliance,health=u.health,visible=True,at=now)
                for tag,row in self.known.items():
                    row['visible']=tag in (ours_now if row['side']==1 else visible)
                if len(self.known)>4096:
                    remove=sorted(self.known,key=lambda t:self.known[t]['at'])[:len(self.known)-4096]
                    for tag in remove:del self.known[tag]
                    self.known_evicted+=len(remove)
                    self.event('engagement_memory_capacity',evicted=len(remove),total=self.known_evicted)
                activity=bool(ours or theirs or left or returned or damage)
            else:
                activity=bool(previous and previous!=current and (previous['enemy'] or current['enemy'])) or bool(not previous and enemy)
            if self.active and (now-self.active['start_game_s']>=30 or now-self.last_activity>=6):
                self._close('30-second observation window' if now-self.active['start_game_s']>=30 else '6 seconds without new contact/damage/death')
            if not activity:return
            if self.active is None:
                self.total+=1
                self.active=dict(id=self.total,status='ongoing',start_game_s=round(now,2),end_game_s=round(now,2),
                    our_before=(previous or current)['our'],our_after=current['our'],
                    enemy_visible_before=(previous or dict(enemy={}))['enemy'],enemy_visible_after=current['enemy'],
                    our_deaths_observed={},enemy_deaths_observed={},enemy_left_vision={},enemy_entered_vision={},
                    our_production_intermediates_removed={},enemy_production_intermediates_removed={},
                    damage_observed=False,source='raw observations/dead_units' if self.mode=='raw' else 'screen counts only; exact death events unavailable')
            row=self.active;row['end_game_s']=round(now,2);row['our_after']=current['our'];row['enemy_visible_after']=current['enemy']
            row['damage_observed'] |= damage
            for field,values in [('our_deaths_observed',ours),('enemy_deaths_observed',theirs),('enemy_left_vision',left),('enemy_entered_vision',returned)]:
                for name,n in values.items():row[field][name]=row[field].get(name,0)+n
            for field,values in [('our_production_intermediates_removed',own_intermediates),('enemy_production_intermediates_removed',enemy_intermediates)]:
                for name,n in values.items():row[field][name]=row[field].get(name,0)+n
            if self.mode=='screen' and previous and previous['camera']!=current['camera']:
                row['camera_changed']=True
            self.last_activity=now

    def public(self,recent=6,max_chars=7000):
        with self.lock:
            rows=list(self.history)+([self.active] if self.active else [])
            selected=[];size=0
            for raw in reversed(rows[-recent:]):
                row=deepcopy(raw)
                for key,value in list(row.items()):
                    if isinstance(value,dict):row[key]=bounded_counts(value)
                length=len(json.dumps(row,ensure_ascii=False))
                if size+length>max_chars:break
                selected.insert(0,row);size+=length
            return dict(total_windows=self.total,closed_windows=self.closed_total,retained_closed=len(self.history),
                evicted_windows=self.evicted,omitted_from_prompt=len(rows)-len(selected),known_unit_evictions=self.known_evicted,
                windows=selected,meaning='Global observation windows, not isolated battles. Count changes include production/morphs. '
                'Left vision is NOT death; death events do NOT identify the killer/cause (may include cancellations/consumption). '
                'Repeated exits/reentries are transitions, not unique enemy casualties.')
