"""Skill layer smoke test against a REAL game observation.

Verifies: parse_world() reads feature_units correctly, and each skill turns
into a legal pysc2 FunctionCall. This is the precondition before any LLM work.
"""
import numpy as np
from absl import app
from pysc2.env import sc2_env
from pysc2.lib import features

from arena.core import parse_world, SkillSet


def main(argv):
    aif = features.AgentInterfaceFormat(
        feature_dimensions=features.Dimensions(screen=(64, 64), minimap=(64, 64)),
        rgb_dimensions=features.Dimensions(screen=(256, 192), minimap=(128, 128)),
        use_feature_units=True,
        use_unit_counts=True,
        use_camera_position=True,
        action_space=sc2_env.ActionSpace.FEATURES)
    env = sc2_env.SC2Env(
        map_name="Acropolis",
        players=[sc2_env.Agent(sc2_env.Race.terran),
                 sc2_env.Bot(sc2_env.Race.protoss, sc2_env.Difficulty.easy)],
        agent_interface_format=aif, step_mul=8,
        game_steps_per_episode=0, visualize=False, score_index=-1)
    obs = env.reset()

    # run a few steps so the base is fully spawned
    from pysc2.lib import actions
    for _ in range(3):
        obs = env.step([actions.FUNCTIONS.no_op(), []])

    w = parse_world(obs[0].observation)
    print("=== WORLD PARSED ===", flush=True)
    print("summary:", w.summary(), flush=True)
    print("camera:", w.camera, "size:", w.camera_size, flush=True)
    print("my_units:", len(w.my_units),
          "minerals:", len(w.mineral_patches),
          "enemies:", len(w.enemy_units), flush=True)
    print("townhalls:", [u.name for u in w.townhalls()], flush=True)
    print("workers   :", len(w.workers()), flush=True)
    for u in w.my_units[:6]:
        print("   %-14s (%2d,%2d) sel=%s done=%s"
              % (u.name, u.x, u.y, u.selected, u.done), flush=True)

    print()
    print("=== SKILLS ===", flush=True)
    skills = SkillSet()
    print("registered:", skills.names(), flush=True)
    for name, kw in [("gather", {}),
                     ("train_worker", {}),
                     ("supply_depot", {}),
                     ("barracks", {}),
                     ("train_unit", {"unit": "marine"}),
                     ("attack_nearest", {}),
                     ("move_out", {}),
                     ("expand", {})]:
        sk = skills.make(name, **kw)
        try:
            res = sk.act(skills.ctx, w)
        except Exception as e:
            print("  %-16s EXCEPTION %s: %s" % (name, type(e).__name__, e),
                  flush=True)
            continue
        if hasattr(res, "ok"):
            print("  %-16s ok=%-5s %s" % (name, res.ok, res.msg), flush=True)
        else:
            fn = res.function
            from pysc2.lib import actions as A
            fname = next((f.name for f in A.FUNCTIONS if f.id == fn), str(fn))
            print("  %-16s -> %s args=%s"
                  % (name, fname, res.arguments), flush=True)

    env.close()
    print("DONE-OK", flush=True)


if __name__ == "__main__":
    app.run(main)
