"""Raw egg/cocoon removals remain evidence, never automatic enemy-kill attribution."""
import unittest
from unittest.mock import patch
from arena.raw_engine import Entity,Snapshot,NAMES
from arena.context import EngagementLog
from arena.intelligence import Intelligence
from arena.autonomous_review import compact_review_evidence

class ProductionLifecycle(unittest.TestCase):
    def world(self,n,units=(),dead=()):return Snapshot(n,500,12,30,tuple(units),(30,30),dead_units=dead,race='zerg')
    def unit(self,tag,kind,side=1):return Entity(tag,kind,side,30,30,100,100)
    def test_hatching_egg_removal_is_not_an_enemy_kill_but_raw_event_is_preserved(self):
        for side in (1,4):
            with self.subTest(side=side),patch.dict(NAMES,{103:'Egg',104:'Drone'}):
                events=[];intel=Intelligence(lambda k,**kw:events.append(kw['intel_event']))
                log=EngagementLog(lambda *a,**kw:None)
                for w in (self.world(10,[self.unit(1,103,side)]),self.world(20,[self.unit(2,104,side)],(1,))):
                    intel.update(w);log.update(w)
                kind='own' if side==1 else 'enemy'
                removal=next(e for e in events if e['kind']==kind+'_production_intermediate_removed')
                self.assertIn('not',removal['attribution'].lower())
                self.assertFalse(any(e['kind'] in ('own_unit_death_observed','enemy_death_observed') for e in events))
                row=log.public()['windows'][0];prefix='our' if side==1 else 'enemy'
                self.assertEqual(row[prefix+'_deaths_observed'],{'Egg':1})
                self.assertEqual(row[prefix+'_production_intermediates_removed'],{'Egg':1})
    def test_attacked_cocoon_stays_unknown_and_real_marine_death_stays_separate(self):
        with patch.dict(NAMES,{8:'BanelingCocoon',48:'Marine'}):
            log=EngagementLog(lambda *a,**kw:None)
            log.update(self.world(10,[self.unit(1,8),self.unit(2,48)]))
            log.update(self.world(20,dead=(1,2)))
            row=log.public()['windows'][0]
            self.assertEqual(row['our_deaths_observed'],{'BanelingCocoon':1,'Marine':1})
            self.assertEqual(row['our_production_intermediates_removed'],{'BanelingCocoon':1})
    def test_review_retains_intermediate_subset_without_adding_it_to_raw_total(self):
        windows=[dict(our_deaths_observed={'Egg':2,'Roach':1},our_production_intermediates_removed={'Egg':2})]*2
        d=compact_review_evidence(dict(errors=[],engagement_windows=windows),[])
        self.assertEqual(d['combat_summary']['our_deaths_observed'],{'Egg':4,'Roach':2})
        self.assertEqual(d['combat_summary']['our_production_intermediates_removed'],{'Egg':4})

if __name__=='__main__':unittest.main()
