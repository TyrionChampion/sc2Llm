"""Translate explicit model values into the existing command representation."""
from copy import deepcopy
import re


def translate_plan(plan, payload):
    """No new values/commands: retain every selected unit, quantity and destination."""
    result=deepcopy(plan);changes=[]
    if not isinstance(result,dict) or not isinstance(result.get('tasks'),list):return result,changes
    names=list(payload.get('world',{}).get('own',{}))
    token=lambda name:re.sub(r'[ _]','',name).lower()
    for index,task in enumerate(result['tasks']):
        if not isinstance(task,dict) or not isinstance(task.get('args'),dict):continue
        args=task['args'];skill=task.get('skill');quantity=task.get('count')
        maximum={'scout':8,'order_units':200,'defend_base':32}.get(skill)
        explicit=(type(quantity) is int and maximum is not None and 1<=quantity<=maximum)
        explicit=explicit or (skill=='order_units' and quantity=='all')
        if maximum is not None and 'count' not in args and 'count' in task and explicit:
            args['count']=quantity;task['count']=1
            changes.append(dict(task=index,kind='quantity_field_location',
                source='task.count',destination='task.args.count',value=quantity))
        if skill=='defend_base' and isinstance(args.get('units'),list):
            for unit_index,name in enumerate(args['units']):
                if not isinstance(name,str) or name in names:continue
                matches=[candidate for candidate in names if token(candidate)==token(name)]
                if len(matches)==1:
                    args['units'][unit_index]=matches[0]
                    changes.append(dict(task=index,kind='observed_unit_name_spelling',
                        original=name,canonical=matches[0]))
        if task.get('args_extra')=={}:
            del task['args_extra']
            changes.append(dict(task=index,kind='empty_unused_field',field='args_extra'))
    return result,changes


def compact_strategy_layout(text):
    """Keep all non-whitespace characters and every ASCII word boundary."""
    word=lambda c:bool(re.fullmatch('[A-Za-z0-9_]',c))
    def spacing(match):
        before=text[match.start()-1] if match.start() else ''
        after=text[match.end()] if match.end()<len(text) else ''
        return ' ' if word(before) and word(after) else ''
    result=re.sub(r'\s+',spacing,text).strip()
    assert re.sub(r'\s','',text)==re.sub(r'\s','',result)
    return result
