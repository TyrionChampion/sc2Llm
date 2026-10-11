"""Smoke test v2: minimal steps, log to file, realtime, verbose-friendly.

Answers: does headless SC2 4.10 + EGL deliver real RGB frames?
Keep it SHORT (10 steps) so a failure is fast and observable.
"""
import os
import sys
import time

import numpy as np
from absl import app
from absl import logging

from pysc2.env import sc2_env
from pysc2.lib import features
from pysc2.agents import scripted_agent

MAP_NAME = "Simple64"
RGB_SCREEN = (256, 192)
RGB_MINIMAP = (128, 128)
MAX_STEPS = 10


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def main(argv):
    del argv
    log("SC2PATH = %s" % os.environ.get("SC2PATH"))
    log("map = %s, steps = %d" % (MAP_NAME, MAX_STEPS))

    rgb_dims = features.Dimensions(screen=RGB_SCREEN, minimap=RGB_MINIMAP)
    aif = features.AgentInterfaceFormat(
        rgb_dimensions=rgb_dims,
        action_space=sc2_env.ActionSpace.RGB,
    )

    log("creating env...")
    t0 = time.time()
    env = sc2_env.SC2Env(
        map_name=MAP_NAME,
        players=[sc2_env.Agent(sc2_env.Race.terran),
                 sc2_env.Agent(sc2_env.Race.terran)],
        agent_interface_format=aif,
        step_mul=8,
        game_steps_per_episode=1000,
        visualize=False,
        score_index=-1,
    )
    log("env created in %.1fs" % (time.time() - t0))

    log("reset()...")
    t1 = time.time()
    obs = env.reset()
    log("reset done in %.1fs, agents=%d" % (time.time() - t1, len(obs)))

    got_rgb = False
    for step in range(MAX_STEPS):
        ts = time.time()
        actions = [[], []]  # no-op both sides: just want frames
        obs = env.step(actions)
        dt = time.time() - ts
        rgb = obs[0].observation.get("rgb_screen")
        log("step %d done in %.1fs, rgb=%s, reward=%s"
            % (step, dt, None if rgb is None else np.asarray(rgb).shape,
               [o.reward for o in obs]))

        if rgb is not None and not got_rgb:
            arr = np.asarray(rgb)
            got_rgb = True
            log(">>> RGB SCREEN RECEIVED <<<")
            log("    shape      : %s" % (arr.shape,))
            log("    dtype      : %s" % arr.dtype)
            log("    min/max    : %s / %s" % (arr.min(), arr.max()))
            log("    unique vals: %d" % len(np.unique(arr)))
            log("    all zeros? : %s" % bool((arr == 0).all()))
            try:
                from PIL import Image
                Image.fromarray(arr.astype(np.uint8)).save("logs/rgb_frame.png")
                log("    saved      : logs/rgb_frame.png")
            except Exception as e:
                log("    (png skipped: %s)" % e)

    env.close()
    log("closed. RESULT: rgb_received=%s" % got_rgb)
    return 0 if got_rgb else 1


if __name__ == "__main__":
    logging.set_verbosity(logging.INFO)
    app.run(main)
