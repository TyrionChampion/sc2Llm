"""Post-game review and regression-gated repairs using the official subscription."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from arena.chatgpt_planner import ChatGPTSubscriptionPlanner
from arena.go_planner import GoPlanner, GO_MODEL, GO_MODELS


REVIEW_RULES = '''你是战后复盘员。用户要求自主迭代至击败easy然后hard bot。
必须检查真实时间线、完整错误事件、生产确认、损失、进攻与投降情况，而非只看输赢。
combat_summary中的our_deaths_observed和enemy_deaths_observed是实际阵亡记录；
enemy_left_vision只是离开视野，不是阵亡。不得忽略这些统计而声称没有损失。
科技实验室研究与母建筑训练可并行；研究不是停产理由。维京/医疗艇无需科技实验室。
女妖/幽灵隐身需要探测；维京是对空火力而非探测器，轨道扫描或渡鸦可提供探测。
只依据本局证据。敌军总兵力未知；引擎只给错误码时不要编造根因。
campaign_history可用于识别重复失败；历史胜局只作参考，其地图、种子、限时和接口可能不同。
若同一路线连续失败，重新比较至少两种可执行方案，评估扩张时机、产能与克制阵容，允许
换战略；不能仅重复相同开局并增加人数。保留扩张运营、防守反击、科技、时机进攻的选择。
重复失败时，findings必须比较本局实际执行与上一复盘方案的偏离；strategy至少比较两种
具体可执行路线的关键兵种、前置、生产吞吐及经济风险后再选择。若仍选失败过的路线，
说明这次可从观测核验的实质变化，不能仅把原军力目标增大或重述原提示词。
返回JSON，包含tasks:[]、reason:中文简述、category:"strategy|feature_gap|environment|win"、
strategy:目标1400字符以内完整新战术（含英文/空格/标点，校验上限1800字符；下一局独立上下文，非旧战术增量）、
findings:[{evidence_ids:[真实错误事件id],detail:"具体诊断"}]、
gap:null或{evidence_ids:[真实错误事件id],description:"需要补的能力",reproduction:"可执行的回归场景"}。
只提出有明确执行器错误证据和可复现失败的功能缺口。缺参数、重复活动命令、取消历史ID、
没有下达现有接口能执行的命令、造兵时未造前置、贪经济、过晚进攻、忘记撤防，属于计划/提示词问题。
unknown skill不自动等于缺功能：先检查现有能力是否已经提供等效操作。
网络/认证失败和旧版游戏不支持的能力属于environment，不应盲改战术或假造胜负。
time_budget不是战场败势；检查runtime_timing的墙钟/速度，Slower下1860墙钟秒约798游戏秒，
运营、侦察和出击须在墙钟允许的实际游戏时间内安排，不能假设总能打满1800游戏秒。
没有可证实功能缺口时category=strategy，优化开局、持续生产、进攻时机、阵容与止损。
如果获胜，仍检查浪费和非法计划，保留有效战术而非随意重写。
对扩张经济战略，必须检查扩张预算之外的钱是否及时转成实际战斗部队：资源持续上涨时，
区分空闲产能未下造兵令、产能不足、供给/前置阻塞与已在生产；按真实证据提出对应改进。
后续战术须保留这项运营基本功：合理预留当前扩张和必要前置，余钱持续造兵；空闲产能
及时批量生产，产能不足就补产能，阻塞就补齐，不为远期计划长期囤钱。兵种和规模由局势
决定，不能因为运营不足就把所有战略改成同一种快攻。
新战术要自主制定可量化的阶段军力、产能和关键升级目标、检查时点与调整条件；数量根据
本局实际收入、成本、供给和敌情选择。检查军力落后时是否仍优先补工而没有足量造兵，
并给出能覆盖检查间隔的生产批次安排，不能只有“持续造兵”的口号。
阶段目标须按实际产能吞吐计算；选择大量掠夺者时评估并行科技实验室产能，不能只增加
无挂件兵营。各兵种达到自己的编队目标后再出击；同轮侦察和出击不代表已确认敌情。
已有order_units支持明确数值count，不足人数时等待，够人数后统一attack_move；可用它实现
一批部队出发的时机。attack_nearest/move_out不等集结，只把当前自由军队立即发出去，反复
使用可能把零散新兵送入敌军。评估现有指令时请考虑这些语义，不能误报为缺少集结功能。
scout外层count固定1，人数必须在args.count；args必填unit/count/reason/targets。
合法示例：{"skill":"scout","count":1,"args":{"unit":"marine","count":1,"reason":"侦察","targets":["enemy_start"]}}。
unit/count/reason不能省略或搬到任务顶层。targets可为named place或[x,y]的列表；
全图矿区搜索使用整个字符串targets:"expansion_sites"，
禁止targets:["expansion_sites"]。order_units才使用单个args.target。
混编可用order_units args.unit="army"选择战斗单位，整数count表示等待足够的自由战斗单位；
它不是只选陆战队，也不会自动训练缺少的兵种。不要虚构bio等未提供的选择器。
确认多次整队战损时必须检查交战记录中的敌方组成和实际损失比例；20名无升级陆战队的简单局
胜利不证明同样编队能战胜困难局的恶狼、掠夺者组合。保留有效经济/生产，针对反证改编队和时机。
达到本局模型调用上限只停止新增请求，已经下达的任务仍执行到时间上限或实际胜负。
剩余请求很少时必须预先安排可执行的生产、部署和集结点；最后一轮只侦察会没有后续决策。
请求耗尽不构成投降理由，但不能取消持续明显败势时的主动投降规则。
切勿提高调用/时间上限、替换模型、用debug作弊或把预算停止算成胜利。'''


def compact_review_evidence(diagnostics, selected):
    """Retain every observed world; encode repeated task summaries as explicit changes."""
    timeline=[];previous={}
    for row in diagnostics.get('timeline',[]):
        current={}
        for task in row.get('active_tasks',[]):
            item={k:task[k] for k in ('id','skill','count','state','reason') if k in task}
            workflow=task.get('workflow',{})
            item['execution']={k:workflow[k] for k in ('args','issued_quantity','observed_completed',
                'unit_tags','route_index') if k in workflow}
            current[task['id']]=item
        timeline.append(dict(world=row['world'],last_decision=row.get('last_decision'),
            task_changes=[item for key,item in current.items() if previous.get(key)!=item],
            tasks_no_longer_active=sorted(set(previous)-set(current))))
        previous=current
    result=dict(diagnostics,errors=selected,timeline=timeline,
        omitted_error_events=len(diagnostics['errors'])-len(selected),
        timeline_encoding='Every world sample retained; task summaries encoded as changes, no hidden enemy inference')
    windows=diagnostics.get('engagement_windows',[])
    if windows:
        summary={}
        for field in ('our_deaths_observed','enemy_deaths_observed','enemy_left_vision','enemy_entered_vision'):
            totals={}
            for window in windows:
                for name,count in window.get(field,{}).items():totals[name]=totals.get(name,0)+count
            summary[field]=totals
        result['combat_summary']=dict(summary,window_count=len(windows),
            closed_windows=sum(row.get('status')=='closed' for row in windows),
            ongoing_sample_windows=sum(row.get('status')!='closed' for row in windows),
            windows_with_damage=sum(bool(row.get('damage_observed')) for row in windows),
            source='Own raw death/contact windows; latest queued observation for an unclosed window. Last ticks may be unreported. Left vision is not death. No killer or individual mission attribution.')
        result['engagement_windows']=windows[-6:]
        result['omitted_engagement_windows']=max(0,len(windows)-6)
        while (len(result['engagement_windows'])>1 and
               len(json.dumps(result,ensure_ascii=False,separators=(',',':')))>50000):
            result['engagement_windows'].pop(0)
            result['omitted_engagement_windows']+=1
    original=diagnostics.get('timeline',[])
    if original:
        result['final_active_tasks']=original[-1].get('active_tasks',[])
        result['final_control_policy']=original[-1].get('defense',{})
    if timeline and len(json.dumps(result,ensure_ascii=False,separators=(',',':')))>50000:
        from arena.autonomous_bot import contact_groups
        result['timeline_worlds']=contact_groups([row['world'] for row in timeline])
        result['timeline']=[dict(world_index=index,**{k:v for k,v in row.items() if k!='world'})
                            for index,row in enumerate(timeline)]
        result['timeline_encoding']+=('; world_index indexes timeline_worlds in original order. '
            'columns_rows_common_v1 restores each row as common + dict(zip(columns,row)); '
            'key_groups_v1 restores groups then selects [group,index] pairs from order. '
            'All world samples and task changes retained losslessly.')
    return result


def executor_failure_context(errors):
    """Ground a capability diagnosis in the exact executor lines cited by real tracebacks."""
    root=Path(__file__).resolve().parent.parent
    ranges={}
    for event in errors:
        for match in re.finditer(r'File "[^"\n]*/(arena/[a-z_]+\.py)", line (\d+)',event.get('traceback','')):
            name,line=match.group(1),int(match.group(2))
            if name not in REPAIRABLE:continue
            entry=ranges.setdefault(name,dict(low=line,high=line,ids=set()))
            entry['low']=min(entry['low'],line);entry['high']=max(entry['high'],line)
            entry['ids'].add(event['id'])
    result=[];size=0
    for name,entry in sorted(ranges.items()):
        path=root/name
        if not path.resolve().is_relative_to(root) or not path.is_file():continue
        lines=path.read_text().splitlines();start=max(0,entry['low']-36)
        end=min(len(lines),entry['high']+35,start+120)
        excerpt='\n'.join(lines[start:end])
        if size+len(excerpt)>12000:continue
        result.append(dict(path=name,first_line=start+1,text=excerpt,evidence_ids=sorted(entry['ids'])))
        size+=len(excerpt)
    return result


def campaign_review_history(directory):
    """Bounded factual context from this ledger and an independently verified prior win."""
    folder=Path(directory).parent.parent.resolve()
    source=folder/'campaign.json'
    if not source.is_file():return {}
    state=json.loads(source.read_text())
    current=Path(directory).parent.resolve()
    history=[]
    for entry in state.get('matches',[])[-4:]:
        path=Path(entry['report']).resolve()
        if not path.is_relative_to(folder) or path.parent==current or not path.is_file():continue
        report=json.loads(path.read_text());samples=[row['world'] for row in report.get('timeline',[])]
        counts={name:max((row.get('own',{}).get(name,0) for row in samples),default=0)
                for name in ('Marine','Marauder','SiegeTank','SiegeTankSieged','SCV')}
        item=dict(number=entry['number'],outcome=report['outcome'],result_source=report['result_source'],
            termination=report['termination'],game_s=report.get('final_world',{}).get('game_s'),
            maximum_per_type_not_simultaneous=counts,
            maximum_minerals=max((row.get('minerals',0) for row in samples),default=0))
        review=path.parent/'review.json'
        if review.is_file():
            item['review_proposed_next_strategy_excerpt']=json.loads(review.read_text()).get('strategy','')[:140]
        history.append(item)
    result=dict(recent_matches=history[-3:],scope='Observed own units and saved outcomes; per-type maxima are not one simultaneous army. Unknown is not confirmed defeat.')
    previous=state.get('previous_campaign',{}).get('path')
    if previous:
        old=Path(previous).resolve()
        if old.is_relative_to(folder.parent) and old.is_file():
            archive=old.parent/'winning-strategy.json'
            if archive.is_file():
                win=json.loads(archive.read_text());report_path=Path(win.get('report','')).resolve()
                if report_path.is_relative_to(old.parent) and report_path.is_file():
                    report=json.loads(report_path.read_text())
                    replay=Path(report.get('replay','')).resolve()
                    verified=(report.get('outcome')=='Victory' and report.get('difficulty')=='hard'
                        and report.get('result_source')=='engine_player_result'
                        and report.get('engine_results',{}).get('1')=='Victory'
                        and replay.is_relative_to(old.parent) and replay.is_file() and replay.stat().st_size>0)
                    if verified:
                        strategy=win.get('strategy','')
                        result['verified_historical_reference']=dict(strategy=strategy[:1200],
                            strategy_truncated=len(strategy)>1200,game_s=report.get('final_world',{}).get('game_s'),
                            source_report=str(report_path),result_source='engine_player_result',
                            limitation='Different seed/time/interface conditions; compare alternatives, do not assume this tactic must win or copy it as a mandatory opening.')
    return result


def review_game(model, directory, budget, diagnostics, strategy, difficulty):
    client = (GoPlanner if model in GO_MODELS else ChatGPTSubscriptionPlanner)(model, directory)
    client.response_kind = 'review'
    # Go prefill + low-effort review can exceed a battle request's latency budget.
    # Reviews run after engine cleanup; this leaves every match/request ceiling intact.
    client.timeout = 300 if model in GO_MODELS else 60
    if model in GO_MODELS:client.max_tokens = 24000
    client.reasoning_effort = 'low' if model in GO_MODELS else 'medium'
    errors = diagnostics['errors']
    selected = []
    seen = set()
    for event in errors:
        key = (event['kind'], str(event.get('error', event.get('reason', ''))))
        if key not in seen and len(selected) < 12:
            selected.append(event)
            seen.add(key)
    evidence = compact_review_evidence(diagnostics,selected)
    evidence['executor_source_context']=executor_failure_context(selected)
    evidence['full_error_evidence_file']=str(Path(directory).parent/'diagnostics.json')
    payload = dict(difficulty=difficulty, previous_strategy=strategy, evidence=evidence,
                   campaign_history=campaign_review_history(directory),
                   supported_commands=['train_worker','train_unit','build_structure','build_supply',
                       'research','gather_gas','expand','gather','attack_nearest','move_out',
                       'defend_base','cancel_task','scout','order_units','cancel_unit_order','surrender',
                       'macro_plan','cancel_expansion','morph_unit','rally_units',
                       'scan','call_mule','supply_drop'],
                   constraints=budget.report())
    context_limit=60000
    if model in GO_MODELS and getattr(client,'native_schema',False):
        from arena.chatgpt_planner import review_output_schema
        from arena.go_schema import response_format
        schema=review_output_schema();client.response_schema=schema
        context_limit-=len(json.dumps(response_format(schema),ensure_ascii=False,separators=(',',':')))
    try:
        text = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        if len(text)>context_limit:
            from arena.autonomous_bot import nested_contact_tables
            payload=nested_contact_tables(payload)
            payload['lossless_table_encoding']='Recursive columns_rows_common_v1/key_groups_v1; decoding rules in system prompt; no observations removed.'
            text=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        if len(text) > context_limit:
            from arena.context_tables import object_pool
            payload=object_pool(payload)
            text=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        if len(text) > context_limit:
            from arena.context_tables import key_table
            payload=key_table(payload)
            text=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        if len(text) > context_limit:
            raise ValueError('Review evidence exceeds context limit; full evidence retained on disk')
        budget.reserve('review')
        rules=REVIEW_RULES+'\ncolumns_rows_common_v1每行=common合并dict(zip(columns,row))；key_groups_v1先读groups，再按order的[group,row]取行。\nreview_object_pool_v1是无损共享编码：objects存放字典/数组/字符串，data为原始对象；遇到仅有$review_ref的对象时用objects[该整数]替换并递归还原其子项。所有原始观测、决策、任务变化仍保留，不能把引用理解为遗漏。直接查必要引用即可，无需逐项重写标签、坐标或展开整份JSON；集中检查真实产出、战损、非法计划、侦察与出击。'
        rules+='\nreview_key_table_v1无损共享字段名：keys为字段名表，data中每个对象的数字字符串键k表示keys[int(k)]；递归还原所有对象键，数组和值不变，再按上述共享对象/表格编码读取。没有删除观测或任务。'
        result = json.loads(client.chat(rules, text))
        if result.get('category') not in ('strategy','feature_gap','environment','win'):
            raise ValueError('Invalid review category')
        revised = result.get('strategy')
        if isinstance(revised,str) and len(revised)>1800:
            from arena.plan_representation import compact_strategy_layout
            formatted=compact_strategy_layout(revised)
            if len(formatted)<=1800:
                folder=Path(directory);folder.mkdir(parents=True,exist_ok=True)
                (folder/'strategy-layout.json').write_text(json.dumps(dict(original_chars=len(revised),
                    formatted_chars=len(formatted),all_non_whitespace_characters_preserved=True,
                    content_removed=False,source='original public Go response'),ensure_ascii=False,indent=2))
                revised=result['strategy']=formatted
        if not isinstance(revised,str) or not revised.strip() or len(revised)>1800:
            raise ValueError('Review must provide a bounded complete strategy')
        if result['category']=='feature_gap':
            gap=result.get('gap')
            ids={event['id'] for event in errors}
            if not isinstance(gap,dict) or not gap.get('reproduction') or not gap.get('evidence_ids'):
                raise ValueError('Feature gap requires a reproducer and cited own error events')
            if not set(gap['evidence_ids'])<=ids:
                raise ValueError('Feature gap cites nonexistent evidence')
        return result
    finally:
        client.close()


# Repairs may change execution capabilities, never budgets, scoring or the environment.
REPAIRABLE={'arena/macro.py','arena/tasks.py','arena/defense_orders.py',
            'arena/spatial.py','arena/combat_micro.py','arena/reflex.py'}
CHECKS=['test_surrender','test_chatgpt_planner','test_autonomous_bot','test_autonomous_review',
        'test_realtime_runtime','test_defense_orders','test_two_front_defense','test_spatial_missions']


def copy_test_workspace(root, target):
    target.mkdir(parents=True)
    shutil.copytree(root/'arena',target/'arena',ignore=shutil.ignore_patterns('__pycache__'))
    for source in root.glob('*.py'):
        shutil.copy2(source,target/source.name)
    (target/'logs').mkdir()


def workspace_files(folder):
    result={}
    for path in folder.rglob('*'):
        if '__pycache__' in path.parts or 'logs' in path.relative_to(folder).parts:continue
        if path.is_symlink():raise ValueError('Repair workspace contains a symlink')
        if path.is_file():result[str(path.relative_to(folder))]=path.read_bytes()
    return result


def checked_tests(python, directory, modules, log):
    environment=os.environ.copy()
    environment['PYTHONPATH']=str(directory)
    result=subprocess.run([str(python),'-W','error::ResourceWarning','-m','unittest','-v',*modules],
                          cwd=directory,env=environment,capture_output=True,text=True,timeout=90)
    output=result.stdout+result.stderr
    log.write_text(output)
    count=re.search(r'Ran ([1-9][0-9]*) tests?',output)
    return dict(exit_code=result.returncode, tests=int(count.group(1)) if count else 0,
                log=str(log),output=output)


def repair_game(root, model, folder, budget, review):
    """Generate a fix; prove regression fails BEFORE and passes AFTER before applying."""
    if model in GO_MODELS:
        return dict(applied=False,reason='Go review identified a gap; operator must reproduce and verify the repair before continuing. No ChatGPT repair fallback.')
    folder=Path(folder);folder.mkdir()
    baseline=folder/'baseline';candidate=folder/'candidate'
    copy_test_workspace(root,baseline);copy_test_workspace(root,candidate)
    (candidate/'diagnosis.json').write_text(json.dumps(review,ensure_ascii=False,indent=2))
    source=folder.parent/'diagnostics.json'
    shutil.copy2(source,candidate/'diagnostics.json')
    initial=workspace_files(candidate)
    budget.reserve('repair')
    login=ChatGPTSubscriptionPlanner(model,folder/'login-check')
    answer=folder/'repair-answer.json'
    instructions=('The user explicitly authorizes autonomous fixes after a real SC2 match. '
        'Read diagnosis.json and diagnostics.json. Fix ONLY the demonstrated execution defect. '
        'Allowed source paths: '+', '.join(sorted(REPAIRABLE))+'. '
        'Add 1-3 new test_autonomy_regression_NAME.py unittest files. Do not edit existing tests. '
        'A new regression MUST fail on the original source and pass after the fix; test actual '
        'behaviour, not a source hash or marker. Do not alter budgets, results, model, dependencies, '
        'authentication, game rules or use debug/cheats. No network requests. '
        'If evidence shows a planning mistake or a nonreproducible issue, do not patch. '
        'Return JSON {"changed":true|false,"regression_modules":[...],"summary":"简体中文"}. '
        'Python for local checks: '+str(root/'.venv311/bin/python')+'.')
    command=[login.binary,'exec','--ignore-user-config','--ephemeral','--skip-git-repo-check',
             '-C',str(candidate),'-s','workspace-write','-c','model_provider="openai"',
             '-c','forced_login_method="chatgpt"','-c','sqlite_home='+json.dumps(str(folder/'state')),
             '-c','log_dir='+json.dumps(str(folder/'state')),'-m',login.model,
             '--json','-o',str(answer),'-']
    try:
        process=subprocess.Popen(command,env=login.env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,text=True,start_new_session=True)
        try:
            stdout,stderr=process.communicate(instructions,timeout=180)
        except BaseException:
            ChatGPTSubscriptionPlanner._stop(process)
            stdout,stderr=process.communicate()
            raise
        finally:
            for pipe in (process.stdin,process.stdout,process.stderr):pipe.close()
            if 'stdout' in locals():(folder/'events.jsonl').write_text(stdout)
            if 'stderr' in locals():(folder/'stderr.log').write_text(stderr)
        if process.returncode:
            raise RuntimeError('Autonomous repair CLI failed; original source unchanged')
        result=json.loads(answer.read_text())
        if result.get('changed') is not True:
            return dict(applied=False,reason=result.get('summary','No reproducible repair'))
        after=workspace_files(candidate)
        changed={name for name in set(initial)|set(after) if initial.get(name)!=after.get(name)}
        source_changes=changed&REPAIRABLE
        new_tests={name for name in changed if re.fullmatch(r'test_autonomy_regression_[a-z0-9_]+\.py',name)
                   and name not in initial}
        if not source_changes or len(source_changes)>3 or not 1<=len(new_tests)<=3:
            raise ValueError('Repair requires a small source change and new regressions')
        if changed-source_changes-new_tests or any(name not in after for name in changed):
            raise ValueError('Repair modified a protected file or deleted source')
        modules=[name[:-3] for name in sorted(new_tests)]
        if sorted(result.get('regression_modules',[]))!=modules:
            raise ValueError('Repair regression inventory mismatch')
        for name in new_tests:
            (baseline/name).write_bytes(after[name])
        python=root/'.venv311/bin/python'
        before=checked_tests(python,baseline,modules,folder/'regression-before.log')
        fixed=checked_tests(python,candidate,modules,folder/'regression-after.log')
        previous_regressions=sorted(path.stem for path in root.glob('test_autonomy_regression_*.py'))
        checks=checked_tests(python,candidate,CHECKS+previous_regressions,folder/'existing-checks.log')
        if (before['exit_code']==0 or before['tests']==0 or
                not any(word in before['output'] for word in ('AssertionError','AttributeError','ValueError')) or
                fixed['exit_code']!=0 or fixed['tests']==0 or checks['exit_code']!=0 or checks['tests']==0):
            raise ValueError('Repair failed before/after reproduction or existing checks; not applied')
        # Apply only after checking the live checkout still matches our captured baseline.
        for name in source_changes:
            if (root/name).is_symlink() or (root/name).read_bytes()!=initial[name]:
                raise ValueError('Source changed during repair; refusing overwrite')
        for name in new_tests:
            if (root/name).exists():raise ValueError('New regression would overwrite a user file')
        for name in sorted(changed):
            (root/name).write_bytes(after[name])
        return dict(applied=True,changed=sorted(changed),regressions=fixed['tests'],
                    existing_checks=checks['tests'],summary=result.get('summary'),
                    source_sha256={name:hashlib.sha256(after[name]).hexdigest() for name in source_changes})
    finally:
        login.close()
