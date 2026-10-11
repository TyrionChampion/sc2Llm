"""Prove AsyncPolicy does not stall the game loop when the LLM is slow.

Uses a fake client that takes `delay` seconds per call, so we can measure the
main loop's cadence with no network involved.
"""
import time

from arena.core import parse_world, SkillSet
from arena.policy import AsyncPolicy, Policy


class SlowClient:
    """Stands in for a real provider: same interface, fixed latency."""

    def __init__(self, delay=3.0, answer='{"skill": "gather"}'):
        self.delay = delay
        self.answer = answer
        self.calls = 0
        self.errors = 0
        self.total_latency = 0.0
        self.p = type("P", (), {"name": "slow-fake"})()
        self.model = "slow-%ss" % delay

    def chat(self, system, user, retries=2):
        self.calls += 1
        time.sleep(self.delay)
        return self.answer


class FakeWorld:
    """Minimal world stub so skills can be built without a live game."""

    def __init__(self, loop=0):
        self.game_loop = loop
        self.minerals = 100
        self.vespene = 0
        self.supply_used = 12
        self.supply_cap = 15
        self.army_count = 0
        self.idle_workers = 0
        self.my_units = []
        self.mineral_patches = []
        self.enemy_units = []
        self.camera = (30, 44)
        self.camera_size = (22, 24)
        self.available_actions = ()

    def summary(self):
        return "loop=%d mins=100 supply=12/15 | (stub)" % self.game_loop

    def workers(self):
        return []

    def townhalls(self):
        return []

    def names(self):
        return []

    def can(self, _):
        return False

    def my(self, *_):
        return []


def run(kind, delay, steps=40):
    client = SlowClient(delay=delay)
    if kind == "async":
        pol = AsyncPolicy(client, decide_every=8)
        pol.update_world(FakeWorld(0))
        pol.start()
    else:
        pol = Policy(client, decide_every=8)

    t0 = time.time()
    for i in range(steps):
        w = FakeWorld(i)
        if kind == "async":
            pol.update_world(w)
        pol.tick(w)
    dt = time.time() - t0
    if kind == "async":
        pol.stop()
    return dt, client.calls


def main():
    delay = 3.0
    steps = 40
    print("假 LLM 每次耗时 %.0f 秒，模拟 %d 个游戏步" % (delay, steps))
    print("（同步模式下 decide_every=8，本应触发约 %d 次 LLM 调用）"
          % (steps // 8))
    print()

    d_sync, c_sync = run("sync", delay, steps)
    print("同步 Policy : 墙钟 %6.1fs   LLM 调用 %d 次" % (d_sync, c_sync))
    print("              → 主循环被 LLM 阻塞，游戏在这段时间里完全停住")

    d_async, c_async = run("async", delay, steps)
    print("异步 Policy : 墙钟 %6.1fs   LLM 调用 %d 次" % (d_async, c_async))
    print("              → 主循环没有被阻塞，游戏持续运行")

    print()
    speedup = d_sync / d_async if d_async else 0
    print("结论: 异步比同步快 %.1fx，且这 %.1fs 里游戏一直在跑" % (speedup, d_async))
    if d_async < 1.0 and d_sync > delay:
        print("✅ 验证通过：LLM 延迟不再阻塞游戏循环")
        return 0
    print("❌ 未达预期，异步可能仍有阻塞点")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
