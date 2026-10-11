"""Real OpenCode requests on hand-written synthetic observations; no local game records or skills sent."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import time
from arena.opencode_bridge import OpenCodeServer,OpenCodePlanner,DEFAULT_MODELS
from arena.battle_plans import SYSTEM,compact_payload,bounded_note
from arena.feedback import FeedbackJournal
from arena.tasks import TaskQueue
from arena.match_control import surrender_reason
from test_realtime_runtime import Backend

def main():
    root=Path(tempfile.mkdtemp(prefix='battle-latency-',dir='opencode-runtime')).resolve()
    server=OpenCodeServer(root/'server',DEFAULT_MODELS);clients=[];report={'root':str(root),'scope':'Actual models, hand-written synthetic observations; no local game records/source sent; no engine or FPS test','players':[]}
    try:
        server.start()
        for i,m in enumerate(DEFAULT_MODELS):
            c=OpenCodePlanner(server,m,i,root/('p%d'%(i+1)));c.phase='battle';clients.append(c)
        def probe(c):
            side='p%d'%(c.index+1)
            payload=compact_payload({
                'world':{'game_s':5,'minerals':100,'supply':[12,15],
                         'own':{'CommandCenter':1,'SCV':12},'visible_enemy':{},'stimpack_researched':False},
                'prepared_strategy':None,'active_tasks':[],
                'control_policy':{'combat_reflex_enabled':False,'idle_worker_gathering':True,'surrender_available':False},
                'feedback':{'events':[],'pending_total':0,'deferred_count':0},
                'skill_repairs':{},'python_skill_library':[],'skill_library':[],
                'test_scope':'Synthetic fixture. Plan basic economy and Marine production; this is not a running game.'})
            q=TaskQueue(Backend(),0,lambda s:None)
            result={'model':c.model,'new_system_chars':len(SYSTEM),'responses':[]}
            for n in range(2):
                prompt=json.dumps(payload,ensure_ascii=False,separators=(',',':'));start=time.monotonic()
                raw=c.chat(SYSTEM,prompt);plan=json.loads(raw)
                if not isinstance(plan,dict) or set(plan)-{'tasks','skills','reason'}:raise ValueError('invalid top-level plan')
                if surrender_reason(plan) is None:q.enqueue(plan['tasks'],5,skills=plan.get('skills'))
                result['responses'].append({'session':c.session_id,'elapsed_s':time.monotonic()-start,'prompt_chars':len(prompt),
                    'plan':plan,'validation':'production TaskQueue schema accepted; no engine execution'})
                payload['previous_strategy']=bounded_note(plan.get('reason',''),600)
                payload['active_tasks']=q.summary()
                payload['skill_library']=list(q.library.values())
            logs=[json.loads(x) for x in c.audit_path.read_text().splitlines()]
            result['token_usage']=[r.get('info',{}).get('tokens') for r in logs if r['record']=='response']
            result['slow_notices']=sum(r['record']=='response_slow' for r in logs)
            print(json.dumps(result,ensure_ascii=False),flush=True)
            return result
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(probe,c) for c in clients]
            for c,f in zip(clients,futures):
                try:report['players'].append(f.result())
                except Exception as error:report['players'].append({'model':c.model,'error':type(error).__name__+': '+str(error)})
        report['passed']=all('error' not in p for p in report['players'])
    finally:
        for c in clients:c.close()
        server.close();Path('logs/battle_latency_synthetic_live.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print('RESULT',json.dumps(report,ensure_ascii=False),flush=True)
    if not report.get('passed'):raise RuntimeError('Real model probe failed; see report')
if __name__=='__main__':main()
