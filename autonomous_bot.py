"""Real ChatGPT-vs-bot matches, bounded reviews, verified repairs and curriculum."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import time
import subprocess
import traceback
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from absl import flags

from arena.autonomous_bot import (BotPlanner, BudgetExhausted, Curriculum, DefeatGuard,RequestCapIdleGuard,
                                  INITIAL_STRATEGY, RequestBudget, match_diagnostics,
                                  production_decision_needed)
from arena.autonomous_review import repair_game, review_game
from arena.chatgpt_planner import ChatGPTSubscriptionPlanner
from arena.go_planner import GoPlanner, GO_MODEL
from arena.commander import Commander
from arena.match_control import surrender_blockers
from arena.raw_engine import RawArena
from arena.tech_tree import TOWNHALLS


def write_json(path, data):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2))
    temporary.replace(path)


def extend_campaign(status, budget, args, number):
    """Grant one further match under explicit until-goal authorization, keeping usage."""
    if number <= args.max_matches:
        return
    if not args.until_hard_win:
        raise ValueError('Further matches require explicit until-hard-win authorization')
    args.max_matches = number
    budget.limit += args.max_requests + 2
    status['limits']['max_matches'] = number
    status.setdefault('budget_extensions', []).append(dict(
        before_match=number, added_requests=args.max_requests+2,
        previously_attempted=budget.used, authorization='until_hard_win'))


def run_match(root, folder, model, difficulty, strategy, budget, args, number):
    folder.mkdir()
    report=dict(number=number,difficulty=difficulty,model=model,outcome='Unknown',
                result_source='unverified',termination=None,timeline=[],game_started=False)
    arena=commander=client=None
    guard=DefeatGuard()
    started=time.monotonic();loss_noticed=None;last_sample=-30
    try:
        base=(GoPlanner if model==GO_MODEL else ChatGPTSubscriptionPlanner)(model,folder/'planner')
        base.structured_battle=True
        base.reasoning_effort='low' if model==GO_MODEL else 'medium'
        client=BotPlanner(base,budget,strategy,args.max_requests,
                          adaptive_cadence=getattr(args,'decision_cadence','fixed')=='adaptive')
        arena=RawArena(render=False,difficulty=difficulty,races=['terran'],
                       bot_race='terran',seed=args.seed+number-1)
        report['game_started']=True
        report['engine_version']=arena.controllers[0].ping().game_version
        arena.surrender_blockers_for=lambda world,index: [] if guard.evidence else surrender_blockers(world)
        commander=Commander(arena,0,client,opening=False,interval=25,
                            feedback_dir=folder/'feedback',combat_micro=True)
        world=arena.initial[0]
        started=time.monotonic()
        idle_guard=RequestCapIdleGuard()
        while not arena.ended:
            evidence=guard.observe(world)
            client.policy=dict(game_s=world.seconds,max_game_s=args.max_game_seconds,max_wall_s=args.max_wall_seconds,
                               clear_disadvantage=evidence,
                               production_decision_due=production_decision_needed(world),
                               decision_urgent=any(enemy.ground_dps>0 and not enemy.hallucination
                                   and any(enemy.distance(own)<(35 if own.type_id in TOWNHALLS else 14)
                                           for own in world.my_units
                                           if own.type_id in TOWNHALLS or (not own.is_building and own.ground_dps>0))
                                   for enemy in world.enemy_units))
            if evidence and loss_noticed is None:
                loss_noticed=world.seconds
                commander.last_request=float('-inf')
                commander.record_feedback('obvious_disadvantage',evidence=evidence,
                    handling='Model must concede now; operator ends match if it ignores the sustained deficit.')
            if not evidence:
                loss_noticed=None
            reason=None
            if commander.client_fatal or getattr(client,'fatal_error',None):
                report['termination']=getattr(client,'fatal_kind',None) or 'model_unavailable'
                report['error']=getattr(client,'fatal_error',None) or 'HTTP '+str(commander.client_fatal)
                break
            if evidence and world.seconds-loss_noticed>=25:
                reason='持续明显劣势，模型未及时投降，按用户止损要求结束对局：'+json.dumps(evidence,ensure_ascii=False)
                report['termination']='obvious_disadvantage_guard'
            elif world.seconds>=args.max_game_seconds or time.monotonic()-started>=args.max_wall_seconds:
                reason='达到本局时间硬上限，停止额度消耗；这不是确认胜利或明显败势判断。'
                report['termination']='time_budget'
            # BotPlanner suppresses further calls at its cap. Keep already issued
            # actions alive within the original game/wall deadline instead of
            # conceding immediately after the last answer.
            if (not reason and client.attempts>=args.max_requests and not commander.thinking
                    and 'request_cap_reached_game_s' not in report):
                report['request_cap_reached_game_s']=round(world.seconds,2)
                commander.record_feedback('model_request_budget_reached',
                    handling='No more model calls; existing commands keep only their original time budget.')
            if reason:
                commander.record_feedback('operator_concession',reason=reason,origin=report['termination'])
                arena.request_surrender(0,reason[:500])
                world=arena.step([[]])[0]
                commander.ingest_feedback(world)
                break
            idle=idle_guard.observe(world,commander.tasks,client.attempts>=args.max_requests,commander.thinking)
            if idle:
                report['termination']='request_budget_idle'
                report['budget_stop_detail']=idle
                commander.record_feedback('request_budget_idle',evidence=idle,handling='Unverified budget stop; not victory or confirmed defeat.')
                break
            actions=commander.tick(world)
            world=arena.step([actions])[0]
            commander.ingest_feedback(world)
            if world.seconds-last_sample>=25:
                report['timeline'].append(dict(world=world.summary(),
                    active_tasks=commander.tasks.summary(),defense=commander.control_policy.copy(),
                    last_decision=commander.last_decision))
                last_sample=world.seconds
                print('BOT_PROGRESS',json.dumps(dict(match=number,difficulty=difficulty,
                    game_s=round(world.seconds,1),calls=client.calls,own=world.summary()['own']),
                    ensure_ascii=False),flush=True)
            time.sleep(.01)
        result=arena.result()
        if arena.ended:
            report['outcome']=result.get(1,'Unknown')
            report['engine_results']=result
            report['result_source']=(arena.surrender_report or {}).get('result_source','engine_player_result')
            report['termination']=report['termination'] or ('model_surrender' if arena.surrender_report else 'engine_end')
        report['final_world']=world.summary()
        report['surrender']=arena.surrender_report
        try:
            arena.save_replay(folder/'match.SC2Replay')
            report['replay']=str(folder/'match.SC2Replay')
        except Exception as error:
            report['replay_error']=str(error)
        report['engine_action_errors']=arena.action_errors
    except BaseException as error:
        report['error']=type(error).__name__+': '+str(error)
        report['traceback']=traceback.format_exc()
        report['termination']=report['termination'] or 'environment_error'
        if arena:
            try:
                arena.save_replay(folder/'match.SC2Replay')
                report['replay']=str(folder/'match.SC2Replay')
            except Exception as replay_error:report['replay_error']=str(replay_error)
        if commander:
            try:report.setdefault('final_world',arena.snapshots()[0].summary())
            except Exception:pass
        if isinstance(error,KeyboardInterrupt):report['termination']='interrupted'
    finally:
        cleanup_errors=[]
        for resource in (client,commander,arena):
            if resource:
                try:
                    (resource.stop if resource is commander else resource.close)()
                except Exception as error:cleanup_errors.append(type(error).__name__+': '+str(error))
        report['model_calls']=client.calls if client else 0
        report['model_attempts']=client.attempts if client else 0
        report['usage']=client.usage if client else {}
        report['input_chars']=client.input_chars if client else 0
        report['wall_s']=round(time.monotonic()-started,2)
        report['cleanup_complete']=not cleanup_errors
        if cleanup_errors:report['cleanup_errors']=cleanup_errors
        report['journal']=commander.feedback.path if commander else None
        write_json(folder/'match.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',default='chatgpt/gpt-6-luna')
    parser.add_argument('--switch-model',choices=['chatgpt/gpt-6-luna',GO_MODEL],
                        help='Explicitly change the provider on a resumed campaign, keeping its ledger')
    parser.add_argument('--max-matches',type=int,default=8)
    parser.add_argument('--until-hard-win',action='store_true',
                        help='Explicitly authorize further bounded matches until verified hard victory')
    parser.add_argument('--max-requests',type=int,default=24)
    parser.add_argument('--max-game-seconds',type=float,default=1800)
    parser.add_argument('--max-wall-seconds',type=float,default=1860)
    parser.add_argument('--decision-cadence',choices=['adaptive','fixed'],default='adaptive')
    parser.add_argument('--start-difficulty',choices=['easy','hard'],default='easy')
    parser.add_argument('--previous-campaign',help='Reference prior experiment without resetting its ledger')
    parser.add_argument('--seed',type=int,default=7)
    parser.add_argument('--worker-config',help=argparse.SUPPRESS)
    parser.add_argument('--resume',type=Path,help='Resume a saved campaign without resetting its budgets')
    args=parser.parse_args()
    if not 1<=args.max_matches<=8 or not 1<=args.max_requests<=24:
        parser.error('Hard ceilings: 8 matches and 24 battle requests per match')
    if not 1<=args.max_game_seconds<=1800 or not 1<=args.max_wall_seconds<=1860:
        parser.error('Hard ceilings: 1800 game seconds and 1860 wall seconds per match')
    if args.model not in ('chatgpt/gpt-6-luna',GO_MODEL):
        parser.error('Unsupported explicitly selected campaign model')
    if args.switch_model and not args.resume:
        parser.error('--switch-model requires --resume')
    root=Path(__file__).resolve().parent
    os.chdir(root)
    if not flags.FLAGS.is_parsed():flags.FLAGS(['autonomous_bot'])
    os.environ.setdefault('ARENA_CHATGPT_HOME',str(root/'session-runtime/chatgpt-subscription'))
    if args.worker_config:
        config=json.loads(Path(args.worker_config).read_text())
        if config['args']['model'] not in ('chatgpt/gpt-6-luna',GO_MODEL):
            parser.error('Unsupported saved worker model')
        budget=RequestBudget(config['budget']['limit'])
        budget.used=config['budget']['attempted'];budget.by_role=config['budget']['by_role']
        folder=Path(config['folder'])
        run_match(root,folder,config['args']['model'],config['difficulty'],config['strategy'],budget,
                  SimpleNamespace(**config['args']),config['number'])
        write_json(folder/'budget.json',budget.report())
        return 0
    output=root/'logs/autonomous-bot';output.mkdir(parents=True,exist_ok=True)
    now=lambda:datetime.now(ZoneInfo('Asia/Shanghai'))
    if args.resume:
        folder=args.resume.resolve()
        if folder.is_file():folder=folder.parent
        if not folder.is_relative_to(output.resolve()):parser.error('Resume must refer to a saved bot campaign')
        status=json.loads((folder/'campaign.json').read_text())
        saved=status['limits']
        if (status['model'] not in ('chatgpt/gpt-6-luna',GO_MODEL) or not 1<=saved['max_matches']
                or (saved['max_matches']>8 and not saved.get('until_hard_win'))
                or not 1<=saved['max_requests']<=24 or not 1<=saved['max_game_seconds']<=1800
                or not 1<=saved['max_wall_seconds']<=1860):
            parser.error('Saved campaign violates model or budget ceilings')
        for key in ('max_matches','max_requests','max_game_seconds','max_wall_seconds','seed'):
            setattr(args,key,saved[key])
        args.decision_cadence=saved.get('decision_cadence','fixed')
        budget=RequestBudget(args.max_matches*(args.max_requests+2))
        usage=status['budget']
        if usage['limit']!=budget.limit or not 0<=usage['attempted']<=budget.limit:
            parser.error('Invalid saved request budget')
        budget.used=usage['attempted'];budget.by_role=usage['by_role']
        matches=status['matches']
        if args.switch_model and args.switch_model!=status['model']:
            status.setdefault('model_changes',[]).append(dict(before_match=len(matches)+1,
                previous_model=status['model'],model=args.switch_model,previous_attempted=budget.used,
                authorization='explicit --switch-model'))
            status['model']=args.switch_model
            saved['model']=args.switch_model
        args.model=status['model']
        if len(matches)>args.max_matches:parser.error('Saved match count exceeds ceiling')
        curriculum=Curriculum(status['next_difficulty'],status.get('goal_reached',False))
        strategy=INITIAL_STRATEGY
        if (folder/'strategy.json').exists():strategy=json.loads((folder/'strategy.json').read_text())['strategy']
        if curriculum.reached_goal:
            print('CAMPAIGN_REPORT',folder/'campaign.json',flush=True);return 0
    else:
        folder=Path(tempfile.mkdtemp(prefix=now().strftime('%Y%m%d-%H%M%S-'),dir=output))
        budget=RequestBudget(args.max_matches*(args.max_requests+2))
        curriculum=Curriculum(args.start_difficulty);strategy=INITIAL_STRATEGY;matches=[]
        status=dict(model=args.model,limits=vars(args),started_at=now().isoformat(),
                    goal='verified engine victory against hard Terran bot',matches=matches)
        if args.previous_campaign:
            previous=Path(args.previous_campaign).resolve()
            if previous.is_dir():previous=previous/'campaign.json'
            if not previous.is_relative_to(output.resolve()):parser.error('Previous campaign must be a saved bot campaign')
            history=json.loads(previous.read_text())
            status['previous_campaign']=dict(path=str(previous),budget=history['budget'],
                                            matches=len(history['matches']),goal_reached=history.get('goal_reached'))
    stop='match_budget'
    if args.until_hard_win:
        status['limits']['until_hard_win']=True
    else:
        status['limits'].setdefault('until_hard_win',False)
    status.pop('finished_at',None);status.pop('stop_reason',None)
    status['state']='running'
    write_json(folder/'campaign.json',status)
    print('CAMPAIGN',folder,flush=True)
    number=len(matches)
    while not curriculum.reached_goal:
        number+=1
        if number>args.max_matches and not args.until_hard_win:
            break
        extend_campaign(status,budget,args,number)
        status.update(budget=budget.report(),next_difficulty=curriculum.difficulty)
        write_json(folder/'campaign.json',status)
        match_folder=folder/('match-%02d-%s'%(number,curriculum.difficulty))
        # Each real match imports the latest verified repair in a fresh interpreter.
        config=folder/('worker-%02d.json'%number)
        write_json(config,dict(folder=str(match_folder),difficulty=curriculum.difficulty,
                   strategy=strategy,budget=budget.report(),args={k:v for k,v in vars(args).items()
                       if k!='resume'},number=number))
        process=subprocess.Popen([str(root/'.venv311/bin/python'),str(root/'autonomous_bot.py'),
                                  '--worker-config',str(config)],start_new_session=True)
        try:
            process.wait(timeout=args.max_wall_seconds+120)
        except BaseException:
            ChatGPTSubscriptionPlanner._stop(process)
            raise
        if process.returncode or not (match_folder/'match.json').exists():
            stop='match_worker_failed';break
        report=json.loads((match_folder/'match.json').read_text())
        usage=json.loads((match_folder/'budget.json').read_text())
        budget.used=usage['attempted'];budget.by_role=usage['by_role']
        entry=dict(number=number,difficulty=curriculum.difficulty,report=str(match_folder/'match.json'),
                   model=report['model'],
                   outcome=report['outcome'],termination=report['termination'],
                   model_calls=report['model_calls'],result_source=report['result_source'])
        matches.append(entry)
        status.update(budget=budget.report(),next_difficulty=curriculum.difficulty)
        write_json(folder/'campaign.json',status)
        print('BOT_RESULT',json.dumps(entry,ensure_ascii=False),flush=True)
        if not report.get('journal') or report.get('error'):
            stop=report.get('termination','environment_error');break
        diagnostics=match_diagnostics(report,Path(report['journal']))
        write_json(match_folder/'diagnostics.json',diagnostics)
        try:
            review=review_game(args.model,match_folder/'review',budget,diagnostics,strategy,curriculum.difficulty)
            write_json(match_folder/'review.json',review)
            entry['review']=str(match_folder/'review.json')
            entry['review_category']=review['category']
            if review['category']=='environment':
                stop='reviewed_environment_blocker';break
            if review['category']=='feature_gap':
                repair=repair_game(root,args.model,match_folder/'repair',budget,review)
                write_json(match_folder/'repair.json',repair)
                entry['repair']=repair
                if not repair['applied']:
                    stop='feature_gap_not_reproduced_or_fixed';break
            strategy=review['strategy']
            write_json(folder/'strategy.json',dict(after_match=number,strategy=strategy,
                       category=review['category'],source_review=entry['review']))
            curriculum.record(report['outcome'],report['result_source'])
            print('BOT_REVIEW',json.dumps(dict(match=number,category=review['category'],
                reason=review.get('reason'),next_difficulty=curriculum.difficulty),ensure_ascii=False),flush=True)
            if curriculum.reached_goal:
                stop='goal_reached';break
        except Exception as error:
            entry['review_error']=type(error).__name__+': '+str(error)
            stop='review_or_repair_failed';break
        status.update(budget=budget.report(),next_difficulty=curriculum.difficulty)
        write_json(folder/'campaign.json',status)
    status.update(stop_reason=stop,goal_reached=curriculum.reached_goal,
                  state='finished',
                  next_difficulty=curriculum.difficulty,budget=budget.report(),
                  finished_at=now().isoformat())
    write_json(folder/'campaign.json',status)
    print('CAMPAIGN_RESULT',json.dumps(status,ensure_ascii=False),flush=True)
    print('CAMPAIGN_REPORT',folder/'campaign.json',flush=True)
    return 0 if curriculum.reached_goal else 2


if __name__=='__main__':
    raise SystemExit(main())
