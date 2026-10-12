"""Lossless compression must preserve facts and provider command schemas."""
import unittest
from arena.autonomous_bot import contact_groups
from arena.context_tables import decode_tables
from arena.chatgpt_planner import battle_output_schema


class ContextTables(unittest.TestCase):
    def records(self):
        shared={'source':'observed context '*100,'at_game_s':519.6875}
        rows=[dict(name=name,minerals=cost,gas=gas,value=flag,shared=shared)
              for name,cost,gas,flag in [('marine',50,0,False),('marauder',100,25,0),
                                       ('siege_tank',150,125,True)]]
        rows.insert(1,dict(name='extra observation',optional=None,shared=shared))
        return rows

    def test_nested_tables_preserve_order_null_missing_and_false_vs_zero(self):
        original=self.records();packed=contact_groups(original)
        self.assertIsInstance(packed,dict)
        restored=decode_tables(packed)
        self.assertEqual(restored,original)
        self.assertIs(type(restored[0]['value']),bool)
        self.assertIs(type(restored[2]['value']),int)
        self.assertNotIn('optional',restored[0]);self.assertIsNone(restored[1]['optional'])

    def test_packed_catalogue_produces_identical_command_schema(self):
        rows=[row for row in self.records() if 'minerals' in row]
        tree={'race':'terran','units':rows,'structures':rows,'research':rows,
              'forms':['siege_tank_sieged']}
        raw={'tech_tree':tree,'control_policy':{'spatial_missions_available':True}}
        packed=dict(raw,tech_tree=dict(tree,units=contact_groups(rows),
                    structures=contact_groups(rows),research=contact_groups(rows)))
        self.assertIsInstance(packed['tech_tree']['units'],dict)
        self.assertEqual(battle_output_schema(packed),battle_output_schema(raw))

    def test_invalid_rows_cannot_silently_drop_values(self):
        for columns,rows,common in [(['tag','pos'],[[1]],{}),(['tag'],[[1,2]],{}),
                                   (['tag','tag'],[[1,2]],{}),(['tag'],[[1]],{'tag':2})]:
            with self.assertRaises(ValueError):
                decode_tables(dict(encoding='columns_rows_common_v1',columns=columns,rows=rows,common=common))

    def test_invalid_group_order_cannot_select_unrelated_record(self):
        for order in [[[1,0]],[[0,-1]],[[True,0]],[[0,1]]]:
            with self.assertRaises(ValueError):
                decode_tables(dict(encoding='key_groups_v1',groups=[[{'tag':1}]],order=order))


if __name__=='__main__':unittest.main()
