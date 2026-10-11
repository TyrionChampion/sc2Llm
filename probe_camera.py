"""Measure how camera_position, feature_units coords and move_camera relate.

We guessed that feature_units x/y are world coords and camera_position is the
top-left of the view. The skills got stuck steering, so measure it instead.
"""
from absl import app
from pysc2.env import sc2_env
from pysc2.lib import actions
from pysc2.lib import features

from arena.core import parse_world


def make_env():
    aif = features.AgentInterfaceFormat(
        feature_dimensions=features.Dimensions(screen=(64, 64), minimap=(64, 64)),
        use_feature_units=True,
        use_camera_position=True,
        action_space=sc2_env.ActionSpace.FEATURES)
    return sc2_env.SC2Env(
        map_name="Acropolis",
        players=[sc2_env.Agent(sc2_env.Race.terran),
                 sc2_env.Bot(sc2_env.Race.protoss, sc2_env.Difficulty.easy)],
        agent_interface_format=aif, step_mul=8,
        game_steps_per_episode=0, visualize=False, score_index=-1)


def probe(obs, label):
    w = parse_world(obs[0].observation)
    th = w.townhalls()
    th_s = ("%s(%d,%d)" % (th[0].name, th[0].x, th[0].y)) if th else "none"
    print("%-22s camera=%-12s size=%-9s townhall=%-22s patches=%d"
          % (label, w.camera, w.camera_size, th_s, len(w.mineral_patches)),
          flush=True)
    return w


def main(argv):
    env = make_env()
    obs = env.reset()
    for _ in range(3):
        obs = env.step([actions.FUNCTIONS.no_op(), []])
    probe(obs, "after reset")

    print("\n--- 测试 move_camera 坐标映射 ---", flush=True)
    for target in ([0, 0], [20, 20], [40, 40], [60, 60]):
        print("发送 move_camera(%s)" % target, flush=True)
        obs = env.step([actions.FunctionCall(
            actions.FUNCTIONS.move_camera.id, [target]), []])
        probe(obs, "  之后")
        obs = env.step([actions.FUNCTIONS.no_op(), []])
        probe(obs, "  再等1步")

    print("\n--- move_camera 是否在当前 available_actions ---", flush=True)
    w = parse_world(obs[0].observation)
    print("move_camera id:", actions.FUNCTIONS.move_camera.id,
          "available:", actions.FUNCTIONS.move_camera.id in w.available_actions,
          flush=True)

    env.close()
    print("DONE-OK", flush=True)


if __name__ == "__main__":
    app.run(main)
