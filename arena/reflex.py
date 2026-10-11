"""Idle-worker mining, with combat reactions disabled by default."""
from arena.raw_engine import SCV, CC, MOVE, ATTACK, GATHER, command
from arena.tech_tree import WORKERS


class ReflexNet:
    def __init__(self, home, emit=print, combat_enabled=False):
        self.home=home; self.emit=emit; self.size=(256,256)
        self.combat_enabled=combat_enabled
        self.cooldown={}; self.retreat_until={}; self.state={}
        self.counts={'retreat':0,'defend':0,'gather':0}
    def _state(self,u,name,now):
        if self.state.get(u.tag)!=name:
            self.state[u.tag]=name
            self.emit('[reflex] tag=%d state=%s game_s=%.1f' % (u.tag,name,now))
    def tick(self,w,protected=()):
        now=w.seconds; own=w.my_units; enemies=w.enemy_units
        commands=[]; reserved=set()
        live={u.tag for u in own}
        for cache in (self.cooldown,self.retreat_until,self.state):
            for tag in set(cache)-live: del cache[tag]
        for u in own:
            if u.is_building or not u.done: continue
            if not self.combat_enabled and u.type_id not in WORKERS: continue
            local=([e for e in enemies if e.can_attack(u) and u.distance(e)<max(8,e.attack_range+3)]
                   if self.combat_enabled else [])
            friends=[f for f in own if not f.is_building and u.distance(f)<9]
            own_power=sum(f.health*max(f.ground_dps,f.air_dps) for f in friends)
            foe_power=sum(e.health*(e.air_dps if u.flying else e.ground_dps) for e in local)
            hurt=u.health/max(1,u.health_max)<.35
            flee=bool(local) and (hurt or foe_power>own_power*1.4)
            if flee: self.retreat_until[u.tag]=now+2
            retreat=self.combat_enabled and now<self.retreat_until.get(u.tag,0)
            if retreat:
                reserved.add(u.tag); self._state(u,'retreat',now)
                if now>=self.cooldown.get(u.tag,0):
                    # Move away from the closest threat, toward safety if possible.
                    if local:
                        e=min(local,key=u.distance)
                        dx,dy=u.x-e.x,u.y-e.y
                        n=max(.1,(dx*dx+dy*dy)**.5)
                        target=(max(1,u.x+dx/n*8),max(1,u.y+dy/n*8))
                    else: target=self.home
                    target=(min(self.size[0]-1,target[0]),min(self.size[1]-1,target[1]))
                    commands.append(command(MOVE,[u.tag],target))
                    self.cooldown[u.tag]=now+.6; self.counts['retreat']+=1
                continue
            # Workers defend only near home; combat units react wherever they are.
            targets=([e for e in enemies if u.can_attack(e) and
                     u.distance(e)<(6 if u.type_id in WORKERS else 12) and
                     (u.type_id not in WORKERS or e.distance(self.home)<18)] if self.combat_enabled else [])
            if targets:
                reserved.add(u.tag); self._state(u,'defend',now)
                if now>=self.cooldown.get(u.tag,0):
                    target=min(targets,key=lambda e:(e.is_building,u.distance(e),e.health))
                    commands.append(command(ATTACK,[u.tag],target=target.tag))
                    self.cooldown[u.tag]=now+.8; self.counts['defend']+=1
                continue
            was_retreat=self.state.get(u.tag)=='retreat'
            if u.type_id in WORKERS and u.tag not in protected and (not u.orders or was_retreat):
                minerals=[m for m in w.units if m.mineral]
                if minerals and now>=self.cooldown.get(u.tag,0):
                    patch=min(minerals,key=u.distance)
                    commands.append(command(GATHER,[u.tag],target=patch.tag))
                    reserved.add(u.tag); self.cooldown[u.tag]=now+1
                    self._state(u,'gather',now); self.counts['gather']+=1
            elif not targets:
                self._state(u,'idle',now)
        return commands,reserved
