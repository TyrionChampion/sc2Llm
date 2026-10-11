"""Premise experiment only: two native API players + independent GPU observer."""
import concurrent.futures
import json
import os
import subprocess
import sys
import time
from absl import flags
from pysc2.lib import remote_controller
from s2clientprotocol import sc2api_pb2 as pb, common_pb2 as common
from arena.native_runtime import ROOT,RUNTIME,GAME,prepare
from arena.raw_engine import command, camera, CC, SCV, TRAIN_SCV


def win(p): return "Z:"+str(p).replace("/","\\")
def main():
    flags.FLAGS([sys.argv[0]]); prepare()
    ports=[18571,18572]
    exe=win(GAME/"Versions/Base97579/SC2_x64.exe")
    lines=["@echo off",f'cd /d "{win(GAME/"Support64")}"']
    for i,port in enumerate(ports):
        temp=RUNTIME/"tmp"/f"multi-{i}";temp.mkdir(exist_ok=True)
        args=[exe,"-listen","127.0.0.1","-port",str(port),"-dataDir",win(GAME)+"\\",
              "-tempDir",win(temp)+"\\","-verbose"]
        if i==1: args+=["-headlessNoRender"]
        else: args +=["-displayMode","0","-windowwidth","1600","-windowheight","900","-windowx","80","-windowy","80"]
        line=subprocess.list2cmdline(args)
        lines.append(('start "" /b ' if i==0 else '')+line)
    batch=RUNTIME/"native-multi-probe.cmd";batch.write_bytes(("\r\n".join(lines)+"\r\n").encode())
    env=os.environ.copy();env["WINEDEBUG"]="-all"
    controllers=[]
    with open(ROOT/"logs/native_multi_process.log","w") as output:
        process=subprocess.Popen([sys.executable,str(ROOT/"arena/native_runtime.py"),"cmd.exe","/c",win(batch)],env=env,stdout=output,stderr=subprocess.STDOUT)
        try:
            class Proc:
                @property
                def running(self): return process.poll() is None
            for port in ports:
                c=remote_controller.RemoteController("127.0.0.1",port,Proc(),timeout_seconds=150)
                controllers.append(c);print("PING",port,c.ping(),flush=True)
            versions=[c.ping().game_version for c in controllers]
            assert len(set(versions))==1,versions
            create=pb.RequestCreateGame(realtime=True,random_seed=7,
                local_map=pb.LocalMap(map_path=win(ROOT/"engine/StarCraftII/Maps/Ladder2019Season3/AcropolisLE.SC2Map")))
            create.player_setup.add(type=pb.Participant)
            create.player_setup.add(type=pb.Participant)
            print("CREATE",controllers[0].create_game(create),flush=True)
            joins=[]
            for i in range(2):
                j=pb.RequestJoinGame(options=pb.InterfaceOptions(raw=True,score=True),player_name=["API P1","API P2","Spectator"][i])
                if i<2: j.race=common.Terran
                else: j.observed_player_id=0
                j.server_ports.game_port=18580;j.server_ports.base_port=18581
                for k in range(1): j.client_ports.add(game_port=18582+k*2,base_port=18583+k*2)
                joins.append(j)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda pair:pair[0].join_game(pair[1]),zip(controllers,joins)))
            print("JOIN_RESULTS",[str(r) for r in results],flush=True)
            warmup=time.monotonic()
            while True:
                obs=[c.observe() for c in controllers]
                if min(o.observation.game_loop for o in obs)>=112: break
                if time.monotonic()-warmup>45: raise RuntimeError("simulation never started")
                time.sleep(.2)
            start=time.monotonic();loop=obs[0].observation.game_loop
            counts=[sum(u.unit_type==SCV and u.alliance==1 for u in o.observation.raw_data.units) for o in obs[:2]]
            assert all(o.observation.player_common.player_id==i+1 for i,o in enumerate(obs[:2]))
            assert all(not any(u.owner==2-i and u.display_type==1 for u in obs[i].observation.raw_data.units) for i in range(2)), "AI fog leaked"
            for i,c in enumerate(controllers[:2]):
                cc=next(u for u in obs[i].observation.raw_data.units if u.unit_type==CC and u.alliance==1)
                print("TRAIN",i,c.actions(pb.RequestAction(actions=[camera((100,100)),command(TRAIN_SCV,[cc.tag])])),flush=True)
            while time.monotonic()-start<35:
                time.sleep(5);obs=[c.observe() for c in controllers]
                wall=time.monotonic()-start;game=(obs[0].observation.game_loop-loop)/22.4
                now=[sum(u.unit_type==SCV and u.alliance==1 for u in o.observation.raw_data.units) for o in obs[:2]]
                print("SAMPLE",json.dumps(dict(wall=wall,game=game,ratio=game/wall,workers=now,loops=[o.observation.game_loop for o in obs])),flush=True)
            assert all(a>b for a,b in zip(now,counts)), (counts,now)
            assert .9<=game/wall<=1.1,(game,wall)
            assert all(not o.action_errors for o in obs),str(obs)
            print("NATIVE_MULTIPLAYER_OK two native raw players, realtime, offscreen workers, fog; NO third observer",flush=True)
        finally:
            for c in controllers:
                try:c.quit()
                except Exception as e: print("QUIT_ERROR",repr(e),flush=True)
                c.close()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=10)
if __name__=="__main__":main()
