"""Bounded Go-vs-Go Linux realtime games. One game per invocation, resumable ledger.

Start: ./run_sc2.sh .venv/bin/python go_duel_series.py
Next:  ./run_sc2.sh .venv/bin/python go_duel_series.py --resume logs/go-duels/<series>
No debug fixtures, scripted opening, hidden enemy input, native GPU or provider fallback.
"""
import argparse
import hashlib
import json
import os
import random
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
PLAYABLE_RACES=('terran','zerg','protoss')
ZERG_SCHEDULE=[['zerg','protoss'],['zerg','terran'],['zerg','protoss'],['zerg','terran']]
RUNTIME_UPDATES=(
    'cancel_native_building args={task_id:owned building macro ID} now stops its macro goal and cancels '
    'the linked unfinished worker-built native building/approach. Already-completed buildings are no-ops, '
    'not destroyed or refunded. This interface does not cancel unit queues, research or townhall upgrades.',
    'Linux SC2Env immediate action responses are now captured and merged with observation action errors, '
    'with exact source/task/batch attribution and duplicate suppression. Previously, zero observation errors '
    'did not establish that every action was accepted. Rejected purchases reach the existing bounded retry logic.',
    'Direct native_abilities now exposes own caster tags, energy, cargo, allocation and current native availability. '
    'use_ability args={caster:owned_tag,ability:exact_name,target:null|unit_tag|[x,y],timeout_s:1..120} '
    'issues one explicit order, no auto-recast; purchases/research remain macros. Release allocated missions first '
    '(Nexus chrono boost can coexist with production). Native acknowledgement is not proof of the full tactical effect.',
    'Unacknowledged native purchases now reserve minerals/gas/supply across potentially lagging realtime frames; '
    'the reserve is not a spend/completion claim. cancel_task can cancel wholly unissued production/research/form '
    'macros, never pending native orders or relocations. Form workflows expose form_sources; paid ravager/lurker/'
    'brood_lord/overseer purchases use train_unit. build_supply/build_structure/expand count1..200 makes a finite '
    'batch. Buildings finish one by one; supply adapts to the race. Repeated active goals still coalesce, no append.',
    'Explicit finite replenishment: train_worker/train_unit args.queue_mode=append adds count units to '
    'an active batch (or starts one), preserving in-flight orders; default coalesce adds zero. Read '
    'active_tasks.production_quantity and task_coalesced evidence. One append/product/reply, count1..200, '
    'outstanding total<=200 (completed products free queue capacity), no automatic production cycle. Ordinary idle-capacity cadence now spreads remaining '
    'requests over the real wall/game horizon; urgent combat retains 25 seconds and all request ceilings remain.',
    'Explicit scout/order_units entrance targets can resolve under observed enemy pressure; automatic production '
    'rally selection still avoids threats. Geometry, occupancy and bounded reachability checks remain enforced.',
    'Active spatial workflows now report native_paths for each owned ground actor, sampled every3 game seconds. '
    'Zero outside the arrival radius means no current native path; own nearby structures are context, not proven '
    'blockers. Query failures are unavailable. Decide whether to lower a Depot, lift/relocate a building, change '
    'form or renew the mission using existing tools; no clearing or alternate goal is selected automatically.',
    'Raw death events may be normal hatching/morphing/cancellation. Production intermediate removals are a subset '
    'of raw deaths, not extra combat casualties or proof of an enemy kill.',
    'Idle attack-move formations can confirm local arrival without a watchdog stop; moving/engaged units or '
    'visible attackable enemies in range prevent this settlement shortcut. Arrival is not area clearance/victory.',
    'Only an observed matching egg order claims an egg; unconfirmed original larva tags do not block another product. '
    'Queen casts track actual observed energy spending during travel, with progress/stall checks; '
    'total deadline defaults to90 game seconds, explicit timeout_s1..300 allows distant travel.',
    'Zerg now has inject_larva count=1 args={base:home|expansion|observed site_N,timeout_s:optional1..300}: one explicit Queen cast, '
    '25 energy, native energy+buff confirmation, no automatic cycle. zerg_injection reports own Queens/energy/base buffs.',
    'Townhall upgrades such as lair/hive/orbital_command now restrict their producer to the requested home, '
    'expansion or observed site, rather than choosing an unrelated idle base.',
    'Scout contacts now report the requested/resolved goal, arrival evidence and remaining route. '
    'Scout defaults to stopping at visible enemy contact, including workers; contact does not prove arrival. '
    'Explicit scout stop_on_contact:false reports contacts and continues the selected move route; order_units move is also available.',
    'Larva purchases now acknowledge a new unclaimed own egg with the matching native product order, '
    'even when the engine uses a larva at another owned base; product completion still requires actual own units.',
    'Available idle Zerg larvae with a completed spawning pool now wake production decisions like other races; '
    'the host still does not select a product or issue extra model requests beyond the existing limits.',
    'Warp-in candidate selection now excludes occupied unit footprints and pending warp positions; '
    'completion still requires observed units.',
    'Protoss train_unit may set warp_target:[x,y] for warp-capable units. Only WarpGates may buy these '
    'units, within3 tiles of the selected centre. No power/vision/legal placement means wait; no home '
    'Gateway fallback. Purchases at different destinations have independent completion bindings.',
    'Player-observed resource site names persist across neutral tag changes and mineral depletion; '
    'remembered positions are geography, not current enemy observations.',
    'Macro resource waiting feedback reports only actual shortages and available unreserved resources.',
    'Go battle input is capped at 90000 characters for the full system and observation (previous local guard: 45000). '
    'Current facts, errors and user advice remain available; actual size is recorded. Per-player requests and '
    'wall/game deadlines are unchanged. Context-budget termination is Unknown, not a tactical loss.',
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


def source_snapshot(folder):
    """Retain the exact bytes used for the manifest, so later fixes remain reviewable."""
    hashes={}
    for p in [Path(__file__),*sorted((ROOT/'arena').glob('*.py'))]:
        data=p.read_bytes();name=str(p.relative_to(ROOT));hashes[name]=hashlib.sha256(data).hexdigest()
        target=folder/'source'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    return hashes


def play(folder,number,strategies,budget,limits,output_window=False,races=RACES):
    races=tuple(races)
    if len(races)!=2 or any(r not in ('terran','zerg','protoss') for r in races):
        raise ValueError('Exactly two supported player races required')
    folder.mkdir()
    report=dict(number=number,status='starting',opponent_kind='LLM',races=list(races),models=[GO_MODEL]*2,
        seed=limits['seed']+number-1,game_speed='slower',realtime=True,debug_used=False,scripted_opening=False,
        time_limits=dict(game_s=limits['game_s'],wall_s=limits['wall_s']),outcomes={},
        result_source='unverified',termination=None,players=[],cleanup_complete=False,outcome='starting',
        identities=[dict(side='P'+str(i+1),model=GO_MODEL,race=races[i]) for i in range(2)],samples=[],
        code_snapshot=str(folder/'source'),code_sha256=source_snapshot(folder))
    commanders=[];clients=[];arena=None;worlds=[];started=time.monotonic()
    guards=[DefeatGuard(),DefeatGuard()]
    sides=[dict(player=i+1,race=race,model=GO_MODEL,outcome='Unknown',result_source='unverified',
        termination=None,timeline=[],strategy=strategies[i]) for i,race in enumerate(races)]
    try:
        for i in range(2):
            own=folder/('p'+str(i+1));own.mkdir()
            base=GoPlanner(GO_MODEL,own/'planner')
            clients.append(BotPlanner(base,PlayerBudget(budget,i),strategies[i],limits['requests'],adaptive_cadence=True))
        arena=RawArena(render=False,races=list(races),seed=report['seed'],game_speed='slower')
        report.update(status='running',game_started=True,engine_version=arena.controllers[0].ping().game_version,
                      map_targets=arena.start_location_report,
                      action_error_channels=list(getattr(arena,'action_error_channels',('observation_action_errors',))))
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
                    match_number=number,player=i+1,opponent_kind='LLM',own_race=races[i],opponent_race=races[1-i],
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
                    players=[dict(race=races[i],own=w.summary()['own'],minerals=w.minerals,gas=w.vespene,
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
                input_char_limit=getattr(clients[i],'context_limit',45000),
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


def match_setup(state,number):
    """Legacy ledgers retain their limits; new schedules keep strategies by race."""
    races=state.get('schedule',[state['races']]*state['games'])[number-1]
    strategies=([state['strategies_by_player_race'][i][race] for i,race in enumerate(races)]
                if 'strategies_by_player_race' in state else
                [state['strategies_by_race'][race] for race in races]
                if 'strategies_by_race' in state else state['strategies'])
    return races,strategies


def selected_schedule(zerg_series=False,races=None,games=None,random_races=False,seed=7):
    if random_races:
        if zerg_series or races is not None:
            raise ValueError('Random races cannot be combined with an explicit schedule')
        count=3 if games is None else games
        if type(count) is not int or not 1<=count<=4:
            raise ValueError('Series must contain 1..4 games')
        rng=random.Random(seed)
        return [[rng.choice(PLAYABLE_RACES),rng.choice(PLAYABLE_RACES)] for _ in range(count)]
    if zerg_series:
        if races is not None or games is not None:
            raise ValueError('The four-game Zerg schedule cannot be overridden')
        return [list(pair) for pair in ZERG_SCHEDULE]
    pair=list(RACES if races is None else races)
    if len(pair)!=2 or any(r not in ('terran','zerg','protoss') for r in pair):
        raise ValueError('Exactly two supported player races required')
    count=3 if games is None else games
    if type(count) is not int or not 1<=count<=4:
        raise ValueError('Series must contain 1..4 games')
    return [list(pair) for _ in range(count)]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume',type=Path)
    parser.add_argument('--requests',type=int,default=24)
    parser.add_argument('--wall-seconds',type=float,default=1860)
    parser.add_argument('--game-seconds',type=float,default=1800)
    parser.add_argument('--seed',type=int,default=7)
    parser.add_argument('--zerg-series',action='store_true',help='Four games: Zerg vs Protoss/Terran, twice each')
    parser.add_argument('--races',nargs=2,choices=('terran','zerg','protoss'),help='Explicit new-series P1 and P2 races')
    parser.add_argument('--random-races',action='store_true',help='Independently draw each player race per game; save draws for resume')
    parser.add_argument('--games',type=int,choices=range(1,5),help='Explicit new-series length; default 3')
    parser.add_argument('--output-window',action='store_true',help='Open decision/chat window; closing it does not stop the game')
    parser.add_argument('--skip-model-reviews',action='store_true',help='Keep local diagnostics and skip external post-game reviews; saved for resume')
    args=parser.parse_args()
    if not 1<=args.requests<=24 or not 1<=args.wall_seconds<=1860 or not 1<=args.game_seconds<=1800:
        parser.error('Original per-player request and match time ceilings must be retained')
    flags.FLAGS(['go_duel_series'])
    root=ROOT/'logs/go-duels';root.mkdir(exist_ok=True)
    if args.resume:
        if args.zerg_series or args.races is not None or args.games is not None or args.random_races:
            parser.error('Resume uses the saved schedule; do not select a new series')
        folder=args.resume.resolve()
        if not folder.is_relative_to(root):raise ValueError('Resume must be an existing Go duel series')
        state=json.loads((folder/'series.json').read_text())
        if state['state']=='running':raise ValueError('Active/incomplete match cannot be silently restarted')
    else:
        try:schedule=selected_schedule(args.zerg_series,args.races,args.games,args.random_races,args.seed)
        except ValueError as e:parser.error(str(e))
        prefix='random-' if args.random_races else 'zerg-' if args.zerg_series else 'tvp-' if args.races is None else ''.join(r[0] for r in schedule[0])+'-'
        folder=Path(tempfile.mkdtemp(prefix=prefix,dir=root)).resolve()
        state=dict(state='ready',games=len(schedule),schedule=schedule,matches=[],models=[GO_MODEL]*2,races=schedule[0],
            limits=dict(requests=args.requests,wall_s=args.wall_seconds,game_s=args.game_seconds,seed=args.seed),
            strategies=[INITIAL_STRATEGY,INITIAL_STRATEGY],
            historical_campaign_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (ROOT/'logs/autonomous-bot').glob('*/campaign.json')})
        if args.zerg_series or (args.races is not None and len(set(args.races))==2):
            state['strategies_by_race']={r:INITIAL_STRATEGY for r in ('zerg','terran','protoss')}
        if args.random_races:
            state['race_selection']=dict(mode='independent_seeded_draws',seed=args.seed,mirrors_allowed=True)
            state['strategies_by_player_race']=[{r:INITIAL_STRATEGY for r in PLAYABLE_RACES} for _ in range(2)]
        prior=[*root.glob('*/budget.json'),ROOT/'logs/tactical-scenarios/ledger.json']
        state['historical_campaign_hashes'].update({str(p):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in prior if p.is_file() and p.parent!=folder})
    if args.skip_model_reviews:state['skip_model_reviews']=True
    limits=state['limits'];budget=DurableBudget(folder/'budget.json',state['games']*(limits['requests']*2+2))
    if len(state['matches'])>=state['games']:
        print('DUEL_SERIES_COMPLETE',folder,flush=True);return 0
    number=len(state['matches'])+1
    races,strategies=match_setup(state,number)
    state.update(state='running',pid=os.getpid(),active_match=number,budget=budget.report())
    write(folder/'series.json',state);write(root/'current.json',dict(series=str(folder)))
    print('DUEL_SERIES',folder,'MATCH',number,flush=True)
    match=play(folder/('match-%02d'%number),number,strategies,budget,limits,args.output_window,races=races)
    state['matches'].append(dict(number=number,report=str(folder/('match-%02d'%number)/'match.json'),
        outcomes=match['outcomes'],termination=match['termination'],result_source=match['result_source']))
    state['state']='reviewing';write(folder/'series.json',state)
    for i,s in enumerate(match['players']):
        if not s.get('journal'):continue
        own=folder/('match-%02d'%number)/('p'+str(i+1))
        try:
            d=match_diagnostics(s,Path(s['journal']));write(own/'diagnostics.json',d)
            if state.get('skip_model_reviews'):
                state['matches'][-1]['model_reviews_skipped']=True
                continue
            review=review_game(GO_MODEL,own/'review',PlayerBudget(budget,i),d,strategies[i],
                'LLM_duel',review_context=dict(mode='LLM_duel',own_race=races[i],opponent_race=races[1-i],match=number,
                    next_match_runtime_updates=list(RUNTIME_UPDATES)))
            write(own/'review.json',review)
            if 'strategies_by_player_race' in state:state['strategies_by_player_race'][i][races[i]]=review['strategy']
            elif 'strategies_by_race' in state:state['strategies_by_race'][races[i]]=review['strategy']
            else:state['strategies'][i]=review['strategy']
            state['matches'][-1].setdefault('reviews',{})[str(i+1)]=dict(category=review['category'],path=str(own/'review.json'))
            print('DUEL_REVIEW',i+1,review['category'],review['reason'],flush=True)
        except Exception as e:
            state['matches'][-1].setdefault('review_errors',{})[str(i+1)]=type(e).__name__+': '+str(e)
        write(folder/'series.json',state)
    state.update(state='finished' if number==state['games'] else 'paused_after_match',budget=budget.report())
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in state['historical_campaign_hashes'].items())
    write(folder/'series.json',state)
    print('DUEL_MATCH_COMPLETE',json.dumps(dict(match=number,outcomes=match['outcomes'],termination=match['termination'],
        budget=budget.report(),series=str(folder)),ensure_ascii=False),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
