"""Skill layer test: pump each skill for several game steps and observe.

Skills need multiple ticks (camera movement is rate-limited), so a single
act() call often just steers. This verifies the full multi-tick behaviour.
"""
from absl import app
from pysc2.env import sc2_env
from pysc2.lib import actions
from pysc2.lib import features

from arena.core import parse_world, SkillSet


def make_env(map_name="Acropolis"):
    aif = features.AgentInterfaceFormat(
        feature_dimensions=features.Dimensions(screen=(64, 64), minimap=(64, 64)),
        rgb_dimensions=features.Dimensions(screen=(256, 192), minimap=(128, 128)),
        use_feature_units=True,
        use_unit_counts=True,
        use_camera_position=True,
        action_space=sc2_env.ActionSpace.FEATURES)
    return sc2_env.SC2Env(
        map_name=map_name,
        players=[sc2_env.Agent(sc2_env.Race.terran),
                 sc2_env.Bot(sc2_env.Race.protoss, sc2_env.Difficulty.easy)],
        agent_interface_format=aif, step_mul=8,
        game_steps_per_episode=0, visualize=False, score_index=-1)


def run_skill(env, skills, obs, name, kwargs, max_steps=12):
    """Pump one skill for up to max_steps, return (obs, log lines)."""
    lines = []
    skill = skills.make(name, **kwargs)
    for i in range(max_steps):
        w = parse_world(obs[0].observation)
        res = skill.act(skills.ctx, w)
        if hasattr(res, "ok"):          # SkillResult: could not act
            action = actions.FUNCTIONS.no_op()
            lines.append("  t%02d FAIL(%s)" % (i, res.msg))
            obs = env.step([action, []])
            # a pure failure won't change on its own; stop early
            break
        action = res
        fname = next((f.name for f in actions.FUNCTIONS if f.id == action.function),
                     str(action.function))
        lines.append("  t%02d %s %s" % (i, fname, action.arguments))
        obs = env.step([action, []])
    return obs, lines


def main(argv):
    env = make_env()
    obs = env.reset()
    for _ in range(3):
        obs = env.step([actions.FUNCTIONS.no_op(), []])
    skills = SkillSet()
    w = parse_world(obs[0].observation)
    print("起始状态: %s" % w.summary(), flush=True)
    print("相机: %s size=%s" % (w.camera, w.camera_size), flush=True)
    print(flush=True)

    for name, kw in [("gather", {}),
                     ("train_worker", {}),
                     ("supply_depot", {}),
                     ("barracks", {}),
                     ("train_unit", {"unit": "marine"}),
                     ("move_out", {})]:
        print("=== %s %s ===" % (name, kw or ""), flush=True)
        obs, lines = run_skill(env, skills, obs, name, kw)
        for ln in lines:
            print(ln, flush=True)
        w = parse_world(obs[0].observation)
        print("  -> %s" % w.summary(), flush=True)
        print(flush=True)

    env.close()
    print("DONE-OK", flush=True)


if __name__ == "__main__":
    app.run(main)
