"""Minimal actual OpenCode format probe; synthetic input, no game launched."""
import json,tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from arena.opencode_bridge import OpenCodeServer,OpenCodePlanner,DEFAULT_MODELS
SCHEMA={'type':'object','properties':{'tasks':{'type':'array','maxItems':2,'items':{'type':'object','properties':{'skill':{'type':'string','enum':['train_worker','supply_depot','barracks','train_unit']},'count':{'type':'integer','minimum':1,'maximum':20}},'required':['skill','count'],'additionalProperties':False}},'reason':{'type':'string','maxLength':120}},'required':['tasks','reason'],'additionalProperties':False}
def main():
 root=Path(tempfile.mkdtemp(prefix='format-probe-',dir='opencode-runtime')).resolve();server=OpenCodeServer(root/'server',DEFAULT_MODELS);report={'root':str(root),'scope':'synthetic schema probe','models':[]}
 try:
  server.start();doc=server.request('GET','/doc');(root/'api-schema.json').write_text(json.dumps(doc))
  config_path=server.root/'config/opencode.json';cfg=json.loads(config_path.read_text())
  cfg['provider']['opencode-go']['models']['qwen3.8-flash']['variants']={'arena_fast':{'thinking':{'type':'disabled'}}}
  config_path.write_text(json.dumps(cfg));server.request('POST','/global/dispose')
  print('prompt schema',json.dumps(doc.get('paths',{}).get('/session/{sessionID}/message',{}).get('post',{}).get('requestBody',{}))[:6000],flush=True)
  def one(i,model):
   captured=[]
   class Proxy:
    def request(self,method,path,**kw):
     if method=='POST' and path=='/session':
      kw['json']['permission'].append({'permission':'StructuredOutput','pattern':'*','action':'allow'})
     if method=='POST' and path.endswith('/message'):
      if model.endswith('qwen3.8-flash'):kw['json']['variant']='arena_fast'
      kw['json']['format']={'type':'json_schema','schema':SCHEMA,'retryCount':0}
      result=server.request(method,path,**kw)
      captured.append({'info':result.get('info'), 'text_parts':[p.get('text') for p in result.get('parts',[]) if p.get('type')=='text'],'part_types':[p.get('type') for p in result.get('parts',[])]})
      return result
     return server.request(method,path,**kw)
   c=OpenCodePlanner(Proxy(),model,i,root/f'p{i+1}');c.phase='battle';c.timeout=60
   row={'model':model}
   try:row['text']=c.chat('You command Terran. Use the StructuredOutput tool to return the plan. Be brief; choose at most two macro tasks and a short reason.', 'Synthetic opening: 500 minerals, 12/15 supply, 12 workers, 1 CommandCenter, no Barracks. Choose useful next construction/production tasks.')
   except Exception as e:row['adapter_result']=type(e).__name__+': '+str(e)
   finally:c.close()
   row['captured']=captured;print(json.dumps(row,ensure_ascii=False),flush=True);return row
  with ThreadPoolExecutor(max_workers=2) as pool:report['models']=list(pool.map(lambda im:one(*im),enumerate(DEFAULT_MODELS)))
 finally:
  server.close();Path('logs/structured_battle_probe_fast.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
if __name__=='__main__':main()
