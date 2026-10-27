"""Acceptance boundaries for real army trials; engine outcomes are tested live."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock,patch
import verify_army_tactics_live as trial

class OutcomeTests(unittest.TestCase):
    def game(self,left,right):
        t=object.__new__(trial.Trial)
        t.initial_ground=[{1,2},{3,4}]
        t.worlds=[SimpleNamespace(my_units=[SimpleNamespace(tag=x) for x in tags]) for tags in (left,right)]
        return t
    def test_enemy_leaving_vision_does_not_count_as_death(self):
        t=self.game([1],[3]);t.worlds[0].enemy_units=[]
        self.assertIsNone(t.outcome())
    def test_medivacs_and_vipers_alone_do_not_count_as_ground_army(self):
        self.assertEqual(self.game([1,80],[90]).outcome(),'ZergVictory')
        self.assertEqual(self.game([80],[3,90]).outcome(),'TerranVictory')
    def test_mutual_ground_elimination_is_draw(self):
        self.assertEqual(self.game([80],[90]).outcome(),'Draw')

class ModelIsolationTests(unittest.TestCase):
    def test_selected_model_is_preserved(self):
        with patch('arena.external_planner.ExternalPlanner'):
            client=trial.BudgetMailbox('unused',Mock(),12,model='gpt-6-luna')
        self.assertEqual(client.model,'gpt-6-luna')
    def test_unknown_model_is_rejected_without_creating_mailbox(self):
        with patch('arena.external_planner.ExternalPlanner') as mailbox:
            with self.assertRaisesRegex(ValueError,'no fallback'):
                trial.BudgetMailbox('unused',Mock(),12,model='unknown')
            mailbox.assert_not_called()
    def test_mismatched_fixture_never_contacts_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake=Mock();fake.report={'fixture_sha256':'changed'}
            with patch.object(trial,'Trial',return_value=fake),patch.object(trial,'BudgetMailbox') as client:
                result=trial.run_model(trial.ArmyConfig(),Path(tmp),Path(tmp)/'mail','expected')
            client.assert_not_called()
            self.assertIn('Model initial state differs',result['error'])
            fake.close.assert_called_once()
    def mailbox(self,answer):
        c=object.__new__(trial.BudgetMailbox);c.budget=Mock();c.mail=Mock()
        c.mail.chat.return_value=json.dumps(answer)
        return c
    def test_production_plan_is_rejected_before_executor(self):
        c=self.mailbox({'tasks':[{'skill':'train_unit','count':20,'args':{'unit':'hydralisk'}}]})
        with self.assertRaisesRegex(ValueError,'no production'):c.chat('rules','{}')
        c.budget.reserve.assert_called_once()
    def test_ability_selection_passes_through_without_target_rewrite(self):
        plan={'tasks':[{'skill':'use_ability','args':{'caster':100,'ability':'effect_abduct','target':200}}],'reason':'自主决定'}
        c=self.mailbox(plan)
        self.assertEqual(json.loads(c.chat('rules','{}')),plan)
    def test_model_failure_is_counted_and_not_replaced(self):
        c=self.mailbox({});c.mail.chat.side_effect=TimeoutError('no response')
        with self.assertRaises(TimeoutError):c.chat('rules','{}')
        c.budget.reserve.assert_called_once()
        c.mail.chat.assert_called_once()

if __name__=='__main__':unittest.main()
