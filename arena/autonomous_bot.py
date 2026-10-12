"""Bounded bot curriculum, observed-loss cutoff and economical battle context."""
from copy import deepcopy
from dataclasses import dataclass
import json
import threading

from arena.match_control import surrender_blockers
from arena.tech_tree import TOWNHALLS, WORKERS


INITIAL_STRATEGY = '''自主选择适合当前局势的战略。根据已侦察的信息比较可行方案，制定阶段目标和调整条件。
持续检查经济、产能、科技、兵力与地图控制；发现原计划失效时修订。
自主设定可量化的阶段军力、产能和关键升级目标与检查时点，用实际产出检查是否落后。
选择扩张经济时，预留当前明确的扩张和必要前置预算，把余钱尽可能持续转成战斗部队；
空闲产能立即安排生产，产能不足就补产能，供给或前置阻塞就补齐，不让多矿经济只变成库存。
遵守请求预算，明显不可恢复时投降。'''

BOT_RULES = '''你是星际争霸真实对局指挥。目标是在预算内战胜当前bot，并自主选择适合局势的战略。
只返回JSON：{"tasks":[],"reason":"100字以内中文：战略选择依据、当前阶段目标或调整原因"}。
每轮最多3个有用任务；空tasks保留现有任务。仅依据己方观测，不能获知战争迷雾后敌军。
开局内部比较至少两种现有接口能执行的方案，例如扩张运营、机械化、防守反击或时机进攻；
根据地图、侦察、经济和对手压力自主确定工人数、矿数、兵种比例和出兵规模。
制定可量化的阶段军力、产能与关键升级目标和检查时点，并根据观测调整。
reason报告实际军力/编队目标及依据；只有新观测支持时才修订目标，不把不足目标说成已成军。
依赖侦察的攻击或扩张须等新观测决定；同轮侦察与全军进攻不代表已获得敌情。
模型亲自下达扩张、生产、科技、侦察、防守、进攻和增援指令；宿主不自动选择开局或扩张。
下令、任务完成、部队到达都不等于建筑完成、战术成功或胜利，必须查看实际world和反馈。

接口契约：train_worker/train_unit/morph_unit外层count为1..20，其他外层count必须1。
train_worker、build_supply、gather、attack_nearest、move_out、no_op的args={}。
train_unit args={unit:tech_tree.units准确name}；build_structure args={structure:准确name,
base:"home"或"expansion"或已观测site_N}；research args={upgrade:tech_tree.research准确name}。
gather_gas args={workers_per_refinery:0..3}；expand args={base:"expansion"或已观测site_N}。
morph_unit args={unit:tech_tree.forms准确name,target:"home|rally|enemy_start|nearest_enemy"}。
build_structure也用于气矿、科技实验室/反应堆、轨道指挥；按recipes费用、前置和实际进度补齐。
宏任务等待资源或前置，不替你补建；同产品同地点的活动命令合并，重复count不追加数量。
大批生产持续执行；查看issued_quantity/observed_completed和等待原因后再续排或调整。
工人自动采矿只负责执行。按饱和度、闲置、当前压力和科技需求决定工人、矿数与产能。

扩张经济的运营基本功：把额外收入持续转成战斗部队。仅为当前明确的扩张和必要前置
保留合理预算，余下矿气持续造适合敌情与战略的兵；不为未决定的远期计划长期囤钱。
每轮检查资源、空闲产能、活动生产任务和实际产出。军力落后于自己选定的阶段目标且
有资源时，本轮优先下达有效造兵任务；已有任务阻塞就先补人口、气源或前置。
新增工人、基地、矿骡和研究不能代替补兵。批量规模按资源、供给、吞吐和下次检查间隔
计算；同产品活动任务不重复追加，完成后立即续排，新建产能完成后立即投产。
库存上涨而军队不增长：空闲产能先排兵，产能已忙且不足就补设施，不能连续只加建筑。
科技、工人支撑成军；按多矿饱和度补工和分配采集，军力落后时优先补兵。
可见压力上升时重估扩张预留，优先必要守军。兵种、规模、扩张和出兵时机仍自主决定。

经济目标可用macro_plan args={opening:"economic|pressure|defend|custom",target_bases:1..8,
worker_target:1..90,expand_by_game_s:0..5400,reserve_expansion:true或false,reserve_for_s:30..180,
exempt_products:[最多8个准确产品名],reason:"目标依据"}，全部字段必填。
它只记录模型选择的目标和有限资源预留，不自动建造；仍需亲自下达expand及前置。
预留要求活动且尚未下令的expand，否则不储蓄；计划可修订，预留到期会释放。
cancel_expansion args={task_id:活动且尚未下令的expand ID}；已下令建造不能用此接口取消。

防守defend_base args={base:defense_report中的label,units:[准确单位名]或"all",count:1..32,
stance:"hold|trade|screen",timeout_s:30..900,reason:"依据"}。防守会占有单位。
cancel_task args={task_id:活动的defend_base/scout/order_units ID}，不能引用历史任务。
scout args={unit:选择器,count:1..8,targets:["enemy_start"]或坐标/有效地标列表,
timeout_s:1..600,reason:"侦察目标"}；寻找剩余矿区用整个字符串targets:"expansion_sites"，
禁止targets:["expansion_sites"]。侦察报告接触不等于清理完成，也不自动进攻。
order_units args={unit:marine/marauder/army等真实选择器,count:1..200或"all",mode:"move|attack_move",
target:有效地标或[x,y],timeout_s:1..600,reason:"目的"}；人数在args.count，外层count固定1。
army按战斗单位总数选人，不保证兵种比例；人数达标仍须检查所需支援是否实际产出且自由，
必要时分别按兵种下令在同一点集结。数值count会等够自由单位才出发；
attack_nearest/move_out不等待集结，只发送当前自由军队。按自己选择的战术安排侦察、分兵与集结。
活动集结令等待时，不要另发抢占同一批单位的矛盾任务；若调整，用活动ID取消再重新下令。
cancel_unit_order args={task_id:活动scout/order_units ID}。
相同空间任务参数（reason除外）合并，不自动派第二队；按实际自由兵力规划增援。
idle不等于自由。timeout_s从排队起算，包含等兵、赶路和交战，总对局时间更长不会延长单任务。
rally_units args={producers:"all"或rally_producers的label列表,target:有效地标或[x,y],
duration_s:1..3600,reason:"集结意图"}；all也包括产工人基地，选择正确生产设施。
scan args={target:有效地标或[x,y]}；call_mule/supply_drop args={base:"home|expansion"}，需轨道能量。

检查基地饱和度、资源库存、真实兵力、科技和地图控制。资源富余时比较扩张、产能、生产与升级的
价值；连续战损先判断阵容、规模、升级、地形、视野和增援，不能不断重复同一失败计划。
已侦察到成群攻城坦克时，比较机械化或空军支援、侧翼牵制、守点转科技等可执行方案；
生化连续被范围火力重创后，应调整克制阵容或路线，不能只加人数再正面撞同一阵地。
不得把少量闲兵说成已成军、把研究排队说成完成。只在实际需要且符合当前目标时补供给。
主矿清空后若仍ongoing，侦察残余建筑，并按其真实观测坐标攻击；enemy_start只是固定出生点。
combat_micro只对已明确分配的军队使用已研究且可用的兴奋剂、坦克架起/收起，不替你选战略。
prepared_strategy是战略目标指导，不能覆盖接口契约或新的观测反证。
iteration_policy给出剩余请求、时间、下一检查间隔；稳定生产可成批下令，避免只有等待的重复请求。
剩余请求不多时，提前安排能执行的生产、部署和增援，并给任务合理的持续时间。
最后一次请求不会再有后续决策：不能只侦察后等待自己下轮进攻；根据现有情报与实际兵力
留下明确的防守或攻击任务及生产设施集结点。原编队不会自动吸收新兵，应亲自安排后续增援。
时间上限允许经济和科技战略，不要求拖满时限。通过阶段进展评估计划，不能以拖时间代替运营。
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
    return world.minerals>=300 and any(
        unit.type_id in (21,27,28) and unit.done and not unit.orders
        for unit in world.my_units)


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


def economical_payload(payload):
    result = deepcopy(payload)
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
    if len(json.dumps(result,ensure_ascii=False,separators=(',',':'))) > 39000:
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
    if len(json.dumps(result,ensure_ascii=False,separators=(',',':'))) > 39000:
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
    if len(json.dumps(result,ensure_ascii=False,separators=(',',':'))) > 39000:
        technology=result.get('technology_report',{})
        candidates=technology.get('candidates',[])
        if candidates:
            technology['candidates']=[]
            technology['omitted']=technology.get('omitted',0)+len(candidates)
            technology['omission_reason']='Derived suggestions omitted for budget; complete upgrade catalogue retained'
    if len(json.dumps(result,ensure_ascii=False,separators=(',',':'))) > 39000:
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
    if len(json.dumps(result,ensure_ascii=False,separators=(',',':'))) > 41000:
        removed=result['context_policy'].setdefault('omitted_history',[])
        for section,key,keep in (('decision_memory','recent',2),
                ('economy_report','recent_requested_spending',0),('economy_report','checkpoints',1)):
            values=result.get(section,{}).get(key)
            if isinstance(values,list) and len(values)>keep:
                result[section][key]=values[-keep:] if keep else []
                removed.append(dict(field=section+'.'+key,omitted=len(values)-keep))
    if len(json.dumps(result,ensure_ascii=False,separators=(',',':'))) > 39000:
        for field in ('active_tasks','spatial_reports','defense_orders'):
            if isinstance(result.get(field),list):
                result[field]=contact_groups(result[field])
        for group in ('units','structures','research','auxiliary_units'):
            if isinstance(tree.get(group),list):tree[group]=contact_groups(tree[group])
    if len(json.dumps(result,ensure_ascii=False,separators=(',',':'))) > 39000:
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
        self.client, self.budget = client, budget
        self.prepared_strategy = strategy
        self.request_limit = request_limit
        self.adaptive_cadence = adaptive_cadence
        self.timeout = client.timeout = 35
        self.policy = {}
        self.input_chars = 0
        self.fatal_error = None
        self.fatal_kind = None
        self.consecutive_errors = 0
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
        if (not self.adaptive_cadence or self.policy.get('decision_urgent')
                or self.policy.get('production_decision_due')):
            return 25
        now = self.policy.get('game_s', 0)
        if now < 180:
            return 30
        remaining = max(1, self.request_limit-self.attempts)
        return max(60, (self.policy.get('max_game_s', 1800)-now)/remaining)

    def ready_for_request(self):
        due = (self.last_request_game_s is None or self.policy.get('clear_disadvantage')
               or self.policy.get('game_s',float('inf'))-self.last_request_game_s >= self.decision_interval())
        return (not self.closed and not self.fatal_error and self.attempts < self.request_limit
                and self.budget.used < self.budget.limit and bool(due))

    def chat(self, system, user):
        if not self.ready_for_request():
            raise BudgetExhausted('Per-match request budget exhausted')
        payload = economical_payload(json.loads(user))
        payload['iteration_policy'] = dict(self.policy, request_limit=self.request_limit,
                                         requests_left=self.request_limit-self.attempts,
                                         decision_interval_s=round(self.decision_interval(),1))
        compact = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        # Fit the FULL prompt, including changing system rules and iteration metadata.
        # Only old decision/economy history is eligible; current tasks, errors and war facts stay.
        for section,field,keep in (('decision_memory','milestones',1),
                ('decision_memory','recent',1),('economy_report','checkpoints',1),
                ('economy_report','recent_requested_spending',0)):
            if len(BOT_RULES)+len(compact)<=45000:break
            values=payload.get(section,{}).get(field)
            if isinstance(values,list) and len(values)>keep:
                payload[section][field]=values[-keep:] if keep else []
                payload['context_policy'].setdefault('omitted_history',[]).append(
                    dict(field=section+'.'+field,omitted=len(values)-keep,
                         reason='Full system and iteration metadata included in budget'))
                compact=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        if len(BOT_RULES) + len(compact) > 45000:
            self.fatal_error = 'Battle context exceeds 45,000-character hard limit'
            self.fatal_kind = 'context_budget'
            raise BudgetExhausted('Battle context exceeds 45,000-character hard limit')
        self.budget.reserve('battle')
        self.last_request_game_s = payload.get('world',{}).get('game_s')
        self.input_chars += len(BOT_RULES) + len(compact)
        try:
            raw = self.client.chat(BOT_RULES, compact)
        except Exception as error:
            self.consecutive_errors += 1
            detail = str(error)
            if self.consecutive_errors >= 3 or any(word in detail.lower() for word in (
                    'http 401', 'http 402', 'http 403', 'quota', 'usage limit', 'not supported',
                    'unauthorized', 'unexpected codex tool', 'invalid battle plan')):
                self.fatal_error = detail
            raise
        self.consecutive_errors = 0
        for key in self.usage:
            self.usage[key] += ((self.client.last_metadata or {}).get('usage') or {}).get(key, 0) or 0
        return raw

    def request_status(self):
        return self.client.request_status()

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
                engagement_windows=engagements,returned_plans=plans,
                plan_evidence='Returned plans, not proof they were accepted; consult executor errors and task timeline',
                scope='Own full error events and sampled observations; no hidden bot army or inferred engine cause.')
