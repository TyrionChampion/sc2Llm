"""Combat ability behavior with deterministic observations, not native performance."""
from dataclasses import replace
import unittest
from arena.combat_micro import CombatMicro,STIM,SIEGE,UNSIEGE
from arena.raw_engine import ATTACK,MOVE,command
from test_realtime_runtime import Backend,unit,world

class Engine(Backend):
    def __init__(self):self.allowed={};self.queries=0
    def available_abilities(self,index,tags):
        self.queries+=1
        return {t:self.allowed.get(t,{STIM,SIEGE,UNSIEGE}) for t in tags}

class CombatMicroTests(unittest.TestCase):
    def setUp(self):
        self.b=Engine();self.events=[]
        self.micro=CombatMicro(self.b,0,lambda kind,**kw:self.events.append(dict(kind=kind,**kw)))
        self.ctx=dict(source='task_queue',skill='move_out',task_id=1)
    def w(self,kind=48,enemy=True,sec=0,**kw):
        own=unit(2,kind,ground_dps=7,air_dps=7 if kind==48 else 0,attack_range=5,**kw)
        data=[own]
        if enemy:data.append(unit(9,48,35,30,4,ground_dps=7))
        return replace(world(data,sec),upgrades=(15,))
    def go(self,w,new=True,protected=(),ctx=None,goal=(160,160),ability=ATTACK,defense=None):
        a=[command(ability,[2],pos=goal)] if new else []
        return self.micro.apply(w,a,[ctx or self.ctx] if a else [],protected,defense)[0]
    def aids(self,a):return [x.action_raw.unit_command.ability_id for x in a]

    def test_no_strategic_order_means_no_automatic_takeover(self):
        self.assertEqual(self.go(self.w(),False),[]);self.assertEqual(self.b.queries,0)
    def test_stim_requires_research(self):
        a=self.go(replace(self.w(),upgrades=()))
        self.assertEqual(self.aids(a),[ATTACK])
    def test_stim_precedes_attack_and_buff_confirms_before_resume(self):
        a=self.go(self.w());self.assertEqual(self.aids(a),[STIM])
        self.assertEqual(self.go(self.w(sec=.2),False),[])
        a=self.go(self.w(sec=.3,buffs=(27,),health=35),False)
        self.assertEqual(self.aids(a),[ATTACK])
        self.assertEqual(self.events[-1]['kind'],'combat_micro_confirmed')
    def test_existing_numeric_buff_never_restimmed(self):
        self.assertEqual(self.aids(self.go(self.w(buffs=(27,)))),[ATTACK])
    def test_low_health_not_stimmed(self):
        self.assertEqual(self.aids(self.go(self.w(health=20))),[ATTACK])
    def test_marauder_buffs_and_health_cost(self):
        w=self.w(51);u=replace(w.my_units[0],health=125,health_max=125)
        w=replace(w,units=(u,*w.enemy_units))
        self.assertEqual(self.aids(self.go(w)),[STIM])
        w=replace(w,game_loop=4,units=(replace(u,health=105,buffs=(24,)),*w.enemy_units))
        self.assertEqual(self.aids(self.go(w,False)),[ATTACK])
    def test_enemy_air_not_stim_target_for_marauder(self):
        w=self.w(51);w=replace(w,units=(w.my_units[0],replace(w.enemy_units[0],flying=True)))
        self.assertEqual(self.aids(self.go(w)),[ATTACK])
    def test_custom_python_owner_excluded(self):
        self.assertEqual(self.aids(self.go(self.w(),protected={2})),[ATTACK])
        self.assertFalse(self.micro.intents)
    def test_custom_skill_action_not_rewritten(self):
        a=self.go(self.w(),ctx=dict(source='python_skill',task_id=3))
        self.assertEqual(self.aids(a),[ATTACK]);self.assertFalse(self.micro.intents)
    def test_tank_sieges_and_holds_without_move_orders(self):
        self.assertEqual(self.aids(self.go(self.w(33))),[SIEGE])
        self.assertEqual(self.go(self.w(32,sec=.5)),[])
        self.assertEqual(self.go(self.w(32,sec=1.8)),[])
        self.assertEqual(self.micro.counts['combat_micro_confirmed'],1)
    def test_type_change_during_transform_is_not_yet_completion(self):
        self.go(self.w(33))
        self.assertEqual(self.go(self.w(32,sec=.1,orders=(SIEGE,)),False),[])
        self.assertEqual(self.micro.counts['combat_micro_confirmed'],0)
        self.assertEqual(self.go(self.w(32,sec=3.2,orders=(SIEGE,)),False),[])
        self.assertEqual(self.micro.counts['combat_micro_failed'],0)
        self.go(self.w(32,sec=3.5),False)
        self.assertEqual(self.micro.counts['combat_micro_confirmed'],1)

    def test_tank_does_not_siege_for_only_air_enemy(self):
        w=self.w(33);w=replace(w,units=(w.my_units[0],replace(w.enemy_units[0],flying=True)))
        self.assertEqual(self.aids(self.go(w)),[ATTACK])
    def test_tank_can_siege_at_commanded_destination(self):
        self.assertEqual(self.aids(self.go(self.w(33,False),goal=(30,30))),[SIEGE])
    def test_tank_unsieges_before_advancing_and_resumes_goal(self):
        self.assertEqual(self.aids(self.go(self.w(32,False))),[UNSIEGE])
        self.assertEqual(self.go(self.w(32,False,sec=.5),False),[])
        self.assertEqual(self.aids(self.go(self.w(33,False,sec=2),False)),[ATTACK])
    def test_unsiege_type_changes_before_movement_is_allowed(self):
        self.go(self.w(32,False))
        self.assertEqual(self.go(self.w(33,False,sec=3.2,orders=(UNSIEGE,)),False),[])
        self.assertEqual(self.micro.counts['combat_micro_failed'],0)
        self.assertEqual(self.aids(self.go(self.w(33,False,sec=3.5),False)),[ATTACK])
    def test_missing_transform_completion_times_out_visibly(self):
        self.go(self.w(33))
        self.go(self.w(32,sec=6.1,orders=(SIEGE,)),False)
        self.assertEqual(self.micro.counts['combat_micro_confirmed'],0)
        self.assertEqual(self.micro.counts['combat_micro_failed'],1)
    def test_withdraw_unsieges_even_under_attack(self):
        class Guard:active=True;tags={2};mode='withdraw'
        self.assertEqual(self.aids(self.go(self.w(32),defense=Guard())),[UNSIEGE])
        # Never resume a former attack while guard waits to compute a fresh withdrawal point.
        self.assertEqual(self.go(self.w(33,sec=2),False,defense=Guard()),[])
    def test_no_contact_hysteresis_prevents_deployment_flicker(self):
        self.go(self.w(33))
        self.go(self.w(32,sec=2),False)
        self.assertEqual(self.go(self.w(32,False,sec=4),False),[])
        self.assertEqual(self.aids(self.go(self.w(32,False,sec=8),False)),[UNSIEGE])
    def test_unavailable_siege_stops_invalid_sieged_movement_and_reports(self):
        self.b.allowed[2]=set()
        self.assertEqual(self.go(self.w(32,False)),[])
        self.assertEqual(self.events[-1]['kind'],'combat_micro_unavailable')
    def test_unobserved_cast_is_failure_not_success(self):
        self.go(self.w())
        self.go(self.w(sec=4),False)
        self.assertEqual(self.micro.counts['combat_micro_confirmed'],0)
        self.assertEqual(self.micro.counts['combat_micro_failed'],1)
    def test_mixed_group_preserves_other_units_and_contexts(self):
        w=self.w();w=replace(w,units=(*w.units,unit(3,54)))
        actions,ctx=self.micro.apply(w,[command(ATTACK,[2,3],pos=(160,160))],[self.ctx])
        self.assertEqual(len(actions),len(ctx));self.assertEqual(self.aids(actions),[ATTACK,STIM])
        self.assertEqual(list(actions[0].action_raw.unit_command.unit_tags),[3])
    def test_one_wounded_marine_does_not_prevent_healthy_squadmate_stim(self):
        w=self.w();w=replace(w,units=(*w.units,unit(3,48,health=5,ground_dps=7,attack_range=5)))
        a,ctx=self.micro.apply(w,[command(ATTACK,[2,3],pos=(160,160))],[self.ctx])
        self.assertEqual(self.aids(a),[ATTACK,STIM])
        self.assertEqual(list(a[0].action_raw.unit_command.unit_tags),[3])

    def test_stop_cancels_old_intent(self):
        self.go(self.w())
        a,c=self.micro.apply(self.w(sec=.2),[command(4,[2])],[dict(source='defense_release')])
        self.assertEqual(self.aids(a),[4]);self.assertFalse(self.micro.intents)

if __name__=='__main__':unittest.main()
