"""Natural complete-match evaluation. Always realtime; never debug/cheat.

./run_sc2.sh .venv311/bin/python evaluate_realtime.py --games 3 --difficulty very_easy
Timeouts/errors stay in the record and are never called draws or wins.
"""
import argparse
import json
import hashlib
from pathlib import Path
import time
from absl import flags
from arena.raw_engine import RawArena
from arena.commander import Commander,ScriptedPlanner
from arena.llm import LLMClient,PROVIDERS
from arena.player_config import make_player


def aggregate(records):
    complete=[r for r in records if r['outcome'] in ('Victory','Defeat','Tie')]
    wins=sum(r['outcome']=='Victory' for r in complete)
    return {'started':len(records),'completed':len(complete),'wins':wins,
            'losses':sum(r['outcome']=='Defeat' for r in complete),
            'ties':sum(r['outcome']=='Tie' for r in complete),
            'timeouts':sum(r['outcome']=='timeout' for r in records),
            'errors':sum(r['outcome']=='error' for r in records),
            'aborted':sum(r['outcome']=='aborted' for r in records),
            'win_rate_completed':wins/len(complete) if complete else None,
            'wins_per_started_match':wins/len(records) if records else None}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--games',type=int,default=3)
    p.add_argument('--difficulty',choices=['very_easy','easy','medium','medium_hard','hard','harder','very_hard'],default='very_easy')
    p.add_argument('--map',default='Acropolis')
    p.add_argument('--seed',type=int,default=7)
    p.add_argument('--seconds',type=float,default=900,help='wall-clock limit per match; timeout is not a result')
    p.add_argument('--provider',default='mock')
    p.add_argument('--model',help='explicit model for OpenCode Go')
    p.add_argument('--race',choices=['terran','zerg','protoss'],default='terran')
    p.add_argument('--bot-race',choices=['terran','zerg','protoss'],default='terran')
    p.add_argument('--tactics',action='store_true',help='independent OpenCode Go battle model')
    p.add_argument('--combat-micro',action='store_true',help='execute researched stim and tank stance')
    p.add_argument('--delay',type=float,default=0,help='scripted planner latency in seconds')
    p.add_argument('--render',action='store_true',help='request P1 1024x768 RGB without a desktop window')
    p.add_argument('--output',default='logs/realtime_evaluation.json')
    args=p.parse_args()
    if args.games<1 or args.seconds<=0 or args.delay<0: p.error('invalid count/duration')
    if args.provider not in PROVIDERS: p.error('unknown provider')
    if args.model and args.provider!='opencode_go':p.error('--model currently requires opencode_go')
    if args.tactics and args.provider!='opencode_go':p.error('--tactics requires opencode_go; no scripted fallback')
    if args.provider=='mock' and args.race!='terran':p.error('mock baseline supports Terran only')
    if args.provider!='mock' and not PROVIDERS[args.provider].configured: p.error('provider key missing; no mock fallback')
    flags.FLAGS(['evaluate_realtime'])
    code_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                 [Path(__file__),*Path('arena').glob('*.py')]}
    records=[]; output=Path(args.output); output.parent.mkdir(parents=True,exist_ok=True)
    def save():
        report={'config':vars(args),'realtime':True,'debug_used':False,
                'planner_kind':'scripted baseline' if args.provider=='mock' else 'LLM',
                'code_sha256':code_hashes,'summary':aggregate(records),'matches':records}
        temp=output.with_suffix('.tmp'); temp.write_text(json.dumps(report,indent=2)); temp.replace(output)
        print('EVALUATION',json.dumps(report['summary']),flush=True)
    for i in range(args.games):
        record={'seed':args.seed+i,'outcome':'running'}; commanders=[]
        try:
            battle_client=None
            if args.provider=='opencode_go':
                from arena.opencode_go import OpenCodeGoPlanner
                client=OpenCodeGoPlanner(model=args.model)
                # Fail before opening a game if the configured subscription cannot serve JSON.
                client.chat('Return only a JSON object.', 'Return {"ready":true}.')
                identity=dict(provider=client.p.name,model=client.model,key_source=client.p.key_env,
                              preflight=client.last_metadata)
                if args.tactics:
                    battle_client=OpenCodeGoPlanner(model=client.model,battle=True,session_id=client.session_id)
            else:
                client=ScriptedPlanner(args.delay) if args.provider=='mock' else LLMClient(args.provider)
                identity=dict(provider=args.provider,model=getattr(client,'model','scripted'))
            record['identity']=identity
            match_feedback=output.parent/(output.stem+'_feedback')/('seed_'+str(args.seed+i))
            record['feedback_dir']=str(match_feedback)
            with RawArena(args.map,render=args.render,difficulty=args.difficulty,seed=args.seed+i,
                          races=[args.race],bot_race=args.bot_race) as arena:
                commander=Commander(arena,0,client,opening=args.provider=='mock',feedback_dir=str(match_feedback),
                                    tactics_enabled=args.tactics,tactical_client=battle_client,
                                    combat_micro=args.combat_micro); commanders=[commander]
                world=arena.initial[0]; start=time.monotonic(); next_report=start+15
                record['samples']=[]
                while not arena.ended and time.monotonic()-start<args.seconds:
                    world=arena.step([commander.tick(world)])[0]
                    if getattr(client,'fatal_status',None) or getattr(battle_client,'fatal_status',None):
                        raise RuntimeError('OpenCode Go account refusal; stopped match without substituting a model')
                    if time.monotonic()>=next_report:
                        sample={'world':world.summary(),'timing':arena.clock.report(),
                                'policy':commander.stats()}
                        record['samples'].append(sample)
                        record.update(world=sample['world'],policy=sample['policy'],timing=sample['timing'])
                        print('MATCH_PROGRESS',json.dumps(sample),flush=True)
                        records.append(record);save();records.pop()
                        next_report=time.monotonic()+15
                    time.sleep(.02)
                results=arena.result()
                player=arena.proto(0).observation.player_common.player_id
                record.update(outcome=results.get(player,'timeout' if not arena.ended else 'error'),
                              results=results,timing=arena.clock.report(),policy=commander.stats(),
                              world=world.summary(),action_errors=arena.action_errors)
                record['model_usage']=getattr(client,'usage',None)
                record['battle_model_usage']=getattr(battle_client,'usage',None)
                try: record['replay']=arena.env.save_replay('Replays')
                except Exception as e:
                    record['replay_error']=str(e); print('REPLAY_ERROR',str(e),flush=True)
                print('MATCH_RESULT',json.dumps(record),flush=True)
        except KeyboardInterrupt:
            record.update(outcome='aborted',error='user interrupted')
            records.append(record); save(); break
        except Exception as e:
            record.update(outcome='error',error='%s: %s' % (type(e).__name__,e))
            print('MATCH_ERROR',record['error'],flush=True)
        finally:
            for c in commanders: c.stop()
        records.append(record); save()
    if any(r['outcome']=='error' for r in records): raise SystemExit(1)

if __name__=='__main__': main()
