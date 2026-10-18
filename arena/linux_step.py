"""Explicit headless Linux SC2 with the tested NativeArena step/pause executor.

This uses the installed Linux API engine, not the Windows/Proton GPU runtime.
Only process/bootstrap differs; actions, error attribution, pause checks, results
and replay handling are inherited from the native executor.
"""
from concurrent.futures import ThreadPoolExecutor

from pysc2.env import sc2_env
from pysc2.lib import features

from arena.native_engine import NativeArena
from arena.raw_engine import GameClock, headless_map_for_speed


class LinuxStepArena(NativeArena):
    def __init__(self, races=('terran','protoss'), seed=7, map_name='Acropolis', step_loops=8):
        if type(step_loops) is not int or not 1<=step_loops<=224:raise ValueError('step_loops must be 1..224')
        if len(races)!=2 or any(r not in ('terran','zerg','protoss') for r in races):
            raise ValueError('two explicit supported races required')
        self.races=list(races);self.realtime=False;self.step_loops=step_loops
        self.game_speed='faster';self.render=False;self.closed=False;self.env=None
        self.controllers=[];self.last=[];self.pool=ThreadPoolExecutor(max_workers=2)
        self.period=.1;self.next_poll=0;self.action_errors=0
        self.stepped_loops=self.paused_polls=self.pause_loop_checks=self.pause_loop_violations=0
        self.last_advance=True;self.last_step_loops=0;self.step_counts={}
        self._pending_surrender=self._terminal_result=self._surrender_replay=None
        self.surrender_report=None
        try:
            interface=features.AgentInterfaceFormat(use_raw_units=True,send_observation_proto=True,
                                                   action_space=sc2_env.ActionSpace.RAW)
            self.env=sc2_env.SC2Env(map_name=headless_map_for_speed(map_name,'faster'),
                players=[sc2_env.Agent(sc2_env.Race[r]) for r in races],
                agent_interface_format=[interface,interface],realtime=False,step_mul=step_loops,
                game_steps_per_episode=0,ensure_available_actions=False,score_index=-1,random_seed=seed)
            if self.env._realtime:raise RuntimeError('Linux step engine unexpectedly realtime')
            self.env.reset()
            self.controllers=list(self.env._controllers)
            infos=self._parallel(lambda c:c.game_info(),self.controllers)
            self.info=infos[0];self.size=(self.info.start_raw.map_size.x,self.info.start_raw.map_size.y)
            pings=self._parallel(lambda c:c.ping(),self.controllers)
            if len({(p.base_build,p.data_version) for p in pings})!=1:raise RuntimeError('Linux engine versions differ')
            self.version=pings[0].game_version
            self.configure_tech_tree(self.controllers[0].data_raw(),'Linux step '+self.version)
            self.last=self._parallel(lambda c:c.observe(),self.controllers)
            self.initial=self.snapshots()
            self.homes=[next(u.pos for u in w.my_units if u.type_id in (18,132,130,59,86,100,101)) for w in self.initial]
            self.configure_start_locations(infos)
            self.clock=GameClock(self.initial[0].game_loop)
            print('[linux step] READY; realtime=False; normal fog; engine='+self.version,flush=True)
        except BaseException:
            self.close();raise

    def close(self):
        if self.closed:return
        self.closed=True
        try:
            if self.env:self.env.close()
        finally:self.pool.shutdown(wait=True,cancel_futures=True)
