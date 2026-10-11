"""Pre-implementation native metadata + actual refinery/gas discriminator."""
import fcntl,json,time
from pathlib import Path
from absl import app
from google.protobuf.json_format import MessageToDict
from s2clientprotocol import debug_pb2 as debug
from arena.native_engine import NativeArena,RUNTIME
from arena.raw_engine import command,GATHER,SCV,CC

def main(argv):
    deadline=time.monotonic()+1900
    while True:
        with open(RUNTIME/'native-match.lock','a') as f:
            try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                if time.monotonic()>deadline:raise TimeoutError('existing match still owns engine slot')
            else:break
        time.sleep(5)
    report=dict(status='starting',scope='real native engine before economy implementation; resources granted only')
    a=None
    try:
        a=NativeArena(names=('Metadata and gas probe',),difficulty='very_easy',render=False)
        data=a.controllers[0].data_raw()
        Path('logs/multirace_engine_data.pb').write_bytes(data.SerializeToString())
        Path('logs/multirace_engine_data.json').write_text(json.dumps(MessageToDict(data,preserving_proto_field_name=True),ensure_ascii=False,indent=2))
        report.update(engine=a.version,unit_types=len(data.units),abilities=len(data.abilities),upgrades=len(data.upgrades),debug_states=dict(debug.DebugGameState.items()))
        a.controllers[0].debug([debug.DebugCommand(game_state=debug.minerals)])
        w=a.step([[]])[0];home=a.homes[0]
        geysers=[u for u in w.units if a.types[u.type_id].has_vespene and u.alliance==3]
        gas=min(geysers,key=lambda u:u.distance(home));workers=sorted(w.my(SCV),key=lambda u:u.distance(gas));builder=workers[0]
        report['refinery_meta']=MessageToDict(a.types[20],preserving_proto_field_name=True)
        report['refinery_ability']=next(MessageToDict(v,preserving_proto_field_name=True) for v in data.abilities if v.ability_id==320)
        report['geyser']=dict(tag=gas.tag,type=gas.type_id,pos=gas.pos)
        report['before_abilities']=sorted(a.available_abilities(0,[builder.tag])[builder.tag])
        assert 320 in report['before_abilities'],'refinery ability unavailable'
        w=a.step([[command(320,[builder.tag],target=gas.tag)]])[0]
        until=time.monotonic()+60
        while time.monotonic()<until:
            w=a.step([[]])[0]
            ref=next((u for u in w.my(20) if u.done),None)
            if ref:break
        else:raise AssertionError('refinery never completed')
        before=a.proto(0).observation.player_common.vespene
        tags=[u.tag for u in workers[:3]]
        w=a.step([[command(GATHER,tags,target=ref.tag)]])[0]
        until=time.monotonic()+25
        while time.monotonic()<until:
            w=a.step([[]])[0]
            after=a.proto(0).observation.player_common.vespene
            if after>before:break
        else:raise AssertionError('gas resource did not increase')
        report.update(status='passed',refinery_tag=ref.tag,gas_before=before,gas_after=after,worker_tags=tags,action_errors=a.action_errors,timing=a.timing())
        assert a.action_errors==0
        a.save_replay('logs/multirace_data_probe.SC2Replay')
        print('DATA_AND_GAS_PROBE_OK',json.dumps(report,ensure_ascii=False),flush=True)
    except BaseException as e:report.update(status='failed',error=type(e).__name__+': '+str(e));raise
    finally:
        Path('logs/multirace_data_probe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        if a:a.close()
if __name__=='__main__':app.run(main)
