"""RGB rendering smoke test — the decisive check.

Run: ./run_sc2.sh .venv311/bin/python smoke_rgb3.py
"""
import os
import sys
import time

import numpy as np
from absl import app

from pysc2.env import sc2_env
from pysc2.lib import actions
from pysc2.lib import features

MAX_STEPS = 12


def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def main(argv):
    del argv
    log("SC2PATH=%s" % os.environ.get("SC2PATH"))
    log("LD_LIBRARY_PATH=%s" % os.environ.get("LD_LIBRARY_PATH"))

    rgb = features.Dimensions(screen=(256, 192), minimap=(128, 128))
    aif = features.AgentInterfaceFormat(
        rgb_dimensions=rgb, action_space=sc2_env.ActionSpace.RGB)

    env = sc2_env.SC2Env(
        map_name="Simple64",
        players=[sc2_env.Agent(sc2_env.Race.terran),
                 sc2_env.Agent(sc2_env.Race.terran)],
        agent_interface_format=aif,
        step_mul=8, game_steps_per_episode=500,
        visualize=False, score_index=-1)
    log("env created")

    obs = env.reset()
    log("reset OK")

    noop = actions.FUNCTIONS.no_op()
    got = False
    frames = 0
    t0 = time.time()
    for i in range(MAX_STEPS):
        obs = env.step([noop, noop])
        o = obs[0].observation
        rgb_screen = o.get("rgb_screen")
        if rgb_screen is None:
            log("step %d: rgb_screen=None  keys=%s" % (i, sorted(o.keys()))[:200])
            continue
        arr = np.asarray(rgb_screen)
        frames += 1
        if not got:
            got = True
            log(">>> RGB SCREEN RECEIVED <<<")
            log("    shape      : %s" % (arr.shape,))
            log("    dtype      : %s" % arr.dtype)
            log("    min/max    : %s / %s" % (arr.min(), arr.max()))
            log("    unique     : %d" % len(np.unique(arr)))
            log("    all zeros? : %s" % bool((arr == 0).all()))
            try:
                from PIL import Image
                Image.fromarray(arr.astype(np.uint8)).save("logs/frame0.png")
                log("    saved      : logs/frame0.png")
            except Exception as e:
                log("    png skipped (%s)" % e)
        if i == MAX_STEPS - 1:
            try:
                from PIL import Image
                Image.fromarray(arr.astype(np.uint8)).save("logs/frame_last.png")
                log("    last frame saved: logs/frame_last.png")
            except Exception:
                pass

    log("done: %d/%d steps had rgb, %.1fs" % (frames, MAX_STEPS, time.time() - t0))
    env.close()
    log("RESULT rgb_received=%s" % got)
    return 0 if got else 1


if __name__ == "__main__":
    app.run(main)
