"""Smoke test: bot-vs-bot SC2 game + verify RGB rendering produces real pixels.

This answers ONE question with evidence: does the headless Linux SC2 4.10
actually deliver rendered RGB frames to pysc2 on this machine (via EGL)?

Run:
    SC2PATH=<workspace>/engine/StarCraftII .venv311/bin/python smoke_rgb.py
"""
import os
import sys
import time

import numpy as np
from absl import app

from pysc2.env import sc2_env
from pysc2.lib import features
from pysc2.agents import scripted_agent

MAP_NAME = "Simple64"

RGB_SCREEN = (256, 192)
RGB_MINIMAP = (128, 128)


def main(argv):
    del argv  # absl passes argv; unused here.
    print("SC2PATH =", os.environ.get("SC2PATH"), flush=True)
    print("map     =", MAP_NAME, flush=True)

    rgb_dims = features.Dimensions(screen=RGB_SCREEN, minimap=RGB_MINIMAP)
    aif = features.AgentInterfaceFormat(
        rgb_dimensions=rgb_dims,
        action_space=sc2_env.ActionSpace.RGB,
    )

    env = sc2_env.SC2Env(
        map_name=MAP_NAME,
        players=[sc2_env.Agent(sc2_env.Race.terran),
                 sc2_env.Agent(sc2_env.Race.terran)],
        agent_interface_format=aif,
        step_mul=8,
        game_steps_per_episode=2000,
        visualize=False,
        score_index=-1,
    )

    bots = [scripted_agent.MoveToBeacon(), scripted_agent.CollectMineralShards()]
    obs = env.reset()
    print("reset OK; agents =", len(obs), flush=True)

    got_rgb = False
    rgb_shape = None
    frames_saved = 0
    t0 = time.time()

    for step in range(60):
        actions = [b.step(o.observation) if i < len(bots) else []
                   for i, o in enumerate(obs)]
        obs = env.step(actions)
        if step % 10 == 0:
            print("  step %3d  reward=%s" % (step, [o.reward for o in obs]),
                  flush=True)

        rgb = obs[0].observation.get("rgb_screen")
        if rgb is not None and not got_rgb:
            arr = np.asarray(rgb)
            got_rgb = True
            rgb_shape = arr.shape
            print("\n>>> RGB SCREEN RECEIVED <<<", flush=True)
            print("    shape      :", arr.shape, flush=True)
            print("    dtype      :", arr.dtype, flush=True)
            print("    min/max    :", arr.min(), arr.max(), flush=True)
            print("    unique vals:", len(np.unique(arr)), flush=True)
            print("    all zeros? :", bool((arr == 0).all()), flush=True)
            try:
                from PIL import Image
                Image.fromarray(arr.astype(np.uint8)).save("logs/rgb_frame.png")
                print("    saved      : logs/rgb_frame.png", flush=True)
            except Exception as e:
                print("    (PIL unavailable, skipped png: %s)" % e, flush=True)

        if step == 2 and obs[0].observation.get("rgb_screen") is not None:
            mini = obs[0].observation.get("rgb_minimap")
            if mini is not None:
                print("    minimap    :", np.asarray(mini).shape, flush=True)

    env.close()
    elapsed = time.time() - t0

    print("\n=== RESULT ===", flush=True)
    print("rgb_screen received :", got_rgb, flush=True)
    print("rgb_screen shape    :", rgb_shape, flush=True)
    print("wall time           : %.1fs for 60 steps" % elapsed, flush=True)
    return 0 if got_rgb else 1


if __name__ == "__main__":
    app.run(main)
