"""Example authored for this runtime, not evidence of model-written code or win rate."""
import math

def tick(ctx, state):
    params = ctx["params"]
    retreat_ratio = params.get("retreat_ratio", 1.3)
    resume_ratio = params.get("resume_ratio", .8)
    health_floor = params.get("health_floor", .35)
    if not (1 < retreat_ratio <= 10 and 0 <= resume_ratio < retreat_ratio and 0 <= health_floor <= 1):
        raise ValueError("invalid retreat_ratio/resume_ratio/health_floor")
    own = ctx["units"]
    # Only immediately threatening, currently visible units influence engagement.
    enemies = [e for e in ctx["enemies"] if e["ground_dps"] > 0 and
               any(math.dist(e["pos"], u["pos"]) <= max(10, e["attack_range"]+2) for u in own)]
    def value(units):
        if any(u["mineral_cost"] is None or u["gas_cost"] is None for u in units):
            raise ValueError("unit cost metadata missing; cannot estimate value (no zero fallback)")
        return sum(u["mineral_cost"]+u["gas_cost"] for u in units)
    own_value = value(own)
    if own_value <= 0:
        raise ValueError("allocated squad has no positive known cost")
    ratio = value(enemies)/own_value
    hp = sum(u["health"]/max(1,u["health_max"]) for u in own)/len(own)
    mode = state.get("mode", "advance")
    changed = state.get("changed_at", ctx["game_s"])
    if mode == "advance" and (ratio >= retreat_ratio or hp < health_floor):
        mode, changed = "retreat", ctx["game_s"]
    elif mode == "retreat" and ratio < resume_ratio and hp >= health_floor and ctx["game_s"]-changed >= 3:
        mode, changed = "advance", ctx["game_s"]
    return {"status":"running", "state":{"mode":mode,"changed_at":changed},
            "reason":f"{mode}: visible enemy/own cost={ratio:.2f}, squad HP={hp:.2f}",
            "actions":[{"action":"move" if mode == "retreat" else "attack_move",
                        "unit_tags":[u["tag"] for u in own],
                        "target":"retreat" if mode == "retreat" else "objective"}]}
