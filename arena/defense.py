"""Independent, bounded base defense. All observations are player-local raw snapshots."""
import json
import math
import queue
import threading
import time
from collections import Counter, deque
from arena.raw_engine import ATTACK, MOVE, NAMES, command
from arena.tech_tree import TOWNHALLS, WORKERS

SYSTEM = """你是独立防守副官，只负责本方基地附近已分配的小队。主指挥官管理经济、科技和进攻。
只返回JSON：{"mode":"defend|hold|withdraw","base_tag":整数,"reason":"简短中文说明"}。
base_tag必须来自bases。defend在该基地附近接战；hold守住基地内侧；withdraw向基地后方撤离。
不下经济、生产、侦察或全军命令。没有看到敌人不等于敌人死亡，不追入雾区。
综合敌人攻击能力、附近友军、血量和空地攻击能力，不把无武器建筑价格当战斗力。
等待你的回复时已授权的基地守备脚本仍在运行。不要因单个伤兵就反复全队撤退。
你的回复会按新观察复核，过期或已解除警报时不执行。"""

class DefenseCoordinator:
    radius = 26
    clear_after = 6
    max_units = 32
    max_age = 6

    def __init__(self, client, tasks, event, audit, clock=time.monotonic):
        self.client=client; self.combat_micro_enabled=False; self.tasks=tasks; self.event=event; self.audit=audit; self.clock=clock
        self.generation=0; self.active=False; self.tags=set(); self.handback=False
        self.bases=[]; self.enemies=[]; self.damaged=[]; self.health={}
        self.last_threat=-1e9; self.last_loop=-1; self.mode="defend"; self.base_tag=None
        self.mode_since=-1e9; self.next_order=0; self.last_orders={}
        self.last_sent=-1e9; self.last_wall=-1e9; self.retry_wall=0; self.signature=None
        self.requests=0; self.accepted=0; self.errors=0; self.expired=0
        self.thinking=False; self.last_decision=None; self.last_error=None; self.first_order_s=None
        self.started_s=None; self.no_units_reported=False; self.recent=deque(maxlen=4); self.action_contexts=[]
        self.inbox=queue.Queue(maxsize=1); self.outbox=queue.Queue(maxsize=1)
        self.closed=threading.Event()
        self.worker=threading.Thread(target=self._work,daemon=True,name="defense-planner")
        self.worker.start()

    def _work(self):
        while not self.closed.is_set():
            try: item=self.inbox.get(timeout=.2)
            except queue.Empty: continue
            start=self.clock(); answer=None; error=None
            try: answer=self.client.chat(SYSTEM,item["prompt"])
            except Exception as exc: error=type(exc).__name__+": "+str(exc)
            result=dict(item,response=answer,error=error,latency_s=self.clock()-start,
                        usage=getattr(self.client,"last_usage",None))
            if not self.closed.is_set(): self.outbox.put_nowait(result)

    def _event(self,kind,**details):
        row=dict(kind=kind,**details)
        self.recent.append(row); self.event(kind,**details)

    def _scan(self,w):
        self.bases=[u for u in w.my_units if u.type_id in TOWNHALLS and not u.flying]
        # No made-up home base after losing town halls.
        self.bases=sorted(self.bases,key=lambda u:u.tag)
        self.enemies=[u for u in w.enemy_units if any(u.distance(b)<=self.radius for b in self.bases)
                      and (u.ground_dps>0 or u.air_dps>0 or u.type_id in (54,56,81,82,129,126,499,111,77,75))]
        self.damaged=[u for u in w.my_units if (u.is_building or u.type_id in WORKERS)
                      and self.health.get(u.tag,u.health)>u.health
                      and any(u.distance(b)<=self.radius for b in self.bases)]
        self.health={u.tag:u.health for u in w.my_units if u.is_building or u.type_id in WORKERS}
        return bool(self.enemies or self.damaged)

    def _target_base(self):
        candidates=self.enemies or self.damaged
        current=next((b for b in self.bases if b.tag==self.base_tag),None)
        if current is not None and (not candidates or any(u.distance(current)<=self.radius for u in candidates)):
            return current
        return min(self.bases,key=lambda b:min((u.distance(b) for u in candidates),default=0))

    def _mobile(self,u):
        from arena.combat_spells import DEPLOYED_FORMS
        if self.combat_micro_enabled and u.type_id in DEPLOYED_FORMS:return True
        meta=getattr(self.tasks.backend,'types',{}).get(u.type_id)
        name=(getattr(meta,'name',None) or NAMES.get(u.type_id,'')).lower()
        return (u.type_id!=32 and not any(form in name for form in ('burrowed','sieged','liberatorag'))
                and (meta is None or getattr(meta,'movement_speed',0)>0))

    def _choose(self,w):
        from arena.combat_spells import SUPPORT_TYPES
        from arena.python_skills import PythonSkillRun
        python={t.id:t for t in self.tasks.tasks if t.active and isinstance(t.program,PythonSkillRun)}
        reclaim={tag for t in python.values() for tag in t.program.tags}
        forbidden=self.tasks.protected()-reclaim-self.tags
        candidates=[u for u in w.my_units if u.done and not u.is_building and not u.hallucination
                    and u.type_id not in WORKERS and u.tag not in forbidden and self._mobile(u)
                    and (u.ground_dps>0 or u.air_dps>0 or self.combat_micro_enabled and u.type_id in SUPPORT_TYPES)
                    and any(u.distance(b)<=45 for b in self.bases)]
        front=self._target_base()
        candidates.sort(key=lambda u:(u.tag in reclaim,u.distance(front),u.tag))
        target=min(12,max(4,len(self.enemies)*2))
        for u in candidates:
            if len(self.tags)>=target: break
            group={u.tag}
            for t in python.values():
                if u.tag in t.program.tags: group.update(t.program.tags)
            # Take whole Python groups, never a worker/building/morph/transport mission.
            entities=[w.by_tag(tag) for tag in group]
            if any(e is None or e.is_building or e.type_id in WORKERS or e.alliance!=1 or not self._mobile(e) for e in entities): continue
            if len(self.tags|group)>self.max_units: continue
            self.tags.update(group)
        if not self.tags and not self.no_units_reported:
            self.no_units_reported=True
            self._event("defense_no_units",generation=self.generation,
                        reason="基地受到威胁，但附近没有符合分配条件的机动部队；副官未请求 API，等待主指挥增援。",
                        handling="Workers and protected/immobile workflows are not silently seized.")
        elif self.tags:self.no_units_reported=False
        if self.tags!=self.tasks.defense_tags:
            self.tasks.take_defense(self.tags,w.seconds)
            self._event("defense_units_assigned",generation=self.generation,unit_tags=sorted(self.tags),
                        handling="Whole conflicting Python missions paused; economic workers and other workflows excluded.")

    def _payload(self,w):
        def encode(u):
            meta=getattr(self.tasks.backend,'types',{}).get(u.type_id)
            return dict(tag=u.tag,type=u.type_id,name=getattr(meta,'name',None) or NAMES.get(u.type_id,'unknown_type_'+str(u.type_id)),pos=[round(u.x,1),round(u.y,1)],hp=round(u.health),
                        max_hp=round(u.health_max),buffs=list(u.buffs),ground=round(u.ground_dps,1),air=round(u.air_dps,1),
                        range=round(u.attack_range,1),flying=u.flying)
        own=[u for u in w.my_units if u.tag in self.tags]
        allies=[u for u in w.my_units if u.tag not in self.tags and u.type_id not in WORKERS
                and (u.ground_dps>0 or u.air_dps>0) and any(u.distance(b)<=self.radius for b in self.bases)]
        enemies=sorted(self.enemies,key=lambda u:(min(u.distance(b) for b in self.bases),u.tag))
        return dict(game_s=round(w.seconds,2),stim_researched=15 in w.upgrades,
                    combat_abilities={'enabled':self.combat_micro_enabled,'handling':'Authorized squad uses three-race local combat abilities; decide defend/hold/withdraw only' if self.combat_micro_enabled else 'No automatic combat ability executor'},generation=self.generation,mode=self.mode,
                    bases=[encode(b) for b in self.bases[:8]],squad=[encode(u) for u in own[:32]],
                    enemies=[encode(u) for u in enemies[:16]],allied_support=[encode(u) for u in allies[:8]],
                    omitted=dict(bases=max(0,len(self.bases)-8),enemies=max(0,len(enemies)-16),allies=max(0,len(allies)-8)),
                    enemy_counts=dict(Counter(str(u.type_id) for u in enemies).most_common(32)),
                    omitted_enemy_types=max(0,len({u.type_id for u in enemies})-32),
                    damaged_tags=[u.tag for u in self.damaged[:8]],last_error=self.last_error,
                    engine_errors=[list(e) for e in w.errors if e[0] in self.tags][:4],
                    current_visible_only=True,previous_reason=(self.last_decision or {}).get("reason"))

    def _receive(self,w):
        try: result=self.outbox.get_nowait()
        except queue.Empty:return
        self.thinking=False; error=result["error"]; plan=None
        self.audit(dict(record="defense_request_finished",request_id=result["id"],
                        game_s=w.seconds,response=result["response"],error=error,
                        latency_s=result["latency_s"],usage=result["usage"]))
        try:
            if error: raise ValueError(error)
            if (not self.active or result["generation"]!=self.generation
                or w.seconds-result["game_s"]>self.max_age or self.clock()-result["wall"]>self.max_age):
                self.expired+=1
                self._event("defense_plan_expired",request_id=result["id"],generation=self.generation,
                            age_game_s=w.seconds-result["game_s"],handling="Ignored; current local guard remains authoritative.")
                return
            plan=json.loads(result["response"])
            if not isinstance(plan,dict) or set(plan)!={"mode","base_tag","reason"}:raise ValueError("expected mode/base_tag/reason only")
            if plan["mode"] not in ("defend","hold","withdraw"):raise ValueError("unknown defense mode")
            if type(plan["base_tag"]) is not int or plan["base_tag"] not in result["base_tags"]:raise ValueError("base_tag not in request")
            if not isinstance(plan["reason"],str) or not 1<=len(plan["reason"])<=240:raise ValueError("reason must be 1..240 characters")
            base=next((b for b in self.bases if b.tag==plan["base_tag"]),None)
            if base is None:raise ValueError("base no longer observed")
            if self.enemies and not any(u.distance(base)<=self.radius for u in self.enemies):
                raise ValueError("selected base has no current threat; choose an attacked base")
            if self.mode=="withdraw" and plan["mode"]!="withdraw" and w.seconds-self.mode_since<4:
                self._event("defense_mode_deferred",request_id=result["id"],reason="4-second withdrawal dwell prevents oscillation")
                return
            if self.mode!=plan["mode"]:self.mode_since=w.seconds
            self.mode=plan["mode"]; self.base_tag=base.tag
            self.accepted+=1; self.last_error=None
            self.last_decision=dict(plan,game_s=w.seconds,request_id=result["id"])
            self._event("defense_plan_accepted",**self.last_decision)
        except (ValueError,TypeError,KeyError) as exc:
            self.errors+=1; self.last_error=str(exc)[:600];self.retry_wall=self.clock()+6
            self._event("defense_request_failed",request_id=result["id"],error=self.last_error,
                        handling="No alternate model. Explicit standing base guard continues; retry backoff 6 wall seconds.")

    def _orders(self,w):
        if not self.tags or w.seconds<self.next_order:return []
        self.next_order=w.seconds+.5
        base=next((b for b in self.bases if b.tag==self.base_tag),None)
        if base is None or (self.damaged and not any(u.distance(base)<=self.radius for u in self.damaged)) or (self.enemies and not any(e.distance(base)<=self.radius for e in self.enemies)):
            base=self._target_base()
            self.base_tag=base.tag
        local=[e for e in self.enemies if e.distance(base)<=self.radius]
        threat=min(local,key=lambda e:e.distance(base)) if local else None
        if threat:
            dx=base.x-threat.x;dy=base.y-threat.y;length=math.hypot(dx,dy) or 1
            rear=(base.x+dx/length*7,base.y+dy/length*7)
        else: rear=base.pos
        rear=tuple(max(2,min(self.tasks.backend.size[i]-2,rear[i])) for i in (0,1))
        output=[]
        for u in w.my_units:
            if u.tag not in self.tags or not self._mobile(u):continue
            capable=[e for e in local if u.can_attack(e)]
            # Hold/withdraw and unsupported weapon types never chase an invulnerable target.
            if self.mode=="defend" and capable:
                pos=min(capable,key=lambda e:e.distance(base)).pos;ability=ATTACK
            else:
                pos=rear;ability=MOVE
                if u.distance(pos)<3:
                    # Clear old attack-move once upon reaching guard position.
                    ability=4;pos=None
            signature=(ability,tuple(round(x,0) for x in pos) if pos else None)
            previous=self.last_orders.get(u.tag)
            if previous and previous[0]==signature and w.seconds-previous[1]<2:continue
            if ability==4 and not u.orders:continue
            output.append(command(ability,[u.tag],pos=pos))
            self.action_contexts.append(dict(source="defense_guard",generation=self.generation,
                mode=self.mode,task_id=None,observed_game_loop=w.game_loop))
            self.last_orders[u.tag]=(signature,w.seconds)
        if output and self.first_order_s is None:
            self.first_order_s=w.seconds
            self._event("defense_first_order",generation=self.generation,game_s=w.seconds,
                        alert_to_dispatch_game_s=w.seconds-self.started_s,confirmed=False)
        return output

    def tick(self,w):
        self.action_contexts=[]
        if w.game_loop==self.last_loop:return []
        self.last_loop=w.game_loop
        if self.handback:
            self.tasks.release_defense(w);self.tags.clear();self.handback=False
        threat=self._scan(w)
        if threat:
            self.last_threat=w.seconds
            if not self.active:
                self.active=True;self.generation+=1;self.started_s=w.seconds;self.first_order_s=None;self.no_units_reported=False
                self.mode="defend";self.mode_since=w.seconds;self.last_orders.clear();self.next_order=0
                self._event("defense_alert",generation=self.generation,bases=[b.tag for b in self.bases],
                            visible_enemies=len(self.enemies),damaged_tags=[u.tag for u in self.damaged],
                            standing_order="Reserve local squad and defend bases while independent LLM thinks.")
        if self.active and (not self.bases or w.seconds-self.last_threat>=self.clear_after):
            self.active=False;self.generation+=1;self.handback=True
            self._event("defense_released",generation=self.generation,unit_tags=sorted(self.tags),
                        reason="No observed threat for 6 game seconds, or no grounded town halls. Disappearance is not a kill.",
                        handling="Stop pursuit now; resume surviving Python missions from fresh observation next tick.")
            self._receive(w)
            tags=[u.tag for u in w.my_units if u.tag in self.tags]
            if tags:
                self.action_contexts=[dict(source="defense_release",generation=self.generation,task_id=None)]
                return [command(4,tags)]
            return []
        self._receive(w)
        if not self.active:return []
        self.tags.intersection_update(u.tag for u in w.my_units)
        self._choose(w)
        output=self._orders(w)
        payload=self._payload(w)
        # Only meaningful changes refresh within a prolonged fight; heartbeat is capped at 12s.
        signature=json.dumps({k:v for k,v in payload.items() if k not in ("game_s","previous_reason")},sort_keys=True)
        wall=self.clock()
        if (self.tags and not self.thinking and wall>=self.retry_wall and wall-self.last_wall>=2
            and w.seconds-self.last_sent>=3 and (signature!=self.signature or w.seconds-self.last_sent>=12)):
            self.requests+=1
            item=dict(id=self.requests,generation=self.generation,game_s=w.seconds,wall=wall,
                      base_tags=[b["tag"] for b in payload["bases"]],
                      prompt=json.dumps(payload,ensure_ascii=False,separators=(",",":")))
            self.audit(dict(record="defense_request_queued",request_id=self.requests,prompt=payload,system=SYSTEM))
            self.inbox.put_nowait(item);self.thinking=True
            self.last_sent=w.seconds;self.last_wall=wall;self.signature=signature
        return output

    def public(self):
        return dict(enabled=True,active=self.active,unit_status="awaiting_available_units" if self.active and not self.tags else "allocated" if self.active else "idle",generation=self.generation,unit_tags=sorted(self.tags),
                    mode=self.mode,thinking=self.thinking,requests=self.requests,accepted=self.accepted,
                    errors=self.errors,expired=self.expired,last_decision=self.last_decision,last_error=self.last_error,
                    first_order_game_s=self.first_order_s,recent=list(self.recent),
                    usage=getattr(self.client,"usage",None),request_status=self.client.request_status()
                    if callable(getattr(self.client,"request_status",None)) else {})

    def close(self):
        self.closed.set();self.worker.join(timeout=.25)
        if self.worker.is_alive():self._event("defense_shutdown_pending",handling="In-flight request cannot command units; response will be discarded.")
