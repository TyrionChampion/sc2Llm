"""Combat intent, live availability, allied safety and three-race spell receipts."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
import tempfile
from arena.combat_micro import CombatMicro
from arena.combat_spells import RULES,CAPABILITIES,SUPPORT_TYPES
from arena.knowledge import cached_tree
from arena.raw_engine import ATTACK,MOVE,command
import test_multirace as fixtures

class MultiraceMicro(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.MultiRace();self.f.setUp();self.b=self.f.backend('zerg')
        self.allowed={r.ability for r in RULES}|{2108,2110,2095,2097,2376,2116,ATTACK,MOVE}
        self.b.available_abilities=lambda i,tags:{t:set(self.allowed) for t in tags}
        self.events=[];self.m=CombatMicro(self.b,0,lambda k,**kw:self.events.append(dict(kind=k,**kw)))
        self.ctx=dict(source='task_queue',skill='order_units',task_id=1)
    def unit(self,tag,kind,**kw):return replace(self.f.unit(tag,kind),**kw)
    def actor(self,kind,**kw):return self.unit(2,kind,**kw)
    def enemy(self,tag=9,kind=48,x=37,y=30,**kw):
        return self.unit(tag,kind,alliance=4,x=x,y=y,ground_dps=7,attack_range=5,**kw)
    def cluster(self,kind=48,**kw):return [self.enemy(9+i,kind=kind,x=37+i*.3,y=30,**kw) for i in range(3)]
    def tick(self,units,sec=0,new=True,ability=ATTACK,ctx=None,protected=(),effects=()):
        w=replace(self.f.world(units,race='zerg'),game_loop=round(sec*22.4),effects=effects)
        actions=[command(ability,[u.tag for u in units if u.alliance==1 and u.type_id in SUPPORT_TYPES|{688,75,77,50,49,80,78,57}],pos=(90,30))] if new else []
        return self.m.apply(w,actions,[ctx or self.ctx] if actions else [],protected)[0]
    def abilities(self,out):return [a.action_raw.unit_command.ability_id for a in out]
    def test_bile_uses_visible_point_and_only_effect_ack_not_damage_claim(self):
        u=self.actor(688);e=self.enemy(kind=32)
        out=self.tick([u,e]);self.assertEqual(self.abilities(out),[2338])
        self.assertAlmostEqual(out[0].action_raw.unit_command.target_world_space_pos.x,e.x)
        self.tick([u,e],.5,new=False,effects=((11,1,37,30,.5),))
        receipt=next(e for e in self.events if e['kind']=='combat_micro_acknowledged')
        self.assertTrue(receipt['effect_confirmed']);self.assertFalse(receipt['damage_confirmed'])
        self.assertFalse(any(e['kind']=='combat_micro_confirmed' for e in self.events))
    def test_bile_can_target_visible_air_and_does_not_cast_on_allies(self):
        u=self.actor(688);e=self.enemy(flying=True)
        self.assertIn(2338,self.abilities(self.tick([u,e])))
        self.setUp();ally=self.unit(3,106,x=37,y=30,flying=True)
        self.assertNotIn(2338,self.abilities(self.tick([u,e,ally])))
    def test_hidden_enemy_is_never_retargeted(self):
        u=self.actor(688);self.tick([u,self.enemy()])
        self.tick([u],1,new=False)
        out=self.tick([u],8,new=False)
        self.assertNotIn(2338,self.abilities(out))
    def test_energy_research_and_friendly_air_are_respected_for_storm(self):
        for energy,ready,ally,expected in [(74,True,False,False),(100,False,False,False),(100,True,True,False),(100,True,False,True)]:
            with self.subTest(energy=energy,ready=ready,ally=ally):
                self.setUp();self.allowed={1036} if ready else set()
                units=[self.actor(75,energy=energy),*self.cluster()]
                if ally:units.append(self.unit(4,106,x=37,y=30,flying=True))
                self.assertEqual(1036 in self.abilities(self.tick(units)),expected)
    def test_unresearched_storm_falls_back_to_feedback(self):
        self.allowed={140};out=self.tick([self.actor(75,energy=150),*self.cluster(kind=126,energy=100)])
        self.assertEqual(self.abilities(out),[140]);self.assertTrue(out[0].action_raw.unit_command.target_unit_tag)
    def test_storm_can_defend_next_to_an_allied_building_it_cannot_damage(self):
        self.allowed={1036};base=self.unit(3,86,x=34,y=32,is_building=True,radius=2.75)
        self.assertIn(1036,self.abilities(self.tick([self.actor(75,energy=100),base,*self.cluster()])))
    def test_guardian_shield_deduplicates_casters_and_confirms_buff(self):
        self.allowed={76};u=self.actor(77,energy=150);v=replace(u,tag=3,x=31)
        out=self.tick([u,v,*self.cluster()]);self.assertEqual(self.abilities(out).count(76),1)
        self.tick([replace(u,energy=75,buffs=(18,)),v,*self.cluster()],1,new=False)
        self.assertEqual(self.m.counts['combat_micro_confirmed'],1)
    def test_force_field_splits_enemy_rear_and_does_not_seal_friendlies(self):
        self.allowed={1526};u=self.actor(77,energy=100)
        enemies=[self.enemy(x=35),*self.cluster()]
        self.assertIn(1526,self.abilities(self.tick([u,*enemies])))
        self.setUp();self.allowed={1526}
        self.assertNotIn(1526,self.abilities(self.tick([u,*enemies,self.unit(3,73,x=36,y=30)])))
    def test_lurker_burrows_in_contact_then_unburrows_before_resuming_attack(self):
        u=self.actor(502);self.assertEqual(self.abilities(self.tick([u,self.enemy()])),[2108])
        buried=replace(u,type_id=503)
        self.assertEqual(self.tick([buried,self.enemy()],1,new=False),[])
        self.assertEqual(self.m.counts['combat_micro_confirmed'],1)
        self.assertEqual(self.abilities(self.tick([buried],5,new=False)),[2110])
        self.assertEqual(self.abilities(self.tick([u],7,new=False)),[ATTACK])
    def test_withdraw_unburrows_and_unavailable_form_never_sends_invalid_move(self):
        u=self.actor(503)
        self.assertEqual(self.abilities(self.tick([u,self.enemy()],ability=MOVE)),[2110])
        self.setUp();self.allowed=set()
        self.assertEqual(self.tick([u,self.enemy()],ability=MOVE),[])
    def test_unfinished_lurker_morph_does_not_count_as_confirmed(self):
        u=self.actor(502);self.tick([u,self.enemy()])
        self.tick([replace(u,type_id=503,orders=(2108,)),self.enemy()],1,new=False)
        self.assertEqual(self.m.counts['combat_micro_confirmed'],0)
    def test_air_contact_does_not_pin_lurker_in_burrowed_form(self):
        u=self.actor(502);self.tick([u,self.enemy()])
        buried=replace(u,type_id=503)
        self.tick([buried,self.enemy(flying=True)],1,new=False)
        self.assertIn(2110,self.abilities(self.tick([buried,self.enemy(flying=True)],5,new=False)))
    def test_bile_can_hit_a_visible_enemy_building(self):
        u=self.actor(688);e=replace(self.enemy(kind=21),is_building=True,ground_dps=0)
        self.assertIn(2338,self.abilities(self.tick([u,e])))
    def test_lurker_burrows_to_attack_an_enemy_building(self):
        u=self.actor(502);e=replace(self.enemy(kind=21),is_building=True,ground_dps=0)
        self.assertIn(2108,self.abilities(self.tick([u,e])))
    def test_army_dispatch_and_tactical_rosters_include_weaponless_casters(self):
        from arena.tasks import TaskQueue
        from arena.tactics import ArmyTactics
        from arena.battle_agent import BattleAgent
        kinds=[75,77,502,503,495,498,54,56,111,499]
        actors=[self.unit(20+i,k) for i,k in enumerate(kinds)]
        q=TaskQueue(self.b,0,lambda _:None);q.enqueue([{'skill':'move_out'}],0)
        out=q.tick(self.f.world(actors,race='zerg'),set())
        self.assertEqual(set(out[0].action_raw.unit_command.unit_tags),{u.tag for u in actors})
        owner=SimpleNamespace(backend=self.b,combat_micro=True)
        for u in actors:
            self.assertTrue(ArmyTactics._eligible(owner,u),u.type_id)
            self.assertTrue(BattleAgent._eligible(owner,u),u.type_id)
    def test_catalogue_audit_distinguishes_manual_skills_from_automatic(self):
        from arena.combat_spells import catalogue_audit
        rows=catalogue_audit(cached_tree())['abilities']
        self.assertTrue(any(r['ability']=='effect_corrosive_bile' and r['mode']=='automatic_local' for r in rows))
        self.assertTrue(any(r['ability']=='effect_tactical_jump' and r['mode']=='explicit_model_order' for r in rows))
    def test_actual_commander_standing_defense_casts_storm_and_releases_burrow(self):
        from arena.commander import Commander
        class Idle:
            compact_battle=True;soft_timeout=12;timeout=60
            def ready_for_request(self):return False
        for actor,stance,expected in [(self.actor(75,energy=100),'hold',1036),(self.actor(503),'screen',2110)]:
            with self.subTest(actor=actor.type_id),tempfile.TemporaryDirectory() as folder:
                self.allowed={expected,ATTACK}  # A burrowed unit has no native MOVE.
                base=self.unit(1,86,is_building=True)
                self.b.initial=[self.f.world([base,actor,*self.cluster()],race='zerg')]
                self.b.register_action_context=lambda *a:None
                c=Commander(self.b,0,Idle(),opening=False,combat_micro=True,feedback_dir=folder)
                try:
                    self.assertTrue({'psi_storm','force_field','corrosive_bile'}<=set(c.control_policy['combat_micro']['abilities']))
                    c.tasks.enqueue([dict(skill='defend_base',args=dict(base='home',units=[self.b.tech_tree.names[actor.type_id]],count=1,
                        stance=stance,reason='明确本基地守备'))],0)
                    out=c.tick(self.b.initial[0])
                    self.assertIn(expected,self.abilities(out))
                    self.assertEqual(c.tasks.tasks[0].program.tags,{actor.tag})
                finally:c.stop()
    def test_widow_mine_uses_its_own_burrow_ability(self):
        out=self.tick([self.actor(498),self.enemy(x=34)])
        self.assertEqual(self.abilities(out),[2095])
    def test_custom_owner_scout_raid_and_hallucinated_caster_are_not_seized(self):
        for ctx,protected,hallu in [(self.ctx,{2},False),(dict(source='python_skill'),(),False),
                (dict(source='task_queue',skill='scout'),(),False),(dict(source='task_queue',skill='raid'),(),False),(self.ctx,(),True)]:
            with self.subTest(ctx=ctx,protected=protected,hallu=hallu):
                self.setUp();out=self.tick([self.actor(688,hallucination=hallu),self.enemy()],ctx=ctx,protected=protected)
                self.assertNotIn(2338,self.abilities(out))
    def test_no_order_no_cast_and_retreat_does_not_cast_offensive_spell(self):
        units=[self.actor(688),self.enemy()]
        self.assertEqual(self.tick(units,new=False),[])
        self.assertEqual(self.abilities(self.tick(units,ability=MOVE)),[MOVE])
    def test_channel_does_not_resume_attack_until_native_order_ends(self):
        self.allowed={2714};u=self.actor(50,energy=100);e=replace(self.enemy(kind=126),health=175,health_max=175)
        self.assertEqual(self.abilities(self.tick([u,e])),[2714])
        channel=replace(u,energy=50,orders=(2714,),order_targets=(9,))
        self.assertEqual(self.tick([channel,e],1,new=False),[])
        self.assertEqual(self.m.counts['combat_micro_acknowledged'],1)
        self.assertEqual(self.abilities(self.tick([replace(channel,orders=()),e],2,new=False)),[ATTACK])
    def test_explicit_retreat_interrupts_a_channel(self):
        self.allowed={2714};u=self.actor(50,energy=100);e=replace(self.enemy(kind=126),health=175,health_max=175)
        self.tick([u,e]);channel=replace(u,energy=50,orders=(2714,),order_targets=(9,))
        self.assertIn(MOVE,self.abilities(self.tick([channel,e],1,ability=MOVE)))
    def test_spellcasters_can_cast_from_assigned_defense_hold_orders(self):
        u=self.actor(75,energy=100)
        self.allowed={1036}
        out=self.tick([u,*self.cluster()],ability=MOVE,ctx=dict(source='defense_guard',mode='defend'))
        self.assertIn(1036,self.abilities(out))
    def test_remaining_caster_families_have_local_choices(self):
        scenarios=[(111,74,self.cluster()),(499,2063,self.cluster()),
            (499,2542,self.cluster(flying=True)),(56,3747,self.cluster(kind=33)),
            (56,3753,self.cluster()),(50,1628,self.cluster(kind=74)),
            (49,2588,[self.enemy(9+i,x=34+i*.2) for i in range(3)]),
            (80,2393,[self.enemy(kind=33,x=35)]),(495,2375,[self.enemy(x=35)]),
            (78,173,[self.enemy(x=33)]),(57,401,[replace(self.enemy(kind=33),health=175,health_max=175)]),
            (126,1664,[self.unit(3,126,x=33,health=10)]),
            (54,386,[self.unit(3,48,x=33,health=10)])]
        for kind,aid,targets in scenarios:
            with self.subTest(kind=kind,aid=aid):
                self.setUp();self.allowed={aid};out=self.tick([self.actor(kind,energy=200),*targets])
                self.assertIn(aid,self.abilities(out))
    def test_rules_map_to_actual_engine_spell_target_kinds(self):
        tree=cached_tree()
        for rule in RULES:
            with self.subTest(rule=rule.action):
                self.assertIn(rule.ability,tree.tactical_abilities(rule.actor))
        self.assertEqual(set(self.m.public()['automatic']),{'terran','zerg','protoss'})

if __name__=='__main__':unittest.main()
