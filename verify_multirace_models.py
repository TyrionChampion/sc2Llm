"""Real two-model three-race API exercise; synthetic worlds, no victory claim."""
import json,tempfile,time
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from arena.opencode_bridge import OpenCodeServer,OpenCodePlanner,DEFAULT_MODELS
from arena.knowledge import cached_tree,ECONOMY,RACES
from arena.battle_plans import SYSTEM
from arena.tasks import TaskQueue

def main():
 root=Path(tempfile.mkdtemp(prefix='three-race-probe-',dir='opencode-runtime')).resolve()
 server=OpenCodeServer(root/'server',DEFAULT_MODELS);report={'scope':'real models, production schema/TaskQueue, synthetic observations; no game execution','root':str(root)}
 try:
  server.start()
  def one(pair):
   i,model=pair;c=OpenCodePlanner(server,model,i,root/f'p{i+1}');c.phase='battle';c.timeout=60;rows=[]
   try:
    for race in ('terran','zerg','protoss'):
     tree=cached_tree();payload={'world':{'race':race,'game_s':180,'minerals':1200,'vespene':150,'supply':[30,80],'own':{},'upgrades':[],'visible_enemy':{}},
      'tech_tree':tree.public(race),'game_knowledge':{'economy':ECONOMY,'race_notes':RACES[race]},'active_tasks':[],
      'python_skill_library':[],'skill_library':[],'control_policy':{'surrender_available':False},'feedback':{'events':[]},
      'exercise':'合成接口演练，不是比赛。请安排本族气矿、每矿3人采气、一次开矿、一种需要瓦斯的常规作战单位以及该单位需要的生产/科技前置和一项相关研究。最多16项任务，允许前置排队等待。用中文reason描述。'}
     row={'race':race,'attempts':[]};rows.append(row)
     for attempt in range(3):
      start=time.monotonic()
      try:
       value=json.loads(c.chat(SYSTEM+' This is an API exercise, NOT a strategic game turn. You MUST include all requested command categories in this ONE response; waiting for prerequisites is allowed and is not a reason to omit research or the requested gas combat unit. Follow payload.exercise exactly.',json.dumps(payload,ensure_ascii=False,separators=(',',':'))))
       q=TaskQueue(SimpleNamespace(tech_tree=tree,races=[race,race]),i,lambda s:None);q.enqueue(value['tasks'],180)
       skills={t['skill'] for t in value['tasks']};assert {'gather_gas','expand','research'}<=skills,'missing requested economy/research exercise'
       units=[tree.units[t['args']['unit']] for t in value['tasks'] if t['skill']=='train_unit'];assert any(any(r['gas']>0 for r in e['recipes']) for e in units),'no gas combat unit'
       row.update(passed=True,seconds=time.monotonic()-start,plan=value);break
      except Exception as e:
       error=type(e).__name__+': '+str(e);row['attempts'].append({'error':error,'plan':locals().get('value')});payload['exercise_feedback']={'error':error,'rejected_plan':locals().get('value'),'required':'ONE response must include gas structure + gather_gas(3) + expand + train_unit of a gas combat unit + research; do not just describe omitted tasks in reason'};print('MODEL_REJECTED',model,race,error,flush=True)
     else:row['passed']=False
     print('MODEL_EXERCISE',model,json.dumps(row,ensure_ascii=False),flush=True)
    return {'model':model,'races':rows}
   finally:c.close()
  with ThreadPoolExecutor(max_workers=2) as pool:report['models']=list(pool.map(one,enumerate(DEFAULT_MODELS)))
 finally:
  server.close();Path('logs/multirace_models.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
 assert all(r['passed'] for m in report['models'] for r in m['races'])
if __name__=='__main__':main()
