"""Own observed resource locations keep their labels when native tags change."""
import unittest
from dataclasses import replace
from arena.raw_engine import Entity,Snapshot
from arena.macro import expansion_clusters,remember_resource_sites
from arena.spatial import resource_sites

class ResourceSiteIdentity(unittest.TestCase):
 def world(self,start=10,positions=((20,20),(22,20),(24,20),(26,20))):
  units=tuple(Entity(start+i,341,3,x,y,100,100,mineral=True) for i,(x,y) in enumerate(positions))
  return Snapshot(0,50,12,15,units,(5,5))
 def test_tag_change_and_depletion_do_not_break_an_issued_location(self):
  a=self.world();memory=remember_resource_sites(a);a=replace(a,resource_site_memory=memory);label=expansion_clusters(a)[0][0];point=resource_sites(a)[label]
  b=self.world(100);b=replace(b,resource_site_memory=remember_resource_sites(b,memory))
  self.assertEqual(expansion_clusters(b)[0][0],label);self.assertEqual(resource_sites(b)[label],point)
  c=self.world(200,((24,20),(26,20)));c=replace(c,resource_site_memory=remember_resource_sites(c,b.resource_site_memory))
  self.assertEqual(expansion_clusters(c)[0][0],label);self.assertEqual(resource_sites(c)[label],point)
  empty=replace(c,units=());self.assertEqual(expansion_clusters(empty),[]);self.assertEqual(resource_sites(empty)[label],point)
 def test_new_observed_site_does_not_reuse_another_site_label(self):
  a=self.world();b=self.world(100,((80,80),(82,80),(84,80),(86,80)));memory=remember_resource_sites(b,remember_resource_sites(a))
  self.assertEqual(len(memory),2);self.assertEqual(expansion_clusters(replace(b,resource_site_memory=memory))[0][0],'site_100')
 def test_another_players_history_is_not_an_input(self):
  a=self.world();b=self.world(100,((80,80),(82,80),(84,80),(86,80)))
  self.assertNotIn('site_10',resource_sites(replace(b,resource_site_memory=remember_resource_sites(b))))
if __name__=='__main__':unittest.main()
