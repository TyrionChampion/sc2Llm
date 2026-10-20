"""Local abilities for explicitly commanded three-race combat units.

No strategic targets, economy decisions or retreat heuristics are invented here.
Python/JSON/morph task owners remain authoritative and opt out of this helper.
"""
from collections import Counter, deque
from copy import deepcopy
import math
from arena.raw_engine import ATTACK, MOVE, command
from arena.combat_spells import (Cast, MICRO_TYPES, FORM_TYPES, DEPLOYED_FORMS,
    CAPABILITIES, spell_choices, form_choice, combat)

STIM=3675
SIEGE=388
UNSIEGE=390

class CombatMicro:
    def __init__(self,backend,index,event):
        self.backend=backend;self.index=index;self.event=event
        self.intents={};self.counts=Counter();self.recent=deque(maxlen=6);self.notices={}
        self.reservations=[]

    def _event(self,kind,**details):
        self.counts[kind]+=1;self.recent.append(dict(kind=kind,**details))
        self.event(kind,**details)

    def _notice(self,key,now,**details):
        if now-self.notices.get(key,-1e9)<5:return
        self.notices[key]=now
        self._event('combat_micro_unavailable',**details)

    def public(self):
        return dict(enabled=True,managed_units=len(self.intents),counts=dict(self.counts),recent=list(self.recent),
                    automatic=CAPABILITIES,
                    evidence='buff/form confirmed separately from spell acknowledgement; no inferred damage/kills',
                    scope='Explicit move_out/attack_nearest/order_units and defense only; scouts, raids, direct spells and custom owners excluded')

    @staticmethod
    def _eligible(context):
        return (context.get('source') in ('defense_guard','army_tactics') or
                (context.get('source')=='task_queue' and context.get('skill') in ('move_out','attack_nearest','order_units')))

    def _matches(self,ability,available):
        tree=getattr(self.backend,'tech_tree',None)
        if tree:return tree.ability_available(ability,available)
        return bool(({STIM,380,253} if ability==STIM else {ability}) & set(available))

    def apply(self,w,batch,contexts,protected=(),defense=None):
        if len(batch)!=len(contexts):raise ValueError('combat micro requires one context per action')
        blocked=set(protected)
        own={u.tag:u for u in w.my_units}
        self.reservations=[x for x in self.reservations if x['until']>w.seconds]
        new=set()
        for tag in list(self.intents):
            if tag not in own or tag in blocked:
                if self.intents[tag].get('pending'):
                    self._event('combat_micro_unconfirmed',unit_tag=tag,action=self.intents[tag]['pending']['action'],
                                reason='单位消失或控制权交还；不能确认技能生效')
                del self.intents[tag]
        for a,ctx in zip(batch,contexts):
            raw=a.action_raw.unit_command
            if not raw.unit_tags:continue
            for tag in raw.unit_tags:
                u=own.get(tag)
                guarding=ctx.get('source') in ('defense_guard','army_tactics') and ctx.get('mode') in ('defend','hold')
                if (not u or not u.done or u.hallucination or u.type_id not in MICRO_TYPES or tag in blocked or
                    not self._eligible(ctx) or raw.ability_id not in ((ATTACK,MOVE,4,18) if guarding else (ATTACK,MOVE))):
                    old=self.intents.pop(tag,None)
                    if old and old.get('pending'):
                        self._event('combat_micro_unconfirmed',unit_tag=tag,action=old['pending']['action'],
                                    reason='新指令接管单位，旧技能结果尚未确认')
                    continue
                goal=dict(ability=raw.ability_id)
                if raw.HasField('target_world_space_pos'):
                    goal['pos']=(raw.target_world_space_pos.x,raw.target_world_space_pos.y)
                elif raw.HasField('target_unit_tag'):goal['target']=raw.target_unit_tag
                elif guarding:goal['pos']=u.pos
                else:
                    self._notice(('target',tag),w.seconds,unit_tag=tag,reason='战斗命令缺少明确目标，未启用微操')
                    continue
                intent=self.intents.setdefault(tag,dict(last_contact=w.seconds,stim_until=0,retry_at=0,pending=None,resume=False,cooldowns={}))
                intent.update(goal=goal,context=deepcopy(ctx))
                new.add(tag)

        modes={}
        if defense is not None and defense.active:
            modes={tag:defense.mode for tag in defense.tags}
        requested={};suppress=set();resume=set()
        for tag,r in list(self.intents.items()):
            u=own[tag]
            if u.type_id not in MICRO_TYPES or not u.done or u.hallucination:
                del self.intents[tag];continue
            pending=r.get('pending')
            if pending:
                spec=pending['spec'];timeout=15 if spec.channel else 3 if pending['ability']==STIM else 6
                active_order=any(self._matches(pending['ability'],{a}) and (
                    spec.target is None or n<len(u.order_targets) and u.order_targets[n]==spec.target) and (
                    spec.pos is None or n<len(u.order_positions) and u.order_positions[n] is not None
                    and math.dist(u.order_positions[n],spec.pos)<=1/64) for n,a in enumerate(u.orders))
                if pending['ability']==STIM:confirmed=bool(set(u.buffs)&{24,27})
                elif spec.form is not None:confirmed=u.type_id==spec.form and not active_order
                else:confirmed=spec.buff is not None and spec.buff in u.buffs
                failed=any(t==tag and self._matches(pending['ability'],{aid}) for t,aid,_ in w.errors)
                evidence=[]
                if not failed and spec.form is None and spec.buff is None:
                    if spec.energy and u.energy<=pending['energy']-1:evidence.append('caster_energy_spent')
                    if active_order:evidence.append('native_cast_order_observed')
                    if spec.effect is not None:
                        point=spec.pos or u.pos
                        if any(e not in pending['effects'] and e[0]==spec.effect and e[1]==1
                               and math.dist(point,(e[2],e[3]))<=2
                               for e in w.effects):evidence.append('native_effect_observed')
                    if not evidence and w.seconds>pending['sent']+.1 and w.seconds>=pending.get('check_at',0):
                        pending['check_at']=w.seconds+.5
                        try:
                            ready=self.backend.available_abilities(self.index,[tag])
                            if not self._matches(spec.ability,ready.get(tag,set())):evidence.append('native_availability_changed')
                        except Exception as error:
                            self._notice(('ack_query',tag),w.seconds,unit_tag=tag,reason='技能确认查询失败：'+type(error).__name__)
                    if evidence and not pending.get('acknowledged'):
                        pending['acknowledged']=True
                        self._event('combat_micro_acknowledged',unit_tag=tag,action=spec.action,
                            game_s=w.seconds,evidence=evidence,effect_confirmed='native_effect_observed' in evidence,
                            damage_confirmed=False)
                    if pending.get('acknowledged'):
                        # Do not cancel a channel with the former attack-move.
                        goal=self.intents[tag]['goal']
                        explicit_retreat=tag in new and goal['ability']==MOVE
                        if spec.channel and active_order and not explicit_retreat and w.seconds-pending['sent']<timeout:
                            suppress.add(tag);continue
                        r['pending']=None;r['resume']=True
                        pending=None
                if confirmed:
                    self._event('combat_micro_confirmed',unit_tag=tag,action=pending['action'],
                                game_s=w.seconds,type_id=u.type_id,buffs=list(u.buffs))
                    r['pending']=None;r['resume']=True
                elif pending is not None and (failed or w.seconds-pending['sent']>timeout):
                    self._event('combat_micro_failed',unit_tag=tag,action=pending['action'],
                                reason=f'引擎拒绝或{timeout}游戏秒内未观察到buff/完整形态转换',game_s=w.seconds)
                    r['pending']=None;r['retry_at']=w.seconds+3;r['resume']=True
                elif pending is not None:
                    suppress.add(tag);continue

            goal=r['goal'];mode=modes.get(tag)
            defending=(mode=='hold' or r['context'].get('source') in ('defense_guard','army_tactics')
                       and r['context'].get('mode') in ('defend','hold'))
            retreating=(mode=='withdraw' or r['context'].get('mode')=='withdraw'
                        or goal['ability']==MOVE and not defending)
            relevant=[e for e in w.enemy_units if not e.flying and u.distance(e)<=13]
            firing=[e for e in relevant if u.distance(e)>=3]
            if u.type_id in FORM_TYPES:
                reach=max(u.attack_range,8) if u.type_id in (502,503) else 5
                contact=any((combat(e) or u.type_id in (502,503) and e.is_building) and (not e.flying or u.type_id not in (502,503))
                            and u.distance(e)<=reach+u.radius+e.radius for e in w.enemy_units)
            else:contact=bool(firing) if u.type_id in (32,33) else any(combat(e) and u.distance(e)<=10 for e in w.enemy_units)
            if contact:r['last_contact']=w.seconds
            point=goal.get('pos')
            target=next((e for e in w.enemy_units if e.tag==goal.get('target')),None)
            if point is None and target is not None:point=target.pos
            if point is None:
                self._notice(('target_lost',tag),w.seconds,unit_tag=tag,reason='目标已离开当前视野，不追踪隐藏敌人')
                suppress.add(tag);continue
            arrived=u.distance(point)<=3
            ability=None;name=None
            if u.type_id in (48,51):
                near=[e for e in w.enemy_units if u.can_attack(e) and u.distance(e)<=u.attack_range+1.5
                      and (e.ground_dps>0 or e.air_dps>0)]
                healthy=u.health>max(10 if u.type_id==48 else 20,.65*u.health_max)
                if (goal['ability']==ATTACK and not retreating and 15 in w.upgrades and healthy and near
                    and not set(u.buffs)&{24,27} and w.seconds>=r['stim_until']):
                    ability=STIM;name='stim'
            elif u.type_id==33:
                if not retreating and (firing or (arrived and not w.enemy_units)) and (goal['ability']==ATTACK or mode=='hold'):
                    ability=SIEGE;name='siege'
            elif u.type_id==32:
                suppress.add(tag)  # Attack-move/move is not a valid movement order while sieged.
                close_only=bool(relevant) and not firing
                advance=(not firing and u.distance(point)>4 and
                         (w.seconds-r['last_contact']>=5 or (tag in new and r['context'].get('source')=='task_queue')))
                if retreating or close_only or advance:ability=UNSIEGE;name='unsiege'

            proposal=None
            if ability is not None:
                proposal=Cast(ability,name,form=(32 if ability==SIEGE else 33) if ability in (SIEGE,UNSIEGE) else None,
                              energy=10 if u.type_id==48 and ability==STIM else 20 if ability==STIM else 0)
            elif u.type_id in FORM_TYPES:
                proposal=form_choice(w,u,point,retreating,goal['ability']==ATTACK or defending,r['last_contact'])
            options=[proposal] if proposal else spell_choices(self.backend,w,u,retreating,goal['ability']==ATTACK or defending)
            if u.type_id in DEPLOYED_FORMS:suppress.add(tag)
            options=[c for c in options if w.seconds>=r['cooldowns'].get(c.action,0)]
            if options and w.seconds>=r['retry_at']:
                requested[tag]=options
            elif r['resume'] and u.type_id not in DEPLOYED_FORMS:
                # Defense computes its fresh withdrawal point; never resume an old attack during retreat.
                if not (mode=='withdraw' and goal['ability']==ATTACK):
                    if tag not in new:resume.add(tag)
                    r['resume']=False

        available={}
        if requested:
            query=getattr(self.backend,'available_abilities',None)
            try:
                if not callable(query):raise RuntimeError('backend has no available_abilities query')
                available=query(self.index,list(requested))
            except Exception as error:
                self._notice(('query',),w.seconds,reason='能力查询失败，未伪造可用技能：'+str(error))
                for tag in requested:self.intents[tag]['retry_at']=w.seconds+3

        generated=[];generated_context=[]
        for tag,options in requested.items():
            r=self.intents[tag]
            def reserved(c):
                point=c.pos or own[tag].pos
                return any(x['ability']==c.ability and (
                    c.area_radius>0 and x['pos'] is not None and math.dist(point,x['pos'])<=c.area_radius*1.5
                    or c.target is not None and x['target']==c.target) for x in self.reservations) or (
                    c.effect is not None and c.area_radius>0 and any(e[0]==c.effect and e[1]==1
                        and math.dist(point,(e[2],e[3]))<=c.area_radius*1.5 for e in w.effects))
            spec=next((c for c in options if self._matches(c.ability,available.get(tag,set())) and not reserved(c)),None)
            if spec is None:
                ability=options[0].ability;name=options[0].action
                self._notice(('ability',tag,ability),w.seconds,unit_tag=tag,action=name,
                             reason='引擎当前未允许此技能；不发送，不宣称已生效')
                r['retry_at']=max(r['retry_at'],w.seconds+1)
                if r['resume'] and own[tag].type_id not in DEPLOYED_FORMS and tag not in new:
                    if not (modes.get(tag)=='withdraw' and r['goal']['ability']==ATTACK):resume.add(tag)
                continue
            ability,name=spec.ability,spec.action
            generated.append(command(ability,[tag],pos=spec.pos,target=spec.target))
            generated_context.append(dict(source='combat_micro',action=name,unit_tag=tag,
                task_id=r['context'].get('task_id'),owner=r['context'].get('source'),observed_game_loop=w.game_loop))
            r['pending']=dict(ability=ability,action=name,sent=w.seconds,spec=spec,energy=own[tag].energy,effects=tuple(w.effects));r['resume']=True
            r['cooldowns'][name]=w.seconds+spec.repeat_s
            if spec.area_s or spec.target is not None:
                self.reservations.append(dict(ability=ability,pos=spec.pos or own[tag].pos,
                    target=spec.target,until=w.seconds+max(1,spec.area_s)))
            if ability==STIM:r['stim_until']=w.seconds+11
            suppress.add(tag);resume.discard(tag)
            self._event('combat_micro_requested',unit_tag=tag,action=name,game_s=w.seconds,
                        owner=r['context'].get('source'),ability_id=ability,target=spec.target,point=spec.pos,confirmed=False)
        for tag in resume-suppress:
            r=self.intents[tag];goal=r['goal']
            generated.append(command(goal['ability'],[tag],pos=goal.get('pos') if goal['ability'] in (MOVE,ATTACK) else None,target=goal.get('target')))
            generated_context.append(dict(source='combat_micro_resume',task_id=r['context'].get('task_id'),
                                          owner=r['context'].get('source'),observed_game_loop=w.game_loop))
            r['resume']=False
        kept=[];bindings=[]
        for a,ctx in zip(batch,contexts):
            raw=a.action_raw.unit_command
            tags=list(raw.unit_tags)
            if not tags or not set(tags)&suppress:
                kept.append(a);bindings.append(ctx);continue
            remaining=[t for t in tags if t not in suppress]
            if remaining:
                copy=type(a)();copy.CopyFrom(a)
                del copy.action_raw.unit_command.unit_tags[:]
                copy.action_raw.unit_command.unit_tags.extend(remaining)
                kept.append(copy);bindings.append(ctx)
        # Bound diagnostics, while durable feedback events remain in the existing journal.
        if len(self.notices)>1024:
            self.notices={k:v for k,v in self.notices.items() if w.seconds-v<10}
        return kept+generated,bindings+generated_context
