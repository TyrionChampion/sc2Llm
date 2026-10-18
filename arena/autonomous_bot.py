"""Bounded bot curriculum, observed-loss cutoff and economical battle context."""
from copy import deepcopy
from dataclasses import dataclass
import json
import threading
import time

from arena.match_control import surrender_blockers
from arena.tech_tree import TOWNHALLS, WORKERS


INITIAL_STRATEGY = '''自主选择适合当前局势的战略。根据已侦察的信息比较可行方案，制定阶段目标和调整条件。
持续检查经济、产能、科技、兵力与地图控制；发现原计划失效时修订。
自主设定可量化的阶段军力、产能和关键升级目标与检查时点，用实际产出检查是否落后。
选择扩张经济时，预留当前明确的扩张和必要前置预算，把余钱尽可能持续转成战斗部队；
空闲产能立即安排生产，产能不足就补产能，供给或前置阻塞就补齐，不让多矿经济只变成库存。
持续核对余钱是否已转成实际兵力；大量待产只是未兑现的需求，反复追加队列不能提高并行产能。
库存持续上涨时，按矿气、人口、前置、可用产能及在建进度找出瓶颈，显式安排能开工的消化计划，
在下一检查点核对开工和完成。工人达到目标或人口紧张时重估补工，保留作战人口。
资金只为明确近期用途作有界预留，选择能形成有效战力的支出，避免无用补给和科技。
遵守请求预算，明显不可恢复时投降。'''

BOT_RULES = '''自主指挥真实星际对局，在预算内战胜对手。
只返回JSON：{"tasks":[],"reason":"100字内中文：路线依据、阶段目标或调整"}。
顶层只需tasks/reason；任务仅skill/count/args。禁止type、response_format和任务级reason。
每轮≤6任务，生产/补给/科技/侦察可并行；空tasks保留任务。仅己方观测，无迷雾敌情。
开局比较两路（扩张/机械/防反/时机进攻），按地图/侦察/经济/压力定工人、基地、兵种比例。
设军力/产能/升级目标与检查时点；reason报军力/目标/依据，按观测调整，达标才称成军。
攻/扩需侦察则先等观测；同轮侦察不等于已有敌情。生产/扩张/科技/侦察/调兵须下令。
宿主不选战略。下令/到达≠建成/战术成功/胜利；核对world/反馈，无敌情≠安全，用scout补情报。

接口契约：训练/变形/建造/扩张外层count为1..200，其他外层count必须1。
build_supply、gather、no_op的args={}，train_worker默认args={}。
train_unit args={unit:tech_tree.units准确name}；build_structure args={structure:准确name,
base:"home"或"expansion"或已观测site_N}；research args={upgrade:tech_tree.research准确name}。
gather_gas args={workers_per_refinery:0..3}；expand args={base:"expansion"或已观测site_N}。
神族可折跃兵可加warp_target:[x,y]，仅折跃门在3格内生产；无合法点则等，不转主矿。
morph_unit args={unit:tech_tree.forms准确name,target:"home|rally|enemy_start|nearest_enemy"}。
morph_unit仅免费形态；付费ravager/lurker/brood_lord/overseer用train_unit。forms里的ravager是解除埋地。
build_structure也用于气矿、科技实验室/反应堆、轨道指挥；按recipes费用、前置和实际进度补齐。
科技实验室研究与母建筑训练并行，不必停产。
宏任务等资源/前置，不自动补建。train_unit/train_worker的args可加queue_mode:"append"，
count为新增1..200，每产品每轮一次，每批待产≤200；默认coalesce合并不追加。
建筑/形态/弹药无append。按active_tasks.production_quantity与等待reason续排，完成不循环。
build_supply count=N造本族N个补给（虫族overlord）；建筑逐个等建成。活动目标重复合并。
plan_rejected整批未执行，纠正后重下。
注卵用inject_larva count=1 args={base:home|expansion|site_N}；看zerg_injection，一次施法不循环。
use_ability args={caster:己方tag,ability:准确name,target:null或tag或[x,y],timeout_s:1..120}；看native_abilities。
每caster每轮一单，无自动重施/选目标；购买/研究用宏。
按饱和/压力/科技选工人、矿数和产能。

仅预留已决定的近期扩张/前置，余钱造兵，不为远期囤钱。
每轮核对库存/产能/任务/军力/目标；落后补兵，阻塞补供给/气源/前置。经济和科研不代替军队。
按资源/供给/吞吐/间隔选批量，完成续排。闲设施排兵，忙且不足增设施。
多矿按饱和补工；受压重估预留/守军，阵容和时机自选。

经济目标可用macro_plan args={opening:"economic|pressure|defend|custom",target_bases:1..8,
worker_target:1..90,expand_by_game_s:0..5400,reserve_expansion:true或false,reserve_for_s:30..180,
exempt_products:[最多8个准确产品名],reason:"目标依据"}，全部字段必填。
它只记录目标/有限预留，不自动建造，须下expand及前置。仅活动且未下令的expand可储蓄，
可修订，到期释放。
cancel_expansion args={task_id:活动且尚未下令的expand ID}；已下令建造不能用此接口取消。

防守defend_base args={base:defense_report中的label,units:[准确单位名]或"all",count:1..32,
stance:"hold|trade|screen",timeout_s:30..900,reason:"依据"}。防守占有单位；reason里的“守坡”不派兵，须下defend_base，出击前撤防。
cancel_task/stop_production args={task_id:己方ID}。前者撤控兵/未发单宏；后者停后续工人/部队购买，在途保留，不退款；append仅加新量。
scout外层count=1，args={unit:真实选择器,count:1..8,targets:["enemy_start"]或坐标/地标列表或字符串"expansion_sites",timeout_s:1..600,reason:"侦察"}。
args.count必填；expansion_sites用整个字符串，不能放数组。
scout默认遇敌含工人即停；args.stop_on_contact:false则报告接触并沿原路线move。接触≠到达。另可order_units move续行。
order_units args={unit:"army"等真实选择器,count:1..200或"all",mode:"move|attack_move",target:地标或[x,y],timeout_s:1..600,reason:"意图"}；外层count=1。
army按战斗单位数选人，不保证配比；达标仍检查支援产出/自由，必要时按兵种同点集结；数值count等够自由兵才出发。
全军进攻用move_out args={scope:"all"}；否则只派自由军队，集结侦察自选。
集结令等待时勿抢同批单位；调整须按活动ID取消重下。
cancel_unit_order args={task_id:活动scout/order_units ID}。
空间任务同参数（除reason）合并，不自动派第二队；按自由兵力增援。
idle≠自由。timeout_s含排队、等兵、赶路、交战；对局延长不延单任务。
rally_units args={producers:"all"或rally_producers的label列表,target:地标或[x,y],
duration_s:1..3600,reason:"意图"}；all含产工人基地。显式入口可受威胁；锚点支持已建25%的己方基地。
scan args={target:有效地标或[x,y]}；call_mule/supply_drop args={base:"home|expansion"}，需轨道能量。

战损后重估阵容/规模/升级/地形/视野/增援，勿重复失败路线。
见坦克群比较机械/空军/侧翼/守点转科技；生化遭范围火力重创须改阵容/路线，勿只加人数正面撞阵。
闲兵≠成军，排队≠完成；提前补供给。主矿清空仍ongoing则侦察残余建筑；enemy_start仅出生点。
combat_micro只对已明确分配的军队使用已研究且可用的兴奋剂、坦克架起/收起，不替你选战略。
prepared_strategy是开局目标；旧局举例非本局，以当前decision_memory/反馈/契约为准。
按iteration_policy的剩余请求、时间与检查间隔批量生产，避免重复等待。
请求将尽预排生产/部署/增援和时限；末轮须按情报/兵力留下攻守令与集结点，不能只侦察等待。
编队不吸收新兵，须派增援。限时仍可经济/科技，按阶段进展评估。
当control_policy.surrender_allowed=true且clear_disadvantage有持续败势证据，本轮必须以唯一
surrender任务立即认输：args={reason:"具体观测败势"}。短暂缺钱、单次拒绝或网络错误不等于败势。
'''


class BudgetExhausted(RuntimeError):
    pass


class RequestCapIdleGuard:
    """End a quiet request-limited game only after every continuable command finishes."""
    def __init__(self,quiet_s=120):
        self.quiet_s=quiet_s;self.started=None

    def observe(self,world,queue,exhausted,thinking=False):
        tasks=getattr(queue,'tasks',None)
        standing={'defend_base','rally_units','macro_plan','gather','no_op','gather_gas'}
        pending=(not isinstance(tasks,list) or any(t.active and t.skill not in standing for t in tasks))
        native_orders=any(u.orders for u in world.my_units if not u.is_building and u.type_id not in WORKERS)
        if not exhausted or thinking or pending or native_orders or world.enemy_units:
            self.started=None;return None
        if self.started is None:self.started=world.seconds
        if world.seconds-self.started>=self.quiet_s:
            return dict(game_s=round(world.seconds,1),quiet_s=round(world.seconds-self.started,1),
                reason='No remaining model requests, production/scouting/attack commands, military native orders or visible enemies; standing defense/rally alone cannot advance the goal. Outcome unverified.')
        return None


def production_decision_needed(world):
    """Wake the model for idle military capacity; never choose or issue a product."""
    idle_larva=(world.race=='zerg' and any(u.type_id==89 and u.done for u in world.my_units)
        and any(u.type_id==151 and u.done and not u.orders for u in world.my_units))
    return world.minerals>=300 and (idle_larva or any(
        unit.type_id in (21,27,28,62,71,67,133) and unit.done and not unit.orders
        for unit in world.my_units))


def match_time_policy(game_s, wall_s, max_game_s, max_wall_s, game_speed):
    """Expose both existing deadlines in the model's normalized game-time units."""
    from arena.game_speed import LOOPS_PER_SECOND
    remaining=max(0,max_wall_s-wall_s)
    deadline=min(max_game_s,game_s+remaining*LOOPS_PER_SECOND[game_speed]/22.4)
    return dict(game_s=game_s,max_game_s=max_game_s,max_wall_s=max_wall_s,
                elapsed_wall_s=round(wall_s,1),remaining_wall_s=round(remaining,1),
                game_speed=game_speed,estimated_game_deadline_s=round(deadline,1))


class RequestBudget:
    def __init__(self, limit):
        if type(limit) is not int or limit < 1:
            raise ValueError('Positive integer request limit required')
        self.limit = limit
        self.used = 0
        self.by_role = {}
        self.lock = threading.Lock()

    def reserve(self, role):
        with self.lock:
            if self.used >= self.limit:
                raise BudgetExhausted('Campaign model request budget exhausted')
            self.used += 1
            self.by_role[role] = self.by_role.get(role, 0) + 1

    def report(self):
        return dict(limit=self.limit, attempted=self.used, by_role=dict(self.by_role))


class DefeatGuard:
    """Conservative OWN visible pressure; no hidden bot state or predicted win rate."""
    def __init__(self, sustain_s=15, grace_s=90):
        self.sustain_s = sustain_s
        self.grace_s = grace_s
        self.started = None
        self.evidence = None

    def observe(self, world):
        own = world.my_units
        army = [u for u in own if not u.is_building and u.type_id not in WORKERS
                and (u.ground_dps > 0 or u.air_dps > 0)]
        workers = [u for u in own if u.type_id in WORKERS]
        bases = [u for u in own if u.type_id in TOWNHALLS]
        enemies = [u for u in world.enemy_units if u.ground_dps > 0 and not u.hallucination
                   and (not bases or any(u.distance(base) < 30 for base in bases))]
        strength = lambda u: max(u.ground_dps, u.air_dps) * min(1, u.health / max(1, u.health_max))
        ours = sum(strength(u) for u in army)
        theirs = sum(strength(u) for u in enemies)
        crippled = not bases or (len(workers) <= 6 and all(
            b.health / max(1, b.health_max) <= .4 for b in bases))
        no_recovery = not surrender_blockers(world)
        overwhelming = crippled and len(enemies) >= 3 and theirs >= max(25, 4 * ours)
        candidate = no_recovery or (world.seconds >= self.grace_s and overwhelming)
        if not candidate:
            self.started = self.evidence = None
            return None
        if self.started is None:
            self.started = world.seconds
        sustained = world.seconds - self.started
        if no_recovery or sustained >= self.sustain_s:
            self.evidence = dict(kind='no_recovery' if no_recovery else 'sustained_visible_overmatch',
                game_s=round(world.seconds, 1), sustained_game_s=round(sustained, 1),
                workers=len(workers), bases=len(bases), army=len(army),
                own_strength=round(ours, 1), visible_enemy_strength=round(theirs, 1),
                visible_attackers=len(enemies),
                source='own observed units and enemies within 30 of owned bases; hidden enemies excluded')
            return self.evidence
        return None


def contact_table(records):
    """Losslessly encode repeated contact keys/constants; retain row order and types."""
    if (not isinstance(records,list) or len(records)<3 or
            not all(isinstance(row,dict) and set(row)==set(records[0]) for row in records)):
        return records
    keys=list(records[0])
    encode=lambda value:json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    common={key:records[0][key] for key in keys if
            all(encode(row[key])==encode(records[0][key]) for row in records[1:])}
    columns=[key for key in keys if key not in common]
    table=dict(encoding='columns_rows_common_v1',common=common,columns=columns,
               rows=[[row[key] for key in columns] for row in records])
    return table if len(encode(table))<len(encode(records)) else records


def contact_groups(records):
    """Preserve heterogeneous alert fields and order while sharing repeated columns."""
    table=contact_table(records)
    if not isinstance(table,list) or not table or not all(isinstance(row,dict) for row in table):
        return table
    groups=[];indices={};order=[]
    for row in table:
        keys=tuple(sorted(row))
        if keys not in indices:
            indices[keys]=len(groups);groups.append([])
        group=indices[keys]
        order.append([group,len(groups[group])]);groups[group].append(row)
    packed=dict(encoding='key_groups_v1',groups=[contact_table(group) for group in groups],order=order)
    size=lambda value:len(json.dumps(value,ensure_ascii=False,separators=(',',':')))
    return packed if size(packed)<size(records) else records


def nested_contact_tables(value):
    """Apply the existing reversible table format to nested observed record lists."""
    if isinstance(value,dict):
        return {key:nested_contact_tables(item) for key,item in value.items()}
    if isinstance(value,list):
        return contact_groups([nested_contact_tables(item) for item in value])
    return value


def economical_payload(payload,threshold=39000):
    result = deepcopy(payload)
    from arena.context_tables import grouped_columns
    native=result.get('native_abilities',{})
    for field,keys in (('actors',('unit','buffs','passengers','allocated','ready_ability_ids')),
                       ('targets',('alliance','unit'))):
        if field in native:native[field]=grouped_columns(native[field],keys)
    if any(isinstance(native.get(field),dict) and native[field].get('encoding')=='column_value_groups_v1'
           for field in ('actors','targets')):
        result.setdefault('context_policy',{})['column_value_groups_encoding']=(
            'column_value_groups_v1 restores a columns/rows table: each group row combines indexed common '
            'values with vary indices; order [group,row] restores rows, or flat indices into concatenated groups. coordinates[col] divides position integers '
            'by scale; type/mask restores numeric types. Extra retained; all values exact.')
    tree = result.get('tech_tree', {})
    for group in ('units', 'structures', 'research'):
        for entry in tree.get(group, []):
            entry.pop('role', None)  # Costs, recipes, prerequisites and all names remain.
    knowledge = result.get('game_knowledge', {})
    result['game_knowledge'] = {key: knowledge[key] for key in ('economy', 'race_notes', 'principle')
                                if key in knowledge}
    battlefield = result.get('battlefield', {})
    areas = battlefield.get('areas', [])
    kept = [area for area in areas if area.get('own_units_near', 0)
            or area.get('visible_enemy_units', 0) or area.get('visible_enemy_buildings', 0)
            or area.get('id') in ('home', 'enemy_start')]
    battlefield['areas'] = kept
    battlefield['omitted_areas'] = battlefield.get('omitted_areas', 0) + len(areas) - len(kept)
    battlefield['omitted_regions'] = len(battlefield.pop('regions', []))
    result.setdefault('context_policy', {})['economical_context'] = (
        'All current tasks, feedback, military observations, tech names/costs/prerequisites kept; '
        'duplicate static playbooks/unit prose and unoccupied map areas/region grid omitted explicitly.')
    if len(json.dumps(result,ensure_ascii=False,separators=(',',':'))) > threshold:
        intelligence=result.get('intelligence',{})
        if 'contacts' in intelligence:
            intelligence['contacts']=contact_table(intelligence['contacts'])
        for alert in intelligence.get('pending_alerts',[]):
            for field in ('contacts','nearby_recent_contacts'):
                if field in alert:alert[field]=contact_table(alert[field])
        if 'pending_alerts' in intelligence:
            intelligence['pending_alerts']=contact_groups(intelligence['pending_alerts'])
        for section,field in (('battlefield','own_unit_groups'),('battlefield','unit_selectors')):
            if field in result.get(section,{}):
                result[section][field]=contact_table(result[section][field])
        result['context_policy']['contact_table_encoding']=(
            'Any columns_rows_common_v1 table is LOSSLESS: each rows entry expands to common merged with '
            'dict(zip(columns,row)); row order, tags, coordinates, times and visibility preserved. '
            'key_groups_v1: decode each groups table, then restore entries by each [group,row] in order. '
            'Nested tables use the same rule. Nearby sightings are not killer attribution or hidden totals.')
    # Historical records remain in the journal; never clip a current observation or error event.
    if len(json.dumps(result,ensure_ascii=False,separators=(',',':'))) > threshold:
        removed=[]
        for section,key,keep in (('decision_memory','recent',4),
                ('economy_report','recent_requested_spending',4),('economy_report','checkpoints',3),
                ('engagement_log','windows',3)):
            values=result.get(section,{}).get(key)
            if isinstance(values,list) and len(values)>keep:
                omitted=len(values)-keep
                result[section][key]=values[-keep:]
                removed.append(dict(field=section+'.'+key,omitted=omitted))
        result['context_policy']['omitted_history']=removed
    if len(json.dumps(result,ensure_ascii=False,separators=(',',':'))) > threshold:
        technology=result.get('technology_report',{})
        candidates=technology.get('candidates',[])
        if candidates:
            technology['candidates']=[]
            technology['omitted']=technology.get('omitted',0)+len(candidates)
            technology['omission_reason']='Derived suggestions omitted for budget; complete upgrade catalogue retained'
    if len(json.dumps(result,ensure_ascii=False,separators=(',',':'))) > threshold:
        # Static advice and repeated explanatory prose are not current observations.
        result['game_knowledge']={}
        for entry in tree.get('research',[]):
            entry.pop('effect',None)
        for row in result.get('spatial_reports',[]):
            row.pop('meaning',None)
        for section in ('battlefield','engagement_log','intelligence'):
            result.get(section,{}).pop('meaning',None)
        battlefield.pop('region_format',None)
        battlefield.pop('region_radius',None)
        result['context_policy']['omitted_static_advice']=True
        result['context_policy']['omitted_upgrade_prose']='Names, costs, recipes and prerequisites retained; explanatory effect prose omitted'
        result['context_policy']['spatial_history_scope']='Historical own observations; not current occupancy or victory'
        result['context_policy']['scope']='All contacts/history are own observations; left vision is not death; no hidden enemy inference'
    if len(json.dumps(result,ensure_ascii=False,separators=(',',':'))) > threshold+2000:
        removed=result['context_policy'].setdefault('omitted_history',[])
        for section,key,keep in (('decision_memory','recent',2),
                ('economy_report','recent_requested_spending',0),('economy_report','checkpoints',1)):
            values=result.get(section,{}).get(key)
            if isinstance(values,list) and len(values)>keep:
                result[section][key]=values[-keep:] if keep else []
                removed.append(dict(field=section+'.'+key,omitted=len(values)-keep))
    if len(json.dumps(result,ensure_ascii=False,separators=(',',':'))) > threshold:
        for field in ('active_tasks','spatial_reports','defense_orders'):
            if isinstance(result.get(field),list):
                result[field]=contact_groups(result[field])
        for group in ('units','structures','research','auxiliary_units'):
            if isinstance(tree.get(group),list):tree[group]=contact_groups(tree[group])
    if len(json.dumps(result,ensure_ascii=False,separators=(',',':'))) > threshold:
        # Keep pruning metadata/history addressable for the full-prompt budget pass.
        result={key:value if key in ('context_policy','decision_memory','economy_report')
                else nested_contact_tables(value) for key,value in result.items()}
        # These fixed report descriptions repeat the system contract, not observed state.
        paths=(('control_policy','defense_orders','scope'),('control_policy','defense_orders','order'),
               ('control_policy','defense_orders','holds'),('control_policy','combat_micro','scope'),
               ('combat_micro','scope'),('defensive_rally','scope'),('defensive_rally','policy'))
        omitted=[]
        for path in paths:
            section=result
            for part in path[:-1]:section=section.get(part,{}) if isinstance(section,dict) else {}
            if isinstance(section,dict) and isinstance(section.get(path[-1]),str):
                section.pop(path[-1]);omitted.append('.'.join(path))
        policy=result['context_policy']
        policy['omitted_report_prose']=omitted
        policy['economical_context']='Current observations, flags, errors, tasks, costs and prerequisites kept; static prose/empty map and explicit old history omitted.'
        if 'contact_table_encoding' in policy:
            policy['contact_table_encoding']='columns_rows_common_v1: common + dict(zip(columns,row)); key_groups_v1: decode groups then select [group,row] in order. Nested tables same; all row values exact.'
        if 'omitted_upgrade_prose' in policy:policy['omitted_upgrade_prose']='Upgrade names/costs/recipes/prerequisites kept; effect prose omitted.'
        if 'spatial_history_scope' in policy:policy['spatial_history_scope']='Recorded sightings are not current occupancy or victory.'
        if 'scope' in policy:policy['scope']='Own observations only; leaving vision is not death; no hidden totals.'
    # Stable catalogue first lets subscription requests reuse the common prompt prefix.
    static=('tech_tree','game_knowledge','prepared_strategy','context_policy')
    return {key:result[key] for key in static if key in result} | {
        key:value for key,value in result.items() if key not in static}


class BotPlanner:
    compact_battle = True
    soft_timeout = 12

    def __init__(self, client, budget, strategy, request_limit=24, adaptive_cadence=False):
        if not isinstance(strategy,str) or not strategy.strip() or len(strategy)>1800:
            raise ValueError('A complete model strategy of 1..1800 characters is required')
        self.client, self.budget = client, budget
        self.context_limit=getattr(client,'max_battle_context_chars',45000)
        if type(self.context_limit) is not int or not 45000<=self.context_limit<=90000:
            raise ValueError('Battle input guard requires 45,000..90,000 characters')
        self.prepared_strategy = strategy
        self.request_limit = request_limit
        self.adaptive_cadence = adaptive_cadence
        self.timeout = client.timeout = (60 if getattr(client,'model','').startswith('opencode-go/') else 35)
        self.policy = {}
        self.input_chars = 0
        self.schema_transport_chars = 0
        self.fatal_error = None
        self.fatal_kind = None
        self.consecutive_errors = 0
        self.consecutive_transient_errors = 0
        self.retry_not_before = 0
        self.last_request_error = None
        self.last_request_game_s = None
        self.usage = dict(input_tokens=0, output_tokens=0, cached_input_tokens=0)

    @property
    def calls(self):
        return self.client.calls

    @property
    def attempts(self):
        return self.client.attempts

    @property
    def closed(self):
        return self.client.closed

    def decision_interval(self):
        if not self.adaptive_cadence or self.policy.get('decision_urgent'):
            return 25
        now = self.policy.get('game_s', 0)
        remaining = max(1, self.request_limit-self.attempts)
        deadline=min(self.policy.get('max_game_s',1800),
                     self.policy.get('estimated_game_deadline_s',float('inf')))
        if self.policy.get('production_decision_due'):
            # Ordinary idle capacity is persistent, not a new emergency every 25s.
            # Spread finite requests over the actual horizon; leave a slot for execution.
            return 25 if now<180 else max(25,min(60,(deadline-now)/(remaining+1)))
        if now < 180:
            return 30
        return max(60, (deadline-now)/remaining)

    def ready_for_request(self):
        due = (self.last_request_game_s is None or self.policy.get('clear_disadvantage')
               or self.policy.get('game_s',float('inf'))-self.last_request_game_s >= self.decision_interval())
        return (not self.closed and not self.fatal_error and self.attempts < self.request_limit
                and self.budget.used < self.budget.limit and bool(due)
                and time.monotonic() >= self.retry_not_before)

    def chat(self, system, user):
        if not self.ready_for_request():
            raise BudgetExhausted('Per-match request budget exhausted')
        from arena.context_tables import decode_tables
        source=decode_tables(json.loads(user));schema_chars=0
        schema_builder=getattr(self.client,'response_schema_for',None)
        if callable(schema_builder):
            schema=schema_builder(source);self.client.response_schema=schema
            if schema is not None:
                from arena.go_schema import response_format
                schema_chars=len(json.dumps(response_format(schema),ensure_ascii=False,separators=(',',':')))
        # Native response schemas constrain the decoder, outside the system/user
        # prompt (confirmed by Go's probe usage). Keep the original input ceiling
        # and record this transport metadata separately, as with CLI schemas.
        payload = economical_payload(source)
        # Commander bounds generic preparation notes to 1200 characters. Campaign reviews
        # accept 1800: restore that SAME model-owned strategy before the full 45000-char guard.
        payload['prepared_strategy']=dict(text=self.prepared_strategy,truncated=False,
                                          original_chars=len(self.prepared_strategy),
                                          source='pre_match_plan',execution_evidence=False)
        payload['iteration_policy'] = dict(self.policy, request_limit=self.request_limit,
                                         requests_left=self.request_limit-self.attempts,
                                         decision_interval_s=round(self.decision_interval(),1),
                                         input_char_limit=self.context_limit)
        compact = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        # Fit the FULL prompt, including changing system rules and iteration metadata.
        # Only old decision/economy history is eligible; current tasks, errors and war facts stay.
        for section,field,keep in (('decision_memory','milestones',1),
                ('decision_memory','recent',1),('economy_report','checkpoints',1),
                ('economy_report','recent_requested_spending',0)):
            if len(BOT_RULES)+len(compact)<=self.context_limit:break
            values=payload.get(section,{}).get(field)
            if isinstance(values,list) and len(values)>keep:
                payload[section][field]=values[-keep:] if keep else []
                payload['context_policy'].setdefault('omitted_history',[]).append(
                    dict(field=section+'.'+field,omitted=len(values)-keep,
                         reason='Full system and iteration metadata included in budget'))
                compact=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        if len(BOT_RULES) + len(compact) > self.context_limit:
            # Share repeated values/field names before refusing dense current combat.
            # Unlike history pruning this retains every current fact and its order.
            from arena.context_tables import object_pool, key_table
            payload['context_policy']['shared_encoding'] = (
                'review_object_pool_v1: {$review_ref:N} references objects[N]. '
                'review_integer_pool_v1: negative integer -(N+1) references objects[N]; original negatives '
                'are {$integer:negative}. Pool entries are literal; recursively decode their children. '
                'review_key_table_v1 or review_key_table_base36_v1: dictionary keys index keys[N], decimal or base36 (0..9,a..z). '
                'Decode these wrappers before nested columns/rows tables; all values retained.')
            payload = object_pool(payload,integer_refs=True)
            compact = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
            if len(BOT_RULES) + len(compact) > self.context_limit:
                payload = key_table(payload,base36=True)
                compact = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        if len(BOT_RULES) + len(compact) > self.context_limit:
            self.fatal_error = 'Battle context exceeds %s-character hard limit'%format(self.context_limit,',')
            self.fatal_kind = 'context_budget'
            raise BudgetExhausted(self.fatal_error)
        self.budget.reserve('battle')
        # Encoded envelopes do not have a top-level world. Cadence must use
        # the original current observation, never depend on wire representation.
        self.last_request_game_s = source.get('world',{}).get('game_s')
        self.input_chars += len(BOT_RULES) + len(compact)
        self.schema_transport_chars += schema_chars
        try:
            raw = self.client.chat(BOT_RULES, compact)
        except Exception as error:
            self.consecutive_errors += 1
            detail = str(error)
            self.last_request_error = detail
            kind = getattr(error, 'error_kind', None)
            permanent = kind in ('quota_exhausted', 'account_rejected', 'model_identity_mismatch') or any(word in detail.lower() for word in (
                    'http 401', 'http 402', 'http 403', 'quota', 'usage limit', 'not supported',
                    'unauthorized', 'unexpected codex tool', 'invalid battle plan'))
            transient = (kind in ('transient_transport', 'transient_http', 'transient_model_output')
                         or isinstance(error, (TimeoutError, ConnectionError))
                         or any(label in detail.lower() for label in (
                             'readtimeout', 'connecttimeout', 'network timeout',
                             'http 408', 'http 425', 'http 429', 'http 500',
                             'http 502', 'http 503', 'http 504')))
            if transient and not permanent:
                self.consecutive_transient_errors += 1
                self.retry_not_before = time.monotonic() + min(
                    60, 10 * 2 ** min(self.consecutive_transient_errors-1, 3))
                # No hidden retries or replay of stale prompts: Commander requests
                # a fresh observation after cooldown, within the existing budgets.
            elif permanent or self.consecutive_errors >= 3:
                self.fatal_error = detail
                self.fatal_kind = 'model_unavailable'
            raise
        self.consecutive_errors = 0
        self.consecutive_transient_errors = 0
        self.retry_not_before = 0
        self.last_request_error = None
        for key in self.usage:
            self.usage[key] += ((self.client.last_metadata or {}).get('usage') or {}).get(key, 0) or 0
        if getattr(self.client,'model','').startswith('opencode-go/'):
            from arena.plan_representation import translate_plan
            original=json.loads(raw);translated,changes=translate_plan(original,decode_tables(payload))
            if changes:
                metadata=dict(self.client.last_metadata or {},representation_translations=changes)
                directory=getattr(self.client,'directory',None)
                if directory:
                    from pathlib import Path
                    record=Path(directory)/('request-%04d'%self.client.attempts)/'representation.json'
                    record.write_text(json.dumps(dict(original=original,translated=translated,
                        changes=changes,strategy_changes=False),ensure_ascii=False,indent=2))
                    metadata['representation_record']=str(record)
                self.client.last_metadata=metadata
                raw=json.dumps(translated,ensure_ascii=False)
        return raw

    @property
    def last_metadata(self):
        return self.client.last_metadata

    def request_status(self):
        status = dict(self.client.request_status())
        remaining = max(0, self.retry_not_before-time.monotonic())
        status.update(consecutive_errors=self.consecutive_errors,
                      input_char_limit=self.context_limit,
                      consecutive_transient_errors=self.consecutive_transient_errors,
                      retry_after_s=round(remaining, 2), last_error=self.last_request_error)
        if remaining and not self.fatal_error and not self.closed:
            status['state'] = 'retry_wait'
        return status

    def close(self):
        self.client.close()


@dataclass
class Curriculum:
    difficulty: str = 'easy'
    reached_goal: bool = False

    def record(self, outcome, result_source):
        # Concessions, budget stops and guesses can NEVER promote a difficulty.
        if outcome == 'Victory' and result_source == 'engine_player_result':
            if self.difficulty == 'easy':
                self.difficulty = 'hard'
            else:
                self.reached_goal = True


def match_diagnostics(report, journal):
    events, counts, engagements, plans = [], {}, [], []
    last_engagement_sample=[]
    for line in journal.read_text().splitlines():
        row = json.loads(line)
        if row.get('record')=='request_queued':
            last_engagement_sample=row.get('prompt',{}).get('engagement_log',{}).get('windows',[])
        if row.get('record')=='engagement_observed':
            engagements.append(row['engagement'])
        if row.get('record')=='request_finished' and row.get('api_returned') and row.get('response'):
            try:plans.append(dict(request_id=row['request_id'],plan=json.loads(row['response'])))
            except (ValueError,TypeError):pass
        if row.get('record') != 'feedback_event':
            continue
        event = row['event']
        counts[event['kind']] = counts.get(event['kind'], 0) + 1
        if any(word in event['kind'] for word in ('failed', 'rejected', 'invalid', 'expired', 'error', 'timeout')):
            events.append(event)
    closed_ids={row['id'] for row in engagements}
    engagements.extend(row for row in last_engagement_sample if row['id'] not in closed_ids)
    return dict(outcome=report['outcome'], termination=report['termination'],
                result_source=report['result_source'], event_counts=counts,
                errors=events, timeline=report['timeline'], final=report['final_world'],
                calls=report['model_calls'], usage=report['usage'],runtime_error=report.get('error'),
                runtime_timing={key:report[key] for key in ('game_speed','wall_s','timing','time_limits') if key in report},
                engagement_windows=engagements,returned_plans=plans,
                plan_evidence='Returned plans, not proof they were accepted; consult executor errors and task timeline',
                scope='Own full error events and sampled observations; no hidden bot army or inferred engine cause.')
