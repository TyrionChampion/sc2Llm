"""Native GPU spectator smoke test: two built-in bots, no LLM API calls.

Run with ./run_sc2.sh .venv311/bin/python native_observer.py --seconds 600
Uses a project-owned GE-Proton prefix and SC2Data copy. Original USB is read-only.
"""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from absl import flags
from pysc2.lib import remote_controller
from s2clientprotocol import sc2api_pb2 as pb, common_pb2 as common
from arena.native_runtime import ROOT, RUNTIME, GAME, prepare


def win(path):
    return "Z:"+str(path).replace("/", "\\")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds",type=float,default=600,help="maximum observation time after warmup")
    args=parser.parse_args()
    if args.seconds<30: parser.error("at least 30 seconds needed for realtime validation")
    flags.FLAGS([sys.argv[0]])
    import fcntl
    lock=open(RUNTIME/"native-match.lock","w")
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    prepare()
    port=18567
    command=[sys.executable,str(ROOT/"arena/native_runtime.py"),win(GAME/"Versions/Base97579/SC2_x64.exe"),
        "-listen","127.0.0.1","-port",str(port),"-dataDir",win(GAME)+"\\",
        "-tempDir",win(RUNTIME/"tmp")+"\\","-displayMode","0","-windowwidth","1600",
        "-windowheight","900","-windowx","80","-windowy","80","-verbose"]
    env=os.environ.copy(); env["WINEDEBUG"]="-all"
    report={"mode":"native single API player vs Medium bot probe, NOT LLM", "realtime":True,
            "runner":"GE-Proton11-7", "map":"AcropolisLE", "samples":[],"validated":False}
    controller=None
    with open(ROOT/"logs/native_bot_probe_process.log","w") as output:
        process=subprocess.Popen(command,stdout=output,stderr=subprocess.STDOUT,env=env)
        try:
            class Proc:
                @property
                def running(self): return process.poll() is None
            controller=remote_controller.RemoteController("127.0.0.1",port,Proc(),timeout_seconds=150)
            ping=controller.ping(); report["engine_version"]=ping.game_version
            print("NATIVE_PING",ping,flush=True)
            create=pb.RequestCreateGame(realtime=True,random_seed=7,
                local_map=pb.LocalMap(map_path=win(ROOT/"engine/StarCraftII/Maps/Ladder2019Season3/AcropolisLE.SC2Map")))
            create.player_setup.add(type=pb.Participant,player_name="API probe")
            create.player_setup.add(type=pb.Computer,race=common.Terran,difficulty=pb.Medium)
            print("CREATE",controller.create_game(create),flush=True)
            print("JOIN",controller.join_game(pb.RequestJoinGame(race=common.Terran,
                options=pb.InterfaceOptions(raw=True,score=True))),flush=True)
            # Native SC2 has a pre-game countdown. Measure only after simulation starts.
            warmup=time.monotonic()
            while True:
                first=controller.observe()
                if first.observation.game_loop>=112: break
                if time.monotonic()-warmup>45: raise RuntimeError("simulation did not start within 45 seconds")
                time.sleep(.25)
            from arena.raw_engine import command, TRAIN_SCV
            base=next(u for u in first.observation.raw_data.units if u.alliance==1 and u.unit_type==18)
            initial_workers=sum(u.alliance==1 and u.unit_type==45 for u in first.observation.raw_data.units)
            response=controller.actions(pb.RequestAction(actions=[command(TRAIN_SCV,[base.tag])]))
            print('BOT_PROBE_TRAIN_RESPONSE',response,flush=True)
            assert list(response.result)==[1],response
            start=time.monotonic(); initial_loop=first.observation.game_loop
            owners=dict(Counter(u.owner for u in first.observation.raw_data.units))
            report.update(observer_id=first.observation.player_common.player_id,owners=owners,
                          warmup_wall_s=time.monotonic()-warmup)
            if report["observer_id"]!=1 or 1 not in owners:
                raise RuntimeError("not API player 1: "+str(report))
            print("OBSERVER_READY",json.dumps({k:v for k,v in report.items() if k!="samples"}),flush=True)
            while time.monotonic()-start<args.seconds:
                time.sleep(5)
                obs=controller.observe(); wall=time.monotonic()-start
                game=(obs.observation.game_loop-initial_loop)/22.4
                sample=dict(wall_s=wall,game_s=game,ratio=game/wall,
                    units=len(obs.observation.raw_data.units),
                    camera=[obs.observation.raw_data.player.camera.x,obs.observation.raw_data.player.camera.y])
                report["samples"].append(sample)
                print("NATIVE_OBSERVER_SAMPLE",json.dumps(sample),flush=True)
                if wall>=30 and not report["validated"]:
                    if not .9<=game/wall<=1.1: raise RuntimeError("realtime drift: "+str(sample))
                    workers=sum(u.alliance==1 and u.unit_type==45 for u in obs.observation.raw_data.units)
                    assert workers>initial_workers,(initial_workers,workers)
                    print('NATIVE_BOT_PROBE_OK',initial_workers,workers,flush=True)
                    report["validated"]=True
                    print("NATIVE_OBSERVER_REALTIME_OK (display FPS must be measured separately)",flush=True)
                    (ROOT/"logs/native_bot_probe_result.json").write_text(json.dumps(report,indent=2))
                if obs.player_result:
                    report["result"]={p.player_id:pb.Result.Name(p.result) for p in obs.player_result}
                    break
            report["end_reason"]="result" if "result" in report else "time limit"
        except BaseException as error:
            report["end_reason"]=type(error).__name__+": "+str(error)
            print("NATIVE_OBSERVER_STOP",report["end_reason"],flush=True)
            raise
        finally:
            (ROOT/"logs/native_bot_probe_result.json").write_text(json.dumps(report,indent=2))
            if controller:
                try: controller.quit()
                except Exception as error: print("QUIT_ERROR",repr(error),flush=True)
                controller.close()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                print("Stopping owned launcher after quit timeout",flush=True)
                process.terminate(); process.wait(timeout=10)

if __name__=="__main__": main()
