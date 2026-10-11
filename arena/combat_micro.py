"""Local abilities for explicitly commanded Terran combat units.

No strategic targets, economy decisions or retreat heuristics are invented here.
Python/JSON/morph task owners remain authoritative and opt out of this helper.
"""
from collections import Counter, deque
from copy import deepcopy
from arena.raw_engine import ATTACK, MOVE, command

STIM=3675
SIEGE=388
UNSIEGE=390

class CombatMicro:
    def __init__(self,backend,index,event):
        self.backend=backend;self.index=index;self.event=event
        self.intents={};self.counts=Counter();self.recent=deque(maxlen=6);self.notices={}

    def _event(self,kind,**details):
        self.counts[kind]+=1;self.recent.append(dict(kind=kind,**details))
        self.event(kind,**details)

    def _notice(self,key,now,**details):
        if now-self.notices.get(key,-1e9)<5:return
        self.notices[key]=now
        self._event('combat_micro_unavailable',**details)

    def public(self):
        return dict(enabled=True,managed_units=len(self.intents),counts=dict(self.counts),recent=list(self.recent),
                    scope='Explicit move_out/attack_nearest/order_units and defense units only; scouts and active Python/JSON/morph owners excluded')

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
                if (not u or u.type_id not in (48,51,33,32) or tag in blocked or
                    not self._eligible(ctx) or raw.ability_id not in (ATTACK,MOVE)):
                    old=self.intents.pop(tag,None)
                    if old and old.get('pending'):
                        self._event('combat_micro_unconfirmed',unit_tag=tag,action=old['pending']['action'],
                                    reason='新指令接管单位，旧技能结果尚未确认')
                    continue
                goal=dict(ability=raw.ability_id)
                if raw.HasField('target_world_space_pos'):
                    goal['pos']=(raw.target_world_space_pos.x,raw.target_world_space_pos.y)
                elif raw.HasField('target_unit_tag'):goal['target']=raw.target_unit_tag
                else:
                    self._notice(('target',tag),w.seconds,unit_tag=tag,reason='战斗命令缺少明确目标，未启用微操')
                    continue
                intent=self.intents.setdefault(tag,dict(last_contact=w.seconds,stim_until=0,retry_at=0,pending=None,resume=False))
                intent.update(goal=goal,context=deepcopy(ctx))
                new.add(tag)

        modes={}
        if defense is not None and defense.active:
            modes={tag:defense.mode for tag in defense.tags}
        requested={};suppress=set();resume=set()
        for tag,r in list(self.intents.items()):
            u=own[tag]
            if u.type_id not in (48,51,33,32):
                del self.intents[tag];continue
            pending=r.get('pending')
            if pending:
                timeout=3 if pending['ability']==STIM else 6
                confirmed=(bool(set(u.buffs)&{24,27}) if pending['ability']==STIM
                           else u.type_id==(32 if pending['ability']==SIEGE else 33) and pending['ability'] not in u.orders)
                if confirmed:
                    self._event('combat_micro_confirmed',unit_tag=tag,action=pending['action'],
                                game_s=w.seconds,type_id=u.type_id,buffs=list(u.buffs))
                    r['pending']=None;r['resume']=True
                elif any(t==tag and aid in (pending['ability'],380,253) for t,aid,_ in w.errors) or w.seconds-pending['sent']>timeout:
                    self._event('combat_micro_failed',unit_tag=tag,action=pending['action'],
                                reason=f'引擎拒绝或{timeout}游戏秒内未观察到buff/完整形态转换',game_s=w.seconds)
                    r['pending']=None;r['retry_at']=w.seconds+3;r['resume']=True
                else:
                    suppress.add(tag);continue

            goal=r['goal'];mode=modes.get(tag)
            retreating=(mode=='withdraw' or (goal['ability']==MOVE and mode!='hold'))
            relevant=[e for e in w.enemy_units if not e.flying and u.distance(e)<=13]
            firing=[e for e in relevant if u.distance(e)>=3]
            if firing:r['last_contact']=w.seconds
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
            else:
                suppress.add(tag)  # Attack-move/move is not a valid movement order while sieged.
                close_only=bool(relevant) and not firing
                advance=(not firing and u.distance(point)>4 and
                         (w.seconds-r['last_contact']>=5 or (tag in new and r['context'].get('source')=='task_queue')))
                if retreating or close_only or advance:ability=UNSIEGE;name='unsiege'

            if ability is not None and w.seconds>=r['retry_at']:
                requested[tag]=(ability,name)
            elif r['resume'] and u.type_id!=32:
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
        for tag,(ability,name) in requested.items():
            r=self.intents[tag]
            if not self._matches(ability,available.get(tag,set())):
                self._notice(('ability',tag,ability),w.seconds,unit_tag=tag,action=name,
                             reason='引擎当前未允许此技能；不发送，不宣称已生效')
                r['retry_at']=max(r['retry_at'],w.seconds+1)
                if r['resume'] and own[tag].type_id!=32 and tag not in new:
                    if not (modes.get(tag)=='withdraw' and r['goal']['ability']==ATTACK):resume.add(tag)
                continue
            generated.append(command(ability,[tag]))
            generated_context.append(dict(source='combat_micro',action=name,unit_tag=tag,
                task_id=r['context'].get('task_id'),owner=r['context'].get('source'),observed_game_loop=w.game_loop))
            r['pending']=dict(ability=ability,action=name,sent=w.seconds);r['resume']=True
            if ability==STIM:r['stim_until']=w.seconds+11
            suppress.add(tag);resume.discard(tag)
            self._event('combat_micro_requested',unit_tag=tag,action=name,game_s=w.seconds,
                        owner=r['context'].get('source'),confirmed=False)
        for tag in resume-suppress:
            r=self.intents[tag];goal=r['goal']
            generated.append(command(goal['ability'],[tag],pos=goal.get('pos'),target=goal.get('target')))
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
