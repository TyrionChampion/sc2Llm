"""Compact native command schemas for the fixed Go endpoint; host checks still apply."""
from collections import Counter
import json


def battle_schema(payload):
    # This pure helper describes the same executor API; it invokes no CLI/provider.
    from arena.chatgpt_planner import battle_output_schema
    schema=battle_output_schema(payload)
    schema['properties']['tasks']['maxItems']=6
    def simplify(node):
        if isinstance(node,list):return [simplify(item) for item in node]
        if not isinstance(node,dict):return node
        result={key:simplify(item) for key,item in node.items() if key!='description'}
        return result
    schema=simplify(schema)
    serial=lambda node:json.dumps(node,ensure_ascii=False,separators=(',',':'),sort_keys=True)
    is_schema=lambda node:isinstance(node,dict) and any(k in node for k in ('type','anyOf','oneOf'))
    counts=Counter()
    def count(node):
        if is_schema(node):counts[serial(node)]+=1
        if isinstance(node,dict):
            for item in node.values():count(item)
        elif isinstance(node,list):
            for item in node:count(item)
    count(schema)
    definitions={};indices={}
    def encode(node):
        if isinstance(node,list):return [encode(item) for item in node]
        if not isinstance(node,dict):return node
        key=serial(node);shared=is_schema(node) and len(key)>80 and counts[key]>1
        if shared and key in indices:return {'$ref':'#/$defs/'+indices[key]}
        result={name:encode(item) for name,item in node.items()}
        if shared:
            name='s'+str(len(definitions));ref={'$ref':'#/$defs/'+name}
            if len(serial(result))*(counts[key]-1)>len(serial(ref))*counts[key]+len(name)+6:
                definitions[name]=result;indices[key]=name;return ref
        return result
    packed=encode(schema)
    if definitions:packed['$defs']=definitions
    return packed


def response_format(schema):
    return dict(type='json_schema',json_schema=dict(name='sc2_commands',strict=True,schema=schema))
