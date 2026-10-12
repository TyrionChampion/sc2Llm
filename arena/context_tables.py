"""Restore lossless context tables before deriving typed command contracts."""


def decode_tables(value):
    if isinstance(value,list):return [decode_tables(item) for item in value]
    if not isinstance(value,dict):return value
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
