"""LLM vs LLM or built-in bot in native GPU player windows, always realtime.

./run_sc2.sh .venv311/bin/python native_llm.py
Optional ARENA_P1/P2_PROVIDER, ARENA_P1/P2_API_KEY and ARENA_P1/P2_MODEL.
No independent observer: each native window has its player's normal fog.
"""
import argparse
import json
from pathlib import Path
import time
from absl import flags
from arena.native_engine import NativeArena, BOT_DIFFICULTIES
from arena.commander import Commander
from arena.player_config import make_player
from arena.lifecycle import ShutdownSignals, write_json_atomic


def main(players_override=None,catalogs_override=None,argv=None,opening=True,repairs_override=None,defense_clients_override=None,developments_override=None,tactical_clients_override=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--map',default='Acropolis')
    parser.add_argument('--game-speed',choices=['slower','normal','faster'],default='normal',help='real simulation speed; Slower/Normal currently require Acropolis')
    parser.add_argument('--p1-race',choices=['terran','zerg','protoss'],default='terran')
    parser.add_argument('--p2-race',choices=['terran','zerg','protoss'],default='terran')
    parser.add_argument('--bot-race',choices=['terran','zerg','protoss'],default='terran')
    parser.add_argument('--p1');parser.add_argument('--p2')
    parser.add_argument('--p1-skills',help='P1 pregame Python skill manifest directory')
    parser.add_argument('--p2-skills',help='P2 pregame Python skill manifest directory')
    parser.add_argument('--bot',choices=sorted(BOT_DIFFICULTIES),help='built-in Terran bot instead of P2 LLM')
    parser.add_argument('--seconds',type=float,default=5400,help='maximum match wall seconds after startup (default: 90 minutes)')
    parser.add_argument('--no-overlay',action='store_true',help='hide the native status text')
    parser.add_argument('--delay',type=float,default=0,help='explicit mock planner latency only')
    parser.add_argument('--output',default='logs/native_llm_result.json')
    parser.add_argument('--combat-micro',action='store_true',help='stim and tank stance execution for explicitly commanded combat units')
    parser.add_argument('--defense',action='store_true',help='independent official DeepSeek non-thinking base defense (official Flash players only)')
    parser.add_argument('--tactics',action='store_true',help='high-level commander plus independent non-thinking battle agent (official Flash players only)')
    parser.add_argument('--verify-chain',action='store_true',help='verify own-race supply, basic production and a basic combat unit')
    args=parser.parse_args(argv)
    if args.seconds<=0:parser.error('--seconds must be positive')
    if args.tactics and (args.defense or defense_clients_override is not None):parser.error('--tactics replaces --defense')
    if args.bot and args.p2_skills:parser.error('--p2-skills requires a P2 LLM')
    if args.bot and args.p2:parser.error('--bot and --p2 are mutually exclusive')
    flags.FLAGS(['native_llm'])
    if players_override is not None:
        players=players_override
        if len(players)!=(1 if args.bot else 2):raise ValueError('player override count does not match mode')
    else:
        players=[make_player(0,args.p1,args.delay)]
        if not args.bot:players.append(make_player(1,args.p2,args.delay))
    from arena.python_skills import PythonCatalog
    from arena.tasks import ALLOWED
    catalogs=catalogs_override if catalogs_override is not None else [PythonCatalog(folder,reserved=ALLOWED) if folder else None
              for folder in ([args.p1_skills] if args.bot else [args.p1_skills,args.p2_skills])]
    identities=[identity for _,identity in players]
    tacticians=tactical_clients_override
    if tacticians is not None and not args.tactics:raise ValueError('tactical overrides require --tactics')
    if tacticians is None and args.tactics:
        from arena.deepseek_planner import DeepSeekFlashPlanner
        if not all(isinstance(client,DeepSeekFlashPlanner) and not client.defense for client,_ in players):
            raise ValueError('--tactics requires official DeepSeekFlashPlanner players; no model substitution')
        tacticians=[DeepSeekFlashPlanner(battle=True) for _ in players]
    if tacticians is not None and (len(tacticians)!=len(players) or any(t is None for t in tacticians)):
        raise ValueError('one tactical client required per API player')
    defenders=defense_clients_override
    if defenders is None and args.defense:
        from arena.deepseek_planner import DeepSeekFlashPlanner
        if not all(isinstance(client,DeepSeekFlashPlanner) and not client.defense for client,_ in players):
            raise ValueError('--defense requires explicit official DeepSeekFlashPlanner players; refusing provider/model substitution')
        defenders=[DeepSeekFlashPlanner(defense=True) for _ in players]
    if defenders is not None and (len(defenders)!=len(players) or any(d is None for d in defenders)):
        raise ValueError('one explicit defense client required per API player')
    if developments_override is not None and len(developments_override)!=len(players):
        raise ValueError('one development coordinator per player required')
    if len(identities)>1 and identities[0]['key_source']==identities[1]['key_source']!='none':
        if all(identity['provider']=='opencode' for identity in identities):
            print('[config] Both players use the same OpenCode login; model sessions and player state are independent.',flush=True)
        else:
            print('[config] Both players use the same configured provider key; planners/state are independent. Set ARENA_P1_API_KEY / ARENA_P2_API_KEY for separate credentials.',flush=True)
    report={'identities':identities,'races':[args.p1_race] if args.bot else [args.p1_race,args.p2_race],'map':args.map,'seed':7,'realtime':True,'max_wall_seconds':args.seconds,'game_speed':args.game_speed,'view':'native player window(s); normal fog',
            'opponent':{'kind':'SC2 built-in bot','race':args.bot_race,'difficulty':args.bot} if args.bot else {'kind':'LLM'},
            'defense_enabled':defenders is not None,'combat_micro_enabled':args.combat_micro,'tactics_enabled':args.tactics,
            'python_skill_catalogs':[c.public() if c else [] for c in catalogs],
            'outcome':'starting','validated_chain':False,'samples':[],'llm_replies':[]}
    commanders=[];arena=None
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
    shutdown=ShutdownSignals();shutdown.install()
    try:
        arena=NativeArena(args.map,names=[i['side']+' '+i['model'] for i in identities],difficulty=args.bot,races=report['races'],bot_race=args.bot_race,game_speed=args.game_speed)
        report.update(engine=arena.version,process_logs=str(arena.directory),map_targets=arena.start_location_report,
                      speed_verification=getattr(arena,'speed_verification',None))
        worlds=arena.initial
        commanders=[Commander(arena,i,client,python_catalog=catalogs[i],opening=opening and report['races'][i]=='terran',repair=repairs_override[i] if repairs_override else None,defense_client=defenders[i] if defenders else None,combat_micro=args.combat_micro,development=developments_override[i] if developments_override else None,tactics_enabled=args.tactics,tactical_client=tacticians[i] if tacticians else None) for i,(client,_) in enumerate(players)]
        started=time.monotonic();last_ui=0;last_log=0;seen_calls=[0,0]
        while not arena.ended and time.monotonic()-started<args.seconds:
            batches=[c.tick(w) for c,w in zip(commanders,worlds)]
            worlds=arena.step(batches)
            for c,w in zip(commanders,worlds):c.ingest_feedback(w)
            now=time.monotonic();timing=arena.timing()
            if now-last_ui>=1 and not arena.ended and not args.no_overlay:
                rows=[]
                for i,(c,identity,w) in enumerate(zip(commanders,identities,worlds)):
                    s=c.stats();warning=s['errors']>0 or s['engine_errors']>0 or s['failed']>0 or s.get('defense_adjutant',{}).get('errors',0)>0 or timing['speed_check']=='DRIFT'
                    status=('MOCK' if identity['provider']=='mock' else 'LLM')+(' THINKING' if s['thinking'] else ' ACTIVE')
                    lines=[identity['side']+' | '+identity['provider']+' / '+identity['model']+' | '+status,
                        'REALTIME '+args.game_speed+' %.2fx | plans=%d errors=%d stale=%d | queue=%d done=%d failed=%d' %
                        (timing['game_wall_ratio'],s['decisions'],s['errors'],s['stale_plans'],len(s['active_tasks']),s['completed'],s['failed']),
                        'Engine rejects=%d | feedback pending=%d delivered=%d' % (s['engine_errors'],s['feedback']['pending'],s['feedback']['delivered']),
                        'Combat reflex %s | Idle worker mining ON' % ('ON' if s['combat_reflex_enabled'] else 'OFF'),
                        'Player perspective / normal fog | Camera is independent of bot orders']
                    defense=s.get('defense_adjutant',{})
                    tactics=s.get('army_tactics',{})
                    if tactics.get('enabled'):
                        lines.append('Tactics %s | army=%d fighting=%d batch=%d replies=%d errors=%d' % (
                            tactics['mode'],len(tactics['unit_tags']),len(tactics['fighting_tags']),tactics['min_group'],tactics['accepted'],tactics['errors']))
                    if defense.get('enabled'):
                        lines.append('Defense %s | units=%d replies=%d errors=%d' % (
                            'THINKING' if defense['thinking'] else defense['mode'] if defense['active'] else 'IDLE',
                            len(defense['unit_tags']),defense['accepted'],defense['errors']))
                    request=s.get('planner_request',{})
                    if s['thinking'] and request.get('state') in ('waiting','slow'):
                        lines.append('LLM %s %.1fs / hard %.0fs | existing tasks continue' %
                            ('SLOW, waiting' if request['state']=='slow' else 'waiting',request['elapsed_s'],request['hard_timeout_s']))
                    if s.get('skill_repairs'):lines.append('Skill repairs: '+', '.join(n+'='+v['state'] for n,v in s['skill_repairs'].items()))
                    if args.bot:lines.append('Opponent: SC2 built-in '+args.bot_race+' / '+args.bot)
                    if s['active_tasks']:
                        lines.append('Tasks: '+', '.join('%s [%s]' % (t['skill'],t['state']) for t in s['active_tasks'][:3]))
                    errors=[line for line in c.log if '[planner-error]' in line]
                    if errors:lines.append('Last error: '+errors[-1][:118])
                    rows.append(('\n'.join(lines),warning))
                arena.overlay(rows);last_ui=now
            for i,c in enumerate(commanders):
                calls=getattr(c.client,'calls',0)
                raw=getattr(c.client,'last_raw',None)
                if raw is not None and calls>seen_calls[i]:
                    reply={'player':i+1,'game_s':worlds[i].seconds,'response':raw}
                    report['llm_replies'].append(reply)
                    print('[real llm reply]',json.dumps(reply,ensure_ascii=False),flush=True)
                    seen_calls[i]=calls
            stats=[c.stats() for c in commanders]
            chain_types={'terran':(19,21,48),'zerg':(106,89,105),'protoss':(60,62,73)}
            chain=all(any(u.type_id==t and u.done for u in w.my_units) for w,race in zip(worlds,report['races']) for t in chain_types[race])
            if chain and not report['validated_chain']:
                report['validated_chain']=True
                print('NATIVE_CHAIN_OK all API players completed own-race supply + basic production + combat unit',flush=True)
            if now-last_log>=10:
                sample={'timing':timing,'worlds':[w.summary() for w in worlds],'commanders':stats,
                        'llm_calls':[getattr(c.client,'calls',0) for c in commanders],
                        'action_errors':arena.action_errors}
                report['samples'].append(sample);report['outcome']='running'
                write_json_atomic(target,report)
                print('[native status]',json.dumps(sample,ensure_ascii=False),flush=True);last_log=now
            if args.verify_chain and chain and all(c.decisions>0 for c in commanders): break
        report['outcome']=arena.result() if arena.ended else ('verified' if args.verify_chain and report['validated_chain'] else 'time limit')
        report['timing']=arena.timing();report['commanders']=[c.stats() for c in commanders]
        report['worlds']=[w.summary() for w in worlds];report['action_errors']=arena.action_errors
        if args.verify_chain and not report['validated_chain']:raise RuntimeError('API player construction chain did not complete')
        for i,(c,identity) in enumerate(zip(commanders,identities)):
            if identity['provider']!='mock' and c.decisions==0:raise RuntimeError('P%d never accepted a real LLM plan' % (i+1))
        if report['timing']['speed_check']=='DRIFT':raise RuntimeError('native match realtime speed drifted')
        print('NATIVE_LLM_RUN_COMPLETE',json.dumps({'outcome':report['outcome'],'timing':report['timing']}),flush=True)
    except BaseException as error:
        report['outcome']='interrupted' if isinstance(error,KeyboardInterrupt) else 'error'
        report['error']=type(error).__name__+': '+str(error)
        print('[native match stopped]',report['error'],flush=True)
        raise
    finally:
        shutdown.cleaning=True
        report["shutdown_signals"]=shutdown.state
        report["cleanup_status"]="in_progress"
        def cleanup_step(stage,operation):
            try:
                return operation()
            except BaseException as error:
                detail=dict(stage=stage,error=type(error).__name__+': '+str(error))
                report.setdefault("cleanup_errors",[]).append(detail)
                print('[cleanup-error]',json.dumps(detail,ensure_ascii=False),flush=True)
                return None
        try:
            # Publish the terminal state BEFORE stopping workers or calling the engine.
            cleanup_step("report_before_cleanup",lambda:write_json_atomic(target,report))
            for i,c in enumerate(commanders):
                cleanup_step("commander_%d_stop"%(i+1),c.stop)
            stats=[cleanup_step("commander_%d_stats"%(i+1),c.stats) for i,c in enumerate(commanders)]
            report["commanders"]=stats
            if arena:
                report['surrender']=arena.surrender_report
                def save_replay():
                    replay=target.with_suffix('.SC2Replay')
                    arena.save_replay(replay)
                    report['replay']=str(replay)
                cleanup_step("save_replay",save_replay)
                cleanup_step("arena_close",arena.close)
            report["cleanup_status"]="failed" if report.get("cleanup_errors") else "complete"
        finally:
            try:
                write_json_atomic(target,report)
            finally:
                shutdown.restore()

if __name__=='__main__':main()
