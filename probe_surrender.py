"""Leave-game discriminator, native realtime headless; no LLM or debug cheats."""
import json,time
from pathlib import Path
from absl import app
from arena.native_engine import NativeArena
from s2clientprotocol import sc2api_pb2 as pb


def main(argv):
    report={'realtime':True,'render':False,'modes':[]}
    try:
        for mode in ('bot','dual'):
            row={'mode':mode};report['modes'].append(row)
            options=dict(names=('Surrender probe',),difficulty='very_easy') if mode=='bot' else dict(names=('Leaver','Opponent'))
            with NativeArena(render=False,**options) as a:
                row['engine']=a.version
                a.step([[] for _ in a.controllers]);a.save_replay('logs/surrender_probe_'+mode+'_before.SC2Replay')
                row['before_status']=[c.status.name for c in a.controllers]
                answer=a.controllers[0].leave();row['leave_reply']=str(answer)
                row['after_status']=[c.status.name for c in a.controllers]
                for i,c in enumerate(a.controllers):
                    side={'player':i+1};row.setdefault('players',[]).append(side)
                    try:
                        observation=c.observe();side['results']={r.player_id:pb.Result.Name(r.result) for r in observation.player_result}
                        side['loop']=observation.observation.game_loop
                    except Exception as e:side['observe_error']=type(e).__name__+': '+str(e)
                    try:
                        replay=c.save_replay();side['replay_bytes']=len(replay)
                        Path('logs/surrender_probe_'+mode+'_p%d_after.SC2Replay' % (i+1)).write_bytes(replay)
                    except Exception as e:side['replay_error']=type(e).__name__+': '+str(e)
                row['timing']=a.timing()
                print('LEAVE_PROBE',json.dumps(row),flush=True)
    finally:Path('logs/surrender_probe.json').write_text(json.dumps(report,indent=2))

if __name__=='__main__':app.run(main)
