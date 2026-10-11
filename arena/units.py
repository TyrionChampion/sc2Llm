"""SC2 unit type IDs we care about.

Values are Blizzard's unit type ids as they appear in feature_units.
Kept minimal on purpose: only what the skills actually need.
"""

# Terran
SCV = 45
MARINE = 48
MARAUDER = 51
COMMAND_CENTER = 18
SUPPLY_DEPOT = 19
REFINERY = 20
BARRACKS = 21
FACTORY = 27
STARPORT = 28
ENGINEERING_BAY = 22
BUNKER = 24
MISSILE_TURRET = 25

# Protoss
PROBE = 84
ZEALOT = 73
STALKER = 74
PYLON = 60
NEXUS = 59
GATEWAY = 62
ASSIMILATOR = 61

# Zerg
DRONE = 104
ZERGLING = 105
HATCHERY = 86
SPAWNING_POOL = 88
EXTRACTOR = 88 + 60  # not used; left explicit to avoid silent wrong id

# Neutral / resources
MINERAL_FIELD = 343
MINERAL_FIELD_RICH = 666
VESPENE_GEYSER = 342

MINERAL_TYPES = {MINERAL_FIELD, MINERAL_FIELD_RICH}
GEYSER_TYPES = {VESPENE_GEYSER}

# Units that can gather / build (workers)
WORKER_TYPES = {SCV, PROBE, DRONE}

# Town halls, per race, for "expand" and "train worker"
TOWNHALL_TYPES = {COMMAND_CENTER, NEXUS, HATCHERY}

# Name -> unit id, for the LLM to ask for units by name.
BY_NAME = {
    "scv": SCV, "marine": MARINE, "marauder": MARAUDER,
    "probe": PROBE, "zealot": ZEALOT, "stalker": STALKER,
    "drone": DRONE, "zergling": ZERGLING,
    "command_center": COMMAND_CENTER, "nexus": NEXUS, "hatchery": HATCHERY,
    "supply_depot": SUPPLY_DEPOT, "refinery": REFINERY, "barracks": BARRACKS,
    "factory": FACTORY, "starport": STARPORT,
    "engineering_bay": ENGINEERING_BAY, "bunker": BUNKER,
    "missile_turret": MISSILE_TURRET,
    "pylon": PYLON, "gateway": GATEWAY, "assimilator": ASSIMILATOR,
}

# Human-readable names for the LLM-visible state summary.
NAMES = {
    COMMAND_CENTER: "CommandCenter", SUPPLY_DEPOT: "SupplyDepot",
    REFINERY: "Refinery", BARRACKS: "Barracks", FACTORY: "Factory",
    STARPORT: "Starport", ENGINEERING_BAY: "EngineeringBay",
    BUNKER: "Bunker", MISSILE_TURRET: "MissileTurret",
    SCV: "SCV", MARINE: "Marine", MARAUDER: "Marauder",
    NEXUS: "Nexus", PYLON: "Pylon", GATEWAY: "Gateway",
    ASSIMILATOR: "Assimilator", PROBE: "Probe", ZEALOT: "Zealot",
    STALKER: "Stalker",
    HATCHERY: "Hatchery", DRONE: "Drone", ZERGLING: "Zergling",
}


def name_of(unit_type):
    return NAMES.get(int(unit_type), "Unit%s" % int(unit_type))
