"""Verified real-time speed map; never substitute API polling or step mode."""
import hashlib
import json
from pathlib import Path
from pysc2 import maps

ROOT = Path(__file__).resolve().parents[1]
LOOPS_PER_SECOND = {"slower": 9.6, "normal": 16.0, "faster": 22.4}


def map_for_speed(map_name, speed):
    if speed not in LOOPS_PER_SECOND:
        raise ValueError("game speed must be slower, normal or faster")
    source = ROOT / "engine/StarCraftII/Maps" / maps.get(map_name).path
    if speed == "faster":
        if not source.is_file():
            raise FileNotFoundError(source)
        return source
    if map_name != "Acropolis":
        raise ValueError("Modified real-time speeds are engine-verified only for Acropolis; select Acropolis or explicitly --game-speed faster")
    label = {"normal":"Normal", "slower":"Slower"}[speed]
    target = ROOT / ("engine/StarCraftII/Maps/Arena/Acropolis"+label+".SC2Map")
    metadata = json.loads(target.with_suffix(".json").read_text())
    if hashlib.sha256(source.read_bytes()).hexdigest() != metadata["source_sha256"]:
        raise RuntimeError("Acropolis source changed; speed derivative must be rebuilt and revalidated")
    if hashlib.sha256(target.read_bytes()).hexdigest() != metadata["map_sha256"]:
        raise RuntimeError("Requested speed map hash mismatch; no silent fallback to Faster")
    return target
