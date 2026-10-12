"""Named expansion defenses must remain callable through structured model output."""
import unittest
from arena.battle_plans import output_schema
from arena.chatgpt_planner import battle_output_schema
from arena.autonomous_bot import contact_table


def defense_base(schema):
    if 'anyOf' in schema:
        schema=schema['anyOf'][0]
    variants=schema['properties']['tasks']['items']['anyOf']
    branch=next(v for v in variants if v['properties']['skill']['enum']==['defend_base'])
    return branch['properties']['args']['properties']['base']['anyOf']


class DefenseSchemaLabels(unittest.TestCase):
    def test_host_and_provider_allow_every_observed_base_label(self):
        payload=dict(control_policy=dict(defense_orders_available=True),
            defense_report=[dict(label='home',visible_attackers=0),
                            dict(label='site_4302569473',visible_attackers=6)])
        for build in (output_schema,battle_output_schema):
            with self.subTest(build=build.__name__):
                base=defense_base(build(payload))
                self.assertEqual(base[0]['enum'],['home','site_4302569473'])
                self.assertEqual(base[1]['maxItems'],2)

    def test_packed_reports_restore_labels_and_do_not_invent_unobserved_bases(self):
        rows=[dict(label=name,visible_attackers=0) for name in
              ('home','site_4302569473','site_8610578433')]
        payload=dict(control_policy=dict(defense_orders_available=True),
                     defense_report=contact_table(rows))
        allowed=defense_base(battle_output_schema(payload))[0]['enum']
        self.assertEqual(allowed,sorted(row['label'] for row in rows))
        self.assertNotIn('expansion',allowed)


if __name__=='__main__':unittest.main()
