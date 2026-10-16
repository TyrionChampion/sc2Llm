"""Lossless table/coordinate compression and the real failed duel context."""
import gzip
import json
from pathlib import Path
import unittest
from copy import deepcopy

from arena.context_tables import grouped_columns,decode_tables,object_pool,key_table
from arena.autonomous_bot import nested_contact_tables,BotPlanner,RequestBudget,BOT_RULES
from test_autonomous_bot import Client


class NativeContext(unittest.TestCase):
    def table(self):
        return dict(columns=['tag','unit','pos','energy','ready'],scope='all rows exact',rows=[
            [4000000000+n,'marine' if n%2 else 'scv',
             [(123456+n)/4096,45 if n%3 else (789012+n)/4096],0,[4,16,23]] for n in range(100)])
    def test_grouped_positions_keep_order_values_and_numeric_types(self):
        source=self.table();before=deepcopy(source);packed=grouped_columns(source,('unit','ready'))
        self.assertEqual(packed['encoding'],'column_value_groups_v1')
        self.assertTrue(packed.get('coordinates'))
        restored=decode_tables(packed)
        self.assertEqual(source,before);self.assertEqual(restored,source)
        for original,actual in zip(source['rows'],restored['rows']):
            self.assertEqual([type(v) for v in original[2]],[type(v) for v in actual[2]])
        self.assertLess(len(json.dumps(packed)),len(json.dumps(source)))
    def test_pool_key_tables_and_nested_group_tables_compose(self):
        source=dict(current=self.table(),errors=[{'error':'preserve this actual rejection','tag':7}])
        packed=dict(current=grouped_columns(source['current'],('unit','ready')),errors=source['errors'])
        packed=nested_contact_tables(packed)
        for base36 in (False,True):
            for integer_refs in (False,True):
                self.assertEqual(decode_tables(key_table(object_pool(packed,integer_refs=integer_refs),base36=base36)),source)
    def test_integer_references_preserve_negative_values_and_reserved_literal_dicts(self):
        repeated=dict(message='a sufficiently long repeated observation',coords=[12.125,-13])
        source=dict(rows=[repeated]*30,negative=-123,literal={'$integer':-5},
                    old_literal={'$review_ref':7},nested=[[-1],False,None])
        packed=object_pool(source,integer_refs=True)
        self.assertEqual(packed['encoding'],'review_integer_pool_v1')
        self.assertEqual(decode_tables(packed),source)
        self.assertEqual(decode_tables(key_table(packed,base36=True)),source)
        for bad in (dict(encoding='review_integer_pool_v1',objects=[],data=-1),
                    dict(encoding='review_integer_pool_v1',objects=[[-1]],data=-1),
                    dict(encoding='review_integer_pool_v1',objects=[],data={'$integer':True})):
            with self.assertRaises(ValueError):decode_tables(bad)
    def test_repeated_native_tags_share_values_without_rounding_or_type_changes(self):
        tags=[4364959746+i for i in range(100)]
        source=dict(tags=tags,actors=[[tag,True,-1.25,None] for tag in tags],
                    targets=[[tag] for tag in tags],events=[dict(tag=tag) for tag in tags],negative=-7)
        packed=object_pool(source,integer_refs=True)
        self.assertEqual(decode_tables(packed),source)
        self.assertEqual(decode_tables(key_table(packed,base36=True)),source)
        self.assertLess(len(json.dumps(packed)),len(json.dumps(source))-1000)
    def test_unrepresentable_coordinates_keep_original_values(self):
        source=self.table();source['rows'][0][2]=[.1,.2]
        packed=grouped_columns(source,('unit','ready'))
        self.assertNotIn('coordinates',packed)
        self.assertEqual(decode_tables(packed),source)
    def test_malformed_layout_rows_order_and_coordinate_masks_are_rejected(self):
        packed=grouped_columns(self.table(),('unit','ready'))
        bad=[]
        p=deepcopy(packed);p['groups'][0]['vary'].append(p['groups'][0]['vary'][0]);bad.append(p)
        p=deepcopy(packed);p['order'].pop();bad.append(p)
        p=deepcopy(packed);p['order'][1]=p['order'][0];bad.append(p)
        p=deepcopy(packed);p['groups'][0]['rows'][0].append(99);bad.append(p)
        p=deepcopy(packed);p['coordinates']['2']['scale']=True;bad.append(p)
        p=deepcopy(packed);p['coordinates']['2']['float_mask']='1';p['coordinates']['2']['type']='mixed';bad.append(p)
        for case in bad:
            with self.assertRaises(ValueError):decode_tables(case)
    def test_base36_key_indices_must_be_canonical_and_in_range(self):
        for index in ('00','A','-1','z'):
            with self.assertRaises(ValueError):decode_tables(dict(encoding='review_key_table_base36_v1',keys=['a'],data={index:1}))
    def test_actual_381_second_protoss_combat_context_fits(self):
        fixture=Path(__file__).parent/'testdata/duel-protoss-native-context.json.gz'
        case=json.loads(gzip.decompress(fixture.read_bytes()));source=case['source']
        client=Client();client.attempts=13
        planner=BotPlanner(client,RequestBudget(200),source['prepared_strategy']['text'],adaptive_cadence=True)
        planner.policy=case['policy']
        planner.chat('',json.dumps(source));restored=decode_tables(client.payload)
        for key in ('native_abilities','feedback','world'):
            self.assertEqual(restored[key],source[key])
        self.assertLessEqual(len(BOT_RULES)+len(json.dumps(client.payload,ensure_ascii=False,separators=(',',':'))),45000)
        self.assertIsNone(planner.fatal_error)
    def test_dense_combat_keeps_full_operator_advice_batch(self):
        from arena.operator_advice import PROTOCOL,MAX_BATCH_CHARS
        case=json.loads(gzip.decompress((Path(__file__).parent/'testdata/duel-protoss-native-context.json.gz').read_bytes()))
        source=case['source']
        text=''.join('观察区域%03d并结合当前视野决定行动。'%i for i in range(100))[:MAX_BATCH_CHARS]
        first=dict(id='a',text=text[:1000],submitted_at=1791964700.5)
        second=dict(id='b',text='',submitted_at=1791964701.5)
        size=lambda row:len(json.dumps(row,ensure_ascii=False,separators=(',',':')))
        second['text']=text[1000:1000+MAX_BATCH_CHARS-size(first)-size(second)]
        self.assertEqual(size(first)+size(second),MAX_BATCH_CHARS)
        source['operator_advice']=dict(messages=[first,second],
            source='user advice to this player; not engine observations',instruction=PROTOCOL,deferred_count=0)
        client=Client();client.attempts=13
        planner=BotPlanner(client,RequestBudget(200),source['prepared_strategy']['text'],adaptive_cadence=True)
        planner.policy=case['policy'];planner.chat('',json.dumps(source))
        restored=decode_tables(client.payload)
        for key in ('operator_advice','world','native_abilities','feedback'):
            self.assertEqual(restored[key],source[key])
        self.assertLessEqual(len(BOT_RULES)+len(json.dumps(client.payload,ensure_ascii=False,separators=(',',':'))),45000)
    def test_actual_507_second_terran_mirror_preserves_current_observations(self):
        case=json.loads(gzip.decompress((Path(__file__).parent/'testdata/duel-terran-mirror-context.json.gz').read_bytes()))
        source=case['source'];client=Client();client.attempts=17
        planner=BotPlanner(client,RequestBudget(200),case['strategy'],adaptive_cadence=True)
        planner.policy=case['policy'];planner.chat('',json.dumps(source));restored=decode_tables(client.payload)
        for key in ('world','native_abilities','feedback','rally_producers'):
            self.assertEqual(restored[key],source[key])
        self.assertLessEqual(len(BOT_RULES)+len(json.dumps(client.payload,ensure_ascii=False,separators=(',',':'))),45000)
    def test_larger_go_guard_keeps_real_mass_zerg_observations_and_advice(self):
        from arena.go_planner import GoPlanner
        from arena.operator_advice import PROTOCOL
        class GoClient(Client):max_battle_context_chars=GoPlanner.max_battle_context_chars
        case=json.loads(gzip.decompress((Path(__file__).parent/'testdata/duel-large-zerg-context.json.gz').read_bytes()))
        source=case['source']
        source['operator_advice']=dict(messages=[dict(id='a',text='保持当前视野并检查部队状态。'*60,submitted_at=1791964800.5)],
            source='user advice to this player; not engine observations',instruction=PROTOCOL,deferred_count=0)
        client=GoClient();client.attempts=17
        planner=BotPlanner(client,RequestBudget(200),case['strategy'],adaptive_cadence=True)
        planner.policy=case['policy'];planner.chat('',json.dumps(source));restored=decode_tables(client.payload)
        for key in ('world','native_abilities','feedback','active_tasks','operator_advice'):
            self.assertEqual(restored[key],source[key])
        self.assertEqual(restored['battlefield']['terrain_context'],source['battlefield']['terrain_context'])
        self.assertEqual(planner.context_limit,90000)
        self.assertLessEqual(len(BOT_RULES)+len(json.dumps(client.payload,ensure_ascii=False,separators=(',',':'))),90000)
        self.assertEqual(planner.budget.used,1);self.assertEqual(planner.request_limit,24)
    def test_go_guard_remains_bounded_and_does_not_send_oversized_input(self):
        from arena.go_planner import GoPlanner
        class GoClient(Client):max_battle_context_chars=GoPlanner.max_battle_context_chars
        client=GoClient();planner=BotPlanner(client,RequestBudget(6),'保持输入保护')
        with self.assertRaises(RuntimeError):planner.chat('',json.dumps(dict(evidence='x'*90000)))
        self.assertEqual(planner.fatal_kind,'context_budget')
        self.assertEqual(planner.budget.used,0);self.assertEqual(client.attempts,0)
    def test_actual_402_second_terran_context_fits_without_losing_native_facts(self):
        fixture=Path(__file__).parent/'testdata/duel-native-context.json.gz'
        source=json.loads(gzip.decompress(fixture.read_bytes()));client=Client();client.attempts=15
        planner=BotPlanner(client,RequestBudget(200),source['prepared_strategy']['text'],adaptive_cadence=True)
        planner.policy=dict(game_s=source['world']['game_s'],max_game_s=1800,max_wall_s=1860,
            elapsed_wall_s=950,remaining_wall_s=910,game_speed='slower',estimated_game_deadline_s=792,
            player=1,own_race='terran',opponent_race='protoss',decision_urgent=False)
        planner.chat('',json.dumps(source))
        restored=decode_tables(client.payload)
        self.assertEqual(restored['native_abilities'],source['native_abilities'])
        self.assertEqual(restored['feedback'],source['feedback'])
        self.assertEqual(restored['world'],source['world'])
        self.assertLessEqual(len(BOT_RULES)+len(json.dumps(client.payload,ensure_ascii=False,separators=(',',':'))),45000)
        self.assertIsNone(planner.fatal_error)
        self.assertEqual(planner.last_request_game_s,source['world']['game_s'])
        planner.policy['game_s']=source['world']['game_s']+1
        self.assertFalse(planner.ready_for_request())
    def test_actual_347_second_zerg_context_preserves_state_and_cadence(self):
        source=json.loads(gzip.decompress((Path(__file__).parent/'testdata/duel-zerg-native-context.json.gz').read_bytes()))
        client=Client();client.attempts=16
        planner=BotPlanner(client,RequestBudget(200),source['prepared_strategy']['text'],adaptive_cadence=True)
        planner.policy=dict(game_s=source['world']['game_s'],max_game_s=1800,max_wall_s=1860,
            elapsed_wall_s=824,remaining_wall_s=1036,game_speed='slower',estimated_game_deadline_s=791,
            match_number=3,player=2,opponent_kind='LLM',own_race='zerg',opponent_race='terran',
            clear_disadvantage=None,production_decision_due=True,decision_urgent=False)
        planner.chat('',json.dumps(source));decoded=decode_tables(client.payload)
        self.assertEqual(decoded['native_abilities'],source['native_abilities'])
        self.assertEqual(decoded['feedback'],source['feedback'])
        self.assertEqual(decoded['world'],source['world'])
        self.assertLessEqual(len(BOT_RULES)+len(json.dumps(client.payload,ensure_ascii=False,separators=(',',':'))),45000)
        self.assertEqual(planner.last_request_game_s,source['world']['game_s'])
        planner.policy['game_s']+=1
        self.assertFalse(planner.ready_for_request())


if __name__=='__main__':unittest.main()
