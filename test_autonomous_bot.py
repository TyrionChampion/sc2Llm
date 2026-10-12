"""Budget, observation-only surrender and real-result curriculum contracts."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from arena.autonomous_bot import (BOT_RULES, BotPlanner, BudgetExhausted, Curriculum,
    DefeatGuard, RequestBudget, economical_payload,match_diagnostics,contact_table,
    production_decision_needed)
from arena.autonomous_review import workspace_files, checked_tests
from arena.raw_engine import RawArena, MARINE, SCV
from arena.commander import Commander
from arena.match_control import surrender_blockers
from arena.spatial import validate_args
from test_realtime_runtime import Backend, world, unit, CC
from test_surrender import Controller, observation


class Client:
    timeout=60;calls=0;attempts=0;closed=False
    last_metadata={'usage':{'input_tokens':11,'output_tokens':3}}
    def chat(self,system,user):
        self.attempts+=1;self.calls+=1;self.payload=json.loads(user)
        return '{"tasks":[]}'
    def close(self):self.closed=True


def raw_fixture():
    arena=RawArena.__new__(RawArena)
    arena.controllers=[Controller()]
    arena.last=[SimpleNamespace(last=lambda:False)]
    arena.proto=lambda i:observation()
    arena.snapshots=lambda:[world([])]
    arena._pending_surrender=arena._terminal_result=arena._surrender_replay=None
    arena.surrender_report=None
    return arena


class AutonomousTests(unittest.TestCase):
    def test_global_budget_counts_all_roles_and_never_exceeds(self):
        budget=RequestBudget(3)
        for role in ('battle','review','repair'):budget.reserve(role)
        with self.assertRaises(BudgetExhausted):budget.reserve('battle')
        self.assertEqual(budget.report(),{'limit':3,'attempted':3,
            'by_role':{'battle':1,'review':1,'repair':1}})

    def test_per_match_limit_and_static_context_keeps_costs(self):
        client=Client();budget=RequestBudget(5);planner=BotPlanner(client,budget,'timing',1)
        payload={'tech_tree':{'units':[{'name':'marine','role':'long prose',
            'recipes':[{'minerals':50,'required_building':21}]}]},'active_tasks':[{'id':8}],
            'feedback':{'events':[{'kind':'failed','reason':'real evidence'}]}}
        planner.chat('ignored',json.dumps(payload))
        self.assertEqual(client.payload['tech_tree']['units'][0]['recipes'][0]['minerals'],50)
        self.assertEqual(client.payload['active_tasks'],payload['active_tasks'])
        self.assertEqual(client.payload['feedback'],payload['feedback'])
        self.assertEqual(planner.usage['input_tokens'],11)
        self.assertFalse(planner.ready_for_request())
        with self.assertRaises(BudgetExhausted):planner.chat('',json.dumps(payload))
        self.assertEqual(budget.used,1)

    def test_oversize_is_fatal_without_spending_request(self):
        planner=BotPlanner(Client(),RequestBudget(5),'x')
        with self.assertRaises(BudgetExhausted):planner.chat('',json.dumps({'evidence':'x'*45000}))
        self.assertTrue(planner.fatal_error);self.assertFalse(planner.ready_for_request())
        self.assertEqual(planner.budget.used,0)

    def test_intelligence_wakeup_cannot_burn_budget_before_25_seconds(self):
        planner=BotPlanner(Client(),RequestBudget(5),'x')
        planner.policy={'game_s':100}
        planner.chat('',json.dumps({'world':{'game_s':100}}))
        planner.policy['game_s']=110
        self.assertFalse(planner.ready_for_request())
        planner.policy['game_s']=125
        self.assertTrue(planner.ready_for_request())
        planner.policy={'game_s':110,'clear_disadvantage':{'kind':'no_recovery'}}
        self.assertTrue(planner.ready_for_request())

    def test_three_transient_failures_stop_and_count_requests(self):
        class Broken(Client):
            def chat(self,*args):
                self.attempts+=1;raise RuntimeError('network timeout')
        planner=BotPlanner(Broken(),RequestBudget(5),'x')
        for _ in range(3):
            with self.assertRaises(RuntimeError):planner.chat('','{}')
        self.assertFalse(planner.ready_for_request());self.assertEqual(planner.budget.used,3)

    def test_adaptive_cadence_spreads_budget_and_accelerates_visible_combat(self):
        planner=BotPlanner(Client(),RequestBudget(26),'x',adaptive_cadence=True)
        planner.client.attempts=6
        planner.last_request_game_s=180
        planner.policy={'game_s':205,'max_game_s':1800}
        self.assertFalse(planner.ready_for_request())
        self.assertGreater(planner.decision_interval(),80)
        planner.policy['decision_urgent']=True
        self.assertTrue(planner.ready_for_request())
        planner.policy={'game_s':270,'max_game_s':1800}
        self.assertTrue(planner.ready_for_request())
        planner.client.attempts=24
        planner.policy['clear_disadvantage']={'kind':'no_recovery'}
        self.assertFalse(planner.ready_for_request())

    def test_idle_production_with_surplus_wakes_model_without_bypassing_call_cap(self):
        idle=unit(7,21,is_building=True)
        self.assertTrue(production_decision_needed(world([idle],205,minerals=300)))
        self.assertFalse(production_decision_needed(world([idle],205,minerals=299)))
        self.assertFalse(production_decision_needed(world([replace(idle,orders=(560,))],205)))
        self.assertFalse(production_decision_needed(world([replace(idle,progress=.5)],205)))
        planner=BotPlanner(Client(),RequestBudget(26),'x',adaptive_cadence=True)
        planner.client.attempts=6;planner.last_request_game_s=180
        planner.policy={'game_s':205,'max_game_s':1800,'production_decision_due':False}
        self.assertFalse(planner.ready_for_request())
        planner.policy['production_decision_due']=True
        self.assertTrue(planner.ready_for_request())
        planner.client.attempts=24
        self.assertFalse(planner.ready_for_request())

    def test_quota_failure_stops_immediately(self):
        class Quota(Client):
            def chat(self,*args):self.attempts+=1;raise RuntimeError('usage limit reached')
        planner=BotPlanner(Quota(),RequestBudget(5),'x')
        with self.assertRaises(RuntimeError):planner.chat('','{}')
        self.assertFalse(planner.ready_for_request())

    def test_context_does_not_mutate_observations(self):
        payload={'battlefield':{'areas':[{'id':'home'},{'id':'empty'},{'id':'contact',
            'visible_enemy_units':2}], 'regions':[{'id':'r1'}]}}
        result=economical_payload(payload)
        self.assertEqual(len(payload['battlefield']['areas']),3)
        self.assertEqual(result['battlefield']['omitted_areas'],1)
        self.assertEqual(result['battlefield']['omitted_regions'],1)
        self.assertEqual(len(result['battlefield']['areas']),2)

    def test_context_reduces_only_declared_history_not_current_errors(self):
        payload={'world':{'current_units':'x'*39500},'feedback':{'events':[{'id':5,'error':'real'}]},
                 'decision_memory':{'recent':[{'id':i} for i in range(8)]}}
        result=economical_payload(payload)
        self.assertEqual(result['world'],payload['world'])
        self.assertEqual(result['feedback'],payload['feedback'])
        self.assertEqual(result['decision_memory']['recent'],[{'id':i} for i in range(4,8)])
        self.assertEqual(result['context_policy']['omitted_history'],
                         [{'field':'decision_memory.recent','omitted':4}])

    def test_large_context_omits_derived_suggestions_keeps_catalogue(self):
        payload={'world':{'current':'x'*39000},'technology_report':{
            'candidates':[{'upgrade':'combat_shield'}],'omitted':2},
            'tech_tree':{'research':[{'name':'combat_shield','minerals':100}]}}
        result=economical_payload(payload)
        self.assertEqual(result['technology_report']['candidates'],[])
        self.assertEqual(result['technology_report']['omitted'],3)
        self.assertEqual(result['tech_tree'],payload['tech_tree'])
        self.assertEqual(result['world'],payload['world'])

    def test_static_prose_pressure_does_not_discard_live_errors_or_contacts(self):
        payload={'world':{'current_units':'x'*36000},
                 'game_knowledge':{'economy':'static advice '*800},
                 'feedback':{'events':[{'id':7,'error':'actual rejection'}]},
                 'intelligence':{'contacts':[{'unit':'CommandCenter','pos':[30,40]}]},
                 'active_tasks':[{'id':2,'skill':'order_units','state':'running'}],
                 'spatial_reports':[{'task_id':1,'state':'failed','reason':'confirmed dead',
                                     'meaning':'repeated historical explanation '*30}]}
        result=economical_payload(payload)
        for key in ('world','feedback','intelligence','active_tasks'):
            self.assertEqual(result[key],payload[key])
        self.assertLess(len(json.dumps(result,ensure_ascii=False,separators=(',',':')))+len(BOT_RULES),45000)
        self.assertEqual(result['spatial_reports'][0]['reason'],'confirmed dead')
        self.assertTrue(result['context_policy']['omitted_static_advice'])
        self.assertIn('meaning',payload['spatial_reports'][0])

    def test_dense_death_alerts_keep_every_contact_in_lossless_tables(self):
        alerts=[]
        for n in range(8):
            contacts=[dict(tag=1000+i,unit='Marine',type_id=48,building=False,
                pos=[130+i,50+n/10],last_seen_game_s=316.6964285714286+n,visible=True)
                for i in range(12)]
            alerts.append(dict(revision=n,kind='own_unit_death_observed',game_s=316+n,
                               nearby_recent_contacts=contacts))
        original={'world':{'current':'x'*28000},'intelligence':{'pending_alerts':alerts},
                  'feedback':{'events':[{'id':7,'error':'actual rejection'}]}}
        packed=economical_payload(original)
        self.assertEqual(packed['world'],original['world'])
        self.assertEqual(packed['feedback'],original['feedback'])
        table=packed['intelligence']['pending_alerts']
        decoded=[table['common']|dict(zip(table['columns'],row)) for row in table['rows']]
        for before,after in zip(alerts,decoded):
            table=after['nearby_recent_contacts']
            self.assertIsInstance(table,dict)
            restored=[table['common']|dict(zip(table['columns'],row)) for row in table['rows']]
            self.assertEqual(restored,before['nearby_recent_contacts'])
            self.assertEqual(after['revision'],before['revision'])
        self.assertLess(len(json.dumps(packed,ensure_ascii=False,separators=(',',':')))+len(BOT_RULES),45000)
        self.assertIsInstance(original['intelligence']['pending_alerts'][0]['nearby_recent_contacts'],list)

    def test_contact_constants_do_not_conflate_false_and_zero(self):
        rows=[{'repeated_prose':'z'*100,'value':v} for v in (False,0,False)]
        table=contact_table(rows)
        restored=[table['common']|dict(zip(table['columns'],row)) for row in table['rows']]
        self.assertIs(type(restored[0]['value']),bool)
        self.assertIs(type(restored[1]['value']),int)

    def test_mixed_alert_shapes_preserve_order_missing_fields_and_current_facts(self):
        contacts=[dict(tag=1000+n,type_id=48,unit='Marine',building=False,
                       pos=[120+n,60],last_seen_game_s=610.25,visible=True) for n in range(12)]
        alerts=[dict(revision=n,kind='own_unit_death_observed',game_s=610+n,
                     unit={'tag':2000+n,'unit':'SCV','pos':[117+n,60]},
                     nearby_recent_contacts=contacts,
                     nearby_recent_contacts_summary={'total':23,'counts_by_type':{'Marine':6,'Marauder':4},
                         'omitted_details':11,'full_record':'intelligence_observed journal'},
                     attribution='Nearby sightings do not establish a killer or hidden army totals.')
                for n in range(7)]
        alerts.insert(3,dict(revision=99,kind='enemy_contact',game_s=613,contacts=[],optional=None))
        original={'world':{'current_units':'x'*35000},'intelligence':{'pending_alerts':alerts},
                  'feedback':{'events':[{'id':7,'error':'actual current rejection'}]}}
        planner=BotPlanner(Client(),RequestBudget(26),'strategy')
        planner.policy={'game_s':627,'max_game_s':1800,'max_wall_s':1860}
        planner.chat('',json.dumps(original))
        def decode(value):
            if isinstance(value,dict):
                if value.get('encoding')=='columns_rows_common_v1':
                    return [decode(value['common']|dict(zip(value['columns'],row))) for row in value['rows']]
                if value.get('encoding')=='key_groups_v1':
                    groups=[decode(group) for group in value['groups']]
                    return [groups[group][row] for group,row in value['order']]
                return {key:decode(item) for key,item in value.items()}
            if isinstance(value,list):return [decode(item) for item in value]
            return value
        self.assertEqual(decode(planner.client.payload['intelligence']['pending_alerts']),alerts)
        self.assertEqual(planner.client.payload['world'],original['world'])
        self.assertEqual(planner.client.payload['feedback'],original['feedback'])
        self.assertEqual(original['intelligence']['pending_alerts'],alerts)

    def test_current_contact_groups_fit_with_full_prompt_and_iteration_policy(self):
        contacts=[dict(unit='Marine' if n%2 else 'Marauder',state='visible',count=n+1,
                       area_center=[130+n,50],age_min_s=0.0,age_max_s=float(n)) for n in range(14)]
        payload={'world':{'game_s':582.5,'current_units':'x'*39500},
                 'intelligence':{'contacts':contacts},
                 'feedback':{'events':[{'id':7,'error':'actual current rejection'}]}}
        planner=BotPlanner(Client(),RequestBudget(26),'observed strategy')
        planner.policy={'game_s':582.5,'max_game_s':1800,'max_wall_s':1860,
                        'clear_disadvantage':None,'decision_urgent':True}
        planner.chat('',json.dumps(payload))
        packed=planner.client.payload['intelligence']['contacts']
        restored=[packed['common']|dict(zip(packed['columns'],row)) for row in packed['rows']]
        self.assertEqual(restored,contacts)
        self.assertEqual(planner.client.payload['world'],payload['world'])
        self.assertEqual(planner.client.payload['feedback'],payload['feedback'])
        self.assertEqual(planner.budget.used,1)

    def test_full_prompt_budget_trims_old_strategy_milestones_not_current_evidence(self):
        milestones=[{'sequence':n,'note':'old strategy '*60} for n in range(8)]
        original={'world':{'game_s':627,'current_units':'x'*39000},
                  'decision_memory':{'milestones':milestones},
                  'intelligence':{'contacts':[{'unit':'SiegeTank','count':3}]},
                  'feedback':{'events':[{'id':9,'error':'actual current rejection'}]}}
        planner=BotPlanner(Client(),RequestBudget(26),'current target')
        planner.policy={'game_s':627,'max_game_s':1800,'max_wall_s':1860}
        planner.chat('',json.dumps(original))
        for field in ('world','intelligence','feedback'):
            self.assertEqual(planner.client.payload[field],original[field])
        self.assertEqual(planner.client.payload['decision_memory']['milestones'],milestones[-1:])
        omissions=planner.client.payload['context_policy']['omitted_history']
        self.assertTrue(any(row['field']=='decision_memory.milestones' and row['omitted']==7 for row in omissions))
        self.assertEqual(original['decision_memory']['milestones'],milestones)
        self.assertEqual(planner.budget.used,1)

    def losing_world(self,sec):
        base=replace(unit(1,CC,is_building=True,health=200),health_max=1500)
        troops=[unit(2,SCV),unit(3,MARINE,ground_dps=10)]
        enemies=[unit(10+i,MARINE,32,30,4,ground_dps=20) for i in range(3)]
        return world([base,*troops,*enemies],sec)

    def test_live_army_does_not_block_sustained_overwhelming_loss(self):
        guard=DefeatGuard()
        self.assertTrue(surrender_blockers(self.losing_world(90)))
        self.assertIsNone(guard.observe(self.losing_world(91)))
        self.assertIsNone(guard.observe(self.losing_world(105)))
        evidence=guard.observe(self.losing_world(107))
        self.assertEqual(evidence['kind'],'sustained_visible_overmatch')
        self.assertEqual(evidence['visible_attackers'],3)

    def test_guard_resets_and_does_not_infer_hidden_pressure(self):
        guard=DefeatGuard();guard.observe(self.losing_world(90))
        safe=replace(self.losing_world(106),units=tuple(u for u in self.losing_world(106).units
                                                     if u.alliance!=4))
        self.assertIsNone(guard.observe(safe));self.assertIsNone(guard.started)
        self.assertIsNone(guard.observe(self.losing_world(110)))

    def test_empty_army_with_workers_and_healthy_base_is_not_clear_loss(self):
        guard=DefeatGuard()
        self.assertIsNone(guard.observe(world([unit(1,CC,is_building=True),unit(2,SCV)],300)))

    def test_only_engine_victories_upgrade_curriculum(self):
        curriculum=Curriculum()
        for outcome,source in [('Defeat','engine_player_result'),('Unknown','unverified'),
                               ('Victory','acknowledged_concession_rule')]:
            curriculum.record(outcome,source)
        self.assertEqual(curriculum.difficulty,'easy')
        curriculum.record('Victory','engine_player_result')
        self.assertEqual(curriculum.difficulty,'hard');self.assertFalse(curriculum.reached_goal)
        curriculum.record('Victory','engine_player_result');self.assertTrue(curriculum.reached_goal)

    def test_raw_concession_saves_before_acknowledged_leave(self):
        arena=raw_fixture();arena.request_surrender(0,'hard request limit')
        snapshots=arena.step([[]])
        self.assertEqual(arena.controllers[0].calls,['save','leave'])
        self.assertTrue(arena.ended);self.assertEqual(arena.result()[1],'Defeat')
        self.assertEqual(arena.surrender_report['result_source'],'acknowledged_concession_rule')
        self.assertEqual(snapshots[0].feedback[0]['kind'],'surrender_completed')
        with tempfile.TemporaryDirectory() as d:
            arena.save_replay(Path(d)/'match.SC2Replay')
            self.assertEqual((Path(d)/'match.SC2Replay').read_bytes(),b'fixture replay bytes')

    def test_raw_save_failure_keeps_playing_without_claimed_defeat(self):
        arena=raw_fixture();arena.controllers[0].save_error=True
        arena.request_surrender(0,'loss');snapshots=arena.step([[]])
        self.assertFalse(arena.ended);self.assertFalse(arena.match_control_pending)
        self.assertEqual(arena.controllers[0].calls,['save'])
        self.assertEqual(snapshots[0].feedback[0]['kind'],'surrender_failed')

    def test_raw_leave_failure_does_not_fabricate_score(self):
        arena=raw_fixture();arena.controllers[0].leave_error=True
        arena.request_surrender(0,'loss')
        with self.assertRaises(RuntimeError):arena.step([[]])
        self.assertFalse(arena.ended);self.assertEqual(arena.surrender_report['state'],'unknown')

    def test_early_surrender_policy_is_opt_in(self):
        backend=Backend();backend.initial=[self.losing_world(106)]
        commander=Commander(backend,0,Client(),opening=False)
        try:
            self.assertTrue(commander._surrender_blockers(backend.initial[0]))
            backend.surrender_blockers_for=lambda w,i:[]
            self.assertEqual(commander._surrender_blockers(backend.initial[0]),[])
        finally:commander.stop()

    def test_prompt_scout_matches_actual_executor(self):
        self.assertIn('targets:["enemy_start"]',BOT_RULES)
        validate_args('scout',dict(unit='scv',count=1,targets=['enemy_start'],
                                 timeout_s=120,reason='侦察'),(200,200))

    def test_repair_manifest_rejects_symlinks_and_tracks_non_python(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);(folder/'protected.txt').write_text('existing content')
            self.assertIn('protected.txt',workspace_files(folder))
            (folder/'bad.py').symlink_to(folder/'protected.txt')
            with self.assertRaises(ValueError):workspace_files(folder)

    def test_resume_preserves_used_budget_and_never_runs_ninth_match(self):
        import autonomous_bot as runner
        parent=Path(runner.__file__).resolve().parent/'logs/autonomous-bot'
        parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as d:
            folder=Path(d)
            status={'model':'chatgpt/gpt-6-luna','limits':{'max_matches':8,'max_requests':24,
                'max_game_seconds':600,'max_wall_seconds':660,'seed':7},
                'budget':{'limit':208,'attempted':192,'by_role':{'battle':192}},
                'matches':[{'number':i} for i in range(1,9)],'next_difficulty':'easy'}
            (folder/'campaign.json').write_text(json.dumps(status))
            with patch('sys.argv',['autonomous_bot.py','--resume',str(folder)]), \
                 patch('autonomous_bot.subprocess.Popen',side_effect=AssertionError('ninth match forbidden')):
                self.assertEqual(runner.main(),2)
            resumed=json.loads((folder/'campaign.json').read_text())
            self.assertEqual(resumed['budget'],status['budget'])
            self.assertEqual(len(resumed['matches']),8)

    def test_until_goal_extension_keeps_prior_usage_and_records_authorization(self):
        import autonomous_bot as runner
        budget=RequestBudget(208)
        budget.used=197;budget.by_role={'battle':186,'review':9,'repair':2}
        args=SimpleNamespace(max_matches=8,max_requests=24,until_hard_win=True)
        status={'limits':{'max_matches':8}}
        runner.extend_campaign(status,budget,args,9)
        self.assertEqual(budget.report(),{'limit':234,'attempted':197,
            'by_role':{'battle':186,'review':9,'repair':2}})
        self.assertEqual(status['limits']['max_matches'],9)
        self.assertEqual(status['budget_extensions'][0]['previously_attempted'],197)
        runner.extend_campaign(status,budget,args,9)
        self.assertEqual(budget.limit,234)
        args.until_hard_win=False
        with self.assertRaises(ValueError):runner.extend_campaign(status,budget,args,10)
        self.assertEqual(budget.used,197)

    def test_new_single_hard_trial_has_thirty_minute_limits_and_linked_history(self):
        import autonomous_bot as runner
        parent=Path(runner.__file__).resolve().parent/'logs/autonomous-bot'
        with tempfile.TemporaryDirectory(dir=parent) as d:
            previous=Path(d)/'previous';previous.mkdir()
            previous_status={'budget':{'limit':312,'attempted':278,'by_role':{'battle':261,'review':15,'repair':2}},
                             'matches':[{}]*12,'goal_reached':True}
            (previous/'campaign.json').write_text(json.dumps(previous_status))
            new=Path(d)/'new';new.mkdir()
            worker=SimpleNamespace(returncode=1,wait=lambda **kw:None)
            with patch('sys.argv',['autonomous_bot.py','--max-matches','1','--start-difficulty','hard',
                                   '--previous-campaign',str(previous),'--seed','18']), \
                 patch('autonomous_bot.tempfile.mkdtemp',return_value=str(new)), \
                 patch('autonomous_bot.subprocess.Popen',return_value=worker):
                self.assertEqual(runner.main(),2)
            config=json.loads((new/'worker-01.json').read_text())
            self.assertEqual(config['difficulty'],'hard')
            self.assertEqual(config['args']['max_game_seconds'],1800)
            self.assertEqual(config['args']['max_wall_seconds'],1860)
            self.assertEqual(config['args']['decision_cadence'],'adaptive')
            self.assertEqual(config['budget']['limit'],26)
            status=json.loads((new/'campaign.json').read_text())
            self.assertEqual(status['previous_campaign']['budget']['attempted'],278)
            self.assertEqual(json.loads((previous/'campaign.json').read_text()),previous_status)

    def test_resume_until_goal_launches_ninth_worker_without_resetting_ledger(self):
        import autonomous_bot as runner
        parent=Path(runner.__file__).resolve().parent/'logs/autonomous-bot'
        with tempfile.TemporaryDirectory(dir=parent) as d:
            folder=Path(d)
            status={'model':'chatgpt/gpt-6-luna','limits':{'max_matches':8,'max_requests':24,
                'max_game_seconds':600,'max_wall_seconds':660,'seed':7},
                'budget':{'limit':208,'attempted':197,'by_role':{'battle':186,'review':9,'repair':2}},
                'matches':[{'number':i} for i in range(1,9)],'next_difficulty':'easy'}
            (folder/'campaign.json').write_text(json.dumps(status))
            worker=SimpleNamespace(returncode=1,wait=lambda **kw:None)
            with patch('sys.argv',['autonomous_bot.py','--resume',str(folder),'--until-hard-win']), \
                 patch('autonomous_bot.subprocess.Popen',return_value=worker) as launch:
                self.assertEqual(runner.main(),2)
            config=json.loads((folder/'worker-09.json').read_text())
            self.assertEqual(config['number'],9)
            self.assertEqual(config['difficulty'],'easy')
            self.assertEqual(config['budget']['attempted'],197)
            self.assertEqual(config['budget']['limit'],234)
            resumed=json.loads((folder/'campaign.json').read_text())
            self.assertEqual(resumed['matches'],status['matches'])
            self.assertEqual(resumed['budget']['by_role'],status['budget']['by_role'])
            self.assertEqual(resumed['stop_reason'],'match_worker_failed')
            self.assertEqual(launch.call_count,1)

    def test_diagnostics_retains_closed_combat_and_latest_open_window(self):
        records=[{'record':'engagement_observed','engagement':{'id':1,'status':'closed',
                    'enemy_deaths_observed':{'Marine':2}}},
                 {'record':'request_queued','prompt':{'engagement_log':{'windows':[
                    {'id':1,'status':'ongoing','enemy_deaths_observed':{'Marine':1}},
                    {'id':2,'status':'ongoing','enemy_deaths_observed':{'SCV':1}}]}}},
                 {'record':'request_finished','api_returned':True,'request_id':1,
                  'response':'{"tasks":[]}'}]
        report={'outcome':'Defeat','termination':'request_budget','result_source':'acknowledged_concession_rule',
                'timeline':[],'final_world':{},'model_calls':1,'usage':{}}
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'journal.jsonl';path.write_text('\n'.join(json.dumps(x) for x in records))
            result=match_diagnostics(report,path)
        self.assertEqual(len(result['engagement_windows']),2)
        self.assertEqual(result['engagement_windows'][0]['enemy_deaths_observed'],{'Marine':2})
        self.assertEqual(result['returned_plans'][0]['plan'],{'tasks':[]})


if __name__=='__main__':unittest.main()
