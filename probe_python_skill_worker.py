"""Pre-implementation discriminator: isolated CPython, no game and no model."""
import json,os,signal,subprocess,sys,sysconfig,time
from pathlib import Path

def main():
    base=Path(sys.base_prefix).resolve()
    command=['bwrap','--unshare-all','--die-with-parent','--new-session','--clearenv',
        '--ro-bind',str(base),'/runtime','--ro-bind','/usr/lib','/usr/lib',
        '--symlink','usr/lib','/lib','--symlink','usr/lib','/lib64',
        '--proc','/proc','--dev','/dev','--tmpfs','/tmp','--chdir','/tmp',
        '/runtime/bin/python3.11','-I','-S','-u','-c']
    code="""import json,math,os
result={'calculation':math.sqrt(81),'secret':os.getenv('SC2_SKILL_PROBE_SECRET'),
        'network_namespace':os.readlink('/proc/self/ns/net')}
try:open('/home/tyrion/Projects/wolf/sc2-arena/HANDOFF.md');result['project_visible']=True
except FileNotFoundError:result['project_visible']=False
print(json.dumps(result))
"""
    env=os.environ.copy();env['SC2_SKILL_PROBE_SECRET']='fake-sentinel-not-a-real-key'
    start=time.monotonic();p=subprocess.run(command+[code],env=env,capture_output=True,text=True,timeout=4)
    assert p.returncode==0,p.stderr
    r=json.loads(p.stdout);r['startup_wall_s']=time.monotonic()-start
    assert r['calculation']==9 and r['secret'] is None and not r['project_visible']
    assert r['network_namespace']!=os.readlink('/proc/self/ns/net')
    start=time.monotonic();p=subprocess.Popen(command+['while True:pass'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    try:p.communicate(timeout=.15);raise AssertionError('infinite code exited')
    except subprocess.TimeoutExpired:
        os.killpg(p.pid,signal.SIGKILL);p.communicate(timeout=2)
    r['infinite_loop_killed_wall_s']=time.monotonic()-start
    Path('logs/python_skill_worker_probe.json').write_text(json.dumps(r,indent=2))
    print('PYTHON_WORKER_PROBE_OK',json.dumps(r))
if __name__=='__main__':main()
