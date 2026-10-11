"""LLM-driven game test using the deterministic mock provider.

Proves the FULL chain works: observation -> World -> prompt -> decision ->
skill -> real pysc2 action -> game state changes.
"""
from absl import app
from pysc2.env import sc2_env
from pysc2.lib import actions, features

from arena.core import parse_world
from arena.llm import LLMClient
from arena.policy import Policy, build_user_prompt


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

    client = LLMClient("mock")
    policy = Policy(client, decide_every=20)
    w = parse_world(obs[0].observation)
    print("provider=%s model=%s decide_every=%d"
          % (client.p.name, client.model, policy.decide_every), flush=True)
    print("START %s" % w.summary(), flush=True)
    print(flush=True)
    print("--- 送给 LLM 的 prompt 示例 ---", flush=True)
    print(build_user_prompt(w, policy.skills)[:600], flush=True)
    print("--- prompt 结束 ---", flush=True)
    print(flush=True)

    acted = 0
    for i in range(100):
        w = parse_world(obs[0].observation)
        action, info = policy.tick(w)
        if action is not None:
            acted += 1
            obs = env.step([action, []])
        else:
            obs = env.step([actions.FUNCTIONS.no_op(), []])
        if i % 20 == 19:
            w = parse_world(obs[0].observation)
            print("t%03d mins=%-4d supply=%d/%d workers=%d skill=%s"
                  % (i, w.minerals, w.supply_used, w.supply_cap,
                     len(w.workers()), policy.skill_name), flush=True)

    print()
    print("=== LLM 决策日志 ===", flush=True)
    for e in policy.log[-10:]:
        print("  " + e, flush=True)
    print()
    print("acted %d/100 steps, stats=%s" % (acted, policy.stats()), flush=True)
    env.close()
    print("DONE-OK", flush=True)


if __name__ == "__main__":
    app.run(main)
