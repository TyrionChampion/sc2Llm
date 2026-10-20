"""One resumable non-realtime Go duel per invocation; isolated historical ledger.

Explicit Linux API engine, normal fog, no debug, no opening or model fallback.
Both players pause together while either model thinks. Resume never replays an
incomplete match or resets request attempts.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import traceback

from absl import flags

from arena.autonomous_bot import BotPlanner, INITIAL_STRATEGY, DefeatGuard, production_decision_needed, match_diagnostics
from arena.commander import Commander
from arena.go_planner import GoPlanner, GO_MODEL
from arena.lifecycle import write_json_atomic
from arena.linux_step import LinuxStepArena
from arena.step_scheduler import PlannerBatcher, AdaptiveSteps
from arena.match_control import surrender_blockers
from arena.tech_tree import TOWNHALLS
from go_duel_series import DurableBudget, PlayerBudget, source_snapshot
from native_llm import planners_thinking

ROOT=Path(__file__).resolve().parent
SCHEDULE=[['terran','protoss'],['protoss','zerg'],['zerg','terran'],['terran','zerg']]


def play(folder, number, state, budget, planner_factory=None):
    models=state.get('models',[GO_MODEL]*2)
    if not isinstance(models,(list,tuple)) or len(models)!=2 or any(not isinstance(m,str) or not m.strip() for m in models):
        raise ValueError('two explicit model identities required')
    folder.mkdir();races=state['schedule'][number-1];limits=state['limits']
    factory=planner_factory or GoPlanner
    sources=source_snapshot(folder)
    own_source=Path(__file__).read_bytes();(folder/'source'/Path(__file__).name).write_bytes(own_source)
    sources[Path(__file__).name]=hashlib.sha256(own_source).hexdigest()
    report=dict(number=number,status='starting',models=list(models),races=races,realtime=False,
        engine_kind='Linux SC2 API',debug_used=False,scripted_opening=False,seed=state['seed']+number-1,
        limits=limits,code_sha256=sources,outcomes={},result_source='unverified',termination=None,
        samples=[],players=[],cleanup_complete=False)
    write_json_atomic(folder/'match.json',report)
    arena=None;clients=[];commanders=[];worlds=[];started=time.monotonic();last_write=-5
    guards=[DefeatGuard(),DefeatGuard()]
    try:
        for i in range(2):
            client=factory(models[i],folder/('p%d'%(i+1))/'planner')
            planner=BotPlanner(client,PlayerBudget(budget,i),INITIAL_STRATEGY,limits['requests'],adaptive_cadence=True)
            # Step matches pause both clients. Give long reasoning a documented
            # bounded window without changing classic realtime profiles.
            planner.timeout=client.timeout=limits['model_timeout_s']
            clients.append(planner)
        arena=LinuxStepArena(races=races,seed=report['seed'])
        report.update(status='running',engine_version=arena.version,map_targets=arena.start_location_report)
        arena.surrender_blockers_for=lambda w,i:[] if guards[i].evidence else surrender_blockers(w)
        commanders=[Commander(arena,i,c,opening=False,interval=25,feedback_dir=folder/('p%d'%(i+1))/'feedback',
                              combat_micro=True) for i,c in enumerate(clients)]
        batcher=PlannerBatcher(commanders,2);adaptive=AdaptiveSteps(8,64)
        worlds=arena.initial;started=time.monotonic();last_sample=-25
        while not arena.ended:
            elapsed=time.monotonic()-started
            if elapsed>=limits['wall_s'] or min(w.seconds for w in worlds)>=limits['game_s']:
                report['termination']='time_budget';break
            for i,(c,w) in enumerate(zip(clients,worlds)):
                c.policy=dict(game_s=w.seconds,max_game_s=limits['game_s'],max_wall_s=limits['wall_s'],
                    elapsed_wall_s=elapsed,remaining_wall_s=max(0,limits['wall_s']-elapsed),
                    estimated_game_deadline_s=limits['game_s'],game_speed='step',realtime=False,
                    match_number=number,player=i+1,own_race=races[i],opponent_race=races[1-i],opponent_kind='LLM',
                    clear_disadvantage=guards[i].observe(w),production_decision_due=production_decision_needed(w),
                    decision_urgent=any(e.ground_dps>0 and any(e.distance(u)<(35 if u.type_id in TOWNHALLS else 14)
                        for u in w.my_units if u.type_id in TOWNHALLS or (not u.is_building and u.ground_dps>0)) for e in w.enemy_units))
            if any(c.fatal_error or cmd.client_fatal for c,cmd in zip(clients,commanders)):
                report['termination']='model_unavailable';break
            batches=[c.tick(w) for c,w in zip(commanders,worlds)]
            batcher.dispatch(worlds)
            advance=not planners_thinking(commanders)
            step=adaptive.choose(worlds,batcher.next_deadline(worlds)) if advance else 8
            worlds=arena.step(batches,advance=advance,step_loops=step)
            for c,w in zip(commanders,worlds):c.ingest_feedback(w)
            if time.monotonic()-last_write>=5:
                last_write=time.monotonic()
                report.update(timing=dict(arena.timing(),planner_batches=batcher.public(),adaptive_steps=adaptive.public()),
                    budget=budget.report(),players=[dict(player=i+1,race=races[i],world=w.summary(),
                        commander=c.stats(),model_attempts=clients[i].attempts,model_calls=clients[i].calls,
                        fatal_kind=clients[i].fatal_kind) for i,(c,w) in enumerate(zip(commanders,worlds))])
                write_json_atomic(folder/'match.json',report)
            if min(w.seconds for w in worlds)-last_sample>=30:
                last_sample=min(w.seconds for w in worlds)
                sample=dict(game_s=last_sample,wall_s=time.monotonic()-started,
                    players=[dict(world=w.summary(),commander=c.stats(),attempts=clients[i].attempts)
                             for i,(c,w) in enumerate(zip(commanders,worlds))])
                report['samples'].append(sample)
                print('STEP_DUEL_PROGRESS',json.dumps(dict(match=number,game_s=round(last_sample,1),
                    wall_s=round(sample['wall_s'],1),attempts=[c.attempts for c in clients],
                    accepted=[c.decisions for c in commanders],errors=[c.errors for c in commanders],
                    army=[w.summary()['own'] for w in worlds]),ensure_ascii=False),flush=True)
        if arena.ended:
            report.update(outcomes=arena.result(),termination='model_surrender' if arena.surrender_report else 'engine_end',
                result_source=(arena.surrender_report or {}).get('result_source','engine_player_result'))
        report.update(timing=dict(arena.timing(),planner_batches=batcher.public(),adaptive_steps=adaptive.public()),
                      engine_action_errors=arena.action_errors,surrender=arena.surrender_report)
    except BaseException as error:
        report.update(error=type(error).__name__+': '+str(error),traceback=traceback.format_exc(),
                      termination=report['termination'] or 'environment_error')
    finally:
        cleanup=[]
        for c in clients:
            try:c.close()
            except Exception as error:cleanup.append(type(error).__name__)
        for c in commanders:
            try:c.stop()
            except Exception as error:cleanup.append(type(error).__name__)
        report['players']=[]
        for i,(c,w) in enumerate(zip(commanders,worlds)):
            own=dict(player=i+1,race=races[i],model=models[i],outcome=report['outcomes'].get(i+1,'Unknown'),
                final_world=w.summary(),commander=c.stats(),model_calls=clients[i].calls,model_attempts=clients[i].attempts,
                fatal_error=clients[i].fatal_error,fatal_kind=clients[i].fatal_kind,journal=str(c.feedback.path),
                usage=clients[i].usage,realtime=False,termination=report['termination'],timing=report.get('timing'),
                result_source=report['result_source'],timeline=[s['players'][i] for s in report['samples']])
            report['players'].append(own)
            write_json_atomic(folder/('p%d'%(i+1))/'match.json',own)
            try:write_json_atomic(folder/('p%d'%(i+1))/'diagnostics.json',match_diagnostics(own,c.feedback.path))
            except Exception as error:report.setdefault('diagnostic_errors',[]).append(type(error).__name__+': '+str(error))
        if arena:
            try:
                replay=folder/'match.SC2Replay';arena.save_replay(replay)
                report.update(replay=str(replay),replay_sha256=hashlib.sha256(replay.read_bytes()).hexdigest())
            except Exception as error:cleanup.append('replay: '+type(error).__name__)
            try:arena.close()
            except Exception as error:cleanup.append('engine: '+type(error).__name__)
        report.update(status='finished',wall_s=time.monotonic()-started,budget=budget.report(),cleanup_errors=cleanup,
            cleanup_complete=not cleanup,source_unchanged=all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==digest
                                                           for p,digest in sources.items()))
        write_json_atomic(folder/'match.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume',type=Path)
    parser.add_argument('--requests',type=int,default=64)
    parser.add_argument('--wall-seconds',type=float,default=2400)
    parser.add_argument('--game-seconds',type=float,default=1800)
    parser.add_argument('--seed',type=int,default=20261018)
    parser.add_argument('--model-timeout-seconds',type=int,default=120)
    args=parser.parse_args()
    if not 1<=args.requests<=96 or not 1<=args.wall_seconds<=3600 or not 1<=args.game_seconds<=1800 or not 35<=args.model_timeout_seconds<=180:
        parser.error('bounded non-realtime profile: requests<=96/player, wall<=3600, game<=1800; model timeout 35..180 seconds')
    if not flags.FLAGS.is_parsed():flags.FLAGS(['go_step_series'])
    root=ROOT/'logs/go-step-duels';root.mkdir(exist_ok=True)
    if args.resume:
        folder=args.resume.resolve()
        if not folder.is_relative_to(root):raise ValueError('resume must use an existing step series')
        state=json.loads((folder/'series.json').read_text())
        if state['state']=='running':raise ValueError('incomplete game cannot be silently replayed')
    else:
        folder=Path(tempfile.mkdtemp(prefix='four-',dir=root)).resolve()
        prior=[*ROOT.glob('logs/autonomous-bot/*/campaign.json'),*ROOT.glob('logs/go-duels/*/budget.json'),
               *root.glob('*/budget.json')]
        state=dict(state='ready',schedule=SCHEDULE,matches=[],models=[GO_MODEL]*2,seed=args.seed,
            limits=dict(requests=args.requests,wall_s=args.wall_seconds,game_s=args.game_seconds),
            historical_campaign_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in prior})
    if len(state['matches'])>=4:print('STEP_SERIES_COMPLETE',folder,flush=True);return 0
    state['limits'].setdefault('model_timeout_s',args.model_timeout_seconds)
    budget=DurableBudget(folder/'budget.json',8*state['limits']['requests'])
    number=len(state['matches'])+1
    state.update(state='running',pid=os.getpid(),active_match=number,budget=budget.report())
    write_json_atomic(folder/'series.json',state);write_json_atomic(root/'current.json',dict(series=str(folder)))
    print('STEP_SERIES',folder,'MATCH',number,flush=True)
    report=play(folder/('match-%02d'%number),number,state,budget)
    state['matches'].append(dict(number=number,report=str(folder/('match-%02d'%number)/'match.json'),
        outcomes=report['outcomes'],termination=report['termination']))
    state.update(state='finished' if number==4 else 'paused_after_match',budget=budget.report())
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in state['historical_campaign_hashes'].items())
    write_json_atomic(folder/'series.json',state)
    print('STEP_MATCH_COMPLETE',json.dumps(state['matches'][-1]),flush=True)
    return 0 if report['termination']!='environment_error' else 1


if __name__=='__main__':raise SystemExit(main())
