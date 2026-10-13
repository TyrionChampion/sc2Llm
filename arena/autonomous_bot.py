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

BOT_RULES = '''你指挥真实星际对局，自主选战略，在预算内战胜当前bot。
只返回JSON：{"tasks":[],"reason":"100字内中文：路线依据、阶段目标或调整"}。
任务用skill，例如{"skill":"train_unit","count":8,"args":{"unit":"marine"}}。
顶层只需tasks/reason；任务仅skill/count/args。禁止type、response_format和任务级reason。
每轮最多6个必要任务，可并排生产、补给、科技和侦察；空tasks保留任务。仅依据己方观测，不能获知迷雾后敌军。
开局比较至少两条可行路线（扩张、机械化、防守反击、时机进攻），依地图/侦察/经济/压力
选工人、基地、兵种比例与规模，设阶段军力/产能/升级目标和检查时点。
reason报告实际军力、目标与依据；新观测支持才修订，未达标不称成军。
依赖侦察的攻击或扩张须等新观测决定；同轮侦察与全军进攻不代表已获得敌情。
生产/扩张/科技/侦察/调兵由你下令，宿主不选开局或扩张。
下令/到达不等于建成、战术成功或胜利，须核对world与反馈。
无敌情只是未侦察，不是安全；用scout补情报，别用无接触论证扩张或长期等待。

接口契约：train_worker/train_unit/morph_unit外层count为1..20，其他外层count必须1。
train_worker、build_supply、gather、attack_nearest、move_out、no_op的args={}。
train_unit args={unit:tech_tree.units准确name}；build_structure args={structure:准确name,
base:"home"或"expansion"或已观测site_N}；research args={upgrade:tech_tree.research准确name}。
gather_gas args={workers_per_refinery:0..3}；expand args={base:"expansion"或已观测site_N}。
morph_unit args={unit:tech_tree.forms准确name,target:"home|rally|enemy_start|nearest_enemy"}。
build_structure也用于气矿、科技实验室/反应堆、轨道指挥；按recipes费用、前置和实际进度补齐。
科技实验室研究与母建筑训练并行，不必停产。
宏任务等待资源或前置，不替你补建；同产品同地点的活动命令合并，重复count不追加数量。
按issued_quantity/observed_completed及等待原因续排；活动同产品任务不追加数量。
工人自动采矿只执行；按饱和/闲置/压力/科技决定工人、矿数、产能。

只预留已决定的近期扩张/必要前置，余钱按局势造兵，不为远期计划囤钱。
每轮核对库存、产能/任务、实际军力与目标；有钱且落后就造兵，阻塞补供给/气源/前置。
工人/基地/矿骡/研究不能代替补兵；按资源/供给/吞吐/间隔选批量，活动同产品不追加，完成续排，新设施投产。
库存涨而军力不涨：闲产能排兵，忙且不足补设施，不连续只加建筑。按多矿饱和度补工/采集，
军力落后优先补兵，压力升高重估扩张预留并优先守军；兵种、规模、扩张/出兵时机自主决定。

经济目标可用macro_plan args={opening:"economic|pressure|defend|custom",target_bases:1..8,
worker_target:1..90,expand_by_game_s:0..5400,reserve_expansion:true或false,reserve_for_s:30..180,
exempt_products:[最多8个准确产品名],reason:"目标依据"}，全部字段必填。
它只记录目标/有限预留，不自动建造，须下expand及前置。仅活动且未下令的expand可储蓄，
计划可修订，预留到期释放。
cancel_expansion args={task_id:活动且尚未下令的expand ID}；已下令建造不能用此接口取消。

防守defend_base args={base:defense_report中的label,units:[准确单位名]或"all",count:1..32,
stance:"hold|trade|screen",timeout_s:30..900,reason:"依据"}。防守占有单位；reason里的“守坡”不派兵，须下defend_base，出击前撤防。
cancel_task args={task_id:活动的defend_base/scout/order_units ID}，不能引用历史任务。
scout示例：{"skill":"scout","count":1,"args":{"unit":"scv","count":1,"targets":["enemy_start"],"timeout_s":120,"reason":"侦察"}}。
args.unit用真实选择器，args.count必填1..8，timeout_s=1..600；targets:["enemy_start"]或坐标/地标列表，或整个字符串"expansion_sites"（禁止放数组）。
侦察接触不等于清理完成，不自动进攻。
order_units示例：{"skill":"order_units","count":1,"args":{"unit":"army","count":36,"mode":"attack_move","target":"enemy_start","timeout_s":240,"reason":"整队出击"}}。
args.unit用真实选择器，args.count=1..200或"all"，mode="move|attack_move"，target=地标或[x,y]，timeout_s=1..600；外层count固定1。
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

连续战损评估阵容、规模、升级、地形、视野和增援，不能重复同一失败计划。
已侦察到成群攻城坦克时，比较机械化或空军支援、侧翼牵制、守点转科技等可执行方案；
生化连续被范围火力重创后，应调整克制阵容或路线，不能只加人数再正面撞同一阵地。
少量闲兵不等于成军，研究排队不等于完成；按实际需要/目标补供给。敌主矿清空且仍ongoing，
侦察残余建筑并按观测坐标攻击；enemy_start仅固定出生点。
combat_micro只对已明确分配的军队使用已研究且可用的兴奋剂、坦克架起/收起，不替你选战略。
prepared_strategy是开局前目标，其中旧局举例不是本局执行记录；执行须看本局decision_memory/反馈，契约与新观测优先。
按iteration_policy的剩余请求、时间与检查间隔批量生产，避免重复等待。
请求将尽时预排可执行的生产、部署、增援和合理任务时限。最后一轮没有后续决策，
不能只侦察等下一轮；根据实际情报/兵力留下明确攻守令及生产集结点。编队不自动吸收新兵，须亲自派增援。
限时允许经济/科技路线，勿拖满时限代替运营，按阶段进展评估。
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
    if len(json.dumps(result,ensure_ascii=False,separators=(',',':'))) > 41000:
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
        deadline=min(self.policy.get('max_game_s',1800),
                     self.policy.get('estimated_game_deadline_s',float('inf')))
        return max(60, (deadline-now)/remaining)

    def ready_for_request(self):
        due = (self.last_request_game_s is None or self.policy.get('clear_disadvantage')
               or self.policy.get('game_s',float('inf'))-self.last_request_game_s >= self.decision_interval())
        return (not self.closed and not self.fatal_error and self.attempts < self.request_limit
                and self.budget.used < self.budget.limit and bool(due))

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
        self.schema_transport_chars += schema_chars
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
        if getattr(self.client,'model','').startswith('opencode-go/'):
            from arena.plan_representation import translate_plan
            original=json.loads(raw);translated,changes=translate_plan(original,payload)
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
                runtime_timing={key:report[key] for key in ('game_speed','wall_s','timing','time_limits') if key in report},
                engagement_windows=engagements,returned_plans=plans,
                plan_evidence='Returned plans, not proof they were accepted; consult executor errors and task timeline',
                scope='Own full error events and sampled observations; no hidden bot army or inferred engine cause.')
