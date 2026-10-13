import json
import tempfile
import unittest
from pathlib import Path

from arena.autonomous_bot import BotPlanner, RequestBudget
from arena.go_planner import GO_MODEL
from arena.plan_representation import translate_plan, compact_strategy_layout
from arena.tasks import TaskQueue
from test_realtime_runtime import Backend


class Reply:
    model=GO_MODEL;timeout=60;closed=False;last_metadata={};calls=attempts=0
    def __init__(self,plan):self.plan=plan
    def chat(self,*args):
        self.calls+=1;self.attempts+=1;return json.dumps(self.plan)


class Representation(unittest.TestCase):
    def test_observed_tank_reinforcement_keeps_quantity_and_target(self):
        task=dict(skill='order_units',count=3,args=dict(unit='siege_tank',mode='attack_move',
            target=[54,102],timeout_s=180,reason='三辆坦克增援'))
        original=dict(tasks=[task],reason='模型明确选择三辆坦克增援')
        planner=BotPlanner(Reply(original),RequestBudget(2),'模型战术')
        plan=json.loads(planner.chat('',json.dumps({'world':{'own':{'SiegeTank':3}}})))
        queue=TaskQueue(Backend(),0,lambda s:None);queue.enqueue(plan['tasks'],0)
        self.assertEqual(queue.tasks[0].program.args['count'],3)
        self.assertEqual(plan['tasks'][0]['args']['target'],[54,102])
        self.assertEqual(original['tasks'][0]['count'],3)

    def test_unknown_or_conflicting_quantity_is_not_guessed(self):
        for task in (dict(skill='scout',args=dict(unit='marine',targets=['enemy_start'],reason='侦察')),
                     dict(skill='order_units',count=3,args=dict(count=2)),
                     dict(skill='scout',count=20,args=dict(unit='marine'))):
            original=dict(tasks=[task]);result,changes=translate_plan(original,{})
            self.assertEqual(result,original);self.assertEqual(changes,[])

    def test_observed_name_alias_only_and_explicit_all_count(self):
        plan=dict(tasks=[dict(skill='defend_base',count=1,args=dict(units=['siege_tank','unknown'],base='home')),
                         dict(skill='order_units',count='all',args=dict(unit='army',target='enemy_start'))])
        out,changes=translate_plan(plan,{'world':{'own':{'SiegeTank':2,'Marine':5}}})
        self.assertEqual(out['tasks'][0]['args']['units'],['SiegeTank','unknown'])
        self.assertEqual(out['tasks'][1]['args']['count'],'all')
        self.assertEqual(out['tasks'][1]['count'],1)
        self.assertEqual(plan['tasks'][1]['count'],'all')

    def test_layout_keeps_content_and_ascii_separators(self):
        original='阶段一： 造兵\n\n240s   SCV 28；build_supply count=1。'
        compact=compact_strategy_layout(original)
        self.assertIn('240s SCV 28',compact)
        self.assertIn('build_supply count=1',compact)
        self.assertEqual(''.join(original.split()),''.join(compact.split()))


if __name__=='__main__':unittest.main()
