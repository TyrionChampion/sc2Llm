"""Restore lossless context tables before deriving typed command contracts."""
import json
import math
from collections import Counter


def grouped_columns(table, group_columns):
    """Share repeated row values while retaining a plain columns/rows table exactly."""
    if not isinstance(table,dict) or not isinstance(table.get('columns'),list) or not isinstance(table.get('rows'),list):return table
    columns=table['columns'];rows=table['rows']
    if any(not isinstance(row,list) or len(row)!=len(columns) for row in rows):return table
    selected=[columns.index(key) for key in group_columns if key in columns]
    if not selected or len(rows)<3:return table
    serial=lambda value:json.dumps(value,ensure_ascii=False,separators=(',',':'))
    coordinates={}
    rows=[list(row) for row in rows]
    if 'pos' in columns:
        index=columns.index('pos');points=[row[index] for row in rows]
        if all(isinstance(point,list) and len(point)==2 and all(type(v) in (int,float) and math.isfinite(v) for v in point) for point in points):
            scale=max(float(v).as_integer_ratio()[1] for point in points for v in point)
            if scale<=2**24:
                encoded=[[int(v*scale) for v in point] for point in points]
                mask=''.join('1' if type(v) is float else '0' for point in points for v in point)
                spec=dict(scale=scale,type='float' if set(mask)=={'1'} else 'int' if set(mask)=={'0'} else 'mixed')
                if spec['type']=='mixed':spec['float_mask']=mask
                if sum(len(serial(p))-len(serial(e)) for p,e in zip(points,encoded))>len(serial(spec))+30:
                    coordinates[str(index)]=spec
                    for row,point in zip(rows,encoded):row[index]=point
    groups=[];indices={};order=[]
    for row in rows:
        key=serial([row[i] for i in selected])
        if key not in indices:indices[key]=len(groups);groups.append([])
        index=indices[key];order.append([index,len(groups[index])]);groups[index].append(row)
    packed=[]
    for group in groups:
        common={str(i):group[0][i] for i in range(len(columns))
                if all(serial(row[i])==serial(group[0][i]) for row in group[1:])}
        vary=[i for i in range(len(columns)) if str(i) not in common]
        packed.append(dict(common=common,vary=vary,rows=[[row[i] for i in vary] for row in group]))
    result=dict(encoding='column_value_groups_v1',columns=columns,groups=packed,order=order,
                extra={key:value for key,value in table.items() if key not in ('columns','rows')})
    offsets=[];total=0
    for group in groups:offsets.append(total);total+=len(group)
    flat=[offsets[group]+row for group,row in order]
    if len(serial(flat))+25<len(serial(order)):
        result.update(order=flat,order_format='flat')
    if coordinates:result['coordinates']=coordinates
    return result if len(serial(result))<len(serial(table)) else table


def index36(index):
    digits='0123456789abcdefghijklmnopqrstuvwxyz';result=''
    while index:result=digits[index%36]+result;index//=36
    return result or '0'


def key_table(value,base36=False):
    """Share JSON field names without removing values or changing dictionary order."""
    keys=[];indices={}
    def encode(node):
        if isinstance(node,list):return [encode(item) for item in node]
        if not isinstance(node,dict):return node
        result={}
        for key,item in node.items():
            if key not in indices:indices[key]=len(keys);keys.append(key)
            result[index36(indices[key]) if base36 else str(indices[key])]=encode(item)
        return result
    data=encode(value)
    packed=dict(encoding='review_key_table_base36_v1' if base36 else 'review_key_table_v1',keys=keys,data=data)
    size=lambda node:len(json.dumps(node,ensure_ascii=False,separators=(',',':')))
    return packed if size(packed)<size(value) else value


def object_pool(value, integer_refs=False):
    """Share repeated JSON containers; preserve every value, sample and ordering."""
    serial=lambda node:json.dumps(node,ensure_ascii=False,separators=(',',':'))
    counts=Counter()
    def count(node):
        if isinstance(node,str):counts[serial(node)]+=1
        elif integer_refs and type(node) is int and node>=1_000_000:counts[serial(node)]+=1
        elif isinstance(node,(dict,list)):
            counts[serial(node)]+=1
            for item in (node.values() if isinstance(node,dict) else node):count(item)
    count(value)
    objects=[];indices={}
    def encode(node):
        if integer_refs and type(node) is int and node<0:return {'$integer':node}
        large_integer=integer_refs and type(node) is int and node>=1_000_000
        if not isinstance(node,(dict,list,str)) and not large_integer:return node
        key=serial(node)
        escaped=isinstance(node,dict) and set(node)==({'$integer'} if integer_refs else {'$review_ref'})
        # Integer references are short enough to share common unit/ability
        # names as well as long prose; the exact profitability check below
        # still rejects any entry that would enlarge the representation.
        pooled=((large_integer or integer_refs or len(key)>=20) and counts[key]>1) or escaped
        reference_for=lambda index:-(index+1) if integer_refs else {'$review_ref':index}
        if pooled and key in indices:return reference_for(indices[key])
        item=({k:encode(v) for k,v in node.items()} if isinstance(node,dict) else
              [encode(v) for v in node] if isinstance(node,list) else node)
        reference=reference_for(len(objects))
        profitable=len(serial(item))*(counts[key]-1)>len(serial(reference))*counts[key]+3
        if not pooled or (not profitable and not escaped):return item
        index=len(objects);objects.append(item);indices[key]=index
        return reference_for(index)
    data=encode(value)
    packed=dict(encoding='review_integer_pool_v1' if integer_refs else 'review_object_pool_v1',objects=objects,data=data)
    return packed if len(serial(packed))<len(serial(value)) else value


def decode_tables(value):
    if isinstance(value,list):return [decode_tables(item) for item in value]
    if not isinstance(value,dict):return value
    if value.get('encoding')=='column_value_groups_v1':
        columns=value['columns'];groups=[]
        if not isinstance(columns,list) or not all(isinstance(key,str) for key in columns) or len(set(columns))!=len(columns):raise ValueError('Invalid grouped columns')
        for group in decode_tables(value['groups']):
            common=group['common'];vary=group['vary']
            if (not isinstance(common,dict) or not isinstance(vary,list)
                    or any(type(i) is not int or not 0<=i<len(columns) for i in vary)
                    or len(set(vary))!=len(vary)
                    or any(not isinstance(i,str) or not i.isascii() or not i.isdigit()
                           or str(int(i))!=i or not 0<=int(i)<len(columns) for i in common)
                    or {int(i) for i in common}&set(vary)
                    or {int(i) for i in common}|set(vary)!=set(range(len(columns)))):
                raise ValueError('Invalid grouped column layout')
            restored=[]
            for row in group['rows']:
                if not isinstance(row,list) or len(row)!=len(vary):raise ValueError('Invalid grouped row width')
                item=[None]*len(columns)
                for index,value_item in common.items():item[int(index)]=decode_tables(value_item)
                for index,value_item in zip(vary,row):item[index]=decode_tables(value_item)
                restored.append(item)
            groups.append(restored)
        rows=[];seen=set()
        flat_rows=[(i,j) for i,group in enumerate(groups) for j in range(len(group))]
        for pair in value['order']:
            if value.get('order_format')=='flat':
                if type(pair) is not int or not 0<=pair<len(flat_rows):raise ValueError('Invalid flat row order')
                pair=list(flat_rows[pair])
            if (not isinstance(pair,list) or len(pair)!=2 or any(type(i) is not int for i in pair)
                    or not 0<=pair[0]<len(groups) or not 0<=pair[1]<len(groups[pair[0]])
                    or tuple(pair) in seen):raise ValueError('Invalid grouped row order')
            rows.append(groups[pair[0]][pair[1]]);seen.add(tuple(pair))
        if len(seen)!=sum(len(group) for group in groups):raise ValueError('Grouped rows omitted')
        for index,spec in value.get('coordinates',{}).items():
            if (not isinstance(index,str) or not index.isascii() or not index.isdigit()
                    or str(int(index))!=index or not 0<=int(index)<len(columns)
                    or not isinstance(spec,dict) or type(spec.get('scale')) is not int
                    or not 1<=spec['scale']<=2**24 or spec.get('type') not in ('int','float','mixed')):
                raise ValueError('Invalid exact coordinate encoding')
            mask=spec.get('float_mask','')
            if spec['type']=='mixed' and (not isinstance(mask,str) or len(mask)!=2*len(rows) or set(mask)-{'0','1'}):
                raise ValueError('Invalid coordinate type mask')
            for row_index,row in enumerate(rows):
                point=row[int(index)]
                if not isinstance(point,list) or len(point)!=2 or any(type(v) is not int for v in point):
                    raise ValueError('Invalid exact coordinate values')
                if any((spec['type']=='int' or spec['type']=='mixed' and mask[2*row_index+k]=='0')
                       and v%spec['scale'] for k,v in enumerate(point)):
                    raise ValueError('Invalid integer coordinate value')
                row[int(index)]=[float(v/spec['scale']) if spec['type']=='float' or spec['type']=='mixed' and mask[2*row_index+k]=='1'
                                 else v//spec['scale'] for k,v in enumerate(point)]
        extra=value.get('extra',{})
        if not isinstance(extra,dict) or set(extra)&{'columns','rows'}:raise ValueError('Invalid grouped table metadata')
        return decode_tables(extra)|dict(columns=columns,rows=rows)
    if value.get('encoding') in ('review_key_table_v1','review_key_table_base36_v1'):
        keys=value['keys']
        base36=value['encoding']=='review_key_table_base36_v1'
        if (not isinstance(keys,list) or not all(isinstance(key,str) for key in keys)
                or len(set(keys))!=len(keys)):
            raise ValueError('Invalid review key table')
        def restore(node):
            if isinstance(node,list):return [restore(item) for item in node]
            if not isinstance(node,dict):return node
            result={}
            for index,item in node.items():
                if not isinstance(index,str) or not index.isascii() or not index or any(c not in ('0123456789abcdefghijklmnopqrstuvwxyz' if base36 else '0123456789') for c in index):
                    raise ValueError('Invalid review key index')
                number=int(index,36 if base36 else 10)
                if (index36(number) if base36 else str(number))!=index or not 0<=number<len(keys):
                    raise ValueError('Invalid review key index')
                result[keys[number]]=restore(item)
            return result
        return decode_tables(restore(value['data']))
    if value.get('encoding') in ('review_object_pool_v1','review_integer_pool_v1'):
        objects=value['objects']
        integer_refs=value['encoding']=='review_integer_pool_v1'
        if not isinstance(objects,list):raise ValueError('Invalid review object pool')
        def restore(node,stack=()):
            if integer_refs and isinstance(node,dict) and set(node)=={'$integer'}:
                if type(node['$integer']) is not int or node['$integer']>=0:raise ValueError('Invalid literal integer')
                return node['$integer']
            if (integer_refs and type(node) is int and node<0
                    or not integer_refs and isinstance(node,dict) and set(node)=={'$review_ref'}):
                index=-node-1 if integer_refs else node['$review_ref']
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
