"""Official DeepSeek duel: high-reasoning economy + non-thinking army tactics.

Run through ./run_sc2.sh .venv311/bin/python official_duel.py.
This starts a NEW game only when explicitly invoked; no automatic restart.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from arena.deepseek_planner import DeepSeekFlashPlanner
from arena.persistent_skills import PersistentSkills
from arena.skill_lab import SkillLab
from arena.live_skills import LiveSkillDevelopment, restore_live
from arena.lifecycle import write_json_atomic
import native_llm

ROOT=Path(__file__).resolve().parent

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=float,default=5400,help='maximum match wall seconds after startup (default: 90 minutes)')
    parser.add_argument('--map',default='Acropolis')
    parser.add_argument('--p1-race',choices=['terran','zerg','protoss'],default='terran')
    parser.add_argument('--p2-race',choices=['terran','zerg','protoss'],default='terran')
    parser.add_argument('--game-speed',choices=['slower','normal','faster'],default='normal',help='real simulation speed; Slower/Normal currently require Acropolis')
    parser.add_argument('--without-combat-micro',action='store_true',help='explicitly disable automatic combat ability execution')
    parser.add_argument('--without-defense',action='store_true',help='disable legacy defense when --without-tactics is selected')
    parser.add_argument('--without-tactics',action='store_true',help='use the legacy rally/defense mode instead of persistent army tactics')
    parser.add_argument('--main-reasoning',choices=['low','high'],default='high',help='economy/production planner reasoning effort')
    parser.add_argument('--without-live-skills',action='store_true',help='disable in-match skill authoring/repair; keep accepted libraries')
    parser.add_argument('--prepare-only',action='store_true',help='local skill validation only: no game, no model requests')
    args=parser.parse_args(argv)
    if args.seconds<=0:parser.error('seconds must be positive')
    if not DeepSeekFlashPlanner().p.configured:raise RuntimeError('DEEPSEEK_API_KEY missing; no fallback')
    root=ROOT/'official-runtime';root.mkdir(exist_ok=True)
    run=Path(tempfile.mkdtemp(prefix='match-',dir=root))
    report=dict(status='preparing',models=['deepseek/deepseek-flash']*2,
        endpoint='https://api.deepseek.com/v1',key_source='DEEPSEEK_API_KEY',same_key=True,
        realtime=True,opening=False,combat_reflex=False,live_source_repair=not args.without_live_skills,live_skill_authoring=not args.without_live_skills,
        game_speed=args.game_speed,max_wall_seconds=args.seconds,
        defense_enabled=args.without_tactics and not args.without_defense,tactics_enabled=not args.without_tactics,
        main_reasoning=args.main_reasoning,tactical_thinking='disabled',
        combat_micro_enabled=not args.without_combat_micro,display=dict(vsync=True,max_fps=60),
        skill_limitations='Existing accepted scripts reused unchanged; defense coordination does not fix their audited tactical weaknesses.')
    races=[args.p1_race,args.p2_race]
    report['races']=races;report['player_skill_modes']=[]
    clients=[];catalogs=[];developments=[]
    live_store=ROOT/'skill-library'/'live-official'
    def save():write_json_atomic(run/'run.json',report)
    save()
    try:
        library=PersistentSkills();source_model='opencode-go/deepseek-v4.1-flash'
        source=library.latest(source_model) if 'terran' in races else None
        if 'terran' in races and source is None:raise RuntimeError('Expected accepted DeepSeek skill library missing; refusing fallback')
        report['shared_skill_source']=(dict(model=source_model,version=source['version'],path=source['path']) if source else None)
        report['skill_limitations']='Terran reuses accepted Marine tactic unchanged. Zerg/Protoss explicitly use race-aware macro commands without a custom Python tactic; no race-incompatible script substitution.'
        for i,race in enumerate(races):
            if race=='terran':
                lab=SkillLab(run/('p%d'%(i+1))/'lab',race=race);lab.setup()
                catalog,prepared=library.restore(source_model,lab,lab.root.parent/'accepted',source['version'])
                strategy=prepared['strategy']
                mode='accepted_terran_python_and_macros'
            else:
                catalog=None
                strategy=('本局你使用'+{'zerg':'虫族','protoss':'神族'}[race]+
                    '。当前持久库只有人族枪兵脚本，本方不加载这些脚本。'
                    '你可使用完整本族宏指令、科技、采气、开矿、作战指令。'
                    '参考本族opening_playbook，自行选择并更新macro_plan。'
                    '当前没有本族自定义Python局部微操技能，不要调用stim_push。')
                mode='explicit_race_macros_without_custom_python'
                print('[official duel P%d] %s: race-aware macro commands; no compatible custom Python tactic loaded (explicit configuration).'%(i+1,race),flush=True)
            catalog,restored=restore_live(live_store,race,run/('p%d'%(i+1))/'live-restored',catalog)
            if restored:
                report.setdefault('live_skill_restores',[]).append(dict(player=i+1,skills=restored))
                strategy+=' 已验收的补充技能见当前python_skill_library，按实际目录调用。'
            if not args.without_live_skills:
                strategy+=' 发现缺少局部技能时可用develop_skill请求后台编写；女王注卵用queen_inject验收。等待ready后自己派遣。'
            developments.append(None if args.without_live_skills else LiveSkillDevelopment(run/('p%d'%(i+1))/'live-development',live_store))
            if developments[-1]:
                for restored_skill in restored:
                    if restored_skill['state']=='loaded':
                        developments[-1].status[restored_skill['name']]=dict(state='ready',verification=restored_skill['verification'],
                            persistent=restored_skill,handling='启动时重新通过隔离验收；按需派遣，原生效果尚未确认。')
            report['player_skill_modes'].append(dict(player=i+1,race=race,mode=mode))
            catalogs.append(catalog);client=DeepSeekFlashPlanner(reasoning_effort=args.main_reasoning)
            client.prepared_strategy=strategy
            clients.append((client,dict(side='P%d'%(i+1),provider='deepseek',model=client.model,
                key_source=client.p.key_env,endpoint=client.p.base_url,race=race)))
        report['identities']=[identity for _,identity in clients]
        report['status']='prepared';save()
        if args.prepare_only:
            print('[official duel] Prepared only; no model calls or game. '+str(run),flush=True)
            return
        with (run/'output-window.log').open('w') as output:
            viewer=subprocess.Popen([sys.executable,str(ROOT/'llm_output_window.py'),'--run',str(run)],
                stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
        time.sleep(.5)
        if viewer.poll() is not None:raise RuntimeError('Chinese output window exited; inspect output-window.log')
        report['output_window_pid']=viewer.pid;report['status']='starting_native_game';save()
        (ROOT/'logs/deepseek_official_duel_current.json').write_text(json.dumps(dict(run=str(run),
            launcher='official_duel.py',defense_enabled=report['defense_enabled'],tactics_enabled=report['tactics_enabled'],races=races),indent=2))
        print('[official duel] Official DeepSeek Flash on both sides; main thinking='+args.main_reasoning+'; tactics='+('OFF' if args.without_tactics else 'ON (thinking disabled)')+'; legacy defense='+str(report['defense_enabled'])+'; game_speed='+args.game_speed+'; same configured key.',flush=True)
        native_llm.main(players_override=clients,catalogs_override=catalogs,opening=False,developments_override=developments,
            argv=['--seconds',str(args.seconds),'--map',args.map,'--game-speed',args.game_speed,
                  '--p1-race',args.p1_race,'--p2-race',args.p2_race,'--output',str(run/'match.json')]+
            (['--defense'] if report['defense_enabled'] else [])+
            (['--tactics'] if report['tactics_enabled'] else [])+
            ([] if args.without_combat_micro else ['--combat-micro']))
        report['status']='finished'
    except BaseException as error:
        report['status']='interrupted' if isinstance(error,KeyboardInterrupt) else 'failed'
        report['error']=type(error).__name__+': '+str(error)
        raise
    finally:
        for development in developments:
            if development:development.close()
        report['main_usage']=[dict(side=i['side'],usage=c.usage) for c,i in clients]
        report['live_skill_development']=[d.public() if d else {'enabled':False} for d in developments]
        save()

if __name__=='__main__':main()
