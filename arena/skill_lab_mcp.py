"""Minimal stdio MCP exposing only one fixed player's skill lab."""
import sys,json,traceback
from pathlib import Path
wire=sys.stdout;sys.stdout=sys.stderr
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from arena.skill_lab import SkillLab
lab=SkillLab(sys.argv[1])
def schema(props,required=()):return dict(type='object',properties=props,required=list(required),additionalProperties=False)
text=dict(type='string')
tools=[
 dict(name='list_files',description='List your private Python skill lab files.',inputSchema=schema({})),
 dict(name='read_file',description='Read your lab documentation, source, fixture or test file.',inputSchema=schema({'path':text},['path'])),
 dict(name='write_file',description='Create or replace your skill source/manifest or unittest file. This invalidates previous test results.',inputSchema=schema({'path':text,'content':text},['path','content'])),
 dict(name='run_tests',description='Execute all your tests/test_*.py unittest tests in isolated local Python. Returns real exit code and traceback.',inputSchema=schema({})),
 dict(name='validate',description='Run host independent contract, action and recorded-failure checks on current skill source. Required in addition to your tests.',inputSchema=schema({}))]
for line in sys.stdin:
    r={}
    try:
        if len(line)>150000:raise ValueError('request too large')
        r=json.loads(line);method=r.get('method');rid=r.get('id')
        if rid is None:continue
        if method=='initialize':value=dict(protocolVersion='2024-11-05',capabilities={'tools':{}},serverInfo=dict(name='sc2-python-lab',version='1'))
        elif method=='ping':value={}
        elif method=='tools/list':value={'tools':tools}
        elif method=='tools/call':
            params=r['params'];name=params['name'];a=params.get('arguments',{})
            try:
                if name=='list_files':out=lab.files()
                elif name=='read_file':out=lab.read(a['path'])
                elif name=='write_file':out=lab.write(a['path'],a['content'])
                elif name=='run_tests':out=lab.run_tests()
                elif name=='validate':out=lab.validate()
                else:raise ValueError('unknown tool')
                value=dict(content=[dict(type='text',text=json.dumps(out,ensure_ascii=False))])
            except Exception as e:value=dict(isError=True,content=[dict(type='text',text=type(e).__name__+': '+str(e))])
            audit_args={k:v for k,v in a.items() if k!='content'}
            if 'content' in a:audit_args['bytes']=len(a['content'].encode())
            lab.audit(name,audit_args,value)
        else:raise ValueError('unsupported MCP method '+str(method))
        reply=dict(jsonrpc='2.0',id=rid,result=value)
    except Exception as e:reply=dict(jsonrpc='2.0',id=r.get('id'),error=dict(code=-32603,message=str(e)))
    wire.write(json.dumps(reply,ensure_ascii=False)+'\n');wire.flush()
