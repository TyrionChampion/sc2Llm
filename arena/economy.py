"""Model-owned macro goals, bounded expansion savings and sampled economy evidence."""
from copy import deepcopy
from collections import deque
import math
from arena.tech_tree import WORKERS, TOWNHALLS, RACE_INFO

OPENINGS = {
 "terran": [
  {"id":"economic","name":"单兵营侦察扩张","sequence":["持续SCV与及时补给站","一兵营、按侦察兵需求采第一气","少量守军或死神侦察，兵营完成后安排轨道升级","为二矿存钱并显式expand，随后补工人、转矿、补产能和科技"],
   "checkpoint":"12工人常见天梯参考：约19～23人口考虑二矿；不是当前地图的硬性时间或人口要求。",
   "deviations":"确认代理建筑、早期大规模来袭或二矿不安全时，说明证据并切换防守。未知敌情先侦察，不等同已确认一波。"},
  {"id":"pressure","name":"单矿有期限压制","sequence":["说明压制兵种、科技与出门条件","只造支撑这次进攻的产能和瓦斯","侦察对方扩张和防守","进攻到期或无法造成损失时，重新安排扩张或转型"],
   "checkpoint":"明确压制结束的游戏时间和撤退条件；不能永久以出兵为由不发展经济。"},
  {"id":"defend","name":"应对已观察到的早攻","sequence":["保SCV与关键建筑、封口或少量地堡和守军","按敌方空地、隐形等实际威胁补对应防御","威胁消退后恢复工人和二矿预算"],
   "checkpoint":"说明敌情与最低防守需求，避免无限增兵。"}],
 "protoss": [
  {"id":"economic","name":"单门侦察扩张","sequence":["持续探机和及时水晶塔","传送门、同化炉、控制核心及最少守军","侦察确认扩张安全后预留星灵枢纽费用","二矿补探机、转矿、供能和科技"],
   "checkpoint":"以实际前置、侦察与可用资源为准；不要为无目标的追加传送门挤占二矿。"},
  {"id":"pressure","name":"有期限单矿科技压制","sequence":["选择核心兵种与必需科技","侦察后决定进攻目标和窗口","受阻后转经济或换阵容"]},
  {"id":"defend","name":"早期守家","sequence":["按实际威胁安排守军、供能保护与必要静态防御","威胁消退恢复经济"]}],
 "zerg": [
  {"id":"economic","name":"孵化场经济扩张","sequence":["幼虫优先补工蜂并及时王虫","按侦察选择先二矿或先血池","血池、必要气矿、女王与少量防守兵","持续补工蜂、转矿，按威胁分配幼虫"],
   "checkpoint":"经济和兵力共用幼虫；造建筑消耗工蜂。不能把人族兵营开局直接套给虫族。"},
  {"id":"pressure","name":"有期限虫族早压","sequence":["限定出兵批次、科技、瓦斯和进攻窗口","确认对手位置与防御","进攻结束后补工蜂和矿区"]},
  {"id":"defend","name":"应对早压","sequence":["按侦察在工蜂和战斗单位之间调配幼虫","补必要女王、静态防御","安全后恢复工蜂和扩张"]}]
}
PROTOCOL = """
Read game_knowledge.opening_playbook and economy_report every decision. On the first decision choose a macro_plan and explicit phase goals; existing prepared_strategy may only describe a combat tactic. Do not mistake it for a macro opening.
Use task macro_plan (count=1) args: opening=economic|pressure|defend|custom, target_bases=1..8, worker_target=1..90, expand_by_game_s=0..5400 (absolute world.game_s, 0=no deadline), reserve_expansion=bool, reserve_for_s=30..180, exempt_products=[] (<=8 exact own-race product names), reason=1..200 Chinese chars. All fields required. At most one macro_plan per reply. It replaces the previous goal atomically and persists across decisions. It NEVER creates units/buildings or cancels existing tasks. Also issue expand and prerequisites explicitly.
When reserve_expansion=true, the earliest unissued expand is scheduled before optional spending, and its live engine recipe cost is earmarked until issued, cancelled/failed, goal reached or reservation expires. No expand task means no savings and a visible missing-task warning. Worker production below worker_target, urgently needed supply (<=4 free), and your explicit exempt_products may use the savings; these expenditures can delay expansion and are reported. Choose only minimal prerequisite/defense exemptions; lists are products, not tasks. Existing production orders are not cancelled. Set reserve_expansion=false to release savings in emergencies; changing plan never cancels an already ordered expansion. To withdraw an unissued expand explicitly use cancel_expansion args={"task_id":ID}, count=1. Already issued native construction is refused by this cancellation interface. Expiry releases savings visibly; renew only with fresh justification.
Check second-base first-observed start/completion, worker idle capacity, supply-block duration, saturation, requested spending and pending obstacles in economy_report. Issued costs are estimates, NOT confirmed spending/income; samples do not prove all intervening activity. Match actual starting observation, current tech_tree costs/prerequisites and game_s rather than wall time or memorized ladder patch timings. No automatic strategy switch or auto expansion.
"""

def plan_schema(product_names):
    return {"type":"object","additionalProperties":False,
        "properties":{
            "opening":{"type":"string","enum":["economic","pressure","defend","custom"]},
            "target_bases":{"type":"integer","minimum":1,"maximum":8},
            "worker_target":{"type":"integer","minimum":1,"maximum":90},
            "expand_by_game_s":{"type":"number","minimum":0,"maximum":5400},
            "reserve_expansion":{"type":"boolean"},
            "reserve_for_s":{"type":"number","minimum":30,"maximum":180},
            "exempt_products":{"type":"array","maxItems":8,"uniqueItems":True,
                               "items":{"type":"string","enum":sorted(product_names)}},
            "reason":{"type":"string","minLength":1,"maxLength":200}},
        "required":["opening","target_bases","worker_target","expand_by_game_s",
                    "reserve_expansion","reserve_for_s","exempt_products","reason"]}

def validate_plan(args,tree,race):
    fields=set(plan_schema([])["required"])
    if not isinstance(args,dict) or set(args)!=fields:
        raise ValueError("macro_plan requires exactly: "+", ".join(sorted(fields)))
    if tree is None:raise ValueError("macro_plan requires a live engine tech_tree")
    if args["opening"] not in ("economic","pressure","defend","custom"):
        raise ValueError("unknown macro opening")
    for key,lo,hi,integer in (("target_bases",1,8,True),("worker_target",1,90,True),
                              ("expand_by_game_s",0,5400,False),("reserve_for_s",30,180,False)):
        v=args[key]
        if type(v) not in ((int,) if integer else (int,float)) or not math.isfinite(v) or not lo<=v<=hi:
            raise ValueError("macro_plan "+key+" outside supported range")
    if type(args["reserve_expansion"]) is not bool:raise ValueError("reserve_expansion must be bool")
    products=args["exempt_products"]
    if not isinstance(products,list) or len(products)>8 or any(not isinstance(n,str) or n not in tree.units or tree.units[n]["race"]!=race for n in products):
        raise ValueError("exempt_products requires <=8 exact own-race product names")
    if len(set(products))!=len(products):raise ValueError("duplicate exempt_products")
    if not isinstance(args["reason"],str) or not args["reason"].strip() or len(args["reason"])>200:
        raise ValueError("macro_plan requires a nonempty reason <=200 characters")
    return deepcopy(args)

class Economy:
    def __init__(self,tree,race,event):
        self.tree=tree;self.race=race;self.event=event;self.plan=None;self.revision=0
        self.reservation={"state":"not_requested","minerals":0,"gas":0}
        self.reservation_key=None;self.last=None;self.initial=None;self.current={}
        self.covered=0.;self.gaps=0.;self.supply_blocked=0.;self.idle_base_s=0.
        self.worker_training_s=0.;self.base_capacity_s=0.;self.idle_worker_s=0.
        self.second_started=None;self.second_completed=None
        self.samples=deque(maxlen=20);self.last_sample=-999.
        self.spend={};self.recent_spend=deque(maxlen=12);self.budget_task=None

    def set_plan(self,args,now,request_id):
        self.revision+=1
        self.plan=dict(deepcopy(args),revision=self.revision,set_game_s=now,
                       reserve_until_game_s=now+args["reserve_for_s"])
        self.event("macro_plan_set",request_id=request_id,plan=deepcopy(self.plan),
                   handling="Goal recorded; production/expansion still requires explicit tasks.")

    def observe(self,w):
        if self.last and w.game_loop<=self.last["loop"]:return
        workers=[u for u in w.my_units if u.type_id in WORKERS]
        bases=[u for u in w.my_units if u.type_id in TOWNHALLS and not u.flying]
        complete=[u for u in bases if u.done]
        train_abilities=set()
        if self.tree:
            name=self.tree.names[RACE_INFO[self.race]["worker"]]
            train_abilities={self.tree.canonical_ability(r["ability"]) for r in self.tree.units[name]["recipes"]}
        idle=sum(not u.orders for u in complete) if self.race!="zerg" else 0
        training=sum(any(self.tree.canonical_ability(a) in train_abilities for a in u.orders) for u in complete) if self.tree and self.race!="zerg" else 0
        sample=dict(loop=w.game_loop,game_s=w.seconds,workers=len(workers),bases=len(bases),
                    completed_bases=len(complete),minerals=w.minerals,gas=w.vespene,
                    supply_used=w.supply_used,supply_cap=w.supply_cap,
                    supply_blocked=w.supply_used>=w.supply_cap and w.supply_cap<200,
                    idle_bases=idle,training_bases=training,idle_workers=sum(not u.orders for u in workers))
        if self.initial is None:
            self.initial={k:sample[k] for k in ("game_s","workers","bases","minerals","supply_used","supply_cap")}
        if self.last:
            delta=w.seconds-self.last["game_s"];dt=min(delta,2.0)
            self.covered+=dt;self.gaps+=max(0,delta-dt)
            self.supply_blocked+=dt*self.last["supply_blocked"]
            self.idle_base_s+=dt*self.last["idle_bases"]
            self.worker_training_s+=dt*self.last["training_bases"]
            self.base_capacity_s+=dt*self.last["completed_bases"]
            self.idle_worker_s+=dt*self.last["idle_workers"]
        # Sampled milestones, including initial already-built bases; not exact build timestamps.
        if len(bases)>=2 and self.second_started is None:
            self.second_started=round(w.seconds,2)
            self.event("economy_milestone",milestone="second_base_first_observed",game_s=self.second_started)
        if len(complete)>=2 and self.second_completed is None:
            self.second_completed=round(w.seconds,2)
            self.event("economy_milestone",milestone="second_base_completed_first_observed",game_s=self.second_completed)
        self.last=sample;self.current=sample.copy()
        self.current["mineral_saturation"]=[dict(tag=u.tag,assigned=u.assigned_harvesters,ideal=u.ideal_harvesters,
            surplus=max(0,u.assigned_harvesters-u.ideal_harvesters),
            missing=max(0,u.ideal_harvesters-u.assigned_harvesters)) for u in complete][:8]
        if w.seconds-self.last_sample>=30:
            self.samples.append({k:sample[k] for k in ("game_s","workers","bases","completed_bases","minerals","gas","supply_used","supply_cap")})
            self.last_sample=w.seconds
            self.event("economy_sample",sample=deepcopy(self.samples[-1]),
                       supply_blocked_sampled_s=round(self.supply_blocked,2))

    def refresh(self,w,tasks):
        self.budget_task=None
        row=dict(state="not_requested",minerals=0,gas=0)
        p=self.plan
        if p and p["reserve_expansion"]:
            if w.seconds>=p["reserve_until_game_s"]:row["state"]="expired"
            elif sum(u.type_id in TOWNHALLS for u in w.my_units)>=p["target_bases"]:row["state"]="base_goal_started"
            else:
                active=[t for t in tasks if t.active and t.skill=="expand"]
                waiting=[t for t in active if getattr(t.program,"issued",0)==0]
                if waiting:
                    t=waiting[0];entry=t.program.spec["entry"]
                    costs={(r["minerals"],r["gas"]) for r in entry["recipes"]}
                    if len(costs)!=1:
                        row["state"]="unsupported_ambiguous_recipe"
                    else:
                        m,g=next(iter(costs));self.budget_task=t.id
                        row.update(state="saving",task_id=t.id,minerals=m,gas=g,
                                   bank_minerals=w.minerals,shortfall_minerals=max(0,m-w.minerals),
                                   reserve_until_game_s=p["reserve_until_game_s"])
                elif active:row["state"]="expansion_order_issued"
                else:row["state"]="missing_expand_task"
        elif p:row["state"]="released_by_model"
        key=(row["state"],row.get("task_id"),self.revision)
        if key!=self.reservation_key:
            self.event("economy_reservation",**row,revision=self.revision)
            self.reservation_key=key
        self.reservation=row

    def exempt(self,w,t):
        if t.id==self.budget_task:return True
        spec=getattr(t.program,"spec",{});entry=spec.get("entry",{});product=entry.get("id")
        p=self.plan
        if not p:return False
        if entry.get("name") in p["exempt_products"]:return True
        if product==RACE_INFO[self.race]["worker"]:
            # Include this batch's issued/pending worker orders, not just completed workers.
            pending=max(0,getattr(t.program,"issued",0)-len(getattr(t.program,"born",set())))
            return sum(u.type_id in WORKERS for u in w.my_units)+pending<p["worker_target"]
        return product==RACE_INFO[self.race]["supply"] and w.supply_cap-w.supply_used<=4 and w.supply_cap<200

    def holdback(self,w,t,bank,gas):
        if self.budget_task is None or self.exempt(w,t):return 0,0
        return min(bank,self.reservation["minerals"]),min(gas,self.reservation["gas"])

    def spent(self,t,minerals,gas,now):
        if minerals<=0 and gas<=0:return
        spec=getattr(t.program,"spec",{});e=spec.get("entry",{})
        category=("expansion" if t.skill=="expand" else "workers" if e.get("id") in WORKERS else
                  "research" if spec.get("kind")=="research" else
                  "structures" if e.get("structure") else "army_or_custom")
        total=self.spend.setdefault(category,dict(minerals=0,gas=0))
        total["minerals"]+=minerals;total["gas"]+=gas
        self.recent_spend.append(dict(game_s=round(now,2),task_id=t.id,product=e.get("name",t.skill),
                                     minerals=minerals,gas=gas))
    def public(self,tasks=()):
        p=self.plan;warnings=[]
        if not p:warnings.append("尚未选择开局或设置宏观目标，请用macro_plan明确阶段计划。" if self.tree else "旧后端无引擎tech_tree，运营预算接口不可用。")
        else:
            if p["expand_by_game_s"] and self.current.get("game_s",0)>p["expand_by_game_s"] and self.current.get("bases",0)<p["target_bases"]:
                warnings.append("扩张开工目标已逾期；说明敌情/障碍并调整计划。")
            if self.reservation["state"] in ("missing_expand_task","expired","unsupported_ambiguous_recipe"):
                warnings.append("扩张预留状态："+self.reservation["state"]+"；当前未预留资源。")
            if self.current.get("workers",0)<p["worker_target"] and self.current.get("idle_bases",0):
                warnings.append("工人未达目标且存在空闲基地；检查训练任务、资源和升级取舍。")
        if self.current.get("supply_blocked"):warnings.append("当前达到人口上限。")
        if any(r["surplus"] for r in self.current.get("mineral_saturation",[])):
            warnings.append("矿区工人过饱和，检查二矿进度并显式gather转移。")
        # Keep this standard-melee rule as compact current report metadata even
        # when the bounded planner omits the long static game-knowledge prose.
        # It reports feasibility; it never cancels/blocks a model's supply order.
        return deepcopy(dict(budget_supported=self.tree is not None,
            standard_supply_limit=200,
            can_increase_supply_cap=(self.current["supply_cap"]<200 if "supply_cap" in self.current else None),
            plan=p,goal_progress=(dict(
                bases_started=self.current.get("bases",0),target_bases=p["target_bases"],
                base_start_goal_met=self.current.get("bases",0)>=p["target_bases"],
                base_completion_goal_met=self.current.get("completed_bases",0)>=p["target_bases"],
                workers_observed=self.current.get("workers",0),worker_target=p["worker_target"],
                worker_goal_met=self.current.get("workers",0)>=p["worker_target"]) if p else None),
            reservation=self.reservation,initial_observed=self.initial,engine_catalogue=getattr(self.tree,"version",None),current=self.current,
            second_base_first_observed_game_s=self.second_started,second_base_completed_first_observed_game_s=self.second_completed,
            sampled_durations=dict(covered_game_s=round(self.covered,2),unsampled_gap_game_s=round(self.gaps,2),
                supply_blocked_s=round(self.supply_blocked,2),idle_worker_seconds=round(self.idle_worker_s,2),
                idle_base_seconds=round(self.idle_base_s,2) if self.race!="zerg" else None,
                worker_training_base_seconds=round(self.worker_training_s,2) if self.race!="zerg" else None,
                base_capacity_seconds=round(self.base_capacity_s,2) if self.race!="zerg" else None),
            requested_spending=self.spend,recent_requested_spending=list(self.recent_spend),
            checkpoints=list(self.samples)[-6:],warnings=warnings,
            expansion_tasks=[dict(id=t.id,state=t.status,reason=t.reason) for t in tasks if t.skill=="expand"][-4:],
            evidence="Player-local samples; initial_observed is not guaranteed spawn time. Durations use previous sample for <=2s, gaps excluded. Base counts include same-site town halls; they do not prove a separate mining economy. Base-seconds sum parallel bases; zerg larva uptime unavailable. Issued spending can include rejected/retried orders; not confirmed cost, income or return on investment."))
