"""Independent Linux 4.10 discriminator; not native 5.0.15 proof."""
import json,time
from pathlib import Path
from absl import app
from pysc2.env import sc2_env
from pysc2.lib import features
from s2clientprotocol import query_pb2 as qp,debug_pb2 as dp,sc2api_pb2 as pb,error_pb2
from google.protobuf.json_format import MessageToDict
from arena.raw_engine import command,GATHER

def main(argv):
 report={'scope':__doc__,'realtime':True,'races':[]}
 try:
  for race,worker,base,gasid,ability in [('terran',45,18,20,320),('zerg',104,86,88,1154),('protoss',84,59,61,882)]:
   rec={'race':race,'actions':[]};report['races'].append(rec)
   print('START',race,flush=True)
   fmt=features.AgentInterfaceFormat(use_raw_units=True,send_observation_proto=True,action_space=sc2_env.ActionSpace.RAW)
   with sc2_env.SC2Env(map_name='Acropolis',players=[sc2_env.Agent(sc2_env.Race[race]),sc2_env.Bot(sc2_env.Race.terran,sc2_env.Difficulty.very_easy)],agent_interface_format=[fmt],realtime=True,step_mul=1,game_steps_per_episode=0,ensure_available_actions=False,random_seed=7) as env:
    env.reset();c=env._controllers[0];data=c.data_raw();types={u.unit_id:u for u in data.units}
    def observe():
     time.sleep(.1);o=c.observe();rec['last_observation']=MessageToDict(o,preserving_proto_field_name=True);return o
    def send(actions):
     reply=c.actions(pb.RequestAction(actions=actions));rec['actions'].append({'actions':[MessageToDict(x,preserving_proto_field_name=True) for x in actions],'result':MessageToDict(reply,preserving_proto_field_name=True)})
     print('ACTION',race,rec['actions'][-1],flush=True)
     assert all(r==1 for r in reply.result),[error_pb2.ActionResult.Name(r) for r in reply.result]
    until=time.monotonic()+30
    while (o:=observe()).observation.game_loop<112:
     if time.monotonic()>until:raise AssertionError('simulation did not start')
    c.debug([dp.DebugCommand(game_state=dp.minerals)])
    o=observe();rec['resource_after_grant']=MessageToDict(o.observation.player_common,preserving_proto_field_name=True)
    home=next(u for u in o.observation.raw_data.units if u.unit_type==base and u.alliance==1)
    gas=min((u for u in o.observation.raw_data.units if u.alliance==3 and types[u.unit_type].has_vespene),key=lambda u:(u.pos.x-home.pos.x)**2+(u.pos.y-home.pos.y)**2)
    workers=[u for u in o.observation.raw_data.units if u.unit_type==worker and u.alliance==1]
    rec.update(gas_meta=MessageToDict(types[gasid],preserving_proto_field_name=True),before_gas=o.observation.player_common.vespene)
    send([command(ability,[workers[0].tag],target=gas.tag)])
    until=time.monotonic()+60
    while time.monotonic()<until:
     o=observe();buildings=[u for u in o.observation.raw_data.units if u.alliance==1 and u.unit_type==gasid and u.build_progress>=1]
     if buildings:break
    else:raise AssertionError(race+' gas structure never completed')
    ref=buildings[0];tags=[u.tag for u in workers[1:4]];send([command(GATHER,tags,target=ref.tag)])
    until=time.monotonic()+25
    while time.monotonic()<until:
     o=observe()
     if o.observation.player_common.vespene>rec['before_gas']:break
    else:raise AssertionError(race+' gas did not increase')
    rec.update(status='passed',gas_after=o.observation.player_common.vespene,gas_building_tag=ref.tag,worker_tags=tags)
    rec.pop('last_observation');print('RACE_GAS_PASSED',json.dumps(rec),flush=True)
  report['status']='passed'
 except BaseException as e:report.update(status='failed',error=type(e).__name__+': '+str(e));raise
 finally:Path('logs/multirace_linux_premise2.json').write_text(json.dumps(report,indent=2))
if __name__=='__main__':app.run(main)
