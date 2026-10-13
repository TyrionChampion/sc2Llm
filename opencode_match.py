"""Two OpenCode models author Python skills, then command a realtime native SC2 match."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from arena.opencode_bridge import OpenCodeServer,OpenCodePlanner,DEFAULT_MODELS
from arena.lab_prepare import develop,lab_mcp,restore_prepared
from arena.skill_lab import SkillLab


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--p1',default=DEFAULT_MODELS[0]);parser.add_argument('--p2',default=DEFAULT_MODELS[1])
    parser.add_argument('--p1-race',choices=['terran','zerg','protoss'],default='terran')
    parser.add_argument('--p2-race',choices=['terran','zerg','protoss'],default='terran')
    parser.add_argument('--prepare-seconds',type=float,default=600,help='per-player autonomous tool/test preparation budget')
    parser.add_argument('--seconds',type=float,default=5400,help='maximum match wall seconds after startup (default: 90 minutes)')
    parser.add_argument('--map',default='Acropolis')
    parser.add_argument('--game-speed',choices=['slower','normal','faster'],default='normal',help='real simulation speed; Slower/Normal currently require Acropolis')
    parser.add_argument('--check-only',action='store_true',help='real model connectivity checks only, no preparation/game')
    parser.add_argument('--drafts-from',help='resume model-authored draft source/tests from a failed preparation; no validation is assumed')
    parser.add_argument('--review-from',help='models review their own stopped-match source/errors, edit/test Python, then rematch')
    parser.add_argument('--prepared-from',help='explicitly reuse exact tested source+tests from a previous lab run, with hash checks and revalidation')
    parser.add_argument('--p1-prepared-from',help='restore only P1 from an exact tested source; other player prepares independently')
    parser.add_argument('--p2-prepared-from',help='restore only P2 from an exact tested source; other player prepares independently')
    parser.add_argument('--reference-model',action='append',default=[],help='explicitly share an accepted model library source/tests as read-only preparation references for both players; repeatable')
    parser.add_argument('--no-skill-library',action='store_true',help='explicitly disable persistent loading/saving for an isolated run')
    parser.add_argument('--skill-library-root',help='explicit persistent library directory; default skill-library/')
    parser.add_argument('--no-live-repair',action='store_true',help='explicitly disable background source repair')
    parser.add_argument('--no-output-window',action='store_true',help='disable the separate player decision/chat window')
    parser.add_argument('--qualify',action='store_true',help='require actual Python dispatch and five consecutive real decisions <=22s before game')
    parser.add_argument('--prepare-only',action='store_true',help='author and validate files, no game')
    args=parser.parse_args()
    if not 10<=args.prepare_seconds<=1200:parser.error('preparation must be 10..1200 seconds')
    if args.seconds<=0:parser.error('match seconds must be positive')
    if args.check_only and args.prepare_only:parser.error('choose one validation mode')
    if sum(bool(x) for x in (args.drafts_from,args.prepared_from,args.review_from))>1:parser.error('choose exactly one source mode: drafts, prepared or review')
    if (args.p1_prepared_from or args.p2_prepared_from) and (args.prepared_from or args.drafts_from or args.review_from):parser.error('per-player preparation cannot be combined with global source modes')
    def stop(signum,frame):raise KeyboardInterrupt('stop signal')
    signal.signal(signal.SIGTERM,stop)
    Path('opencode-runtime').mkdir(exist_ok=True)
    root=Path(tempfile.mkdtemp(prefix='match-',dir='opencode-runtime')).resolve();root.chmod(0o700)
    from arena.persistent_skills import PersistentSkills
    races=[args.p1_race,args.p2_race]
    library_root=Path(args.skill_library_root or 'skill-library').resolve()
    from arena.skill_references import reference_materials,attach_references
    references=reference_materials(args.reference_model,library_root)
    libraries=[None if args.no_skill_library else PersistentSkills(library_root if race=='terran' else library_root/('race-'+race)) for race in races]
    for i,library in enumerate(libraries):print('[skill-library P%d %s] '%(i+1,races[i])+('DISABLED explicitly' if library is None else 'enabled: '+str(library.root)),flush=True)
    servers=[];clients=[];developers=[];labs=[];repairs=[]
    report=dict(status='starting',models=[args.p1,args.p2],races=races,realtime=True,run_directory=str(root),references=[{k:r[k] for k in ('model','version','path')} for r in references])
    print('[opencode match] logs and authored files: '+str(root),flush=True)
    try:
        for i,model in enumerate((args.p1,args.p2)):
            player=root/('p%d'%(i+1));lab=SkillLab(player/'lab',race=races[i]);lab.setup();labs.append(lab)
            if args.drafts_from:
                source=Path(args.drafts_from).resolve();previous=json.loads((source/'run.json').read_text())
                if previous.get('races',['terran','terran'])[i]!=races[i]:raise ValueError('draft player race mismatch')
                if previous['models'][i]!=model:raise ValueError('draft author model mismatch')
                oldlab=SkillLab(source/('p%d'%(i+1))/'lab')
                for folder in ('skills','tests'):
                    for p in (oldlab.root/folder).iterdir():lab.write(folder+'/'+p.name,oldlab.read(folder+'/'+p.name))
                from arena.lab_prepare import copy_lab_fixtures
                copy_lab_fixtures(oldlab,lab)
                report['drafts_from']=str(source)
                print('[lab P%d] loaded own previous drafts; tests/validation still required'%(i+1),flush=True)
            if args.review_from:
                from arena.match_review import load_review
                load_review(args.review_from,i,model,lab)
                report['review_from']=str(Path(args.review_from).resolve())
            attach_references(lab,references)
            server=OpenCodeServer(player/'server',[model],mcp=lab_mcp(lab.root));servers.append(server);server.start()
            developer=OpenCodePlanner(server,model,i,player/'developer',lab_tools=True);developers.append(developer)
            if args.review_from or (args.drafts_from and (lab.root/'fixtures/last_match_review.json').exists()):
                from arena.match_review import REVIEW_NOTE
                developer.review_note=REVIEW_NOTE
            mcp=server.request('GET','/mcp',params=developer.params)
            if mcp.get('lab',{}).get('status')!='connected':raise RuntimeError('local lab tools unavailable: '+str(mcp))
            clients.append(OpenCodePlanner(server,model,i,player/'battle'))
            clients[-1].race=races[i]
        def check(c):
            c.timeout=25;start=time.monotonic()
            raw=c.chat('Return only the requested JSON without tools.', 'Return exactly {"tasks":[],"reason":"SC2 connectivity check"}')
            if json.loads(raw).get('tasks')!=[]:raise ValueError('connectivity response not valid task JSON')
            return dict(model=c.model,session_id=c.session_id,latency_s=time.monotonic()-start,status='passed')
        with ThreadPoolExecutor(max_workers=2) as pool:report['connectivity']=list(pool.map(check,clients))
        report['status']='connectivity_passed'
        if args.check_only:return
        def prepare(c):
            destination=root/('p%d'%(c.index+1))/'accepted'
            source=args.prepared_from or (args.p1_prepared_from if c.index==0 else args.p2_prepared_from)
            library=libraries[c.index]
            lab=labs[c.index];head=library.latest(c.model) if library else None
            explicit=bool(source or args.drafts_from or args.review_from)
            if head and explicit:
                from arena.python_skills import PythonCatalog
                base=Path(source)/('p%d'%(c.index+1))/'lab/skills' if source else lab.root/'skills'
                if PythonCatalog(base).public()!=head['manifest']['skills']:
                    raise ValueError('Explicit source differs from persistent latest for '+c.model+'; no implicit rollback. Omit source flags to inherit latest, or use --no-skill-library for an isolated historical run.')
            if head and not explicit:
                value=library.restore(c.model,lab,destination,head['version'])
            else:
                if library and head is None:print('[skill-library] no stored version for '+c.model+'; preparing/importing first version explicitly',flush=True)
                value=(restore_prepared(source,c.index,c.model,lab,destination) if source else
                       develop(c,lab,destination,args.prepare_seconds))
                if library:
                    value[1]['persistent']=library.publish(c.model,lab,value[0],value[1],expected=head['version'] if head else None,
                        origin={'kind':'pregame','run':str(root),'player':c.index+1,'references':report['references']})
                    (destination.parent/(destination.name+'-report.json')).write_text(json.dumps(value[1],ensure_ascii=False,indent=2))
            return c.index,value
        results={}
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending=[pool.submit(prepare,c) for c in developers]
            try:
                for f in as_completed(pending):i,value=f.result();results[i]=value
            except BaseException:
                for c in developers:c.close()
                raise
        report['preparation']=[results[i][1] for i in (0,1)];report['status']='prepared'
        if args.review_from or args.qualify:
            from arena.match_review import rehearse_dispatch
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures=[pool.submit(rehearse_dispatch,c,results[c.index][0],results[c.index][1]['strategy']) for c in clients]
                report['dispatch_rehearsal']=[f.result() for f in futures]
        if args.qualify:
            from arena.match_review import qualify_with_coaching
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures=[pool.submit(qualify_with_coaching,c,results[c.index][0],results[c.index][1]['strategy']) for c in clients]
                report['decision_qualification']=[f.result() for f in futures]
        if args.prepare_only:return
        (root/'run.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        if not args.no_output_window:
            viewer_log=open(root/'output-window.log','w')
            try:
                viewer=subprocess.Popen([sys.executable,str(Path(__file__).resolve().with_name('llm_output_window.py')),'--run',str(root)],stdout=viewer_log,stderr=subprocess.STDOUT,start_new_session=True)
            finally:viewer_log.close()
            time.sleep(.5)
            if viewer.poll() is not None:raise RuntimeError('Output window exited during startup; see '+str(root/'output-window.log'))
            report['output_window']={'pid':viewer.pid,'log':str(root/'output-window.log'),'scope':'public output and per-player next-round advice; closing it does not stop match'}
            (root/'run.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            print('[output window] launched; log='+str(root/'output-window.log'),flush=True)
        print('[opencode match] both preparations passed; opening two native player windows; realtime=True',flush=True)
        for c in clients:
            c.phase='battle';c.timeout=60;c.calls=0;c.last_raw=None;c.prepared_strategy=results[c.index][1]['strategy']
        if not args.no_live_repair:
            from arena.skill_repair import SkillRepair
            repairs=[SkillRepair(developers[i],labs[i],root/('p%d'%(i+1))/'repairs',library=libraries[i],library_version=results[i][1].get('persistent',{}).get('version')) for i in (0,1)]
        import native_llm
        native_llm.main(players_override=[(c,c.identity()) for c in clients],catalogs_override=[results[i][0] for i in (0,1)],repairs_override=repairs or None,
            argv=['--p1-race',races[0],'--p2-race',races[1],'--map',args.map,'--game-speed',args.game_speed,'--seconds',str(args.seconds),'--output',str(root/'match.json')],opening=False)
        report['status']='match_finished'
    except BaseException as error:
        report.update(status='interrupted' if isinstance(error,KeyboardInterrupt) else 'failed',error=type(error).__name__+': '+str(error))
        print('[opencode match] STOPPED: '+report['error'],flush=True)
        raise
    finally:
        for repair in repairs:repair.close()
        for c in clients+developers:
            try:c.close()
            except Exception as error:print('[opencode cleanup error] '+str(error),flush=True)
        for server in servers:server.close()
        (root/'run.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print('[opencode match] final status='+report['status']+'; report='+str(root/'run.json'),flush=True)

if __name__=='__main__':main()
