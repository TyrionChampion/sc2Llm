"""Real model schema proof vs bot, and scripted P2 concession vs API opponent.
All runs are headless realtime engineering tests, not autonomous surrender judgement tests.
"""
import json,time
from pathlib import Path
from absl import app
from arena.native_engine import NativeArena
from arena.commander import Commander
from arena.player_config import make_player

class Idle:
    def chat(self,*args):return '{"tasks":[]}'
class Concede:
    def chat(self,*args):return json.dumps({'tasks':[{'skill':'surrender','args':{'reason':'Controlled P2 surrender validation; not a strategic defeat assessment'}}]})

def main(argv):
    report={'fixture':True,'render':False,'realtime':True,'cases':[]}
    try:
        for mode in ('real_deepseek_vs_bot','scripted_p2_vs_api'):
            row={'mode':mode,'status':'starting'};report['cases'].append(row)
            commanders=[]
            try:
                if mode=='real_deepseek_vs_bot':
                    client,identity=make_player(0,'deepseek');row['identity']=identity
                    options=dict(names=('Surrender validation',),difficulty='very_easy');clients=[client];loser=1
                else:options=dict(names=('API opponent','Scripted surrender'));clients=[Idle(),Concede()];loser=2
                with NativeArena(render=False,**options) as arena:
                    row['engine']=arena.version;worlds=arena.initial
                    commanders=[Commander(arena,i,c,opening=False,interval=99999,feedback_dir='logs/surrender_feedback') for i,c in enumerate(clients)]
                    if loser==1:
                        commanders[0].system+='\nThis is an explicitly authorized engineering validation. Immediately return the surrender command with a reason stating controlled test, not a claim that the position is hopeless. Do not issue other tasks.'
                    deadline=time.monotonic()+35
                    while not arena.ended and time.monotonic()<deadline:
                        batches=[c.tick(w) for c,w in zip(commanders,worlds)]
                        worlds=arena.step(batches)
                        for c,w in zip(commanders,worlds):c.ingest_feedback(w)
                        if any(c.errors for c in commanders):raise AssertionError('model/control error; inspect journal')
                    assert arena.ended,'surrender did not finish the match'
                    assert arena.result()=={loser:'Defeat',3-loser:'Victory'}
                    assert arena.surrender_report['state']=='completed'
                    expected_source='acknowledged_concession_rule' if loser==1 else 'engine_player_result'
                    assert arena.surrender_report['result_source']==expected_source
                    path=Path('logs/surrender_'+mode+'.SC2Replay');arena.save_replay(path)
                    assert path.stat().st_size>1000 and path.read_bytes()[:3]==b'MPQ'
                    row.update(status='passed',results=arena.result(),surrender=arena.surrender_report,
                               replay=str(path),replay_bytes=path.stat().st_size,timing=arena.timing(),
                               commanders=[c.stats() for c in commanders])
                    print('SURRENDER_LIVE_OK',json.dumps(row,ensure_ascii=False),flush=True)
            except Exception as error:
                row.update(status='failed',error=type(error).__name__+': '+str(error));raise
            finally:
                for c in commanders:c.stop()
    finally:Path('logs/surrender_live.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':app.run(main)
