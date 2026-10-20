"""Visible local spell choices inside an explicitly assigned combat mission.

No strategic movement, production, hidden targets or invented availability. Choices
are proposals; CombatMicro queries the engine before issuing any of them.
"""
from dataclasses import dataclass
from s2clientprotocol import data_pb2

# Units without a conventional weapon must still join an explicit army order.
SUPPORT_TYPES=frozenset((54,56,75,77,111,126,495,499,498,500,502,503,694))
DEPLOYED_FORMS={32:(33,390),503:(502,2110),500:(498,2097)}
FORM_TYPES=frozenset((502,503,498,500))

@dataclass(frozen=True)
class Cast:
    ability:int
    action:str
    pos:tuple|None=None
    target:int|None=None
    energy:float=0
    repeat_s:float=1
    form:int|None=None
    buff:int|None=None
    effect:int|None=None
    area_radius:float=0
    area_s:float=0
    channel:bool=False

@dataclass(frozen=True)
class Rule:
    actor:int
    ability:int
    action:str
    energy:float
    reach:float
    radius:float=0
    minimum:int=1
    repeat_s:float=3
    kind:str='enemy'
    filter:str='combat'
    friendly_fire:bool=False
    buff:int|None=None
    effect:int|None=None
    area_s:float=0
    channel:bool=False

RULES=(
 Rule(688,2338,'corrosive_bile',0,9,.7,1,7,filter='combat',friendly_fire=True,effect=11,area_s=2.5),
 Rule(75,1036,'psi_storm',75,8,1.5,3,3,filter='combat',friendly_fire=True,effect=1,area_s=3),
 Rule(75,140,'feedback',50,10,filter='energy',repeat_s=3),
 Rule(77,76,'guardian_shield',75,8,minimum=2,repeat_s=11,kind='self',filter='ranged',buff=18,effect=2,area_s=11,radius=4),
 Rule(77,1526,'force_field',50,9,1.5,3,8,filter='force_field',friendly_fire=True,area_s=11),
 Rule(111,74,'fungal_growth',75,10,2.25,3,4,filter='unfungaled',area_s=3),
 Rule(499,2542,'parasitic_bomb',125,8,3,3,7,filter='air',area_s=7),
 Rule(499,2063,'blinding_cloud',100,10,2,3,6,filter='ranged_ground',friendly_fire=True,effect=10,area_s=6),
 Rule(499,2067,'abduct',75,9,filter='valuable',repeat_s=4),
 Rule(126,1664,'transfusion',50,7,kind='ally',filter='wounded_bio',repeat_s=2),
 Rule(50,1628,'emp',75,10,1.5,2,4,filter='shield_or_energy',friendly_fire=True,area_s=2),
 Rule(50,2714,'snipe',50,10,filter='valuable_bio',repeat_s=3,channel=True),
 Rule(56,3747,'interference_matrix',50,9,filter='mechanical_psionic',repeat_s=5),
 Rule(56,3753,'anti_armor_missile',75,10,2.5,3,6,filter='combat',area_s=6),
 Rule(49,2588,'kd8_charge',0,5,1.75,2,14,filter='ground',friendly_fire=True,area_s=2),
 Rule(54,386,'heal',1,4,kind='ally',filter='heal_bio',repeat_s=1,channel=True),
 Rule(78,173,'graviton_beam',50,4,filter='liftable',repeat_s=3,channel=True),
 Rule(57,401,'yamato',0,10,filter='valuable',repeat_s=71,channel=True),
 Rule(80,2393,'prismatic_alignment',0,6,kind='self',filter='armored',repeat_s=43),
 Rule(495,2375,'pulsar_beam_on',25,6,kind='self',filter='ground',repeat_s=2,buff=99),
)
SPELL_TYPES=frozenset(r.actor for r in RULES)|{495,54}
MICRO_TYPES=SPELL_TYPES|FORM_TYPES|{48,51,33,32}
CAPABILITIES={
 'terran':['stim','siege/unsiege','widow_mine_burrow','kd8_charge','emp/snipe','matrix/anti_armor','heal/afterburners','yamato'],
 'zerg':['corrosive_bile','lurker_burrow/unburrow','fungal_growth','abduct/blinding_cloud/parasitic_bomb','transfusion'],
 'protoss':['storm/feedback','guardian_shield/force_field','graviton_beam','prismatic_alignment','pulsar_beam'],
}


def attribute(backend,u,attr):
    tree=getattr(backend,'tech_tree',None)
    meta=getattr(tree,'types',{}).get(u.type_id)
    return meta is not None and attr in meta.attributes


def combat(u):
    return u.ground_dps>0 or u.air_dps>0 or u.type_id in SUPPORT_TYPES or u.type_id in (50,57,75,78,80,688)


def filtered(rule,e,backend):
    if e.hallucination:return False
    f=rule.filter
    if f=='wounded_bio':return attribute(backend,e,data_pb2.Biological) and e.health_max-e.health>=75
    if f=='heal_bio':return not e.is_building and attribute(backend,e,data_pb2.Biological) and e.health_max-e.health>=8
    if e.is_building:return rule.action=='corrosive_bile'
    if f=='energy':return e.energy>=50
    if f=='shield_or_energy':
        tree=getattr(backend,'tech_tree',None);meta=getattr(tree,'types',{}).get(e.type_id)
        return e.energy>=20 or meta is not None and meta.race==3
    if f=='mechanical_psionic':return combat(e) and (attribute(backend,e,data_pb2.Mechanical) or attribute(backend,e,data_pb2.Psionic))
    if f=='valuable_bio':return e.health>=100 and attribute(backend,e,data_pb2.Biological)
    if f=='valuable':return combat(e) and e.health>=125
    if f=='liftable':return combat(e) and not e.flying and not attribute(backend,e,data_pb2.Massive) and 5 not in e.buffs
    if f=='armored':return combat(e) and attribute(backend,e,data_pb2.Armored)
    if f=='air':return combat(e) and e.flying and 132 not in e.buffs
    if f=='unfungaled':return combat(e) and 17 not in e.buffs
    if f in ('ground','force_field','ranged_ground') and e.flying:return False
    if f=='force_field' and attribute(backend,e,data_pb2.Massive):return False
    if f in ('ranged','ranged_ground') and e.attack_range<2:return False
    return combat(e)


def spell_choices(backend,w,u,retreating,attack):
    """Bounded choices, sorted by rule priority then local observed value."""
    if u.type_id==54 and retreating and any(u.distance(e)<=9 and e.air_dps>0 for e in w.enemy_units):
        return [Cast(2116,'afterburners',repeat_s=14)]
    if u.type_id==495 and 99 in u.buffs:
        if retreating or not any(not e.flying and combat(e) and u.distance(e)<=8 for e in w.enemy_units):
            return [Cast(2376,'pulsar_beam_off',repeat_s=1)]
        return []
    choices=[]
    for r in RULES:
        if r.actor!=u.type_id or u.energy<r.energy or r.buff in u.buffs:continue
        if retreating and r.kind not in ('ally','self'):continue
        if r.kind!='ally' and not (attack or retreating):continue
        if retreating and r.action not in ('guardian_shield',):continue
        targets=w.my_units if r.kind=='ally' else w.enemy_units
        near=[e for e in targets if e.tag!=u.tag and e.done and filtered(r,e,backend)
              and u.distance(e)<=r.reach+u.radius+e.radius]
        near=sorted(near,key=lambda e:(u.distance(e),e.tag))[:64]
        if r.kind=='self':
            if len(near)<r.minimum:continue
            if r.action=='guardian_shield' and sum(u.distance(a)<=4 for a in w.my_units if not a.is_building)<2:continue
            choices.append(Cast(r.ability,r.action,energy=r.energy,repeat_s=r.repeat_s,buff=r.buff,effect=r.effect,
                                area_radius=r.radius,area_s=r.area_s));continue
        # Area spells use only visible target centres, never guessed hidden armies.
        ranked=sorted(near,key=lambda e:(-sum(e.distance(a)<=r.radius for a in near),
             -(e.health_max-e.health if r.kind=='ally' else e.energy if r.filter=='energy' else e.health),e.tag))
        for e in ranked[:8]:
            neighbours=[a for a in near if e.distance(a)<=r.radius] if r.radius else [e]
            if len(neighbours)<r.minimum:continue
            if r.friendly_fire and any(a.tag!=u.tag and not (r.action=='psi_storm' and a.is_building)
                    and (r.action not in ('force_field','kd8_charge') or not a.flying)
                    and a.distance(e)<=r.radius+a.radius+.5 for a in w.my_units):continue
            if r.friendly_fire and u.distance(e)<=r.radius+u.radius+.5:continue
            if r.filter=='force_field':
                # Split the rear of a visible group; do not seal our own front line.
                if min((u.distance(a) for a in near),default=99)>u.distance(e)-1.5:continue
                if any(a.distance(e)<=3+a.radius for a in w.my_units if not a.flying):continue
                if any(attribute(backend,a,data_pb2.Massive) and a.distance(e)<=2+a.radius for a in w.enemy_units):continue
            point=r.radius>0 and r.action not in ('parasitic_bomb','anti_armor_missile')
            choices.append(Cast(r.ability,r.action,pos=e.pos if point else None,target=None if point else e.tag,
                 energy=r.energy,repeat_s=r.repeat_s,effect=r.effect,area_radius=r.radius,area_s=r.area_s,channel=r.channel))
    return choices


def form_choice(w,u,point,retreating,attack,last_contact):
    if u.type_id not in FORM_TYPES:return None
    lurker=u.type_id in (502,503);mobile=502 if lurker else 498;deployed=503 if lurker else 500
    reach=(max(u.attack_range,8) if lurker else 5)
    enemies=[e for e in w.enemy_units if (combat(e) or lurker and e.is_building)
             and (not e.flying or not lurker) and u.distance(e)<=reach+u.radius+e.radius]
    arrived=u.distance(point)<=3
    if u.type_id==mobile:
        if not retreating and attack and (enemies or arrived):
            return Cast(2108 if lurker else 2095,'lurker_burrow' if lurker else 'mine_burrow',form=deployed,repeat_s=1)
    elif retreating or (not enemies and not arrived and w.seconds-last_contact>=3):
        return Cast(2110 if lurker else 2097,'lurker_unburrow' if lurker else 'mine_unburrow',form=mobile,repeat_s=1)
    return None


def catalogue_audit(tree):
    """Classify every published ladder-unit ability, without claiming readiness."""
    pairs={(r.actor,tree.canonical_ability(r.ability)) for r in RULES}
    pairs|={(actor,tree.canonical_ability(aid)) for actor,aid in (
        (48,3675),(51,3675),(33,388),(32,390),(502,2108),(503,2110),
        (498,2095),(500,2097),(495,2376),(54,2116))}
    rows=[]
    # Include reversible forms: they do not all have ordinary production recipes.
    from arena.tech_tree import FORMS
    forms={target for _,target,_ in FORMS}
    units={e['id']:e for race in ('terran','protoss','zerg') for e in tree.race_units(race,False).values()}
    for uid in forms:
        if uid in tree.types and not tree.types[uid].has_minerals and not tree.types[uid].has_vespene:
            units.setdefault(uid,dict(id=uid,name=tree.names[uid]))
    ordinary={'smart','move_move','attack_attack','stop_stop','hold_position_hold','patrol_patrol'}
    for uid,entry in sorted(units.items()):
        for spell in tree.public_spells(uid):
            aid=spell['ability_id'];name=spell['name']
            mode='automatic_local' if (uid,tree.canonical_ability(aid)) in pairs else (
                'native_order' if name in ordinary else 'explicit_model_order')
            rows.append(dict(unit=entry['name'],type_id=uid,ability=name,ability_id=aid,mode=mode))
    return dict(engine=tree.version,scope='Catalogue coverage, not current availability or native effect validation',abilities=rows)
