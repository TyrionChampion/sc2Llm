"""Minimal REALTIME raw-command/camera discrimination; no LLM, no cheats."""
import json
import math
import time
from absl import app
from pysc2.env import sc2_env
from pysc2.lib import features
from s2clientprotocol import sc2api_pb2 as pb, query_pb2 as query


def command(ability, tag, pos=None):
    a = pb.Action()
    c = a.action_raw.unit_command
    c.ability_id = ability
    c.unit_tags.append(tag)
    if pos:
        c.target_world_space_pos.x, c.target_world_space_pos.y = pos
    return a


def main(argv):
    del argv
    def interface(render):
        return features.AgentInterfaceFormat(
            feature_dimensions=features.Dimensions(screen=64, minimap=64),
            rgb_dimensions=features.Dimensions(screen=(1024,768), minimap=256) if render else None,
            use_raw_units=True, use_feature_units=True, send_observation_proto=True,
            action_space=sc2_env.ActionSpace.RAW)
    with sc2_env.SC2Env(map_name='Acropolis', players=[sc2_env.Agent(sc2_env.Race.terran)]*2,
            agent_interface_format=[interface(True), interface(False)], realtime=True,
            step_mul=1, game_steps_per_episode=0, ensure_available_actions=False,
            score_index=-1, random_seed=7) as env:
        assert env._realtime
        obs=env.reset()
        def raw(o): return o.observation['_response_observation']().observation
        bases=[next(u for u in raw(o).raw_data.units if u.alliance==1 and u.unit_type==18) for o in obs]
        cameras=[]
        for b in bases:
            a=pb.Action(); a.action_raw.camera_move.center_world_space.x=100
            a.action_raw.camera_move.center_world_space.y=100; cameras.append(a)
        obs=env.step(cameras)
        # No step/observe calls during this wait: REALTIME must progress on its own.
        t0=time.monotonic(); loop0=raw(obs[0]).game_loop
        time.sleep(3)
        obs=env.step([pb.Action(),pb.Action()])
        elapsed=time.monotonic()-t0; loops=raw(obs[0]).game_loop-loop0
        print('FREE_RUNNING',json.dumps({'wall_s':elapsed,'loops':loops,'game_s':loops/22.4,'ratio':loops/22.4/elapsed}),flush=True)
        assert loops>=40, 'game did not advance independently'
        for o in obs:
            b=next(u for u in raw(o).raw_data.units if u.alliance==1 and u.unit_type==18)
            assert not b.is_on_screen, 'camera failed to leave base'
        obs=env.step([command(524,b.tag) for b in bases])
        print('TRAIN_SENT_OFFSCREEN', [b.tag for b in bases],flush=True)
        deadline=time.monotonic()+35
        while time.monotonic()<deadline:
            obs=env.step([pb.Action(),pb.Action()])
            counts=[sum(u.alliance==1 and u.unit_type==45 for u in raw(o).raw_data.units) for o in obs]
            if min(counts)>=13: break
            time.sleep(.04)
        assert min(counts)>=13, counts
        print('OFFSCREEN_SCV_OK',counts,flush=True)
        pending=[]
        for i,o in enumerate(obs):
            r=raw(o); b=bases[i]
            worker=next(u for u in r.raw_data.units if u.alliance==1 and u.unit_type==45)
            points=[(math.floor(b.pos.x)+dx,math.floor(b.pos.y)+dy) for dx,dy in [(7,0),(-7,0),(0,7),(0,-7),(7,7),(-7,-7),(7,-7),(-7,7)]]
            q=query.RequestQuery()
            for x,y in points:
                p=q.placements.add(ability_id=319,placing_unit_tag=worker.tag)
                p.target_pos.x=x; p.target_pos.y=y
            results=env._controllers[i].query(q).placements
            chosen=next((p for p,r in zip(points,results) if r.result==1),None)
            assert chosen, [(p,r.result) for p,r in zip(points,results)]
            pending.append(command(319,worker.tag,chosen))
            print('BUILD_OFFSCREEN',i,chosen,flush=True)
        obs=env.step(pending)
        deadline=time.monotonic()+50
        while time.monotonic()<deadline:
            obs=env.step([pb.Action(),pb.Action()])
            counts=[sum(u.alliance==1 and u.unit_type==19 and u.build_progress>=1 for u in raw(o).raw_data.units) for o in obs]
            errors=[[e.result for e in o.observation['_response_observation']().action_errors] for o in obs]
            if any(errors): print('ACTION_ERRORS',errors,flush=True)
            if min(counts)>=1: break
            time.sleep(.04)
        assert min(counts)>=1, counts
        print('VERDICT_OK realtime=True, offscreen SCV + finished depot for both players',flush=True)
if __name__=='__main__': app.run(main)
