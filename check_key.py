"""Check that a provider's key works and the model answers in the format we need.

Usage:
    ./run_sc2.sh .venv311/bin/python check_key.py            # uses .env
    ./run_sc2.sh .venv311/bin/python check_key.py openai     # another provider
"""
import sys

from arena.llm import LLMClient, available_providers
from arena.policy import SYSTEM_PROMPT, parse_decision

DEMO_STATE = (
    "CURRENT STATE\n"
    "loop=400 mins=245 gas=0 supply=14/15 army=0 idle_workers=0 bases=1 "
    "visible_enemies=0 | CommandCenter x1, SCV x12\n\n"
    "AVAILABLE SKILLS\n"
    "  - attack_nearest\n  - barracks\n  - build_supply\n  - expand\n"
    "  - gather\n  - move_out\n  - no_op\n  - supply_depot\n"
    "  - train_unit\n  - train_worker\n\n"
    "ARG HINTS\n"
    '  train_unit: {"unit": "marine"}\n\n'
    "Respond with the JSON object now."
)


def main():
    provider = sys.argv[1] if len(sys.argv) > 1 else None

    print("provider 状态")
    for n, s in sorted(available_providers().items()):
        print("  %-10s %s" % (n, s))
    print()

    try:
        client = LLMClient(provider) if provider else LLMClient(
            __import__("os").environ.get("ARENA_PROVIDER", "deepseek"))
    except Exception as e:
        print("❌ 无法创建 client:", e)
        return 1

    print("使用:", client)
    if not client.p.configured:
        print("❌ 没有 key：请在 .env 里设置 %s" % client.p.key_env)
        return 1

    print("\n发送一次真实请求...")
    try:
        text = client.chat(SYSTEM_PROMPT, DEMO_STATE)
    except Exception as e:
        print("❌ 请求失败:", type(e).__name__, str(e)[:300])
        return 1

    print("✅ 收到回复（%.2fs，往返 %d 次调用）" % (client.avg_latency, client.calls))
    print("---- 模型原始输出 ----")
    print(text[:600])
    print("----------------------")

    d, err = parse_decision(text)
    if d is None:
        print("\n⚠️  回复里没有我们需要的 JSON：%s" % err)
        print("    这说明该模型不太听话，需要在 prompt 里更强约束，告诉我我来调。")
        return 2
    print("\n✅ 解析成功:", d)
    if d.get("skill") not in (
            "attack_nearest", "barracks", "build_structure", "build_supply",
            "expand", "gather", "move_out", "no_op", "supply_depot",
            "train_unit", "train_worker"):
        print("⚠️  它选了未知技能 %r（运行时会被拒绝并记日志）" % d.get("skill"))
        return 3
    print("✅ 技能在允许列表内，可以直接上场。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
