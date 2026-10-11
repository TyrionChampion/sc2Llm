"""Native GPU API players: 1v1 LLMs, or one LLM against a built-in computer.

Uses the existing raw snapshot/parser, placement queries and task/reflex contract.
No SC2Env, RGB readback, RequestStep, fog cheats or camera writes in this backend.
"""
import concurrent.futures
from dataclasses import replace
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import portpicker
from pysc2 import maps
from pysc2.lib import remote_controller
from s2clientprotocol import sc2api_pb2 as pb, common_pb2 as common, debug_pb2 as debug, error_pb2
from arena.native_runtime import ROOT, RUNTIME, GAME, prepare
from arena.raw_engine import RawArena, GameClock, CC
from arena.game_speed import map_for_speed, LOOPS_PER_SECOND as SPEED_LOOPS


def win(path): return "Z:"+str(path).replace("/", "\\")


BOT_DIFFICULTIES={name:pb.Difficulty.Value(value) for name,value in (
    ("very_easy","VeryEasy"),("easy","Easy"),("medium","Medium"),
    ("medium_hard","MediumHard"),("hard","Hard"),("harder","Harder"),("very_hard","VeryHard"))}


class NativeArena(RawArena):
    def __init__(self,map_name="Acropolis",names=("P1","P2"),seed=7,control_hz=10,difficulty=None,render=True,races=None,bot_race="terran",game_speed="faster"):
        if difficulty is not None and difficulty not in BOT_DIFFICULTIES:
            raise ValueError('unsupported built-in bot difficulty: '+str(difficulty))
        map_path=map_for_speed(map_name,game_speed)
        self.game_speed=game_speed;self.speed_verification=None
        count=1 if difficulty is not None else 2
        self.races=list(races or ['terran']*count)
        if len(self.races)!=count or any(r not in ('terran','zerg','protoss') for r in self.races):raise ValueError('explicit race per API player required')
        if bot_race not in ('terran','zerg','protoss'):raise ValueError('invalid bot race')
        race_enum={'terran':common.Terran,'zerg':common.Zerg,'protoss':common.Protoss}
        if len(names)!=count: raise ValueError('expected %d API player name(s)' % count)
        if not 1<=control_hz<=22.4: raise ValueError("control_hz must be 1..22.4")
        self.controllers=[]; self.process=None; self.output=None; self.ports=[]; self.lock=None
        self.pool=concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self.closed=False; self.action_errors=0; self._last_overlay=0
        self.period=1/control_hz; self.next_poll=0
        self._pending_surrender=None;self.surrender_report=None
        self._terminal_result=None;self._surrender_replay=None
        try:
            prepare()
            self.lock=open(RUNTIME/"native-match.lock","w")
            try: fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise RuntimeError("another native match is already running")
            self.directory=Path(tempfile.mkdtemp(prefix="match-",dir=RUNTIME))
            self.ports=[portpicker.pick_unused_port() for _ in range(1 if count==1 else 6)]
            api=self.ports[:count]; network=self.ports[count:]
            executable=win(GAME/"Versions/Base97579/SC2_x64.exe")
            batch_lines=["@echo off",f'cd /d "{win(GAME/"Support64")}"']
            for i,port in enumerate(api):
                temp=self.directory/f"p{i+1}";temp.mkdir()
                command=[executable,"-listen","127.0.0.1","-port",str(port),
                    "-dataDir",win(GAME)+"\\","-tempDir",win(temp)+"\\","-displayMode","0",
                    "-windowwidth","1600","-windowheight","900",
                    "-windowx",str(80+180*i),"-windowy",str(80+120*i),"-verbose"]
                if not render: command.append("-headlessNoRender")
                batch_lines.append(('start "" /b ' if i<count-1 else '')+subprocess.list2cmdline(command))
            batch=self.directory/"match.cmd";batch.write_bytes(("\r\n".join(batch_lines)+"\r\n").encode())
            env=os.environ.copy();env["WINEDEBUG"]="-all"
            self.output=open(self.directory/"process.log","w")
            self.process=subprocess.Popen([sys.executable,str(ROOT/"arena/native_runtime.py"),
                "cmd.exe","/c",win(batch)],env=env,stdout=self.output,stderr=subprocess.STDOUT)
            print('[native] realtime=True; %s; opponent=%s; normal fog' % (('%d GPU participant window(s)' % count) if render else 'headless validation',difficulty or 'LLM'),flush=True)
            print('[native] process logs:',self.directory,flush=True)
            for port in api:
                controller=remote_controller.RemoteController("127.0.0.1",port,self,timeout_seconds=150)
                self.controllers.append(controller)
            pings=[c.ping() for c in self.controllers]
            if len({(p.base_build,p.data_version) for p in pings})!=1:
                raise RuntimeError("native clients have different engine/data builds")
            self.version=pings[0].game_version
            print('[native] engine',self.version,flush=True)
            print("[native] requested game speed:",game_speed,"map:",map_path,flush=True)
            if not map_path.is_file(): raise FileNotFoundError(map_path)
            create=pb.RequestCreateGame(realtime=True,disable_fog=False,random_seed=seed,
                local_map=pb.LocalMap(map_path=win(map_path)))
            for name in names: create.player_setup.add(type=pb.Participant,player_name=name)
            if difficulty is not None:
                create.player_setup.add(type=pb.Computer,race=race_enum[bot_race],
                                        difficulty=BOT_DIFFICULTIES[difficulty],player_name='SC2 '+difficulty)
            self.controllers[0].create_game(create)
            joins=[]
            for player_index,name in enumerate(names):
                join=pb.RequestJoinGame(race=race_enum[self.races[player_index]],player_name=name,
                    options=pb.InterfaceOptions(raw=True,score=True,raw_affects_selection=False))
                if network:
                    join.server_ports.game_port=network[0];join.server_ports.base_port=network[1]
                    join.client_ports.add(game_port=network[2],base_port=network[3])
                joins.append(join)
            results=self._parallel(lambda pair:pair[0].join_game(pair[1]),list(zip(self.controllers,joins)))
            if [r.player_id for r in results]!=list(range(1,count+1)): raise RuntimeError('unexpected player assignment: '+str(results))
            infos=self._parallel(lambda c:c.game_info(),self.controllers)
            self.info=infos[0]
            self.size=(self.info.start_raw.map_size.x,self.info.start_raw.map_size.y)
            self.configure_tech_tree(self.controllers[0].data_raw(),self.version)
            warmup=time.monotonic()
            while True:
                self.last=self._parallel(lambda c:c.observe(),self.controllers)
                if min(o.observation.game_loop for o in self.last)>=112: break
                if time.monotonic()-warmup>45: raise RuntimeError("native realtime simulation did not start")
                time.sleep(.1)
            self.initial=self.snapshots()
            self.homes=[next(u.pos for u in w.my_units if u.type_id in (18,132,130,59,86,100,101)) for w in self.initial]
            self.configure_start_locations(infos)
            if game_speed!="faster":
                started=time.monotonic();first_loop=self.initial[0].game_loop
                while time.monotonic()-started<8:
                    time.sleep(.1)
                    self.last=self._parallel(lambda c:c.observe(),self.controllers)
                elapsed=time.monotonic()-started
                observed=[(o.observation.game_loop-first_loop)/elapsed for o in self.last]
                self.speed_verification=dict(requested=game_speed,wall_s=elapsed,
                    loops_per_wall_second=observed,expected=SPEED_LOOPS[game_speed])
                print("[native speed verification]",self.speed_verification,flush=True)
                if any(not .85*SPEED_LOOPS[game_speed]<=rate<=1.15*SPEED_LOOPS[game_speed] for rate in observed):
                    raise RuntimeError(game_speed+" realtime speed verification failed; refusing silently faster/step-mode match")
                self.initial=self.snapshots()
            self.clock=GameClock(self.initial[0].game_loop,expected_loops_per_second=SPEED_LOOPS[game_speed])
            print('[native] READY; AI observations are player-specific, display frames stay inside SC2',flush=True)
        except BaseException:
            self.close();raise

    @property
    def running(self): return self.process is not None and self.process.poll() is None
    def _parallel(self,fn,values): return list(self.pool.map(fn,values))
    def proto(self,index): return self.last[index]
    @property
    def ended(self): return self._terminal_result is not None or any(bool(o.player_result) for o in self.last)
    def result(self):
        return dict(self._terminal_result) if self._terminal_result is not None else super().result()
    @property
    def match_control_pending(self):return self._pending_surrender is not None
    def __exit__(self,*args): self.close()

    def step(self,batches):
        if self.ended: raise RuntimeError('cannot step a finished native match')
        if len(batches)!=len(self.controllers): raise ValueError('action batch count must match API players')
        if self.match_control_pending:return self._finish_surrender()
        before=self.snapshots()
        pending=[[] for _ in batches]; response_failures=[[] for _ in batches]; sent_at=[None for _ in batches]
        def send(pair):
            i,batch=pair
            if not batch: return
            sent_at[i]=time.time()
            response=self.controllers[i].actions(pb.RequestAction(actions=batch))
            if response is None: return  # Native terminal response will be read by observe.
            if len(response.result)!=len(batch):
                raise RuntimeError("action response count mismatch: %d actions / %d results" % (len(batch),len(response.result)))
            for n,(action,code) in enumerate(zip(batch,response.result)):
                if code!=error_pb2.Success:
                    response_failures[i].append((n,code))
                    raw=action.action_raw.unit_command
                    for tag in raw.unit_tags:
                        pending[i].append((tag,raw.ability_id,code))
                    print('[native action-response] P%d %s' % (i+1,error_pb2.ActionResult.Name(code)),flush=True)
        self._parallel(send,list(enumerate(batches)))
        time.sleep(max(0,self.next_poll-time.monotonic()))
        self.next_poll=time.monotonic()+self.period
        self.last=self._parallel(lambda c:c.observe(),self.controllers)
        for i,errors in enumerate(pending):
            existing={(e.unit_tag,e.ability_id,e.result) for e in self.last[i].action_errors}
            for tag,ability,code in errors:
                if (tag,ability,code) not in existing:
                    self.last[i].action_errors.add(unit_tag=tag,ability_id=ability,result=code)
        worlds=self.snapshots();self.clock.update(worlds[0].game_loop)
        for i,w in enumerate(worlds):
            for tag,ability,code in w.errors:
                self.action_errors+=1
                print('[action-error] P%d tag=%d ability=%d result=%s' %
                    (i+1,tag,ability,error_pb2.ActionResult.Name(code)),flush=True)
        return self.attach_action_feedback(before,worlds,batches,response_failures,sent_at)

    def request_surrender(self,index,reason,request_id=None):
        if type(index) is not int or not 0<=index<len(self.controllers):raise ValueError('invalid surrender player')
        if not isinstance(reason,str) or not reason.strip() or len(reason)>500:raise ValueError('surrender needs a reason of 1..500 characters')
        if self.ended:raise ValueError('match already ended')
        if self.match_control_pending:raise ValueError('another match-ending request was already accepted')
        self._pending_surrender=dict(player=index+1,reason=reason.strip(),request_id=request_id,
                                     requested_game_loop=self.proto(index).observation.game_loop,state='requested')
        print('[surrender requested]',self._pending_surrender,flush=True)

    def _finish_surrender(self):
        entry=self._pending_surrender.copy();index=entry['player']-1
        self.surrender_report=entry;worlds=self.snapshots();self._action_bindings={}
        # Save BEFORE leave: the leaver transitions to launched and cannot save/observe afterward.
        try:
            replay=self.controllers[index].save_replay()
            if not replay:raise RuntimeError('empty pre-surrender replay')
        except Exception as error:
            entry.update(state='failed',stage='save_before_leave',error=str(error),leave_sent=False)
            self._pending_surrender=None
            print('[surrender failed; game continues]',entry,flush=True)
            event=dict(kind='surrender_failed',player=entry['player'],reason=entry['reason'],error=str(error),
                       stage='save_before_leave',leave_sent=False,request_id=entry['request_id'])
            return [replace(w,feedback=(event,)) for w in worlds]
        self._surrender_replay=replay;entry['replay_scope']='before_leave'
        try:
            response=self.controllers[index].leave()
            if response is None:raise RuntimeError('leave response missing')
        except Exception as error:
            entry.update(state='unknown',stage='leave',error=str(error),leave_sent=True)
            print('[surrender uncertain; no win/loss fabricated]',entry,flush=True)
            raise RuntimeError('surrender leave outcome uncertain: '+str(error)) from error
        entry.update(state='acknowledged',leave_acknowledged=True,leaver_status=self.controllers[index].status.name)
        expected={entry['player']:'Defeat',2 if entry['player']==1 else 1:'Victory'}
        observed={};entry['warnings']=[]
        for other,c in enumerate(self.controllers):
            if other==index:continue
            try:
                deadline=time.monotonic()+3
                while True:
                    observation=c.observe();self.last[other]=observation
                    observed={r.player_id:pb.Result.Name(r.result) for r in observation.player_result}
                    if observed or time.monotonic()>=deadline:break
                    time.sleep(.1)
                if observed:
                    if observed!=expected:raise RuntimeError('engine result contradicts concession: '+str(observed))
                    try:
                        terminal_replay=c.save_replay()
                        if not terminal_replay:raise RuntimeError('empty terminal replay')
                        self._surrender_replay=terminal_replay;entry['replay_scope']='terminal_from_opponent'
                    except Exception as error:
                        entry['warnings'].append('Terminal replay unavailable; preserving pre-leave replay: '+str(error))
            except Exception as error:
                if observed and observed!=expected:
                    entry.update(state='result_mismatch',engine_results=observed,error=str(error));raise
                entry['warnings'].append('Opponent terminal observation unavailable: '+str(error))
        if observed:
            entry.update(result_source='engine_player_result',engine_results=observed)
        else:
            entry.update(result_source='acknowledged_concession_rule',engine_results=None)
            entry['warnings'].append('No post-leave engine result available; match rules score acknowledged voluntary surrender as a loss.')
        self._terminal_result=expected;self._pending_surrender=None
        entry.update(state='completed',results=expected,replay_bytes=len(self._surrender_replay))
        worlds=self.snapshots();self.clock.update(max(w.game_loop for w in worlds))
        print('[surrender completed]',entry,flush=True)
        event=dict(kind='surrender_completed',**entry)
        return [replace(w,feedback=(event,)) for w in worlds]

    def timing(self):
        timing=self.clock.report()
        timing['game_speed']=self.game_speed
        timing['control_hz']=timing.pop('fps')
        timing['display_fps']='native DXVK HUD; not API polling rate'
        return timing

    def overlay(self,rows):
        """DebugDraw text only. Never enables debug game_state or other cheats."""
        for c,(text,warning) in zip(self.controllers,rows):
            draw=debug.DebugCommand()
            entry=draw.draw.text.add(text=text,size=18)
            entry.virtual_pos.x=.025;entry.virtual_pos.y=.07
            entry.color.r=255 if warning else 120;entry.color.g=100 if warning else 255;entry.color.b=100 if warning else 180
            c.debug([draw])

    def rgb(self): raise RuntimeError('native windows render directly; RGB readback is disabled')
    def save_replay(self,path):
        replay=self._surrender_replay if self._surrender_replay is not None else self.controllers[0].save_replay()
        Path(path).write_bytes(replay)

    def close(self):
        if self.closed: return
        self.closed=True
        for c in self.controllers:
            try: c.quit()
            except Exception as e: print('[native close]',type(e).__name__,str(e)[:150],flush=True)
            c.close()
        if self.process:
            try:self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                print('[native close] terminating owned launcher after timeout',flush=True)
                self.process.terminate();self.process.wait(timeout=10)
        self.pool.shutdown(wait=False,cancel_futures=True)
        if self.output:self.output.close()
        for port in self.ports:portpicker.return_port(port)
        if self.lock:self.lock.close()
