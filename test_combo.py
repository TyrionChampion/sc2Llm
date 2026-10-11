"""Combination test: call skills in sequence like a real agent would.

This is the honest end-to-end check of the skill layer: does the economy
actually grow, do buildings appear, does supply go up?
"""
from absl import app
from pysc2.env import sc2_env
from pysc2.lib import actions, features

from arena.core import parse_world, SkillSet


def main(argv):
    aif = features.AgentInterfaceFormat(
        feature_dimensions=features.Dimensions(screen=(64, 64), minimap=(64, 64)),
        rgb_dimensions=features.Dimensions(screen=(256, 192), minimap=(128, 128)),
        use_feature_units=True, use_camera_position=True,
        action_space=sc2_env.ActionSpace.FEATURES)
    env = sc2_env.SC2Env(
        map_name="Acropolis",
        players=[sc2_env.Agent(sc2_env.Race.terran),
                 sc2_env.Bot(sc2_env.Race.protoss, sc2_env.Difficulty.easy)],
        agent_interface_format=aif, step_mul=8,
        game_steps_per_episode=0, visualize=False, score_index=-1)
    obs = env.reset()
    for _ in range(3):
        obs = env.step([actions.FUNCTIONS.no_op(), []])

    skills = SkillSet()
    w = parse_world(obs[0].observation)
    print("START %s" % w.summary(), flush=True)

    # commands pumped in order; each entry runs for `n` ticks
    plan = [("gather", 4), ("train_worker", 4), ("gather", 6),
            ("supply_depot", 8), ("train_worker", 3), ("supply_depot", 8)]
    for name, n in plan:
        sk = skills.make(name)
        for i in range(n):
            w = parse_world(obs[0].observation)
            res = sk.act(skills.ctx, w)
            if hasattr(res, "ok"):
                print("  %-14s t%d FAIL(%s)" % (name, i, res.msg), flush=True)
                obs = env.step([actions.FUNCTIONS.no_op(), []])
            else:
                fn = next((f.name for f in actions.FUNCTIONS
                           if f.id == res.function), "?")
                obs = env.step([res, []])
                if i == n - 1:
                    print("  %-14s 最后动作 %s" % (name, fn), flush=True)
        w = parse_world(obs[0].observation)
        print("  [%s 后] %s" % (name, w.summary()), flush=True)

    w = parse_world(obs[0].observation)
    print()
    print("FINAL %s" % w.summary(), flush=True)
    env.save_replay(__import__("os").path.abspath("Replays"))
    env.close()
    print("DONE-OK", flush=True)


if __name__ == "__main__":
    app.run(main)
