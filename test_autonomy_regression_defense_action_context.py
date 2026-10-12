"""Explicit base defense must preserve action provenance through combat micro."""
from dataclasses import replace
import unittest

from arena.combat_micro import CombatMicro, STIM
from arena.raw_engine import ATTACK
from arena.tasks import TaskQueue
from test_defense_orders import DefenseQueue, marine, enemy
from test_realtime_runtime import unit, world, CC


class DefenseActionContext(unittest.TestCase):
    def execute(self, ordered, researched=False):
        backend=DefenseQueue()
        backend.available_abilities=lambda index,tags: {tag:{STIM} for tag in tags}
        queue=TaskQueue(backend,0,lambda message:None)
        if ordered:
            queue.enqueue([{'skill':'defend_base','count':1,'args':{
                'base':'home','units':['marine'],'count':2,'stance':'hold',
                'timeout_s':60,'reason':'显式保护基地'}}],0)
        observation=world([unit(1,CC,is_building=True),marine(2,30,30),
                           marine(3,31,30),enemy(9,34,30)],1)
        observation=replace(observation,upgrades=(15,) if researched else ())
        actions=queue.tick(observation,set())
        micro=CombatMicro(backend,0,lambda kind,**details:None)
        result,contexts=micro.apply(observation,actions,queue.action_contexts,
                                    queue.protected()-queue.defense_tags)
        return result,contexts

    def test_explicit_defenders_attack_with_matching_provenance(self):
        actions,contexts=self.execute(True)
        self.assertEqual([a.action_raw.unit_command.ability_id for a in actions],[ATTACK,ATTACK])
        self.assertEqual(len(contexts),2)
        self.assertTrue(all(c['source']=='defense_guard' and c['task_id']==1 for c in contexts))

    def test_researched_defenders_can_use_micro_without_taking_free_army(self):
        actions,contexts=self.execute(True,researched=True)
        self.assertEqual([a.action_raw.unit_command.ability_id for a in actions],[STIM,STIM])
        self.assertEqual(len(contexts),2)
        self.assertEqual(self.execute(False,researched=True),([],[]))

    def test_cancel_defense_stops_and_releases_managed_units(self):
        backend=DefenseQueue();queue=TaskQueue(backend,0,lambda message:None)
        observation=world([unit(1,CC,is_building=True),marine(2,30,30),enemy(9,34,30)],1)
        queue.enqueue([{'skill':'defend_base','args':{'base':'home','units':['marine'],
                      'count':1,'stance':'hold','timeout_s':60,'reason':'显式防守'}}],0)
        micro=CombatMicro(backend,0,lambda kind,**details:None)
        actions=queue.tick(observation,set())
        micro.apply(observation,actions,queue.action_contexts,queue.protected()-queue.defense_tags)
        queue.enqueue([{'skill':'cancel_task','args':{'task_id':1}}],1)
        actions=queue.tick(replace(observation,game_loop=44),set())
        result,contexts=micro.apply(observation,actions,queue.action_contexts,
                                    queue.protected()-queue.defense_tags)
        self.assertEqual([a.action_raw.unit_command.ability_id for a in result],[4])
        self.assertEqual(contexts[0]['source'],'defense_release')
        self.assertFalse(queue.defense_tags)
        self.assertFalse(micro.intents)


if __name__=='__main__':unittest.main()
