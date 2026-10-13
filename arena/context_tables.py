"""Restore lossless context tables before deriving typed command contracts."""
import json
from collections import Counter


def key_table(value):
    """Share JSON field names without removing values or changing dictionary order."""
    keys=[];indices={}
    def encode(node):
        if isinstance(node,list):return [encode(item) for item in node]
        if not isinstance(node,dict):return node
        result={}
        for key,item in node.items():
            if key not in indices:indices[key]=len(keys);keys.append(key)
            result[str(indices[key])]=encode(item)
        return result
    data=encode(value)
    packed=dict(encoding='review_key_table_v1',keys=keys,data=data)
    size=lambda node:len(json.dumps(node,ensure_ascii=False,separators=(',',':')))
    return packed if size(packed)<size(value) else value


def object_pool(value):
    """Share repeated JSON containers; preserve every value, sample and ordering."""
    serial=lambda node:json.dumps(node,ensure_ascii=False,separators=(',',':'))
    counts=Counter()
    def count(node):
        if isinstance(node,str):counts[serial(node)]+=1
        elif isinstance(node,(dict,list)):
            counts[serial(node)]+=1
            for item in (node.values() if isinstance(node,dict) else node):count(item)
    count(value)
    objects=[];indices={}
    def encode(node):
        if not isinstance(node,(dict,list,str)):return node
        key=serial(node)
        escaped=isinstance(node,dict) and set(node)=={'$review_ref'}
        pooled=(len(key)>=20 and counts[key]>1) or escaped
        if pooled and key in indices:return {'$review_ref':indices[key]}
        item=({k:encode(v) for k,v in node.items()} if isinstance(node,dict) else
              [encode(v) for v in node] if isinstance(node,list) else node)
        reference={'$review_ref':len(objects)}
        profitable=len(serial(item))*(counts[key]-1)>len(serial(reference))*counts[key]+3
        if not pooled or (not profitable and not escaped):return item
        index=len(objects);objects.append(item);indices[key]=index
        return {'$review_ref':index}
    data=encode(value)
    packed=dict(encoding='review_object_pool_v1',objects=objects,data=data)
    return packed if len(serial(packed))<len(serial(value)) else value


def decode_tables(value):
    if isinstance(value,list):return [decode_tables(item) for item in value]
    if not isinstance(value,dict):return value
    if value.get('encoding')=='review_key_table_v1':
        keys=value['keys']
        if (not isinstance(keys,list) or not all(isinstance(key,str) for key in keys)
                or len(set(keys))!=len(keys)):
            raise ValueError('Invalid review key table')
        def restore(node):
            if isinstance(node,list):return [restore(item) for item in node]
            if not isinstance(node,dict):return node
            result={}
            for index,item in node.items():
                if not isinstance(index,str) or not index.isascii() or not index.isdigit():
                    raise ValueError('Invalid review key index')
                number=int(index)
                if str(number)!=index or not 0<=number<len(keys):
                    raise ValueError('Invalid review key index')
                result[keys[number]]=restore(item)
            return result
        return decode_tables(restore(value['data']))
    if value.get('encoding')=='review_object_pool_v1':
        objects=value['objects']
        if not isinstance(objects,list):raise ValueError('Invalid review object pool')
        def restore(node,stack=()):
            if isinstance(node,dict) and set(node)=={'$review_ref'}:
                index=node['$review_ref']
                if type(index) is not int or not 0<=index<len(objects) or index in stack:
                    raise ValueError('Invalid or cyclic review object reference')
                # Entries are literal containers; only their children may be references.
                return children(objects[index],(*stack,index))
            return children(node,stack)
        def children(node,stack):
            if isinstance(node,list):return [restore(item,stack) for item in node]
            if isinstance(node,dict):return {k:restore(v,stack) for k,v in node.items()}
            return node
        return decode_tables(restore(value['data']))
    if value.get('encoding')=='columns_rows_common_v1':
        columns,rows,common=value['columns'],value['rows'],value['common']
        if (not isinstance(columns,list) or not all(isinstance(key,str) for key in columns)
                or len(set(columns))!=len(columns) or not isinstance(common,dict)
                or set(columns)&set(common) or not isinstance(rows,list)
                or any(not isinstance(row,list) or len(row)!=len(columns) for row in rows)):
            raise ValueError('Invalid context table; refusing to truncate or overwrite fields')
        return [decode_tables(common|dict(zip(columns,row))) for row in rows]
    if value.get('encoding')=='key_groups_v1':
        groups=[decode_tables(group) for group in value['groups']]
        if not all(isinstance(group,list) for group in groups):
            raise ValueError('Invalid context table groups')
        result=[]
        for pair in value['order']:
            if (not isinstance(pair,list) or len(pair)!=2 or any(type(index) is not int for index in pair)
                    or not 0<=pair[0]<len(groups) or not 0<=pair[1]<len(groups[pair[0]])):
                raise ValueError('Invalid context table ordering')
            result.append(groups[pair[0]][pair[1]])
        return result
    return {key:decode_tables(item) for key,item in value.items()}
