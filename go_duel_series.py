"""Three bounded Go-vs-Go Linux realtime games. One game per invocation, resumable ledger.

Start: ./run_sc2.sh .venv/bin/python go_duel_series.py
Next:  ./run_sc2.sh .venv/bin/python go_duel_series.py --resume logs/go-duels/<series>
No debug fixtures, scripted opening, hidden enemy input, native GPU or provider fallback.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import time
import traceback
from absl import flags

from arena.autonomous_bot import (BotPlanner,RequestBudget,INITIAL_STRATEGY,DefeatGuard,
    match_time_policy,match_diagnostics,production_decision_needed)
from arena.autonomous_review import review_game
from arena.commander import Commander
from arena.go_planner import GoPlanner,GO_MODEL
from arena.match_control import surrender_blockers
from arena.raw_engine import RawArena
from arena.tech_tree import TOWNHALLS

ROOT=Path(__file__).resolve().parent
RACES=('terran','protoss')
RUNTIME_UPDATES=(
    'Warp-in candidate selection now excludes occupied unit footprints and pending warp positions; '
    'completion still requires observed units.',
    'Player-observed resource site names persist across neutral tag changes and mineral depletion; '
    'remembered positions are geography, not current enemy observations.',
    'Macro resource waiting feedback reports only actual shortages and available unreserved resources.',
    'Current battle observations can use reversible object/key sharing within the unchanged 45000-character limit.',
    'Cancelling a known owned already-ended scout/order/defense mission is now an observable no-op; '
    'unknown IDs remain rejected, other valid orders in the reply can proceed.',
    'Macro failure feedback now retains pending order positions and currently observed new own products '
    'for diagnosis, without inferring unobserved completion.')


def write(path,data):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)


class DurableBudget(RequestBudget):
    def __init__(self,path,limit):
        super().__init__(limit);self.path=path;self.persist_lock=threading.Lock()
        if path.exists():
            d=json.loads(path.read_text())
            if d['limit']!=limit or sum(d['by_role'].values())!=d['attempted']:
                raise ValueError('Invalid existing duel budget; no reset')
            self.used=d['attempted'];self.by_role=d['by_role']
        else:write(path,self.report())

    def reserve(self,role):
        with self.persist_lock:
            super().reserve(role);write(self.path,self.report())


class PlayerBudget:
    def __init__(self,budget,index):self.base=budget;self.index=index
    @property
    def used(self):return self.base.used
    @property
    def limit(self):return self.base.limit
    def report(self):return self.base.report()
    def reserve(self,role):self.base.reserve(role+'_P'+str(self.index+1))


def player_snapshot(index,world,commander):
    return dict(world=world.summary(),active_tasks=commander.tasks.summary(),
        last_decision=commander.last_decision,defense=commander.control_policy.copy())


def play(folder,number,strategies,budget,limits,output_window=False):
    folder.mkdir()
    report=dict(number=number,status='starting',opponent_kind='LLM',races=list(RACES),models=[GO_MODEL]*2,
        seed=limits['seed']+number-1,game_speed='slower',realtime=True,debug_used=False,scripted_opening=False,
        time_limits=dict(game_s=limits['game_s'],wall_s=limits['wall_s']),outcomes={},
        result_source='unverified',termination=None,players=[],cleanup_complete=False,outcome='starting',
        identities=[dict(side='P'+str(i+1),model=GO_MODEL,race=RACES[i]) for i in range(2)],samples=[],
        code_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [Path(__file__),*sorted((ROOT/'arena').glob('*.py'))]})
    commanders=[];clients=[];arena=None;worlds=[];started=time.monotonic()
    guards=[DefeatGuard(),DefeatGuard()]
    sides=[dict(player=i+1,race=race,model=GO_MODEL,outcome='Unknown',result_source='unverified',
        termination=None,timeline=[],strategy=strategies[i]) for i,race in enumerate(RACES)]
    try:
        for i in range(2):
            own=folder/('p'+str(i+1));own.mkdir()
            base=GoPlanner(GO_MODEL,own/'planner')
            clients.append(BotPlanner(base,PlayerBudget(budget,i),strategies[i],limits['requests'],adaptive_cadence=True))
        arena=RawArena(render=False,races=list(RACES),seed=report['seed'],game_speed='slower')
        report.update(status='running',game_started=True,engine_version=arena.controllers[0].ping().game_version,
                      map_targets=arena.start_location_report)
        arena.surrender_blockers_for=lambda w,i: [] if guards[i].evidence else surrender_blockers(w)
        commanders=[Commander(arena,i,c,opening=False,interval=25,feedback_dir=folder/('p'+str(i+1))/'feedback',
                              combat_micro=True) for i,c in enumerate(clients)]
        worlds=arena.initial;started=time.monotonic();last_sample=-30;last_ui=float('-inf')
        if output_window:
            with (folder/'output-window.log').open('w') as stream:
                viewer=subprocess.Popen([sys.executable,str(ROOT/'llm_output_window.py'),'--run',str(folder)],
                    stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
            report['output_window']=dict(pid=viewer.pid,log=str(folder/'output-window.log'))
        while not arena.ended:
            elapsed=time.monotonic()-started
            if elapsed>=limits['wall_s'] or worlds[0].seconds>=limits['game_s']:
                report['termination']='time_budget';break
            for i,(c,w) in enumerate(zip(clients,worlds)):
                loss=guards[i].observe(w)
                c.policy=dict(**match_time_policy(w.seconds,elapsed,limits['game_s'],limits['wall_s'],'slower'),
                    match_number=number,player=i+1,opponent_kind='LLM',own_race=RACES[i],opponent_race=RACES[1-i],
                    clear_disadvantage=loss,production_decision_due=production_decision_needed(w),
                    decision_urgent=any(e.ground_dps>0 and not e.hallucination and
                        any(e.distance(u)<(35 if u.type_id in TOWNHALLS else 14) for u in w.my_units
                            if u.type_id in TOWNHALLS or (not u.is_building and u.ground_dps>0)) for e in w.enemy_units))
            if any(c.fatal_error or cmd.client_fatal for c,cmd in zip(clients,commanders)):
                report['termination']=('context_budget' if any(c.fatal_kind=='context_budget' for c in clients)
                                       else 'model_unavailable');break
            batches=[c.tick(w) for c,w in zip(commanders,worlds)]
            worlds=arena.step(batches)
            for c,w in zip(commanders,worlds):c.ingest_feedback(w)
            if worlds[0].seconds-last_sample>=25:
                last_sample=worlds[0].seconds
                for i,(w,c) in enumerate(zip(worlds,commanders)):
                    sides[i]['timeline'].append(player_snapshot(i,w,c))
                report['players']=[dict(s,model_calls=clients[i].calls,model_attempts=clients[i].attempts)
                                   for i,s in enumerate(sides)]
                report['budget']=budget.report();write(folder/'match.json',report)
                print('DUEL_PROGRESS',json.dumps(dict(match=number,game_s=round(worlds[0].seconds,1),
                    players=[dict(race=RACES[i],own=w.summary()['own'],minerals=w.minerals,gas=w.vespene,
                        attempts=clients[i].attempts,accepted=commanders[i].decisions,errors=commanders[i].errors)
                        for i,w in enumerate(worlds)],budget=budget.used),ensure_ascii=False),flush=True)
            if elapsed-last_ui>=5:
                last_ui=elapsed;report['outcome']='running'
                report['samples']=[dict(worlds=[w.summary() for w in worlds],
                    commanders=[c.stats() for c in commanders],timing=arena.clock.report())]
                report['user_intervention']=any(c.operator_advice.messages for c in commanders)
                write(folder/'match.json',report)
            time.sleep(.02)
        if arena.ended:
            report['outcomes']=arena.result()
            report['result_source']=(arena.surrender_report or {}).get('result_source','engine_player_result')
            report['termination']='model_surrender' if arena.surrender_report else 'engine_end'
        report['surrender']=arena.surrender_report
        report['engine_action_errors']=arena.action_errors
        report['timing']=arena.clock.report()
    except Exception as error:
        report.update(error=type(error).__name__+': '+str(error),traceback=traceback.format_exc())
        report['termination']=report['termination'] or 'environment_error'
    finally:
        cleanup=[]
        for c in clients:
            try:c.close()
            except Exception as e:cleanup.append(str(e))
        for i,c in enumerate(commanders):
            try:c.stop()
            except Exception as e:cleanup.append(str(e))
            s=sides[i];s.update(outcome=report['outcomes'].get(i+1,'Unknown'),
                result_source=report['result_source'],termination=report['termination'],
                final_world=worlds[i].summary() if worlds else {},model_calls=clients[i].calls,
                model_attempts=clients[i].attempts,usage=clients[i].usage,wall_s=round(time.monotonic()-started,2),
                fatal_kind=clients[i].fatal_kind,fatal_error=clients[i].fatal_error,
                game_speed='slower',time_limits=report['time_limits'],timing=report.get('timing'),
                journal=str(c.feedback.path),commander=c.stats(),input_chars=clients[i].input_chars)
            write(folder/('p'+str(i+1))/'match.json',s)
        if arena:
            try:
                arena.save_replay(folder/'match.SC2Replay');report['replay']=str(folder/'match.SC2Replay')
            except Exception as e:cleanup.append('replay: '+str(e))
            try:arena.close()
            except Exception as e:cleanup.append('engine: '+str(e))
        report.update(status='finished',outcome=report['outcomes'] or ('time limit' if report['termination']=='time_budget' else 'error'),
                      players=sides,budget=budget.report(),cleanup_complete=not cleanup,
                      cleanup_errors=cleanup,wall_s=round(time.monotonic()-started,2))
        write(folder/'match.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume',type=Path)
    parser.add_argument('--requests',type=int,default=24)
    parser.add_argument('--wall-seconds',type=float,default=1860)
    parser.add_argument('--game-seconds',type=float,default=1800)
    parser.add_argument('--seed',type=int,default=7)
    parser.add_argument('--output-window',action='store_true',help='Open decision/chat window; closing it does not stop the game')
    args=parser.parse_args()
    if not 1<=args.requests<=24 or not 1<=args.wall_seconds<=1860 or not 1<=args.game_seconds<=1800:
        parser.error('Original per-player request and match time ceilings must be retained')
    flags.FLAGS(['go_duel_series'])
    root=ROOT/'logs/go-duels';root.mkdir(exist_ok=True)
    if args.resume:
        folder=args.resume.resolve()
        if not folder.is_relative_to(root):raise ValueError('Resume must be an existing Go duel series')
        state=json.loads((folder/'series.json').read_text())
        if state['state']=='running':raise ValueError('Active/incomplete match cannot be silently restarted')
    else:
        folder=Path(tempfile.mkdtemp(prefix='tvp-',dir=root)).resolve()
        state=dict(state='ready',games=3,matches=[],models=[GO_MODEL]*2,races=list(RACES),
            limits=dict(requests=args.requests,wall_s=args.wall_seconds,game_s=args.game_seconds,seed=args.seed),
            strategies=[INITIAL_STRATEGY,INITIAL_STRATEGY],
            historical_campaign_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (ROOT/'logs/autonomous-bot').glob('*/campaign.json')})
    limits=state['limits'];budget=DurableBudget(folder/'budget.json',3*(limits['requests']*2+2))
    if len(state['matches'])>=3:
        print('DUEL_SERIES_COMPLETE',folder,flush=True);return 0
    number=len(state['matches'])+1
    state.update(state='running',pid=os.getpid(),active_match=number,budget=budget.report())
    write(folder/'series.json',state);write(root/'current.json',dict(series=str(folder)))
    print('DUEL_SERIES',folder,'MATCH',number,flush=True)
    match=play(folder/('match-%02d'%number),number,state['strategies'],budget,limits,args.output_window)
    state['matches'].append(dict(number=number,report=str(folder/('match-%02d'%number)/'match.json'),
        outcomes=match['outcomes'],termination=match['termination'],result_source=match['result_source']))
    state['state']='reviewing';write(folder/'series.json',state)
    for i,s in enumerate(match['players']):
        if not s.get('journal'):continue
        own=folder/('match-%02d'%number)/('p'+str(i+1))
        try:
            d=match_diagnostics(s,Path(s['journal']));write(own/'diagnostics.json',d)
            review=review_game(GO_MODEL,own/'review',PlayerBudget(budget,i),d,state['strategies'][i],
                'LLM_duel',review_context=dict(mode='LLM_duel',own_race=RACES[i],opponent_race=RACES[1-i],match=number,
                    next_match_runtime_updates=list(RUNTIME_UPDATES)))
            write(own/'review.json',review);state['strategies'][i]=review['strategy']
            state['matches'][-1].setdefault('reviews',{})[str(i+1)]=dict(category=review['category'],path=str(own/'review.json'))
            print('DUEL_REVIEW',i+1,review['category'],review['reason'],flush=True)
        except Exception as e:
            state['matches'][-1].setdefault('review_errors',{})[str(i+1)]=type(e).__name__+': '+str(e)
        write(folder/'series.json',state)
    state.update(state='finished' if number==3 else 'paused_after_match',budget=budget.report())
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in state['historical_campaign_hashes'].items())
    write(folder/'series.json',state)
    print('DUEL_MATCH_COMPLETE',json.dumps(dict(match=number,outcomes=match['outcomes'],termination=match['termination'],
        budget=budget.report(),series=str(folder)),ensure_ascii=False),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
