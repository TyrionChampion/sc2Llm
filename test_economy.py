"""Synthetic native-contract regressions; no model requests and no claimed ladder wins."""
import json
import unittest
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from arena.economy import Economy, validate_plan, OPENINGS
from arena.knowledge import cached_tree, model_knowledge
from arena.tasks import TaskQueue, PlanValidationError
from arena.raw_engine import Entity, Snapshot
from arena.battle_plans import output_schema, capture, revalidate
from arena.context import battle_payload

def plan(**kw):
    return dict(opening="economic",target_bases=2,worker_target=24,
        expand_by_game_s=130,reserve_expansion=True,reserve_for_s=90,
        exempt_products=[],reason="基础防守后优先二矿，避免无目标追加产能",**kw) if not kw else {**plan(),**kw}
def control(**kw):return dict(skill="macro_plan",args=plan(**kw))

class EconomyTests(unittest.TestCase):
    def setUp(self):
        self.tree=deepcopy(cached_tree());self.events=[]
        self.backend=SimpleNamespace(tech_tree=self.tree,races=["terran"],homes=[(30,30)],size=(200,200),
            available_abilities=lambda i,tags:{t:{318,560,524,319,321,422} for t in tags},
            enemy_start=lambda i:(170,170),placement=lambda i,a,t,ps:ps[0] if ps else None,
            placement_with_addon=lambda i,a,t,ps:ps[0] if ps else None)
        self.q=TaskQueue(self.backend,0,lambda s:None,lambda kind,**kw:self.events.append(dict(kind=kind,**kw)))
        self.units=[self.u(1,18,is_building=True,assigned_harvesters=12,ideal_harvesters=16),
                    self.u(2,21,is_building=True)]
        self.units += [self.u(10+i,45,x=28+i/10) for i in range(12)]
        self.units += [Entity(100+i,341,3,70+i,70,100,100,mineral=True) for i in range(6)]
        self.cost=self.tree.units["command_center"]["recipes"][0]["minerals"]
        self.w=Snapshot(224,self.cost,12,23,tuple(self.units),(30,30))
    def u(self,tag,kind,**kw):
        return Entity(tag,kind,1,kw.pop("x",30),kw.pop("y",30),100,100,**kw)
    def acts(self,w=None):
        return [a.action_raw.unit_command.ability_id for a in self.q.tick(w or self.w,set())]
    def test_prioritize_expansion_over_preexisting_training(self):
        self.q.enqueue([{"skill":"train_unit","count":20},{"skill":"expand"},control()],10)
        self.assertEqual(self.acts(),[318])
        self.assertEqual(self.q.economy.reservation["state"],"expansion_order_issued")
        self.assertEqual(self.q.economy.public()["requested_spending"]["expansion"]["minerals"],self.cost)
    def test_no_plan_keeps_existing_strategy(self):
        self.q.enqueue([{"skill":"train_unit"},{"skill":"expand"}],10)
        self.assertEqual(self.acts(),[560])
        self.assertIsNone(self.q.economy.plan)
    def test_savings_survive_multiple_observations_below_cost(self):
        self.q.enqueue([control(),{"skill":"train_unit","count":20},{"skill":"expand"}],10)
        for sec,minerals in [(10,50),(11,150),(12,self.cost-1)]:
            self.assertEqual(self.acts(replace(self.w,game_loop=int(sec*22.4),minerals=minerals)),[])
        self.assertEqual(self.acts(replace(self.w,game_loop=300)),[318])
    def test_live_recipe_changed_cost_not_hardcoded(self):
        self.tree.units["command_center"]["recipes"][0]["minerals"]=400
        self.q.enqueue([control(),{"skill":"train_unit"},{"skill":"expand"}],10)
        self.assertEqual(self.acts(replace(self.w,minerals=350)),[])
        self.assertEqual(self.q.economy.reservation["minerals"],400)
        self.assertEqual(self.acts(replace(self.w,minerals=400,game_loop=300)),[318])
    def test_expiry_visible_and_does_not_cancel_expansion(self):
        self.q.enqueue([control(reserve_for_s=30),{"skill":"train_unit"},{"skill":"expand"}],10)
        self.acts(replace(self.w,minerals=100))
        actions=self.acts(replace(self.w,game_loop=1000,minerals=100))
        self.assertEqual(actions,[560]);self.assertEqual(self.q.economy.reservation["state"],"expired")
        self.assertTrue(any(t.active and t.skill=="expand" for t in self.q.tasks))
        self.assertTrue(any(e["kind"]=="economy_reservation" and e["state"]=="expired" for e in self.events))
    def test_expansion_failure_releases_savings(self):
        self.q.enqueue([control(),{"skill":"expand"},{"skill":"train_unit"}],10)
        expansion=next(t for t in self.q.tasks if t.skill=="expand")
        self.q.update(expansion,"failed","controlled test failure")
        self.assertEqual(self.acts(replace(self.w,minerals=100)),[560])
        self.assertEqual(self.q.economy.reservation["state"],"missing_expand_task")
    def test_no_expand_task_is_visible_no_auto_task(self):
        self.q.enqueue([control()],10);self.assertEqual(self.acts(),[])
        report=self.q.economy.public()
        self.assertEqual(report["reservation"]["state"],"missing_expand_task")
        self.assertEqual(len(self.q.tasks),1);self.assertTrue(report["warnings"])
    def test_explicit_model_release_and_update_survive_wait(self):
        self.q.enqueue([control(),{"skill":"expand"},{"skill":"train_unit"}],10)
        self.acts(replace(self.w,minerals=100))
        self.q.enqueue([control(opening="defend",reserve_expansion=False)],11)
        self.q.enqueue([],11)
        self.assertEqual(self.acts(replace(self.w,game_loop=250,minerals=100)),[560])
        self.assertEqual(self.q.economy.plan["opening"],"defend")
        self.assertEqual(self.q.economy.revision,2)
    def test_worker_and_urgent_supply_exemptions(self):
        for task,ability,supply,extras in [
            ({"skill":"train_worker"},524,12,{}),
            ({"skill":"build_supply"},319,22,{}),
            ({"skill":"train_unit"},560,12,{"exempt_products":["marine"]})]:
            with self.subTest(task=task):
                q=TaskQueue(self.backend,0,lambda s:None)
                q.enqueue([control(**extras),{"skill":"expand"},task],10)
                w=replace(self.w,minerals=150,supply_used=supply)
                acts=q.tick(w,set())
                self.assertEqual([a.action_raw.unit_command.ability_id for a in acts],[ability])
    def test_worker_target_and_nonurgent_supply_do_not_bypass_budget(self):
        self.q.enqueue([control(worker_target=12),{"skill":"expand"},{"skill":"train_worker"},{"skill":"build_supply"}],10)
        self.assertEqual(self.acts(replace(self.w,minerals=150)),[])
    def test_unknown_invalid_or_two_controls_reject_atomically(self):
        for bad in [control(worker_target=True),control(exempt_products=["nexus"]),
                    control(reserve_for_s=float("nan")),control(reason=""),control(target_bases=99)]:
            with self.subTest(bad=bad):
                with self.assertRaises(PlanValidationError):
                    self.q.enqueue([control(),bad,{"skill":"expand"}],10)
                self.assertIsNone(self.q.economy.plan);self.assertEqual(self.q.tasks,[])
        with self.assertRaises(PlanValidationError):
            self.q.enqueue([control(),{"skill":"not_real"}],10)
        self.assertIsNone(self.q.economy.plan)
    def test_late_plan_cannot_change_budget(self):
        before=capture(self.w,self.q)
        self.assertIn("fresh observation",revalidate({"tasks":[control()]},before,self.w,self.q,(30,30),40)[0])
    def test_goal_completion_and_morph_not_false_second_base(self):
        self.q.enqueue([control(),{"skill":"expand"}],10)
        self.q.economy.observe(self.w)
        morphed=replace(self.w,game_loop=250,units=(replace(self.units[0],type_id=132),*self.units[1:]))
        self.q.economy.observe(morphed)
        self.assertIsNone(self.q.economy.second_started)
        w=replace(morphed,game_loop=300,units=(*morphed.units,self.u(90,18,x=65,y=65,is_building=True,progress=.1)))
        self.q.economy.observe(w);self.q.economy.refresh(w,self.q.tasks)
        self.assertIsNotNone(self.q.economy.second_started);self.assertIsNone(self.q.economy.second_completed)
        self.assertEqual(self.q.economy.reservation["state"],"base_goal_started")
        complete=replace(w,game_loop=400,units=(*w.units[:-1],replace(w.units[-1],progress=1)))
        self.q.economy.observe(complete)
        self.assertAlmostEqual(self.q.economy.second_completed,17.86,2)
    def test_metrics_12_workers_gap_idle_saturation_and_bounded_history(self):
        e=self.q.economy;w=replace(self.w,supply_used=23)
        e.observe(w);e.observe(replace(w,game_loop=448))
        report=e.public()
        self.assertEqual(report["initial_observed"]["workers"],12)
        self.assertEqual(report["sampled_durations"]["supply_blocked_s"],2)
        self.assertEqual(report["sampled_durations"]["unsampled_gap_game_s"],8)
        self.assertEqual(report["sampled_durations"]["idle_base_seconds"],2)
        e.set_plan(plan(expand_by_game_s=15),20,1)
        e.observe(replace(w,game_loop=672))
        self.assertTrue(any("逾期" in t for t in e.public()["warnings"]))
        for i in range(1,101):e.observe(replace(w,game_loop=int((40+i*30)*22.4)))
        self.assertEqual(len(e.samples),20);self.assertEqual(len(e.public()["checkpoints"]),6)
    def test_training_orders_measured_and_zerg_not_fake_uptime(self):
        e=self.q.economy
        w=replace(self.w,units=(replace(self.units[0],orders=(524,)),*self.units[1:]))
        e.observe(w);e.observe(replace(w,game_loop=246))
        self.assertGreater(e.public()["sampled_durations"]["worker_training_base_seconds"],.9)
        z=Economy(self.tree,"zerg",lambda *a,**k:None)
        z.observe(replace(self.w,race="zerg"))
        self.assertIsNone(z.public()["sampled_durations"]["idle_base_seconds"])
    def test_schema_playbook_and_prefix_for_all_races(self):
        for race in ("terran","zerg","protoss"):
            knowledge=model_knowledge(self.tree,replace(self.w,race=race))
            payload={"world":self.w.summary(),"game_knowledge":knowledge,"tech_tree":self.tree.public(race),"economy_report":self.q.economy.public()}
            variants=output_schema(payload)["anyOf"][0]["properties"]["tasks"]["items"]["anyOf"]
            branch=next(v for v in variants if v["properties"]["skill"]["enum"]==["macro_plan"])
            self.assertIn("args",branch["required"])
            self.assertEqual(len(knowledge["opening_playbook"]),3)
            ordered=battle_payload(payload)
            self.assertLess(list(ordered).index("game_knowledge"),list(ordered).index("world"))
            self.assertGreater(list(ordered).index("economy_report"),list(ordered).index("world"))
    def test_cancel_expansion_releases_budget_atomically(self):
        self.q.enqueue([control(),{"skill":"expand"},{"skill":"train_unit"}],10)
        expansion=next(t for t in self.q.tasks if t.skill=="expand")
        cancel={"skill":"cancel_expansion","args":{"task_id":expansion.id}}
        with self.assertRaises(PlanValidationError):
            self.q.enqueue([cancel,{"skill":"unknown"}],10)
        self.assertTrue(expansion.active)
        self.q.enqueue([cancel],10)
        self.assertEqual(self.acts(replace(self.w,minerals=100)),[560])
        self.assertEqual(expansion.status,"cancelled")
    def test_cannot_cancel_issued_native_expansion(self):
        self.q.enqueue([control(),{"skill":"expand"}],10);self.acts()
        t=next(t for t in self.q.tasks if t.skill=="expand")
        with self.assertRaises(PlanValidationError):
            self.q.enqueue([{"skill":"cancel_expansion","args":{"task_id":t.id}}],10)
        self.assertTrue(t.active)
    def test_unclear_cost_reported_without_fake_reservation(self):
        entry=self.tree.units["command_center"]
        entry["recipes"].append({**entry["recipes"][0],"minerals":999})
        self.q.enqueue([control(),{"skill":"expand"}],10)
        self.acts(replace(self.w,minerals=10))
        self.assertEqual(self.q.economy.reservation["state"],"unsupported_ambiguous_recipe")
        self.assertTrue(any("unsupported" in w for w in self.q.economy.public()["warnings"]))
    def test_control_can_release_budget_with_full_queue(self):
        from arena.tasks import Task
        self.q.tasks=[Task(100+i,"no_op",intent=("fixture",i)) for i in range(32)]
        self.q.enqueue([control(reserve_expansion=False)],10)
        self.assertFalse(self.q.economy.plan["reserve_expansion"])
    def test_budget_does_not_spam_feedback_each_tick(self):
        self.q.enqueue([control(),{"skill":"expand"},{"skill":"train_unit"}],10)
        for i in range(30):self.acts(replace(self.w,game_loop=224+i,minerals=100))
        statuses=[e for e in self.events if e["kind"]=="task_status"]
        self.assertLess(len(statuses),7)
        self.assertTrue(next(t for t in self.q.summary() if t["skill"]=="train_unit")["budget"])
    def test_current_plan_and_report_reach_actual_commander_payload(self):
        import tempfile
        from arena.commander import Commander
        from pathlib import Path
        class Client:
            compact_battle=True;soft_timeout=15;timeout=40
            def chat(self,system,prompt):return '{"tasks":[],"reason":"受控测试等待"}'
        self.backend.initial=[self.w]
        self.backend.register_action_context=lambda *a:None
        with tempfile.TemporaryDirectory() as d:
            c=Commander(self.backend,0,Client(),opening=False,feedback_dir=d)
            try:
                c.outbox.put((self.w.seconds,{"tasks":[control(),{"skill":"expand"}],"reason":"测试经济开局"},
                              None,0,True,"fixture",1,[]))
                c.tick(self.w)
                self.assertEqual(c.decisions,1);self.assertEqual(c.errors,0)
                rows=[json.loads(x) for x in Path(c.feedback.path).read_text().splitlines()]
                prompt=next(r["prompt"] for r in rows if r["record"]=="request_queued")
                self.assertEqual(prompt["economy_report"]["plan"]["opening"],"economic")
                self.assertEqual(prompt["economy_report"]["initial_observed"]["workers"],12)
                self.assertEqual(len(prompt["game_knowledge"]["opening_playbook"]),3)
                self.assertEqual(c.stats()["economy_report"],c.tasks.economy.public(c.tasks.tasks))
            finally:c.stop()
    def test_viewer_consumes_public_chinese_economy_events(self):
        from llm_output_window import Feed
        self.q.enqueue([control(),{"skill":"expand"}],10);self.acts()
        f=Feed()
        for e in self.events:f.consume({"record":"feedback_event","event":dict(e,game_loop=224)})
        self.assertTrue(any("经济扩张" in r["body"] for r in f.entries))
        self.assertTrue(any("等待建成" in r["body"] for r in f.entries))
    def test_legacy_backend_does_not_advertise_unsupported_plan(self):
        import tempfile
        from arena.commander import Commander
        from test_realtime_runtime import Backend
        class Client:
            def chat(self,*args):return '{"tasks":[]}'
        b=Backend();b.initial=[self.w]
        with tempfile.TemporaryDirectory() as d:
            c=Commander(b,0,Client(),opening=False,feedback_dir=d)
            try:
                self.assertNotIn("On the first decision choose a macro_plan",c.system)
                self.assertFalse(c.tasks.economy.public()["budget_supported"])
                self.assertNotIn("请用macro_plan",str(c.tasks.economy.public()["warnings"]))
                with self.assertRaises(PlanValidationError):c.tasks.enqueue([control()],10)
            finally:c.stop()
    def test_isolation(self):
        other=Economy(self.tree,"terran",lambda *a,**k:None)
        self.q.economy.set_plan(plan(),10,1);self.q.economy.observe(self.w)
        self.assertIsNone(other.plan);self.assertIsNone(other.initial)

if __name__=="__main__":unittest.main()
