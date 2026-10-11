"""Three-race ladder recipes, bound to the running engine's data and ability checks.
Relationship provenance: tech_recipe_map.json (MIT reference retained in repository).
Never infer that a listed recipe is currently executable; query the actual producer.
"""
from copy import deepcopy
from pathlib import Path
import json,re
from pysc2.lib import units as unit_enum, upgrades as upgrade_enum, actions
from s2clientprotocol import common_pb2

ROOT=Path(__file__).parent
REFERENCE=json.loads((ROOT/'tech_recipe_map.json').read_text())
ROLES=json.loads((ROOT/'unit_roles_zh.json').read_text())
WORKERS={45,84,104}
TOWNHALLS={18,132,130,59,86,100,101}
GAS_BUILDINGS={20,61,88,1960,1955,1956}
ADDONS={37:21,38:21,39:27,40:27,41:28,42:28}
RACE_INFO={'terran':dict(worker=45,townhall=18,supply=19,gas=20),
           'zerg':dict(worker=104,townhall=86,supply=106,gas=88),
           'protoss':dict(worker=84,townhall=59,supply=60,gas=61)}
UNIT_NAMES={int(u):u.name for group in (unit_enum.Terran,unit_enum.Zerg,unit_enum.Protoss) for u in group}
UPGRADE_NAMES={int(u):u.name for u in upgrade_enum.Upgrades}

def snake(name):
    return re.sub(r'([a-z0-9])([A-Z])',r'\1_\2',re.sub(r'([A-Z]+)([A-Z][a-z])',r'\1_\2',name)).lower()

# Free reversible forms are operations, not additional purchases.
FORMS=[(33,32,388),(32,33,390),(35,34,403),(34,35,405),(53,484,1998),(484,53,1978),
       (52,691,2362),(691,52,2364),(498,500,2095),(500,498,2097),(81,136,1528),(136,81,1530),
       (82,1911,3741),(1911,82,3739),(129,1912,3743),(1912,129,3745),(19,47,556),(47,19,558),
       (21,46,452),(46,21,554),(18,36,417),(36,18,419),(27,43,485),(43,27,520),
       (28,44,518),(44,28,522),(132,134,1522),(134,132,1524),(689,734,2558),(734,689,2560),
       (98,139,1725),(139,98,1729),(99,140,1727),(140,99,1731),(62,133,1518),(133,62,1520)]
for base,buried,down,up in [(9,115,1374,1376),(104,116,1378,1380),(107,117,1382,1384),
    (111,127,1444,1446),(502,503,2108,2110),(126,125,1433,1435),(688,690,2340,2342),
    (110,118,1386,1388),(494,493,2014,2016),(109,131,1512,1514),(105,119,1390,1392)]:
    FORMS.extend([(base,buried,down),(buried,base,up)])

class TechTree:
    def __init__(self,data,version='unknown'):
        self.version=version
        self.types={u.unit_id:u for u in data.units}
        self.abilities={a.ability_id:a for a in data.abilities}
        self.upgrades={u.upgrade_id:u for u in data.upgrades}
        self.units={};self.research={};self.forms={};self.excluded=[];self.diagnostics=[]
        self.names={uid:snake(UNIT_NAMES.get(uid,u.name)) for uid,u in self.types.items()}
        self.upgrade_names={uid:snake(UPGRADE_NAMES.get(uid,u.name)) for uid,u in self.upgrades.items()}
        # Recent SC2 builds shifted newly appended numeric ids. Bind their stable
        # link/button identity from live metadata; never treat an unrelated live id as valid.
        norm=lambda text:re.sub('[^a-z0-9]','',text.lower())
        self.reference_ids={}
        for aid,name in REFERENCE.get('ability_names',{}).items():
            aid=int(aid)
            if aid<4000:continue
            matches=[a.ability_id for a in data.abilities if a.available and a.button_name and norm(a.link_name+a.button_name)==norm(name)]
            if len(matches)==1:
                self.reference_ids[aid]=matches[0]
                if matches[0]!=aid:self.diagnostics.append(dict(kind='ability_identity_binding',reference=aid,name=name,engine=matches[0]))
            else:
                self.reference_ids[aid]=None
        training=deepcopy(REFERENCE['training'])
        for target,producer in ADDONS.items():training.setdefault(str(producer),{})[str(target)]={'ability':self.types[target].ability_id,'addon':True}
        training.setdefault('75',{})['141']={'ability':1766,'merge':True,'actor_types':[75,76]}
        training.setdefault('79',{})['85']={'ability':1042}
        training.setdefault('26',{})['58']={'ability':710}
        for producer,products in training.items():
            for target,rule in products.items():
                producer=int(producer);target=int(target);rule=deepcopy(rule)
                u=self.types.get(target);p=self.types.get(producer)
                if u is None or p is None or not u.available or not p.available:
                    self.excluded.append(dict(kind='production',producer=producer,target=target,reason='unit unavailable in running mod'));continue
                ability=self.reference_ids.get(rule['ability'],rule['ability'])
                # Correct creation ids changed by a patch, but preserve warp-in and caster-specific recipes.
                if (ability not in self.abilities or not self.abilities[ability].available) and u.ability_id in self.abilities and self.abilities[u.ability_id].available:
                    self.diagnostics.append(dict(kind='engine_creation_id',unit=self.names[target],reference=ability,engine=u.ability_id))
                    ability=u.ability_id
                a=self.abilities.get(ability)
                if a is None or not a.available:
                    self.excluded.append(dict(kind='production',target=self.names[target],ability=ability,reason='ability unavailable in running mod'));continue
                building=8 in u.attributes
                consumed=(producer==104 and building) or producer in (105,110,107,112,106,893) or (building and producer in (18,86,100,92))
                minerals=u.mineral_cost;gas=u.vespene_cost;supply=u.food_required
                if consumed:
                    minerals-=p.mineral_cost;gas-=p.vespene_cost;supply=max(0,supply-p.food_required)
                if rule.get('merge'):minerals=gas=supply=0
                if target==31:minerals=gas=supply=0  # Raven spell consumes energy, not the turret's combat-value minerals.
                if target==893:minerals,gas=25,25  # Explicit ventral-sac fee; included in the live cost verification matrix.
                quantity=2 if target in (105,693) else 1
                minerals*=quantity;gas*=quantity;supply*=quantity
                if min(minerals,gas,supply)<0:raise RuntimeError('negative recipe cost for '+self.names[target])
                recipe=dict(producer=producer,ability=ability,minerals=minerals,gas=gas,supply=supply,
                    quantity=quantity,consumes_actor=consumed or producer==151 or rule.get('merge',False),actor_count=2 if rule.get('merge') else 1,
                    actor_types=rule.get('actor_types',[producer]),placement=producer==133 or a.target==2 or a.target==5 and a.is_building,
                    target_kind=a.target,addon=rule.get('addon',False),required_building=rule.get('required_building',u.tech_requirement),
                    required_upgrade=rule.get('required_upgrade'),requires_techlab=rule.get('requires_techlab',False),
                    requires_power=rule.get('requires_power',False))
                key=self.names[target]
                entry=self.units.setdefault(key,dict(id=target,name=key,race=common_pb2.Race.Name(u.race).lower(),
                    structure=building,role=ROLES.get(str(target),{}).get('role',''),recipes=[]))
                entry['recipes'].append(recipe)
        for producer,items in REFERENCE['research'].items():
            for upgrade,rule in items.items():self._add_research(int(producer),int(upgrade),rule)
        # Match new/legacy researches whose owning structure is declared in the ability registry.
        for producer,ability_ids in REFERENCE['abilities'].items():
            producer=int(producer)
            if producer not in self.types or not self.types[producer].available or 8 not in self.types[producer].attributes:continue
            for upgrade,u in self.upgrades.items():
                if u.ability_id and u.ability_id in ability_ids and self.upgrade_names[upgrade] not in self.research:
                    self._add_research(producer,upgrade,dict(ability=u.ability_id))
        for producer,target,ability in FORMS:
            if not all(uid in self.types and self.types[uid].available for uid in (producer,target)):continue
            if ability not in self.abilities or not self.abilities[ability].available:continue
            self.forms.setdefault(self.names[target],[]).append(dict(producer=producer,target=target,ability=ability,
                placement=self.abilities[ability].target in (2,5),required_upgrade=84 if target==133 else None))
        # User-facing spell names use raw function names where available, engine labels for new ids.
        self.ability_names={a.ability_id:snake(a.friendly_name or a.button_name or a.link_name or 'ability_'+str(a.ability_id)).replace(' ','_') for a in data.abilities}
        for f in actions.RAW_FUNCTIONS:
            if f.ability_id and not f.name.endswith('_autocast'):
                self.ability_names[f.ability_id]=snake(re.sub(r'_(quick|pt|unit)$','',f.name))
        self.by_ability_name={name:aid for aid,name in self.ability_names.items()}
        self.production_abilities={r['ability'] for e in self.units.values() for r in e['recipes']}
        self.research_abilities={r['ability'] for e in self.research.values() for r in e['recipes']}

    def _add_research(self,producer,upgrade,rule):
        u=self.upgrades.get(upgrade);p=self.types.get(producer)
        if u is None or p is None or not p.available:return
        ability=self.reference_ids.get(rule['ability'],rule['ability']);a=self.abilities.get(ability)
        if (a is None or not a.available) and u.ability_id in self.abilities and self.abilities[u.ability_id].available:
            self.diagnostics.append(dict(kind='engine_research_id',upgrade=self.upgrade_names[upgrade],reference=ability,engine=u.ability_id))
            ability=u.ability_id;a=self.abilities[ability]
        if a is None or not a.available:
            self.excluded.append(dict(kind='research',upgrade=self.upgrade_names[upgrade],reason='research ability unavailable'));return
        key=self.upgrade_names[upgrade]
        e=self.research.setdefault(key,dict(id=upgrade,name=key,race=common_pb2.Race.Name(p.race).lower(),
            minerals=u.mineral_cost,gas=u.vespene_cost,seconds=round(u.research_time/22.4,2),recipes=[]))
        e['recipes'].append(dict(producer=producer,ability=ability,required_building=rule.get('required_building'),
            required_upgrade=rule.get('required_upgrade'),requires_power=rule.get('requires_power',False)))

    def tactical_abilities(self,unit_type):
        # Only declared unit actions, excluding all purchases/research. Local spells may summon units.
        abilities={self.reference_ids.get(a,a) for a in REFERENCE['abilities'].get(str(unit_type),[])}
        forbidden=self.research_abilities|{r['ability'] for e in self.units.values() for r in e['recipes'] if r.get('minerals',0) or r.get('gas',0) or r.get('supply',0)}
        return {a for a in abilities if a in self.abilities and self.abilities[a].available and a not in forbidden and self.abilities[a].target in (1,2,3,4,5)}

    def public_spells(self,unit_type,available=None):
        return [dict(name=self.ability_names[a],ability_id=a,target={1:'none',2:'point',3:'unit',4:'point_or_unit',5:'point_or_none'}[self.abilities[a].target])
            for a in sorted(self.tactical_abilities(unit_type)) if available is None or self.ability_available(a,available)]

    def canonical_ability(self,ability):
        seen=set()
        while ability in self.abilities and self.abilities[ability].remaps_to_ability_id and ability not in seen:
            seen.add(ability);ability=self.abilities[ability].remaps_to_ability_id
        return ability

    def ability_available(self,ability,available):
        return self.canonical_ability(ability) in {self.canonical_ability(a) for a in available}

    def matches(self,unit_type,required):
        return unit_type==required or required in self.types[unit_type].tech_alias

    def race_units(self,race,structures=None):
        return {k:v for k,v in self.units.items() if v['race']==race and (structures is None or v['structure']==structures)}

    # Power is a per-recipe boolean in the data, but the literal string
    # 'pylon_or_phasing_prism_power' repeated in ~60 recipes on every request. A short token
    # with one legend line carries the same information. Requirement NAMES stay: the model
    # needs the prerequisite chain to order buildings deliberately; only the encoding is tight.
    POWER_TOKEN = 'power'

    def public(self,race,include_spells=False):
        from arena.knowledge import effect,building_role
        def recipes(entry):
            result=[]
            for r in entry['recipes']:
                item={k:r[k] for k in ('minerals','gas','supply','quantity') if k in r}
                item['producer']=self.names[r['producer']]
                requires=[]
                if r.get('required_building'):requires.append(self.names[r['required_building']])
                if r.get('required_upgrade'):requires.append(self.upgrade_names[r['required_upgrade']])
                if r.get('requires_techlab'):requires.append('tech_lab')
                if r.get('requires_power'):requires.append(self.POWER_TOKEN)
                if requires:item['requires']=requires
                if r.get('consumes_actor'):item['consumes_producer']=True
                if r.get('actor_count',1)>1:item['producer_count']=r['actor_count']
                result.append(item)
            return result
        return dict(race=race,engine=self.version,scope='standard multiplayer; engine query is final authority',
            units=[dict(name=e['name'],role=e['role'],**({'spells':self.public_spells(e['id'])} if include_spells else {}),recipes=recipes(e)) for e in self.race_units(race,False).values()],
            structures=[dict(name=e['name'],role=building_role(e['name']),**({'spells':self.public_spells(e['id'])} if include_spells else {}),recipes=recipes(e)) for e in self.race_units(race,True).values()],
            research=[dict(name=e['name'],effect=effect(e['name']),minerals=e['minerals'],gas=e['gas'],recipes=recipes(e)) for e in self.research.values() if e['race']==race],
            forms=[name for name,rs in self.forms.items() if self.types[rs[0]['target']].race=={'terran':1,'zerg':2,'protoss':3}[race]],
            limitations=['A catalogue entry is not proof of current prerequisites, resources, placement or game effect.',
                         "requires token '" + self.POWER_TOKEN + "' means the producer needs pylon or phasing-prism power"],
            auxiliary_units=[dict(name=self.names[int(uid)],role=role['role']) for uid,role in ROLES.items() if int(uid) in self.types and self.types[int(uid)].race=={'terran':1,'zerg':2,'protoss':3}[race] and self.names[int(uid)] not in self.units])
        # data_adjustments (raw ability-id rebinding diagnostics) is intentionally not published:
        # it never drove a decision, it is already printed as '[tech-data adjustment]' at load
        # time, and abilities are addressed by name everywhere. It cost 673 characters per request.
