"""Profile the actual REALTIME desktop loop; startup excluded from stage timers.

Historical step-driven measurements live in logs/profile_client_before_raw.py.
This entry point never changes realtime=True or lowers the game resolution.
"""
import argparse
import json
from pathlib import Path
import time
import numpy as np
from absl import flags
import sc2_client
from arena.raw_engine import RawArena
from arena.commander import Commander


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seconds',type=float,default=30)
    p.add_argument('--output',default='logs/realtime_profile.json')
    args=p.parse_args()
    if args.seconds<=0: p.error('seconds must be positive')
    flags.FLAGS(['profile_client'])
    samples={}; arena_seen=[]; timing_seen=[]; in_step=False
    def wrap(obj,name,label):
        original=getattr(obj,name)
        def measured(*a,**kw):
            start=time.perf_counter()
            try: return original(*a,**kw)
            finally: samples.setdefault(label,[]).append((time.perf_counter()-start)*1000)
        setattr(obj,name,measured)
    original_step=RawArena.step
    original_snap=RawArena.snapshots
    def step(self,batches):
        nonlocal in_step
        in_step=True; start=time.perf_counter()
        try: return original_step(self,batches)
        finally:
            samples.setdefault('env_step',[]).append((time.perf_counter()-start)*1000)
            in_step=False
            if not arena_seen: arena_seen.append(self)
            timing_seen[:]=[self.clock.report()]
    def snapshot(self):
        start=time.perf_counter()
        try: return original_snap(self)
        finally:
            if in_step: samples.setdefault('parse_raw_worlds',[]).append((time.perf_counter()-start)*1000)
    RawArena.step=step; RawArena.snapshots=snapshot
    wrap(Commander,'tick','commander_tick_per_player')
    wrap(sc2_client.pygame.image,'frombuffer','surface')
    wrap(sc2_client.pygame.display,'flip','display_flip')
    sc2_client.SHOT_AFTER=args.seconds
    sc2_client.SHOT_PATH=str(Path(args.output).with_suffix('.png'))
    original_events=sc2_client.pygame.event.get
    sc2_client.pygame.event.get=lambda:[e for e in original_events() if e.type==sc2_client.pygame.QUIT]
    from collections import defaultdict
    sc2_client.pygame.key.get_pressed=lambda:defaultdict(bool)
    sc2_client.pygame.mouse.get_pos=lambda:(512,384)
    sc2_client.main(['sc2_client','Acropolis','mock'])
    if not arena_seen: raise RuntimeError('no measured frames')
    report={'realtime':True,'seconds_requested':args.seconds,
            'timing':timing_seen[0],
            'stages':{k:{'count':len(v),'mean_ms':float(np.mean(v)),
                         'p95_ms':float(np.percentile(v,95))} for k,v in samples.items()},
            'note':'env_step includes parsing; stage times are nested. Mock commanders; fixed camera.'}
    if report['timing']['wall_s']<args.seconds: raise RuntimeError('profile interrupted')
    Path(args.output).write_text(json.dumps(report,indent=2))
    print('PROFILE_COMPLETE',json.dumps(report),flush=True)

if __name__=='__main__': main()
