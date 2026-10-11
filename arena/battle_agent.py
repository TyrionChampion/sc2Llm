"""One independent battle LLM; target/squad choices come from that LLM only."""
import json
import math
import queue
import threading
import time
from collections import Counter, deque

from arena.raw_engine import ATTACK, MOVE, NAMES, command
from arena.tech_tree import TOWNHALLS, WORKERS
from arena.spatial import ground_destination, validate_target

SYSTEM = '''你是唯一的战斗指挥Agent，主指挥负责运营、科技、兵种和全局战略。
你可以直接调动squads中的远处部队支援任意受袭基地，不需要先回主矿集结。
根据actual_alerts、combat_alerts、基地位置、敌我空地攻击能力、距离和血量决定派哪支小队。
只输出JSON：{"orders":[{"squad":"输入中的小队ID","mode":"defend|attack|hold|withdraw",
"target":"输入中的基地ID或[x,y]","reason":"简短中文说明"}],"reason":"简短中文判断"}。
最多8条；同一小队只能出现一次。defend的target必须为当前actual_alerts中的基地ID，或combat_alerts中的前线坐标。
attack必须遵守strategic_order；初始授权只允许保卫基地，主指挥授权进攻后才可主动攻击。
landmarks.enemy_start是地图已知出生点，不保证仍有敌军。move_out已获授权且没有当前可见敌人时，先在安全前线集结或向该已知点推进；attack_nearest没有可见目标时不要擅自搜索。
hold和withdraw可以使用己方基地ID或地图内坐标；请综合威胁选择守备或撤退位置。
orders=[]保留已有命令。已经有人增援且局势未变时不要重复派遣所有小队。
基地受袭应考虑远处援军，不能因为附近没有兵就忽略，也不要因一名伤兵召回全军。
你只控制输入提供的小队；经济工人、自定义技能、运输任务和其他任务的单位不可抢用。
visible_enemies仅当前己方视野，不可编造雾中敌人。基地受伤但敌人不可见时原因未知。
发单不代表到达，到达不代表击退；读assignments与recent_feedback核对实际结果。
脚本只执行你的调兵与已授权技能，不会自行选择战略增援、全军撤退或进攻目标；move_out的安全前线推进是已授权任务的机械执行。
公开reason均为简体中文。不要输出推理过程、生产命令、Python源码或未提供的单位tag。'''


class BattleAgent:
    uses_assembly_anchor = False
    anchor = None
    max_age_wall = 12
    max_age_game = 12

    def __init__(self,backend,index,rally,event,audit,client,tasks,clock=time.monotonic,combat_micro=False):
        if client is None:raise ValueError('BattleAgent requires an explicit model client')
        self.backend=backend;self.index=index;self.rally=rally;self.event=event;self.audit=audit
        self.client=client;self.tasks=tasks;self.clock=clock
        self.combat_micro=combat_micro
        self.mode='llm_battle';self.objective='defend_base';self.min_group=0
        self.tags=set();self.fighting=set();self.assignments={};self.generation=0
        self.strategy=dict(attack_authorized=False,objective='defend_base',reason='主指挥初始授权保护己方基地')
        self.threat_times={};self.previous_health={};self.alerts=[];self.previous_alerts=set()
        self.previous_combat_state={};self.combat_deaths_seen=set();self.previous_nearby_enemy_tags=set()
        self.combat_threats={};self.previous_combat_alerts=set();self.combat_alerts=[]
        self.last_loop=-1;self.next_request=0;self.next_review=0;self.dirty=True
        self.thinking=False;self.requests=0;self.accepted=0;self.errors=0;self.expired=0
        self.last_decision=None;self.recent=deque(maxlen=12);self.action_contexts=[]
        self.request_trace={};self.last_shape=None;self.first_alert={};self.empty_move_out_orders=0
        self.request_bodies=[]  # last serialized prompts; powers the prefix-cache measurement
        self.inbox=queue.Queue(maxsize=1);self.outbox=queue.Queue(maxsize=1)
        self.closed=threading.Event()
        self.worker=threading.Thread(target=self._work,daemon=True,name='battle-agent')
        self.worker.start()

    def _event(self,kind,**details):
        row=dict(kind=kind,game_s=getattr(self,'game_s',None),wall=self.clock(),**details)
        self.recent.append(row);self.event(kind,**{k:v for k,v in row.items() if k!='kind'})

    def mission(self,skill):
        if skill=='defend':
            objective='defend_base'
            new=dict(attack_authorized=False,objective=objective,reason='主指挥显式选择防守')
        else:
            objective='visible_enemy' if skill=='attack_nearest' else 'enemy_start'
            new=dict(attack_authorized=True,objective=objective,reason='主指挥显式下达 '+skill)
        if new==self.strategy:return
        self.strategy=new;self.objective=objective;self.generation+=1;self.dirty=True
        self._event('tactical_mission',strategic_order=new,reason=new['reason'])

    @staticmethod
    def _combat_unit(u):
        return (u.done and not u.is_building and not u.hallucination and u.type_id not in WORKERS
                and (u.ground_dps>0 or u.air_dps>0))

    def _forward_point(self):
        enemy_start=getattr(self.backend,'enemy_start',None)
        if not callable(enemy_start):return None
        enemy=tuple(enemy_start(self.index));home=tuple(self.backend.homes[self.index])
        point=tuple(home[i]+(enemy[i]-home[i])*.55 for i in (0,1))
        return tuple(max(2,min(self.backend.size[i]-2,point[i])) for i in (0,1))

    def _move_out_fallback(self,r):
        if self.strategy['objective']!='enemy_start' or self.alerts or self.combat_alerts:
            return []
        assigned={tag for tag,a in self.assignments.items() if not a['failed']}
        candidates=[]
        for sid,tags in r.get('squads',{}).items():
            free=[tag for tag in tags if tag not in assigned]
            if free:candidates.append((sid,free))
        candidates.sort(key=lambda item:(-len(item[1]),item[0]))
        point=self._forward_point()
        if point is None or not candidates:return []
        return [dict(squad=sid,mode='attack',target=list(point),
                     reason='move_out已授权且当前无可见敌人，先向安全前线集结')
                for sid,tags in candidates[:2]]

    def _eligible(self,u):
        meta=getattr(self.backend,'types',{}).get(u.type_id)
        name=(getattr(meta,'name',None) or NAMES.get(u.type_id,'')).lower()
        return (u.done and not u.is_building and not u.hallucination and u.type_id not in WORKERS
            and not u.cargo_taken and not u.passengers and (u.ground_dps>0 or u.air_dps>0)
            and 'burrowed' not in name and 'liberatorag' not in name
            and (u.type_id!=32 or self.combat_micro)
            and (u.type_id in (32,33) or meta is None or getattr(meta,'movement_speed',0)>0))

    def _work(self):
        while not self.closed.is_set():
            try:item=self.inbox.get(timeout=.2)
            except queue.Empty:continue
            started=self.clock();answer=None;error=None
            try:answer=self.client.chat(SYSTEM,item['prompt'])
            except Exception as exc:error=type(exc).__name__+': '+str(exc)
            if not self.closed.is_set():
                self.outbox.put(dict(item,response=answer,error=error,latency_s=self.clock()-started,
                    usage=getattr(self.client,'last_usage',None)))

    def _scan(self,w,bases):
        now=w.seconds;self.alerts=[]
        damaged=[u for u in w.my_units if (u.is_building or u.type_id in WORKERS)
                 and u.health<self.previous_health.get(u.tag,u.health)]
        self.previous_health={u.tag:u.health for u in w.my_units if u.is_building or u.type_id in WORKERS}
        for b in bases:
            enemies=[e for e in w.enemy_units if e.distance(b)<=26 and (e.ground_dps>0 or e.air_dps>0)]
            victims=[u for u in damaged if u.distance(b)<=26]
            if enemies or victims:self.threat_times[b.tag]=now
            if now-self.threat_times.get(b.tag,-1e9)>4:continue
            key='base_'+str(b.tag)
            row=dict(base=key,pos=list(b.pos),hp=round(b.health,1),visible_enemies=len(enemies),
                damaged_own_units=len(victims),last_threat_game_s=self.threat_times[b.tag],
                evidence='current visible armed enemies' if enemies else 'recent own damage; cause/attacker may be unknown')
            self.alerts.append(row)
        active={a['base'] for a in self.alerts}
        for a in self.alerts:
            if a['base'] not in self.previous_alerts:
                self.first_alert[a['base']]=dict(wall=self.clock(),game_s=now)
                self._event('tactical_base_alert',alert=a,reason='基地出现威胁；等待战斗Agent选择增援，不自动调兵')
        for cleared in self.previous_alerts-active:
            self._event('tactical_base_alert_cleared',base=cleared,reason='近期威胁不再观察到，不等于敌人已死亡')
        if active!=self.previous_alerts:self.dirty=True
        self.previous_alerts=active

        # Mobile combat can happen away from a town hall. Keep a short-lived,
        # player-local alert so the battle model can recall a remote squad.
        current={u.tag:u for u in w.my_units if self._combat_unit(u)}
        damaged=[];dead=[];nearby_tags=set();fronts={}
        for tag,u in current.items():
            old=self.previous_combat_state.get(tag)
            if old is not None and u.health < old['health']:
                damaged.append(u)
            nearby=[e for e in w.enemy_units if e.distance(u)<=max(14,u.attack_range+6)
                    and (e.ground_dps>0 or e.air_dps>0)]
            nearby_tags.update(e.tag for e in nearby)
            if nearby:
                center=(sum(e.x for e in nearby)/len(nearby),sum(e.y for e in nearby)/len(nearby))
                key='front_%d_%d'%(int(center[0]//12),int(center[1]//12))
                row=fronts.setdefault(key,dict(pos=[round(center[0],1),round(center[1],1)],
                    visible_enemies=set(),damaged_own_units=0,dead_own_units=0,
                    evidence='current visible armed enemies'))
                row['visible_enemies'].update(e.tag for e in nearby)
        prior_tags=set(self.previous_combat_state)
        for tag in prior_tags-set(current):
            if tag in w.dead_units and tag not in self.combat_deaths_seen:
                old=self.previous_combat_state[tag];dead.append(old)
                self.combat_deaths_seen.add(tag)
        for u in damaged:
            key='front_%d_%d'%(int(u.x//12),int(u.y//12))
            row=fronts.setdefault(key,dict(pos=[round(u.x,1),round(u.y,1)],visible_enemies=set(),
                damaged_own_units=0,dead_own_units=0,evidence='recent own combat-unit damage; cause may be unknown'))
            row['damaged_own_units']+=1
        for old in dead:
            key='front_%d_%d'%(int(old['pos'][0]//12),int(old['pos'][1]//12))
            row=fronts.setdefault(key,dict(pos=[round(old['pos'][0],1),round(old['pos'][1],1)],visible_enemies=set(),
                damaged_own_units=0,dead_own_units=0,evidence='recent own combat-unit death; cause may be unknown'))
            row['dead_own_units']+=1
        self.previous_combat_state={tag:dict(health=u.health,pos=list(u.pos),type_id=u.type_id) for tag,u in current.items()}
        new_nearby=nearby_tags-self.previous_nearby_enemy_tags
        self.previous_nearby_enemy_tags=nearby_tags
        base_positions=[tuple(a['pos']) for a in self.alerts]
        for key,row in fronts.items():
            if any(math.dist(row['pos'],base)<=26 for base in base_positions):continue
            row['visible_enemies']=len(row['visible_enemies'])
            row['last_threat_game_s']=now
            self.combat_threats[key]=dict(row)
            if key not in self.previous_combat_alerts or new_nearby or damaged or dead:
                self.dirty=True
        self.combat_threats={key:row for key,row in self.combat_threats.items()
                             if now-row['last_threat_game_s']<=4}
        self.combat_alerts=[dict(id=key,**row) for key,row in sorted(self.combat_threats.items())]
        active_combat={row['id'] for row in self.combat_alerts}
        for row in self.combat_alerts:
            if row['id'] not in self.previous_combat_alerts:
                self._event('combat_alert',alert=row,
                            reason='移动战斗单位附近出现敌人或近期受伤/死亡；触发战术Agent检查')
        for key in self.previous_combat_alerts-active_combat:
            self._event('combat_alert_cleared',alert_id=key,
                        reason='近期没有新的可见接触或战斗单位损失')
        self.previous_combat_alerts=active_combat

    @staticmethod
    def _squads(own):
        bins={}
        for u in own:bins.setdefault((u.flying,int(u.x//24),int(u.y//24)),[]).append(u)
        groups={}
        for group in bins.values():
            group.sort(key=lambda u:u.tag)
            for start in range(0,len(group),32):
                part=group[start:start+32];groups['squad_'+str(part[0].tag)]=part
        return dict(list(sorted(groups.items()))[:16]),max(0,len(groups)-16)

    @staticmethod
    def _unit(u):
        return dict(type=NAMES.get(u.type_id,str(u.type_id)),pos=[round(u.x,1),round(u.y,1)],
            hp=round(u.health,1),max_hp=u.health_max,ground_dps=round(u.ground_dps,1),
            air_dps=round(u.air_dps,1),range=round(u.attack_range,1),flying=u.flying)

    @staticmethod
    def _shared_prefix(a, b):
        """Length of the common leading text of two serialized prompts."""
        n = min(len(a), len(b)); i = 0
        while i < n and a[i] == b[i]: i += 1
        return i

    def _fixed_head_chars(self):
        """Length of the match-stable JSON head (everything before the first volatile key)."""
        recent=self.request_bodies[-2:]
        return self._shared_prefix(recent[0],recent[1]) if len(recent)==2 else 0

    def cache_prefix_chars(self):
        """Common leading characters of the last two serialized request bodies.

        Prefix caching only pays for the part of the prompt that is byte-identical from
        the start; per-request fields early in the JSON destroy it. Reported in stats so a
        regression that reintroduces ordering noise is visible without reading the code.
        """
        recent = self.request_bodies[-2:]
        if len(recent) < 2:return 0
        return self._shared_prefix(recent[0], recent[1])

    def _request(self,w,own,bases):
        now=self.clock()
        ready=getattr(self.client,'ready_for_request',None)
        if self.thinking or not own or now<self.next_request or (callable(ready) and not ready()):return
        if not self.alerts and not self.combat_alerts and not self.strategy['attack_authorized'] and not self.dirty:return
        if not self.alerts and not self.combat_alerts and not self.strategy['attack_authorized']:
            self.dirty=False;return  # No task requiring inference. No periodic empty API calls.
        if not self.dirty and now<self.next_review:return
        squads,omitted=self._squads(own)
        rows=[]
        for sid,units in squads.items():
            rows.append(dict(id=sid,count=len(units),types=dict(Counter(NAMES.get(u.type_id,str(u.type_id)) for u in units)),
                center=[round(sum(getattr(u,k) for u in units)/len(units),1) for k in ('x','y')],
                members=[self._unit(u) for u in units],
                distance_to_bases={('base_'+str(b.tag)):round(min(u.distance(b) for u in units),1) for b in bases}))
        enemy_start=getattr(self.backend,'enemy_start',None)
        landmarks=dict(home=list(self.backend.homes[self.index]),
            enemy_start=list(enemy_start(self.index)) if callable(enemy_start) else None,
            enemy_start_source='own player game_info' if callable(enemy_start) else 'unavailable: backend has no enemy_start query; no substitute')
        landmarks['forward_assembly']=list(self._forward_point()) if self._forward_point() else None
        # Prefix-cache layout: head = fixed for the whole match, body = per observation.
        # Measured on the 2026-10-09 match (logs/feedback/p1-mnno1pwx.jsonl), the previous
        # fully-volatile order left a median shared prefix of 13 characters between
        # consecutive battle requests because game_s came first, so this role paid nearly
        # full price for every input token. Keep stable keys first; put new volatile keys last.
        payload=dict(map_size=list(self.backend.size),
            landmarks=landmarks,
            combat_abilities=dict(enabled=self.combat_micro,abilities=['stim','siege','unsiege'],
                scope='Executor handles available researched abilities only after your explicit combat orders'),
            control='Own current observation only. All squads are available; other owners excluded. Choose remote squads directly.',
            game_s=round(w.seconds,2),strategic_order=self.strategy,
            actual_alerts=self.alerts,combat_alerts=self.combat_alerts,
            bases=[dict(id='base_'+str(b.tag),pos=list(b.pos),hp=b.health,max_hp=b.health_max) for b in bases[:16]],
            squads=rows,omitted_squads=omitted,visible_enemies=[self._unit(u) for u in w.enemy_units[:48]],
            omitted_enemies=max(0,len(w.enemy_units)-48),assignments=self._assignment_rows(),recent_feedback=list(self.recent)[-6:])
        prompt=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        self.request_bodies.append(prompt);self.request_bodies=self.request_bodies[-4:]
        item=dict(request_id=self.requests+1,prompt=prompt,
            squads={sid:[u.tag for u in us] for sid,us in squads.items()},generation=self.generation,
            game_s=w.seconds,wall=now)
        self.audit(dict(record='tactical_request_queued',request_id=item['request_id'],prompt=payload))
        self.audit(dict(record='tactical_prompt_layout',request_id=item['request_id'],
                        prompt_chars=len(prompt),
                        fixed_head_chars=self._fixed_head_chars(),
                        shared_prefix_chars=self.cache_prefix_chars()))
        self.request_trace[item['request_id']]=dict(queued_wall=now,queued_game_s=w.seconds)
        self.request_trace=dict(list(self.request_trace.items())[-16:])
        self.inbox.put_nowait(item);self.requests+=1;self.thinking=True;self.dirty=False
        self.next_request=now+1.5;self.next_review=now+(6 if self.alerts else 12)

    def _receive(self,w,own,bases):
        try:r=self.outbox.get_nowait()
        except queue.Empty:return
        self.thinking=False
        self.audit(dict(record='tactical_request_finished',request_id=r['request_id'],game_s=w.seconds,
            response=r['response'],error=r['error'],latency_s=r['latency_s'],usage=r['usage']))
        self.request_trace.setdefault(r['request_id'],{}).update(received_wall=self.clock(),latency_s=r['latency_s'])
        if r['generation']!=self.generation or self.clock()-r['wall']>self.max_age_wall or w.seconds-r['game_s']>self.max_age_game:
            self.expired+=1;self.dirty=True
            self._event('tactical_plan_expired',request_id=r['request_id'],reason='主任务已变或回复超过12秒；未执行旧回复');return
        try:
            if r['error']:raise ValueError(r['error'])
            p=json.loads(r['response'])
            if not isinstance(p,dict) or set(p)!={'orders','reason'}:raise ValueError('expected orders/reason only')
            if not isinstance(p['reason'],str) or not 1<=len(p['reason'])<=240:raise ValueError('reason requires 1..240 chars')
            if not isinstance(p['orders'],list) or len(p['orders'])>8:raise ValueError('orders requires 0..8 entries')
            if not p['orders'] and self.strategy['objective']=='enemy_start':
                fallback=self._move_out_fallback(r)
                if fallback:
                    p=dict(p,orders=fallback,reason=p['reason']+'；已授权move_out，先向安全前线集结')
                    self._event('tactical_move_out_fallback',orders=fallback,
                                reason='模型没有当前可见目标，执行已授权的安全前线推进')
                    self.empty_move_out_orders=0
                else:self.empty_move_out_orders+=1
            elif p['orders']:self.empty_move_out_orders=0
            available={u.tag:u for u in own};base_map={'base_'+str(b.tag):b for b in bases}
            alerts={a['base'] for a in self.alerts};used=set();clean=[];dead_adjustments=[]
            for order in p['orders']:
                if not isinstance(order,dict) or set(order)!={'squad','mode','target','reason'}:raise ValueError('invalid order fields')
                sid=order['squad'];mode=order['mode'];target=order['target']
                if not isinstance(sid,str) or sid not in r['squads'] or sid in used:raise ValueError('unknown/duplicate squad')
                used.add(sid)
                if mode not in ('defend','attack','hold','withdraw'):raise ValueError('unknown order mode')
                if not isinstance(order['reason'],str) or not 1<=len(order['reason'])<=200:raise ValueError('order reason requires 1..200 chars')
                original=r['squads'][sid];missing=set(original)-set(available)
                if missing-set(w.dead_units):raise ValueError('squad control/observation changed; no replacement or seizure')
                tags=[tag for tag in original if tag in available]
                if not tags:raise ValueError('no observed surviving squad members')
                if missing:dead_adjustments.append(dict(squad=sid,observed_dead_tags=sorted(missing),surviving_count=len(tags)))
                base_id=None
                if isinstance(target,str):
                    if target not in base_map:raise ValueError('target base not currently observed')
                    base_id=target;point=base_map[target].pos
                else:point=tuple(validate_target(target,self.backend.size))
                if mode=='defend':
                    if base_id is not None:
                        if base_id not in alerts:raise ValueError('defense target no longer has observed/recent threat')
                    elif not any(math.dist(point,a['pos'])<=12 for a in self.combat_alerts):
                        raise ValueError('combat target no longer has observed/recent threat')
                if mode=='attack' and not self.strategy['attack_authorized']:raise ValueError('main commander has not authorized strategic attack')
                clean.append(dict(squad=sid,tags=tags,mode=mode,target=target,requested_point=point,
                    base=base_id,reason=order['reason'],request_id=r['request_id']))
            # Entire plan validated before changing any unit's assignment.
            for adjustment in dead_adjustments:self._event('tactical_squad_adjusted',**adjustment,reason='仅使用已观察存活成员，不补员')
            for order in clean:
                for tag in order['tags']:
                    self.assignments[tag]=dict(order,issued=False,arrived=False,failed=False,created_game_s=w.seconds)
            self.accepted+=1;self.last_decision=dict(p,request_id=r['request_id'],game_s=w.seconds)
            self._event('tactical_plan',request_id=r['request_id'],orders=p['orders'],reason=p['reason'])
        except (ValueError,TypeError,KeyError) as error:
            self.errors+=1;self.dirty=True;self.next_request=max(self.next_request,self.clock()+2)
            self._event('tactical_plan_rejected',request_id=r['request_id'],error=str(error),reason='未执行该回复，已有明确指令继续；无备用模型或脚本派兵')

    def _orders(self,w,own,bases):
        available={u.tag:u for u in own};base_map={'base_'+str(b.tag):b for b in bases};out=[]
        groups={}
        for tag,a in self.assignments.items():
            if tag in available:groups.setdefault((a['request_id'],a['squad']),[]).append(tag)
        for key,tags in groups.items():
            a=self.assignments[tags[0]]
            errors=[list(e) for e in w.errors if e[0] in tags and e[1] in (MOVE,ATTACK)]
            if errors and not a['failed']:
                for tag in tags:self.assignments[tag]['failed']=True
                self.dirty=True;self._event('tactical_order_failed',request_id=a['request_id'],squad=a['squad'],errors=errors,
                    reason='引擎拒绝；停止重发，交回战斗Agent重新决定')
            if a['failed']:continue
            if a['base'] and a['base'] not in base_map:
                for tag in tags:self.assignments[tag]['failed']=True
                self.dirty=True;self._event('tactical_order_failed',squad=a['squad'],reason='目标基地已不在己方观察中，未沿用旧目标');continue
            units=[available[tag] for tag in tags]
            point=a.get('actual_point',a['requested_point'])
            if a['issued']:
                if not a['arrived'] and all(u.distance(point)<=6 for u in units):
                    for tag in tags:self.assignments[tag]['arrived']=True
                    self._event('tactical_arrived',request_id=a['request_id'],squad=a['squad'],base=a['base'],point=list(point),
                        reason='观察到小队到达，不代表敌军已被击退')
                continue
            try:
                if any(not u.flying for u in units):point=ground_destination(w,self.tasks,units,point)
                ability=MOVE if a['mode']=='withdraw' else ATTACK
                query=getattr(self.backend,'available_abilities',None)
                tree=getattr(self.backend,'tech_tree',None)
                if not callable(query) or tree is None:raise ValueError('engine ability query/catalogue unavailable')
                abilities=query(self.index,tags)
                if any(u.type_id!=32 and not tree.ability_available(ability,abilities.get(u.tag,set())) for u in units):
                    raise ValueError('movement ability unavailable; no fabricated dispatch')
                out.append(command(ability,tags,pos=point))
                self.action_contexts.append(dict(source='army_tactics',mode=a['mode'],task_id=None,
                    request_id=a['request_id'],squad=a['squad'],base=a['base'],decision_source='battle_llm'))
                for tag in tags:self.assignments[tag].update(issued=True,actual_point=point)
                trace=self.request_trace.setdefault(a['request_id'],{})
                trace.setdefault('first_order_wall',self.clock());trace.setdefault('first_order_game_s',w.seconds)
                self._event('tactical_orders_issued',request_id=a['request_id'],squad=a['squad'],mode=a['mode'],base=a['base'],
                    unit_tags=tags,requested_point=list(a['requested_point']),actual_point=list(point),reason=a['reason'],confirmed=False)
            except Exception as error:
                for tag in tags:self.assignments[tag]['failed']=True
                self.errors+=1;self.dirty=True
                self._event('tactical_order_failed',request_id=a['request_id'],squad=a['squad'],error=type(error).__name__+': '+str(error),
                    reason='命令未发出；等待模型重新选择，不改派主矿或自动撤退')
        return out

    def tick(self,w,protected=()):
        self.action_contexts=[]
        if w.game_loop==self.last_loop:return []
        self.last_loop=w.game_loop;self.game_s=w.seconds
        own=[u for u in w.my_units if u.tag not in protected and self._eligible(u)]
        self.tags={u.tag for u in own}
        for tag in set(self.assignments)-self.tags:
            self._event('tactical_unit_released',unit_tag=tag,reason='单位消失或其他任务接管；没有擅自补员')
            del self.assignments[tag];self.dirty=True
        bases=[u for u in w.my_units if u.type_id in TOWNHALLS and not u.flying]
        self._scan(w,bases)
        shape=(tuple(sorted(self.tags)),tuple(sorted(b.tag for b in bases)))
        if shape!=self.last_shape:self.dirty=True;self.last_shape=shape
        self._receive(w,own,bases)
        actions=self._orders(w,own,bases)
        self.fighting={tag for tag,a in self.assignments.items() if a['mode'] in ('defend','attack') and not a['failed']}
        self._request(w,own,bases)
        return actions

    def _assignment_rows(self):
        rows={}
        for tag,a in self.assignments.items():
            key=(a['request_id'],a['squad'])
            row=rows.setdefault(key,dict(squad=a['squad'],request_id=a['request_id'],mode=a['mode'],target=a['target'],
                count=0,issued=a['issued'],arrived=a['arrived'],failed=a['failed']))
            row['count']+=1
        return list(rows.values())[:16]

    # Per-request wall-clock trace is diagnostic only: nothing reads it from the model payload
    # (the launcher reads request_status), yet it was 2,289 characters of every battle prompt.
    # Kept on the instance and in the audit journal, excluded from public().
    PUBLIC_KEYS = ('enabled','kind','planner_enabled','mode','objective','min_group','unit_tags','fighting_tags',
                   'rally','thinking','requests','accepted','errors','expired','last_decision','strategic_order',
                   'base_alerts','combat_alerts','assignments','recent_feedback','prompt_chars',
                   'shared_prefix_chars','ownership','safety')

    # Give the model the newest outcomes only; older ones are in the audit journal.
    PUBLIC_FEEDBACK_ITEMS = 6

    def public(self):
        return dict(enabled=True,kind='independent_battle_llm',planner_enabled=True,mode=self.mode,
            objective=self.objective,min_group=0,unit_tags=sorted(self.tags),fighting_tags=sorted(self.fighting),rally=None,
            thinking=self.thinking,requests=self.requests,accepted=self.accepted,errors=self.errors,expired=self.expired,
            last_decision=self.last_decision,strategic_order=self.strategy,base_alerts=self.alerts,
            combat_alerts=self.combat_alerts,
            assignments=self._assignment_rows(),recent_feedback=list(self.recent)[-self.PUBLIC_FEEDBACK_ITEMS:],
            prompt_chars=len(self.request_bodies[-1]) if self.request_bodies else 0,
            shared_prefix_chars=self.cache_prefix_chars(),
            ownership='Unassigned army only; custom/economic/transport tasks excluded',
            safety='LLM decides support/attack/withdraw; no global auto-retreat, assembly detour or scripted reinforcement')

    def close(self):
        self.closed.set();self.worker.join(timeout=.3)
