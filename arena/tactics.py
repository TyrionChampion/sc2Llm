"""Persistent army ownership, safe reinforcement batches and an optional fast planner.

Only player-local observations are used. Custom workflows always retain ownership.
The model chooses intent; bounded local execution continues while it is thinking.
"""
import json
import math
import queue
import threading
import time
from collections import Counter

from arena.raw_engine import ATTACK, MOVE, command
from arena.tech_tree import TOWNHALLS, WORKERS
from arena.combat_spells import SUPPORT_TYPES, DEPLOYED_FORMS

SYSTEM = '''你是独立战术指挥官。运营指挥负责经济、科技、兵种和生产，你只负责未被自定义技能占用的部队。
只输出JSON：{"mode":"assemble|attack|hold|withdraw","objective":"enemy_start|visible_enemy|defend_base","min_group":6,"reason":"简短中文说明"}。
min_group为4到24的整数。先在安全区域集结成组，避免新兵逐个送进敌军火力；根据空地攻击能力、血量和局部敌我力量决定进退。
attack攻击指定目标；defend_base只保卫受威胁基地，不追到敌方出生点。没有可见敌人不代表敌人死亡。
你的命令持续生效，新兵分批集结增援。执行层在等待回复时处理危险路线、接敌和紧急撤退。
当前安全检查可能暂缓进攻。不得输出生产任务、坐标、单位tag或控制自定义技能的单位。'''


class ArmyTactics:
    min_group = 6
    max_age = 6

    def __init__(self, backend, index, rally, event, audit, client=None, clock=time.monotonic,combat_micro=False):
        self.backend=backend; self.index=index; self.rally=rally
        self.event=event; self.audit=audit; self.client=client; self.clock=clock;self.combat_micro=combat_micro
        self.mode='assemble'; self.objective='defend_base'; self.generation=0
        self.tags=set(); self.fighting=set(); self.memory={}; self.last_orders={}
        self.anchor=None; self.reason='等待安全集结'; self.next_scan=0; self.last_loop=-1
        self.withdraw_until=0; self.next_request=0; self.thinking=False
        self.requests=0; self.accepted=0; self.errors=0; self.expired=0
        self.last_decision=None; self.action_contexts=[]
        self.last_state=None; self.navigation={}; self.risk_cache={}; self.threat_cells={};self.closed=threading.Event()
        self.inbox=queue.Queue(maxsize=1); self.outbox=queue.Queue(maxsize=1)
        self.worker=None
        if client is not None:
            self.worker=threading.Thread(target=self._work,daemon=True,name='tactical-planner')
            self.worker.start()

    def mission(self, skill):
        if skill=='defend':
            self.mode='assemble'; self.objective='defend_base'
        else:
            self.mode='attack'; self.objective='visible_enemy' if skill=='attack_nearest' else 'enemy_start'
        self.generation+=1; self.next_request=0
        self.event('tactical_mission',mode=self.mode,objective=self.objective,
                   handling='Persistent mission; new units assemble in reinforcement batches.')

    def public(self):
        return dict(enabled=True,planner_enabled=self.client is not None,mode=self.mode,
                    objective=self.objective,min_group=self.min_group,unit_tags=sorted(self.tags),
                    fighting_tags=sorted(self.fighting),rally=list(self.anchor) if self.anchor else None,
                    reason=self.reason,thinking=self.thinking,requests=self.requests,
                    accepted=self.accepted,errors=self.errors,expired=self.expired,last_decision=self.last_decision,
                    ownership='Unassigned combat units only; workers, transports, custom skills and morph owners excluded',
                    safety='Known threats only; safe endpoints and static routes do not prove fog is empty')

    def _work(self):
        while not self.closed.is_set():
            try:item=self.inbox.get(timeout=.2)
            except queue.Empty:continue
            answer=None; error=None
            try:answer=self.client.chat(SYSTEM,item['prompt'])
            except Exception as exc:error=type(exc).__name__+': '+str(exc)
            if not self.closed.is_set():
                self.outbox.put(dict(item,response=answer,error=error,
                                     usage=getattr(self.client,'last_usage',None)))

    def _eligible(self,u):
        meta=getattr(self.backend,'types',{}).get(u.type_id)
        name=getattr(meta,'name','').lower()
        return (u.done and not u.is_building and not u.hallucination and u.type_id not in WORKERS
                and not u.cargo_taken and not u.passengers and (u.ground_dps>0 or u.air_dps>0 or self.combat_micro and u.type_id in SUPPORT_TYPES)
                and (u.type_id!=32 or self.combat_micro)
                and (self.combat_micro and u.type_id in DEPLOYED_FORMS or u.type_id in (32,33) or ('burrowed' not in name and
                     (meta is None or getattr(meta,'movement_speed',0)>0))))

    def _remember(self,w):
        self.risk_cache.clear()
        for u in w.enemy_units:
            if u.ground_dps>0 or u.air_dps>0:self.memory[u.tag]=(u,w.seconds)
        for tag,(u,seen) in list(self.memory.items()):
            if tag in w.dead_units or w.seconds-seen>(30 if u.is_building or u.type_id==32 else 12):
                del self.memory[tag]
        self.threat_cells={}
        for enemy,_ in self.memory.values():
            radius=max(9,enemy.attack_range+enemy.radius+3)
            for flying,dps in ((False,enemy.ground_dps),(True,enemy.air_dps)):
                if dps<=0:continue
                for x in range(math.floor((enemy.x-radius)/16),math.floor((enemy.x+radius)/16)+1):
                    for y in range(math.floor((enemy.y-radius)/16),math.floor((enemy.y+radius)/16)+1):
                        self.threat_cells.setdefault((x,y,flying),[]).append(enemy)

    def _hazards(self,pos,flying):
        return self.threat_cells.get((math.floor(pos[0]/16),math.floor(pos[1]/16),flying),())

    def _risk(self,pos,flying=False):
        key=(round(pos[0],1),round(pos[1],1),flying)
        if key in self.risk_cache:return self.risk_cache[key]
        risk=sum((u.air_dps if flying else u.ground_dps)
                   for u in self._hazards(pos,flying)
                   if u.distance(pos)<max(9,u.attack_range+u.radius+3))
        self.risk_cache[key]=risk
        return risk

    def _route(self,start,end,flying=False):
        key=(tuple(round(x) for x in start),tuple(round(x,1) for x in end),flying)
        if key in self.navigation:return self.navigation[key]
        if flying:
            count=max(1,math.ceil(math.dist(start,end)/2))
            points=[(start[0]+(end[0]-start[0])*i/count,start[1]+(end[1]-start[1])*i/count) for i in range(count+1)]
        elif self.rally.grid is None:return None
        else:
            try:
                count=max(1,math.ceil(math.dist(start,end)*2))
                direct=[(start[0]+(end[0]-start[0])*i/count,start[1]+(end[1]-start[1])*i/count) for i in range(count+1)]
                cells=[(int(x),int(y)) for x,y in direct]
                clear=all(self.rally.grid.free(x,y) for x,y in cells)
                clear=clear and all(self.rally.grid.free(a[0],b[1]) and self.rally.grid.free(b[0],a[1]) for a,b in zip(cells,cells[1:]))
                if clear:points=direct
                else:
                    path=self.rally.grid.route(start,end)
                    points=[(x+.5,y+.5) for x,y in path]
                    if len(getattr(self.rally.grid,'cache',{}))>1024:self.rally.grid.cache.clear()
            except ValueError:return None
        if len(self.navigation)>512:self.navigation.clear()
        self.navigation[key]=points
        return points

    def _safe_route(self,start,end,flying=False,escape=False):
        route=self._route(start,end,flying)
        if not route:return False
        # Escape may leave an existing threat but may not enter additional fire.
        initial=self._risk(start,flying) if escape else 0
        exposed={u.tag:u.distance(start) for u in self._hazards(start,flying)
                 if (u.air_dps if flying else u.ground_dps)>0 and u.distance(start)<max(9,u.attack_range+u.radius+3)}
        escaped=initial==0
        for point in route:
            risk=self._risk(point,flying)
            if risk==0:escaped=True
            if risk>(0 if escaped else initial):return False
            if risk and escape:
                for enemy in self._hazards(point,flying):
                    if (enemy.air_dps if flying else enemy.ground_dps)<=0:continue
                    distance=enemy.distance(point)
                    if distance<max(9,enemy.attack_range+enemy.radius+3):
                        if enemy.tag not in exposed or distance<exposed[enemy.tag]-.75:return False
        return self._risk(end,flying)==0

    def _choose_anchor(self,w,bases,own):
        if self.rally.grid is None:return None
        home=self.backend.homes[self.index]
        candidates=[]
        if self.anchor and any(b.distance(self.anchor)<24 for b in bases):candidates.append(self.anchor)
        for b in sorted(bases,key=lambda b:b.distance(home)):
            # Include rear and lateral positions, not just the threatened entrance.
            for radius in (8,14):
                for n in range(8):
                    p=(b.x+radius*math.cos(n*math.pi/4),b.y+radius*math.sin(n*math.pi/4))
                    if not self.rally.grid.free(int(p[0]),int(p[1])):continue
                    if any((u.is_building or u.mineral or u.geyser) and u.distance(p)<u.radius+2 for u in w.units):continue
                    candidates.append(p)
        safe=[p for p in candidates if not self._risk(p) and not self._risk(p,True)]
        safe.sort(key=lambda p:(0 if self.anchor and math.dist(p,self.anchor)<1 else 1,math.dist(p,home)))
        # Bound A* work; never route every unit to every candidate on the realtime thread.
        safe=safe[:12]
        # A reachable safe base route is mandatory; producer-specific routes are checked on units too.
        safe=[p for p in safe if any(self._safe_route(b.pos,p,escape=True) for b in bases)]
        if not safe:return None
        query=getattr(self.backend,'path_distances',None)
        if not callable(query):return None
        for p in safe[:8]:
            base=min(bases,key=lambda b:b.distance(p))
            try:reachable=query(self.index,base.pos,[p])
            except Exception as error:
                self.event('tactical_path_error',error=str(error));return None
            if reachable and (reachable[0]>0 or base.distance(p)<1):return p
        return None

    @staticmethod
    def _power(units,opponents):
        return sum(max((u.air_dps if e.flying else u.ground_dps for e in opponents),default=0)
                   *max(0,u.health)/max(1,u.health_max) for u in units)

    def _signature(self,w,own):
        return (tuple(sorted((u.tag,u.type_id,int(4*u.health/max(1,u.health_max))) for u in own)),
                tuple(sorted((e.tag,int(e.x/4),int(e.y/4),int(e.health/25)) for e in w.enemy_units)),
                tuple(sorted(u.tag for u in w.my_units if u.type_id in TOWNHALLS)))

    def _receive(self,w,signature):
        try:r=self.outbox.get_nowait()
        except queue.Empty:return
        self.thinking=False
        self.audit(dict(record='tactical_request_finished',request_id=r.get('request_id'),game_s=w.seconds,response=r['response'],error=r['error'],
                        latency_s=self.clock()-r['wall'],usage=r.get('usage')))
        if (r['generation']!=self.generation or r['signature']!=signature or
            w.seconds-r['game_s']>self.max_age or self.clock()-r['wall']>self.max_age):
            self.expired+=1
            self.event('tactical_plan_expired',request_id=r.get('request_id'),reason='战况、控制权或任务已变化，或回复超过6秒有效期')
            return
        try:
            if r['error']:raise ValueError(r['error'])
            p=json.loads(r['response'])
            if not isinstance(p,dict) or set(p)!={'mode','objective','min_group','reason'}:raise ValueError('invalid tactical fields')
            if p['mode'] not in ('assemble','attack','hold','withdraw'):raise ValueError('invalid mode')
            if p['objective'] not in ('enemy_start','visible_enemy','defend_base'):raise ValueError('invalid objective')
            if type(p['min_group']) is not int or not 4<=p['min_group']<=24:raise ValueError('invalid group size')
            if not isinstance(p['reason'],str) or not 1<=len(p['reason'])<=240:raise ValueError('invalid reason')
            self.mode=p['mode'];self.objective=p['objective'];self.min_group=p['min_group']
            self.last_decision=p;self.accepted+=1
            if self.mode!='attack':self.fighting.clear()
            self.event('tactical_plan',**p)
        except (ValueError,TypeError) as error:
            self.errors+=1;self.event('tactical_plan_rejected',error=str(error))

    def _request(self,w,own,bases,signature):
        now=self.clock()
        if self.client is None or self.thinking or not own or now<self.next_request:return
        def summary(units):
            return dict(count=len(units),types=dict(Counter(str(u.type_id) for u in units)),
                        units=[dict(type=u.type_id,name=getattr(getattr(self.backend,'types',{}).get(u.type_id),'name',str(u.type_id)),
                                    pos=[round(u.x,1),round(u.y,1)],hp=round(u.health,1),max_hp=u.health_max,
                                    ground=u.ground_dps,air=u.air_dps,range=u.attack_range,flying=u.flying)
                               for u in units[:32]],omitted=max(0,len(units)-32))
        mission={k:v for k,v in self.public().items() if k not in ('unit_tags','fighting_tags')}
        mission.update(army_count=len(self.tags),fighting_count=len(self.fighting))
        enemies=sorted(w.enemy_units,key=lambda e:min((e.distance(u) for u in own),default=0))
        payload=dict(game_s=w.seconds,mission=mission,army=summary(own),
                     visible_enemies=summary(enemies),bases=[list(b.pos) for b in bases],
                     known_threats=len(self.memory),control='Only army contains controllable units.')
        item=dict(request_id=self.requests+1,prompt=json.dumps(payload,ensure_ascii=False,separators=(',',':')),signature=signature,
                  generation=self.generation,game_s=w.seconds,wall=now)
        self.audit(dict(record='tactical_request_queued',request_id=self.requests+1,prompt=payload))
        self.inbox.put_nowait(item);self.thinking=True;self.requests+=1;self.next_request=now+3

    def tick(self,w,protected=()):
        self.action_contexts=[]
        if w.game_loop==self.last_loop:return []
        self.last_loop=w.game_loop
        own=[u for u in w.my_units if u.tag not in protected and self._eligible(u)]
        previous=set(self.tags);self.tags={u.tag for u in own};self.fighting.intersection_update(self.tags)
        self.last_orders={t:v for t,v in self.last_orders.items() if t in self.tags}
        for tag in self.tags-previous:self.last_orders.pop(tag,None)
        self._remember(w)
        bases=[u for u in w.my_units if u.type_id in TOWNHALLS and u.done and not u.flying]
        signature=self._signature(w,own);self._receive(w,signature)
        if w.seconds>=self.next_scan or (self.anchor and (self._risk(self.anchor) or self._risk(self.anchor,True))):
            self.anchor=self._choose_anchor(w,bases,own) if bases else None
            self.next_scan=w.seconds+2
        self._request(w,own,bases,signature)
        nearby=[e for e in w.enemy_units if (e.ground_dps or e.air_dps) and
                any(u.distance(e)<max(18,e.attack_range+4) for u in own)]
        engaged=[u for u in own if any(u.distance(e)<22 for e in nearby)]
        frontline=sum(u.tag in self.fighting for u in engaged)
        weaker=bool(nearby and (not self.fighting or frontline>=max(1,len(self.fighting)*.4))
                    and self._power(nearby,engaged)>1.25*self._power(engaged,nearby))
        if self.fighting and len(self.fighting)<max(2,self.min_group//2):
            self.fighting.clear();self.withdraw_until=w.seconds+4
        if weaker:
            self.withdraw_until=w.seconds+4;self.fighting.clear()
        withdrawing=self.mode=='withdraw' or w.seconds<self.withdraw_until
        if not self.anchor:self.reason='没有可确认的安全集结路线，停止旧移动指令'
        elif withdrawing:self.reason='撤往安全集结区，等待兵力恢复'
        else:self.reason='新兵在安全区成组；已分配部队执行持续任务'
        ready=[u for u in own if self.anchor and u.distance(self.anchor)<=7 and u.health>=.4*u.health_max]
        threat=[e for e in w.enemy_units if any(e.distance(b)<26 for b in bases)]
        objective=None
        if self.objective=='enemy_start':objective=self.backend.enemy_start(self.index)
        elif self.objective=='visible_enemy' and w.enemy_units:
            objective=min(w.enemy_units,key=lambda e:e.distance(self.anchor or self.backend.homes[self.index])).pos
        elif self.objective=='defend_base' and threat:
            objective=min(threat,key=lambda e:e.distance(self.anchor or self.backend.homes[self.index])).pos
        # While waiting for a planner, a formed group can protect its own threatened base.
        local_defend=self.mode=='assemble' and bool(threat)
        if local_defend:objective=min(threat,key=lambda e:e.distance(self.anchor or self.backend.homes[self.index])).pos
        if self.anchor and not withdrawing and (self.mode=='attack' or local_defend) and objective:
            reinforcements=[u for u in ready if u.tag not in self.fighting]
            if len(reinforcements)>=self.min_group:
                self.fighting.update(u.tag for u in reinforcements)
        if self.mode in ('hold','withdraw') or objective is None:self.fighting.clear()
        out=[]
        for u in own:
            ability=ATTACK;point=self.anchor
            if not self.anchor:ability=4;point=None
            elif withdrawing or u.health<.25*u.health_max:
                ability=MOVE
                if not self._safe_route(u.pos,point,u.flying,escape=True):ability=4;point=None
            elif u.tag in self.fighting and objective:
                point=objective
            elif not self._safe_route(u.pos,point,u.flying,escape=True):
                ability=4;point=None  # Stop the native production rally; allow native retaliation.
            elif self._risk(u.pos,u.flying):ability=MOVE
            elif self.mode=='hold' and u.distance(point)<3:ability=18;point=None
            key=(ability,tuple(round(x,1) for x in point) if point else None)
            old=self.last_orders.get(u.tag)
            # Never spam attack orders each frame; refresh on intent change, rejection or idle.
            rejected=any(tag==u.tag for tag,_,_ in w.errors)
            if old and old[0]==key and not rejected and (u.orders or w.seconds-old[1]<2):continue
            self.last_orders[u.tag]=(key,w.seconds)
            out.append(command(ability,[u.tag],pos=point))
            self.action_contexts.append(dict(source='army_tactics',mode='withdraw' if ability==MOVE else self.mode,
                                            task_id=None,unit_tag=u.tag))
        state=(self.reason,tuple(self.anchor) if self.anchor else None)
        if state!=self.last_state:
            self.last_state=state;self.event('tactical_state',reason=self.reason,rally=state[1])
        return out

    def close(self):
        self.closed.set()
        if self.worker:self.worker.join(timeout=.3)
