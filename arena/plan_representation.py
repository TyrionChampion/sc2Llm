"""Translate explicit model values into the existing command representation."""
from copy import deepcopy
import re


def translate_plan(plan, payload):
    """No new values/commands: retain every selected unit, quantity and destination."""
    result=deepcopy(plan);changes=[]
    if not isinstance(result,dict) or not isinstance(result.get('tasks'),list):return result,changes
    names=list(payload.get('world',{}).get('own',{}))
    token=lambda name:re.sub(r'[ _]','',name).lower()
    batch_skills={'train_worker','train_unit'}
    if payload.get('tech_tree'):
        batch_skills.update(('build_supply','build_structure','expand','supply_depot','barracks','morph_unit'))
    for index,task in enumerate(result['tasks']):
        if not isinstance(task,dict) or not isinstance(task.get('args'),dict):continue
        args=task['args'];skill=task.get('skill');quantity=task.get('count')
        if (skill in ('train_worker','train_unit') and task.get('queue_mode') in ('append','coalesce')
                and ('queue_mode' not in args or args['queue_mode']==task['queue_mode'])):
            mode=task.pop('queue_mode');args['queue_mode']=mode
            changes.append(dict(task=index,kind='queue_mode_field_location',source='task.queue_mode',
                destination='task.args.queue_mode',value=mode))
        if skill=='scout' and args.get('targets')==['expansion_sites']:
            args['targets']='expansion_sites'
            changes.append(dict(task=index,kind='singleton_route_selector',source='task.args.targets',
                original=['expansion_sites'],canonical='expansion_sites'))
        if skill in batch_skills and type(args.get('count')) is int and 1<=args['count']<=200:
            selected=args['count']
            if quantity is None or type(quantity) is int and quantity in (1,selected):
                task['count']=selected;del args['count'];quantity=selected
                changes.append(dict(task=index,kind='quantity_field_location',source='task.args.count',
                    destination='task.count',value=selected))
        if skill in ('scout','order_units','defend_base','rally_units') and isinstance(task.get('reason'),str):
            outer=task['reason'];inner=args.get('reason')
            if inner is None or isinstance(inner,str):
                combined=outer if not inner or inner==outer else outer+'；'+inner
                if 1<=len(combined)<=240:
                    args['reason']=combined;del task['reason']
                    changes.append(dict(task=index,kind='reason_field_location',source='task.reason',
                        destination='task.args.reason',value=combined))
        product_field={'train_unit':('unit',('units','auxiliary_units')),
                       'morph_unit':('unit',('units','structures','auxiliary_units')),
                       'build_structure':('structure',('structures',)),
                       'research':('upgrade',('research',))}.get(skill)
        if product_field:
            field,groups=product_field;name=args.get(field)
            if isinstance(name,str):
                catalogue={entry['name'] for group in groups
                    for entry in payload.get('tech_tree',{}).get(group,[])
                    if isinstance(entry,dict) and isinstance(entry.get('name'),str)}
                if skill=='morph_unit':
                    catalogue.update(form for form in payload.get('tech_tree',{}).get('forms',[]) if isinstance(form,str))
                matches=[candidate for candidate in catalogue if token(candidate)==token(name)]
                if name not in catalogue and len(matches)==1:
                    args[field]=matches[0]
                    changes.append(dict(task=index,kind='catalogue_product_name_spelling',field=field,
                        original=name,canonical=matches[0]))
        if skill in ('scout','order_units') and isinstance(args.get('unit'),str):
            name=args['unit']
            selectors={value for value in payload.get('battlefield',{}).get('unit_selectors',[])
                       if isinstance(value,str)}
            matches=[candidate for candidate in selectors if token(candidate)==token(name)]
            if name not in selectors and len(matches)==1:
                args['unit']=matches[0]
                changes.append(dict(task=index,kind='unit_selector_name_spelling',original=name,canonical=matches[0]))
        if skill=='macro_plan' and isinstance(args.get('exempt_products'),list):
            catalogue={entry['name'] for group in ('units','structures','auxiliary_units')
                for entry in payload.get('tech_tree',{}).get(group,[])
                if isinstance(entry,dict) and isinstance(entry.get('name'),str)}
            for entry_index,name in enumerate(args['exempt_products']):
                if not isinstance(name,str) or name in catalogue:continue
                matches=[candidate for candidate in catalogue if token(candidate)==token(name)]
                if len(matches)==1:
                    args['exempt_products'][entry_index]=matches[0]
                    changes.append(dict(task=index,kind='catalogue_product_name_spelling',
                        field='exempt_products',original=name,canonical=matches[0]))
        maximum={'scout':8,'order_units':200,'defend_base':32}.get(skill)
        explicit=(type(quantity) is int and maximum is not None and 1<=quantity<=maximum)
        explicit=explicit or (skill=='order_units' and quantity=='all')
        if maximum is not None and 'count' not in args and 'count' in task and explicit:
            args['count']=quantity;task['count']=1
            changes.append(dict(task=index,kind='quantity_field_location',
                source='task.count',destination='task.args.count',value=quantity))
        elif maximum is not None and explicit and quantity!=1 and type(args.get('count')) is type(quantity) and args['count']==quantity:
            task['count']=1
            changes.append(dict(task=index,kind='redundant_quantity_field',source='task.count',
                destination='task.args.count',value=quantity))
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
