"""Non-blocking planning mailbox + persistent executor + independent ReflexNet."""
import json
import queue
import threading
import time
from arena.tasks import TaskQueue, PlanValidationError
from arena.feedback import FeedbackJournal
from arena.composite_skills import PROTOCOL
from arena.match_control import surrender_blockers, surrender_reason, SURRENDER_PROTOCOL
from s2clientprotocol import error_pb2
from arena.reflex import ReflexNet
from arena.raw_engine import GATHER
from arena import battle_plans
from arena.intelligence import Intelligence
from arena.rally import DefensiveRally, RALLY_NAMES
from arena.defense_orders import base_threats
from arena.context import battle_payload, DecisionMemory, EngagementLog
from arena.spatial import Battlefield
from arena.operator_advice import OperatorAdvice

SYSTEM = battle_plans.SYSTEM
OPENING=[{'skill':'train_worker','count':2},{'skill':'supply_depot'},
         {'skill':'barracks'},{'skill':'train_unit','args':{'unit':'marine'},'count':6}]

class ScriptedPlanner:
    """Explicit deterministic baseline, not a claim of LLM intelligence."""
    def __init__(self,delay=0): self.delay=delay; self.calls=0
    def chat(self,system,user):
        del system
        self.calls+=1
        if self.delay: time.sleep(self.delay)
        s=json.loads(user)['world']; own=s['own']; used,cap=s['supply']
        tasks=[]
        if cap-used<6: tasks.append({'skill':'supply_depot'})
        if own.get('SCV',0)<22: tasks.append({'skill':'train_worker','count':2})
        desired_barracks=1 if s['minerals']<300 else 3
        if own.get('Barracks',0)<desired_barracks: tasks.append({'skill':'barracks'})
        tasks.append({'skill':'train_unit','args':{'unit':'marine'},'count':8})
        if own.get('Marine',0)>=8 or s['visible_enemy']:
            tasks.append({'skill':'attack_nearest'})
        return json.dumps({'tasks':tasks,'reason':'scripted baseline economy + marine attack'})

class Commander:
    def __init__(self,backend,index,client,opening=True,interval=6,max_plan_age=30,feedback_dir="logs/feedback",combat_reflex=False,python_catalog=None,repair=None,defense_client=None,combat_micro=False,development=None,tactics_enabled=False,tactical_client=None,display_player=None):
        if tactics_enabled and (defense_client is not None or combat_reflex):
            raise ValueError('army tactics replaces legacy defense/combat reflex ownership')
        if tactical_client is not None and not tactics_enabled:
            raise ValueError('tactical client requires tactics_enabled')
        self.index=index; self.client=client; self.interval=interval; self.backend=backend
        self.display_player=index+1 if display_player is None else display_player
        if type(self.display_player) is not int or self.display_player not in (1,2):raise ValueError('invalid display player')
        self.compact_battle=bool(getattr(client,"compact_battle",False))
        self.request_defense_generations={}; self.defense=None
        self.request_observations={}; self.previous_strategy=None; self.late_plans_accepted=0
        self.last_decision=None
        self.strategic_intent=None; self.strategic_intent_game_s=float('-inf')
        if repair and development:raise ValueError('choose one Python maintenance coordinator')
        self.development=development
        if development:development.bind(self)
        self.repair=repair
        if repair:repair.bind(self)
        self.game_loop=backend.initial[index].game_loop
        self.feedback=FeedbackJournal(self.display_player,feedback_dir)
        self.operator_advice=OperatorAdvice(self.feedback.path,self.display_player,self.feedback.audit)
        self.request_advice_ids={}
        self._seen_feedback_world=None; self.engine_errors=0
        self.intelligence=Intelligence(self.record_feedback); self.request_intel_revisions={}
        self.battlefield=Battlefield(backend,index)
        self.engagements=EngagementLog(self.record_feedback); self.decision_memory=DecisionMemory()
        self.max_plan_age=max_plan_age; self.log=[]; self.decisions=0; self.errors=0
        self.plans_rejected=0; self.thinking=False; self.last_request=float("-inf")
        self.client_fatal=None  # set when the provider refuses at account level; stops new requests
        self.tasks=TaskQueue(backend,index,self.emit,self.record_feedback,python_catalog=python_catalog)
        if defense_client is not None:
            from arena.defense import DefenseCoordinator
            self.interval=max(interval,25)
            self.defense=DefenseCoordinator(defense_client,self.tasks,self.record_feedback,self.feedback.audit)
        self.combat_micro=None
        if combat_micro:
            from arena.combat_micro import CombatMicro
            self.combat_micro=CombatMicro(backend,index,self.record_feedback)
            if self.defense:self.defense.combat_micro_enabled=True
        self.rally=DefensiveRally(backend,index,self.record_feedback)
        self.tactics=None
        if tactics_enabled:
            if tactical_client is not None:
                from arena.battle_agent import BattleAgent
                self.tactics=BattleAgent(backend,index,self.rally,self.record_feedback,self.feedback.audit,tactical_client,self.tasks,combat_micro=combat_micro)
            else:
                from arena.tactics import ArmyTactics
                self.tactics=ArmyTactics(backend,index,self.rally,self.record_feedback,self.feedback.audit,combat_micro=combat_micro)
                self.record_feedback('tactical_baseline_only',reason='显式无模型战术基线；不是双Agent模式，不调用战斗模型')
            self.tasks.army_tactics=self.tactics
            self.interval=max(interval,20)
        self.surrender=None
        self.surrender_available=callable(getattr(backend,'request_surrender',None))
        self.reflex=ReflexNet(backend.homes[index],self.emit,combat_enabled=combat_reflex)
        self.control_policy=dict(combat_reflex_enabled=combat_reflex,
            automatic_script_retreat=combat_reflex,automatic_script_defense=combat_reflex,
            idle_worker_gathering=True,surrender_available=self.surrender_available,
            surrender_allowed=False,surrender_blockers=[],
            python_tactics_enabled=bool(python_catalog),python_tactic_local_retreat=bool(python_catalog))
        self.system=SYSTEM+('\nCombat reflex is explicitly enabled.' if combat_reflex else
            '\nCombat reflex is OFF globally: no global auto-retreat or auto-defense. '
            'Use attack_nearest/move_out for attack-move orders; SC2 native combat remains active.')
        if python_catalog:
            from arena.python_skills import PROTOCOL as PYTHON_PROTOCOL
            self.system+=PYTHON_PROTOCOL
            self.record_feedback('python_skill_catalog',skills=python_catalog.public())
        if repair:
            self.system+='\nFailed Python skills can be repaired in a background developer session. Read skill_repairs (current, independent of feedback pagination). Repairing/failed skills are blocked. When state=ready, decide whether to redeploy using a NEW task with complete args. Source replacement does not automatically allocate troops or restart a mission.'
        self.system+=(SURRENDER_PROTOCOL if self.surrender_available else '\nSurrender is unavailable on this backend; do not issue it.')
        if self.compact_battle:
            self.system=battle_plans.SYSTEM+(SURRENDER_PROTOCOL if self.surrender_available else
                '\nSurrender is unavailable on this backend; do not issue it.')
            self.record_feedback('battle_request_policy',history='fresh session per decision',
                feedback='full events, smaller pages; one oldest event then failures; deferred events retained',
                soft_timeout_s=client.soft_timeout,hard_timeout_s=client.timeout,
                late_plan_check_after_game_s=max_plan_age,absolute_max_game_s=70)
        if self.tasks.tech_tree is None:
            from arena.economy import PROTOCOL as ECONOMY_PROTOCOL
            self.system=self.system.replace(ECONOMY_PROTOCOL,'')
            self.system+='\nThis legacy backend has no engine tech_tree: macro_plan and cancel_expansion are unavailable; do not issue them.'
            from arena.spatial import PROTOCOL as SPATIAL_PROTOCOL
            self.system=self.system.replace(SPATIAL_PROTOCOL,'')
            from arena.defense_orders import PROTOCOL as DEFENSE_PROTOCOL
            self.system=self.system.replace(DEFENSE_PROTOCOL,'')
        self.control_policy['spatial_missions_available']=self.tasks.tech_tree is not None
        self.control_policy['defense_orders_available']=self.tasks.tech_tree is not None
        self.control_policy['defense_orders']=dict(
            enabled=True,
            scope='Model-ordered persistent base defence; no automatic defence, no model call per tick',
            order='defend_base args={reason,timeout_s?}; cancel with cancel_task args={task_id} before switching to offence',
            holds='free combat units only; custom skills, other missions and workers are never taken')
        if self.defense:
            self.control_policy['defense_adjutant']=dict(enabled=True,strategic_interval_game_s=self.interval,
                scope='Local base guard, up to 32 allocated units; no workers; own fog only',
                standing_order='On base threat, local squad immediately guards while independent LLM decides',
                coordination='Python missions pause with frozen deadlines; fresh worker resumes on handback; missing members require redispatch')
            self.system+='\nAn independent defensive adjutant handles local base threats. Read defense_adjutant and paused tasks. You manage economy, technology and strategic missions; do not repeatedly cancel defense. Owned defense units are unavailable for other orders.'
        if self.combat_micro:
            self.control_policy['combat_micro']=dict(enabled=True,abilities=['stim','siege','unsiege'],
                scope='After explicit attack_nearest/move_out/order_units or defensive assignment only; scouts and active Python/JSON/morph units excluded',
                rules='Healthy researched unstimmed bio uses stim individually at contact; tanks deploy at ground contact/arrival and undeploy to advance/withdraw. No automatic strategic attack or retreat.')
            self.system+=battle_plans.COMBAT_MICRO_PROTOCOL
        if development:
            from arena.live_skills import PROTOCOL as DEVELOPMENT_PROTOCOL
            self.system+='\n'+DEVELOPMENT_PROTOCOL
            self.control_policy['live_skill_development']=True
        if self.tactics:
            self.control_policy['army_tactics']=dict(enabled=True,fast_planner=tactical_client is not None,
                ownership='Unassigned army; custom Python/JSON/morph workflows retain priority',
                standing_order='New units assemble safely in batches; local threat checks continue while models think')
            self.system+='\nArmy tactics is ON and supersedes the legacy rally/reflex descriptions above. You manage economy, production, technology and strategic objectives. An independent fast battle LLM chooses free squads and defense/attack/hold/withdraw targets, including direct recall of distant squads to attacked bases. attack_nearest/move_out set a persistent mission; do not repeat them every decision. Read army_tactics. Custom skills keep their units; unassigned units are controlled by tactics even when combat_reflex is OFF.'
            self.system+='\nWhen strategic_intent_policy.required is true, include top-level strategic_intent as exactly attack_nearest, move_out, or defend. Use defend for a deliberate defensive hold; attack_nearest and move_out authorize the corresponding persistent tactical mission.'
            if getattr(self.tactics,'uses_assembly_anchor',True) is False:
                self.control_policy['army_tactics'].update(kind='independent_battle_llm',
                    all_army_attack=True,
                    standing_order='Battle LLM may select distant free squads to defend threatened bases; no scripted strategic dispatch or retreat')
                self.system+='\nBattle agent controls free combat squads. Avoid persistent order_units/Python ownership for routine army movement because that prevents its defense recall. Use attack_nearest/move_out to authorize strategic attack; it decides local defense, reinforcement and retreat. Explicit custom missions still keep ownership.'
        self.emit('[control] combat reflex %s; idle-worker gathering ON' % ('ON' if combat_reflex else 'OFF'))
        self.record_feedback('control_policy',**self.control_policy)
        self.reflex.size=backend.size
        self.inbox=queue.Queue(maxsize=1); self.outbox=queue.Queue(maxsize=1)
        self.stop_event=threading.Event()
        self.worker=threading.Thread(target=self._worker,daemon=True)
        if opening and self.tasks.race!='terran':raise ValueError('scripted opening is Terran-only; use opening=False with LLM for other races')
        if isinstance(client,ScriptedPlanner) and self.tasks.race!='terran':raise ValueError('ScriptedPlanner is explicitly Terran-only')
        if opening:
            self.tasks.enqueue(OPENING,backend.initial[index].seconds,'explicit scripted opening')
        self.worker.start()
    def emit(self,message):
        line='[P%d] %s' % (self.display_player,message)
        self.log.append(line); del self.log[:-100]
        print(line,flush=True)

    def _strategic_intent_status(self,w):
        if self.tactics is None:
            return dict(enabled=False,required=False,reason='army tactics disabled')
        protected=self.tasks.protected()
        combat=sum(1 for u in w.my_units if u.tag not in protected and not u.is_building
                   and u.type_id not in __import__('arena.tech_tree',fromlist=['WORKERS']).WORKERS
                   and (u.ground_dps>0 or u.air_dps>0))
        high_supply=w.supply_used>=140 or (w.supply_cap>=180 and w.supply_used>=.7*w.supply_cap)
        long_idle=w.seconds-self.strategic_intent_game_s>=90
        tactical_strategy=getattr(self.tactics,'strategy',None)
        if isinstance(tactical_strategy,dict):
            active=bool(tactical_strategy.get('attack_authorized'))
        else:
            # ArmyTactics predates BattleAgent's strategy record and exposes
            # the same state as mode/objective instead.
            active=bool(getattr(self.tactics,'mode',None)=='attack' and
                        getattr(self.tactics,'objective',None) in ('enemy_start','visible_enemy'))
        required=bool((high_supply or combat>=8) and not active and (self.strategic_intent is None or long_idle))
        return dict(enabled=True,required=required,high_supply=high_supply,free_combat_units=combat,
                    last_intent=self.strategic_intent,last_intent_game_s=None if self.strategic_intent_game_s==-float('inf') else round(self.strategic_intent_game_s,1),
                    choices=['attack_nearest','move_out','defend'],
                    reason='高人口或自由军力达到门槛，必须明确选择进攻、推进或防守' if required else '当前战略意图仍有效')
    def record_feedback(self,kind,**details):
        if kind=='engagement_event':
            self.feedback.audit(dict(record='engagement_observed',game_loop=self.game_loop,**details))
            return
        if kind=='intelligence_event':
            # Complete sightings are durable; current intel travels independently of task pagination.
            self.feedback.audit(dict(record='intelligence_observed',game_loop=self.game_loop,**details))
            return
        event=self.feedback.add(kind,self.game_loop,**details)
        if kind in ('scout_contact','spatial_mission_report'):
            self.last_request=float('-inf')  # Wake the next model decision; never dispatch a new mission here.
        if kind=='python_skill_failed' and self.repair:self.repair.on_failure(details)
        if kind=='python_skill_failed' and self.development:self.development.on_failure(details)
        return event
    def ingest_feedback(self,w):
        self.game_loop=w.game_loop
        if w is self._seen_feedback_world: return
        self._seen_feedback_world=w
        self.engagements.update(w)
        self.battlefield.update(w)
        if self.intelligence.update(w) and self.defense is None:
            self.last_request=float("-inf")  # Next decision gets new intel, even if the current request is busy.
        if w.feedback:
            for event in w.feedback:
                self.record_feedback(event['kind'],**{k:v for k,v in event.items() if k!='kind'})
                if event['kind']=='engine_action_rejected':self.engine_errors+=1
                elif event['kind']=='surrender_failed' and event['player']==self.index+1:
                    self.errors+=1;self.surrender=None
                    self.emit('[surrender-error] '+event['error'])
                elif event['kind']=='surrender_completed':
                    if self.surrender:self.surrender['state']='completed'
                    self.emit('[match ended] P%d surrendered: %s' % (event['player'],event['reason']))
                    for t in self.tasks.tasks:
                        if t.active:self.tasks.update(t,'cancelled','match ended by surrender')
        elif w.errors:
            # Explicit incomplete-context report, never silently omit a rejection.
            for tag,ability,code in w.errors:
                self.record_feedback('engine_action_rejected',unit_tag=tag,ability_id=ability,
                    result_code=code,result_name=error_pb2.ActionResult.Name(code),
                    context_missing=True,cause='unknown',
                    handling='No correlated command evidence was supplied by the backend.')
                self.engine_errors+=1
                self.emit('[feedback-warning] engine error has no correlated action context')
    def _worker(self):
        while not self.stop_event.is_set():
            try: sent,prompt,request_id,feedback_ids=self.inbox.get(timeout=.2)
            except queue.Empty: continue
            start=time.monotonic(); answer=None; delivered=False
            try:
                self.feedback.audit(dict(record='request_started',request_id=request_id,feedback_ids=feedback_ids))
                answer=self.client.chat(self.system,prompt)
                delivered=True
                # Strict JSON; malformed responses are visible and leave prior tasks intact.
                parsed=json.loads(answer)
                if not isinstance(parsed,dict): raise ValueError('response must be a JSON object')
                if 'tasks' not in parsed:
                    if 'skill' not in parsed: raise ValueError('response needs tasks')
                    reason=parsed.pop('reason','')
                    parsed={'tasks':[parsed],'reason':reason}
                result=(sent,parsed,None,time.monotonic()-start,delivered,answer,request_id,feedback_ids)
            except Exception as e:
                result=(sent,None,'%s: %s' % (type(e).__name__,e),time.monotonic()-start,delivered,answer,request_id,feedback_ids)
            try:
                self.feedback.audit(dict(record='request_finished',request_id=request_id,
                    api_returned=delivered,response=answer,error=result[2],latency_s=result[3],
                    usage=getattr(self.client,'last_usage',None)))
            except Exception as e:
                result=(sent,None,'feedback audit failed: '+str(e),time.monotonic()-start,
                        False,answer,request_id,feedback_ids)
            if not self.stop_event.is_set(): self.outbox.put(result)
    @staticmethod
    def _account_level_failure(error):
        """Return the refusing HTTP status when the provider rejected us account-wide.

        Balance exhaustion, a revoked key or a forbidden project will not recover by waiting,
        so the loop must stop asking (see arena/llm.py FATAL_HTTP_CODES).
        """
        if not error:return None
        text=str(error)
        for code in (401,402,403):
            if 'HTTP %d' % code in text:return code
        return None

    def tick(self,w):
        self.ingest_feedback(w)
        accepting,reason=self._advice_availability()
        self.operator_advice.poll(accepting,reason)
        if self.repair:self.repair.poll()
        if self.development:self.development.poll()
        if self.surrender or getattr(self.backend,'match_control_pending',False):return []
        old_generation=self.defense.generation if self.defense else 0
        defense_actions=self.defense.tick(w) if self.defense else []
        if self.defense and self.defense.generation!=old_generation and not self.defense.active:
            self.last_request=float('-inf')  # Fresh strategy after handback, not a resumption of a stale plan.
        try: sent,plan,error,latency,delivered,raw,request_id,feedback_ids=self.outbox.get_nowait()
        except queue.Empty: pass
        else:
            self.thinking=False
            before=self.request_observations.pop(request_id,None)
            sent_generation=self.request_defense_generations.pop(request_id,0)
            ownership_changed=bool(self.defense and sent_generation!=self.defense.generation)
            combat_plan=bool(plan and (plan.get('skills') or any(
                isinstance(t,dict) and isinstance(t.get('skill'),str) and (t.get('skill') in {'attack_nearest','move_out','morph_unit','cancel_python_task','use_ability'}
                or t.get('skill') in self.tasks.library
                or (self.tasks.python_catalog and t.get('skill') in self.tasks.python_catalog.entries))
                for t in (plan.get('tasks') if isinstance(plan.get('tasks'),list) else []))))
            late=w.seconds-sent>self.max_plan_age
            late_issues=(battle_plans.revalidate(plan,before,w,self.tasks,self.backend.homes[self.index],w.seconds-sent)
                         if late and self.compact_battle and not error else [])
            intel_revision=self.request_intel_revisions.pop(request_id,0)
            if delivered:
                self.feedback.acknowledge(feedback_ids,request_id)
                self.intelligence.acknowledge(intel_revision)
            self.operator_advice.finish(self.request_advice_ids.pop(request_id,[]),request_id,delivered)
            if error:
                self.errors+=1; self.emit('[planner-error] '+error)
                self.record_feedback('model_response_invalid' if delivered else 'model_request_failed',
                    request_id=request_id,error=error,response=raw,
                    handling='Existing tasks continue; no new plan accepted.')
                account_failure=self._account_level_failure(error)
                if account_failure and not self.client_fatal:
                    # Observable failure for the operator: the account cannot serve requests, so no
                    # further decision will arrive. Stop polling instead of logging the same error
                    # every interval while the match runs without an AI.
                    self.client_fatal=account_failure
                    self.record_feedback('model_credentials_failed',request_id=request_id,
                        error=error,status=account_failure,
                        handling='No further model requests will be sent for this player. Existing tasks keep '
                                 'executing, but no new plan can arrive: top up / fix the credential and restart the match.',
                        account_level=account_failure)
            elif ownership_changed and combat_plan:
                self.plans_rejected+=1;self.last_request=float('-inf')
                self.record_feedback('plan_expired',request_id=request_id,plan=plan,
                    error='Defense ownership changed while main planner was thinking; combat plan requires fresh observation',
                    handling='Entire plan rejected atomically. Economic-only plans remain admissible.')
                self.emit('[planner] defense ownership changed; stale combat plan rejected')
            elif late and (not self.compact_battle or late_issues):
                self.plans_rejected+=1
                self.emit('[planner] discarded stale plan age=%.1fs; %s' % (w.seconds-sent,'; '.join(late_issues) or 'existing queue continues'))
                self.last_request=float('-inf')
                self.record_feedback('plan_expired',request_id=request_id,plan=plan,
                    age_game_s=w.seconds-sent,max_age_game_s=70 if self.compact_battle else self.max_plan_age,
                    revalidation_issues=late_issues,
                    handling='Entire stale plan rejected; existing tasks continue.')
            else:
                try:
                    allowed={'tasks','skills','reason','strategic_intent'}
                    if self.development:allowed.add('develop_skill')
                    extra=set(plan)-allowed
                    if extra: raise ValueError('unsupported plan fields: '+', '.join(sorted(extra)))
                    if 'skills' in plan and not isinstance(plan['skills'],list):
                        raise ValueError('skills must be a list')
                    developing='develop_skill' in plan
                    if developing:
                        if plan.get('tasks')!=[] or plan.get('skills'):
                            raise ValueError('develop_skill requires tasks=[] and no JSON definitions; previous tasks continue')
                        self.development.validate(plan['develop_skill'])
                    intent=plan.get('strategic_intent')
                    if intent is not None and intent not in ('attack_nearest','move_out','defend'):
                        raise ValueError('strategic_intent must be attack_nearest, move_out or defend')
                    tasks=list(plan.get('tasks') or [])
                    inferred=next((t.get('skill') for t in tasks if isinstance(t,dict) and t.get('skill') in ('attack_nearest','move_out')),None)
                    if intent is None:intent=inferred
                    intent_status=self._strategic_intent_status(w)
                    reason=surrender_reason(plan)
                    if reason is None and not developing and intent_status['required'] and intent is None:
                        raise ValueError('strategic_intent required: choose attack_nearest, move_out or defend')
                    if reason is None and not developing and intent=='defend' and inferred:
                        raise ValueError('strategic_intent defend conflicts with attack task')
                    if reason is None and not developing and intent in ('attack_nearest','move_out') and inferred is None:
                        if len(tasks)>=16:raise ValueError('strategic_intent needs room for its attack task')
                        tasks.append({'skill':intent,'count':1})
                        plan=dict(plan,tasks=tasks)
                    if reason is not None:
                        if not self.surrender_available:raise ValueError('surrender is not supported by this backend')
                        blockers=self._surrender_blockers(w)
                        if blockers:raise ValueError('surrender rejected; still in the game: '+'; '.join(blockers))
                        self.backend.request_surrender(self.index,reason,request_id)
                        self.surrender=dict(state='requested',reason=reason,request_id=request_id)
                        self.record_feedback('surrender_requested',player=self.index+1,reason=reason,request_id=request_id)
                        self.emit('[surrender] requested: '+reason)
                    elif developing:
                        self.development.request(plan['develop_skill'],w)
                    else:
                        self.tasks.enqueue(plan['tasks'],w.seconds,'planner',request_id=request_id,skills=plan.get('skills'))
                        if intent=='defend' and self.tactics:self.tactics.mission('defend')
                        if intent is not None:
                            self.strategic_intent=intent;self.strategic_intent_game_s=w.seconds
                            self.record_feedback('strategic_intent',intent=intent,request_id=request_id,
                                                 handling='主指挥明确选择战略方向；攻击仍需通过现有任务与动作校验。')
                    self.decisions+=1
                    self.decision_memory.add(w.game_loop,plan)
                    self.previous_strategy=battle_plans.bounded_note(plan.get('reason',''),600)
                    self.last_decision={'game_s':w.seconds,'reason':plan.get('reason'),'request_id':request_id}
                    if late:
                        self.late_plans_accepted+=1
                        self.record_feedback('late_plan_revalidated',request_id=request_id,age_game_s=w.seconds-sent,
                            request_world=before['world'].summary(),current_world=w.summary(),plan=plan,
                            handling='Mechanical state checks passed; not proof of strategic quality.')
                        self.emit('[planner] late plan revalidated against current observation age=%.1fs'%(w.seconds-sent))
                    self.emit('[planner] accepted latency=%.2fs reason=%s' % (latency,str(plan.get('reason',''))[:120]))
                except (ValueError,KeyError,TypeError,OSError) as e:
                    self.errors+=1; self.emit('[planner-error] '+str(e))
                    self.record_feedback('plan_rejected',request_id=request_id,plan=plan,
                        error=str(e),issues=e.issues if isinstance(e,PlanValidationError) else [],
                        handling='Entire plan rejected atomically; existing tasks continue.')
        if self.surrender or getattr(self.backend,'match_control_pending',False):return []
        reactions,reserved=self.reflex.tick(w,self.tasks.protected())
        reserved.update(self.tasks.defense_tags)
        macro=self.tasks.tick(w,reserved)
        contexts=[dict(source='worker_gather' if a.action_raw.unit_command.ability_id==GATHER else 'reflex',behavior=self.reflex.state.get(
                    a.action_raw.unit_command.unit_tags[0],'unknown'),task_id=None) for a in reactions]
        contexts+=self.tasks.action_contexts
        tactical_actions=[]
        if self.tactics:
            protected=self.tasks.protected()|reserved|{tag for a in macro for tag in a.action_raw.unit_command.unit_tags}
            tactical_actions=self.tactics.tick(w,protected)
        rally=self.rally.tick(w,{tag for a in macro for tag in a.action_raw.unit_command.unit_tags}|self.tasks.rally_owned_tags(),
                              tactical_point=self.tactics.anchor if self.tactics else None,
                              tactical_control=self.tactics is not None and getattr(self.tactics,'uses_assembly_anchor',True))
        contexts += [dict(source="defensive_rally",task_id=None) for a in rally]
        contexts+=self.defense.action_contexts if self.defense else []
        contexts+=self.tactics.action_contexts if self.tactics else []
        batch=reactions+macro+rally+defense_actions+tactical_actions
        if self.combat_micro:
            protected=self.tasks.protected()-self.tasks.defense_tags
            protected-={tag for t in self.tasks.tasks if t.active and t.skill=='order_units' for tag in t.program.tags}
            if self.tactics:
                for tag,intent in list(self.combat_micro.intents.items()):
                    if intent['context'].get('source')=='army_tactics' and tag not in self.tactics.tags:
                        del self.combat_micro.intents[tag]
            batch,contexts=self.combat_micro.apply(w,batch,contexts,protected,self.defense)
        self.backend.register_action_context(self.index,[(a.SerializeToString(),ctx) for a,ctx in zip(batch,contexts)])
        if self.surrender_available and self.surrender is None:
            # Conceding is allowed only when the observed position has no recovery path: an early
            # surrender with a live army is a misread, not reasonable behaviour. The blockers are
            # handed to the model so it knows what still keeps it in the game.
            blockers=self._surrender_blockers(w)
            self.control_policy['surrender_allowed']=not blockers
            self.control_policy['surrender_blockers']=blockers
        if (self.client_fatal is None and not self.thinking
                and (w.seconds-self.last_request>=self.interval or getattr(self.client,"on_demand",False))
                and (not callable(getattr(self.client,"ready_for_request",None)) or self.client.ready_for_request())):
            feedback=(self.feedback.batch(max_events=8,max_chars=8000,prioritize_failures=True)
                      if self.compact_battle else self.feedback.batch())
            payload={'world':w.summary(),'intelligence':self.intelligence.public(w),'battlefield':self.battlefield.public(w),'defensive_rally':self.rally.public(),'engagement_log':self.engagements.public(),
                     'strategic_intent':self.strategic_intent,'strategic_intent_policy':self._strategic_intent_status(w),
                     'spatial_reports':[t.program.result(t) for t in self.tasks.tasks if not t.active and t.skill in ('scout','order_units')][-8:],
                     'defense_orders':[dict(t.program.summary(),task_id=t.id,task_state=t.status)
                         for t in self.tasks.tasks if t.skill=='defend_base' and t.active and t.program],
                     'defense_report':base_threats(w,self.backend,self.index,
                         ordered_tags={t.program.base_tag for t in self.tasks.tasks
                                       if t.skill=='defend_base' and t.active and t.program and t.program.base_tag}),
                     'economy_report':self.tasks.economy.public(self.tasks.tasks),
                     'decision_memory':self.decision_memory.public(),'prepared_strategy':getattr(self.client,'prepared_strategy',None),'active_tasks':self.tasks.summary(),'control_policy':self.control_policy,
                     'recent_events':self.log[-8:],'feedback':feedback,
                     'skill_repairs':self.repair.public() if self.repair else {},
                     'live_skill_development':self.development.public() if self.development else {'enabled':False},
                     'python_skill_library':self.tasks.python_catalog.public() if self.tasks.python_catalog else [],
                     'skill_library':list(self.tasks.library.values())}
            if self.defense:payload['defense_adjutant']=self.defense.public()
            if self.combat_micro:payload['combat_micro']=self.combat_micro.public()
            if w.race=='zerg' and self.tasks.tech_tree is not None:
                from arena.queen import report as injection_report
                payload['zerg_injection']=injection_report(w,self.tasks)
            if self.tactics:payload['army_tactics']=self.tactics.public()
            advice=self.operator_advice.batch()
            if advice['messages']:payload['operator_advice']=advice
            if self.tasks.tech_tree is not None:
                from arena.native_abilities import report as ability_report
                payload['native_abilities']=ability_report(w,self.tasks)
                from arena.native_building_cancel import report as cancellation_report
                payload['native_building_cancellation']=cancellation_report(self.tasks)
                from arena.native_queue_cancel import report as queue_cancellation_report
                payload['native_queue_cancellation']=queue_cancellation_report(self.tasks)
                from arena.native_morph_cancel import report as morph_cancellation_report
                payload['native_morph_cancellation']=morph_cancellation_report(self.tasks)
                from arena.knowledge import model_knowledge
                from arena.macro import site_summary
                from arena.rally import producer_inventory
                inventory,labels=producer_inventory(self.backend,w,self.index)
                payload.update(tech_tree=self.tasks.tech_tree.public(w.race),game_knowledge=model_knowledge(self.tasks.tech_tree,w),
                    expansion_sites=site_summary(w,self.backend.homes[self.index]),
                    rally_producers=inventory,
                    rally_orders=[t.program.public() for t in self.tasks.rally_orders()],
                    rally_landmarks=sorted(RALLY_NAMES),
                    gas_policy={'workers_per_refinery':self.tasks.gas_workers_per_refinery,'allocated':len(self.tasks.gas_worker_tags)})
            if self.compact_battle:payload=battle_plans.compact_payload(payload,self.previous_strategy)
            payload=battle_payload(payload)
            prompt=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
            self.feedback.requests+=1; request_id=self.feedback.requests
            ids=[e['id'] for e in feedback['events']]
            self.request_intel_revisions[request_id]=self.intelligence.revision
            if self.defense:self.request_defense_generations[request_id]=self.defense.generation
            self.feedback.audit(dict(record='request_queued',request_id=request_id,game_loop=w.game_loop,
                feedback_ids=ids,system=self.system,prompt=payload))
            if self.compact_battle:self.request_observations[request_id]=battle_plans.capture(w,self.tasks)
            advice_ids=[m['id'] for m in advice['messages']]
            self.request_advice_ids[request_id]=advice_ids
            if advice_ids:self.operator_advice.sent(advice_ids,request_id)
            self.inbox.put_nowait((w.seconds,prompt,request_id,ids))
            self.thinking=True; self.last_request=w.seconds
        return batch
    def stats(self):
        return {'army_tactics':self.tactics.public() if self.tactics else {'enabled':False},
                'operator_advice':self.operator_advice.public(),
                'combat_micro':self.combat_micro.public() if self.combat_micro else {'enabled':False},
                'defense_adjutant':self.defense.public() if self.defense else {'enabled':False},
                'economy_report':self.tasks.economy.public(self.tasks.tasks),
                'decisions':self.decisions,'errors':self.errors,'thinking':self.thinking,
                'model_credentials_failed':self.client_fatal,
                'model_decisions_available':self.client_fatal is None,
                'last_decision':self.last_decision,'intelligence_revision':self.intelligence.revision,'defensive_rally':self.rally.public(),
                'surrender':self.surrender,'skill_repairs':self.repair.public() if self.repair else {},
                'live_skill_development':self.development.public() if self.development else {'enabled':False},
                'planner_request':self.client.request_status() if callable(getattr(self.client,'request_status',None)) else {},
                'late_plans_accepted':self.late_plans_accepted,
                'stale_plans':self.plans_rejected,'active_tasks':self.tasks.summary(),
                     'python_skill_library':self.tasks.python_catalog.public() if self.tasks.python_catalog else [],
                     'skill_library':list(self.tasks.library.values()),
                'reflex':dict(self.reflex.counts),'combat_reflex_enabled':self.reflex.combat_enabled,
                'completed':sum(t.status=='completed' for t in self.tasks.tasks),
                'failed':sum(t.status=='failed' for t in self.tasks.tasks),
                'engine_errors':self.engine_errors,'feedback':self.feedback.stats()}
    def _surrender_blockers(self,w):
        policy=getattr(self.backend,'surrender_blockers_for',None)
        return policy(w,self.index) if callable(policy) else surrender_blockers(w)
    def _advice_availability(self):
        if self.surrender or self.client_fatal or getattr(self.client,'closed',False) or getattr(self.client,'fatal_error',None):
            return False,'模型已停止，无法接收下一轮建议'
        limit=getattr(self.client,'request_limit',None)
        budget=getattr(self.client,'budget',None)
        if ((limit is not None and getattr(self.client,'attempts',0)>=limit)
                or (budget is not None and budget.used>=budget.limit)):
            return False,'本局模型请求已用完，没有下一轮决策'
        return True,''

    def stop(self):
        self.operator_advice.close()
        if self.tactics:self.tactics.close()
        if self.defense:self.defense.close()
        if self.repair:self.repair.close()
        if self.development:self.development.close()
        self.tasks.close()
        self.feedback.audit(dict(record="commander_stopped",feedback=self.feedback.stats()))
        self.stop_event.set()
        self.worker.join(timeout=.3)
        if self.worker.is_alive(): self.emit('[planner] in-flight request still running; result will be discarded')
