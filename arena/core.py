"""Skill layer: turn pysc2's 573 atomic actions into callable skills.

Design notes
------------
pysc2 exposes ATOMIC actions only ("Build_SupplyDepot_screen(queued, xy)",
"select_point(act, xy)").  A single pysc2 step submits exactly ONE action per
agent, so a skill that needs a sequence keeps its own little state machine and
is pumped once per game step via act(world).

Skills are addressed by NAME (strings) so the LLM layer never imports pysc2.
"""
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from pysc2.lib import actions
from pysc2.lib.actions import FUNCTIONS

from arena import units as U

# --- feature_units column indices (pysc2.lib.features.FeatureUnit) ---
FU_TYPE = 0
FU_ALLIANCE = 1
FU_HEALTH = 2
FU_BUILD_PROGRESS = 6
FU_OWNER = 11
FU_X = 12
FU_Y = 13
FU_IS_SELECTED = 17
FU_MINERALS = 20

ALLIANCE_SELF = 1
ALLIANCE_ENEMY = 4
ALLIANCE_NEUTRAL = 3

# feature_screen layer index for the buildable mask (see SCREEN_FEATURES)
FS_BUILDABLE = 25

BUILDING_TYPES = {
    U.SUPPLY_DEPOT, U.BARRACKS, U.FACTORY, U.STARPORT, U.REFINERY,
    U.ENGINEERING_BAY, U.BUNKER, U.MISSILE_TURRET,
    U.PYLON, U.GATEWAY, U.ASSIMILATOR,
} | set(U.TOWNHALL_TYPES)


@dataclass
class Unit:
    type_id: int
    alliance: int
    x: int
    y: int
    health: int = 0
    selected: bool = False
    build_progress: int = 100
    minerals: int = 0

    @property
    def is_self(self):
        return self.alliance == ALLIANCE_SELF

    @property
    def is_enemy(self):
        return self.alliance == ALLIANCE_ENEMY

    @property
    def is_neutral(self):
        return self.alliance == ALLIANCE_NEUTRAL

    @property
    def is_worker(self):
        return self.type_id in U.WORKER_TYPES

    @property
    def is_building(self):
        return self.type_id in BUILDING_TYPES

    @property
    def done(self):
        return self.build_progress >= 100

    @property
    def name(self):
        return U.name_of(self.type_id)


@dataclass
class World:
    """A readable snapshot of one player's observation."""
    units: List[Unit] = field(default_factory=list)
    minerals: int = 0
    vespene: int = 0
    supply_used: int = 0
    supply_cap: int = 0
    army_count: int = 0
    idle_workers: int = 0
    game_loop: int = 0
    camera: Tuple[int, int] = (0, 0)
    camera_size: Tuple[int, int] = (24, 24)
    available_actions: Tuple[int, ...] = ()
    mineral_patches: List[Unit] = field(default_factory=list)
    geysers: List[Unit] = field(default_factory=list)
    my_units: List[Unit] = field(default_factory=list)
    enemy_units: List[Unit] = field(default_factory=list)
    # (y, x) -> 1 where a structure may legally be placed on screen.
    # This is feature_screen[FS_BUILDABLE]; used for honest siting
    # instead of guessing offsets (see best_build_tile).
    buildable: Optional[np.ndarray] = None

    def my(self, *type_ids):
        want = set(type_ids)
        return [u for u in self.my_units if u.type_id in want]

    def workers(self):
        return [u for u in self.my_units if u.is_worker]

    def on_screen(self, units=None):
        """Units whose coords are actually within the 64x64 action space.

        feature_units reports OFF-SCREEN units with negative / out-of-range
        coordinates (seen as mineral patches at (-25,-30) in logs/coord.log).
        Feeding those to select_point produces an illegal action, so every
        skill must filter first.
        """
        src = self.my_units if units is None else units
        return [u for u in src if 0 <= u.x < 64 and 0 <= u.y < 64]

    def townhalls(self):
        return [u for u in self.my_units if u.type_id in U.TOWNHALL_TYPES]

    def producers(self, building_types):
        want = set(building_types)
        return [u for u in self.my_units
                if u.type_id in want and u.done and not u.is_worker]

    def can(self, fn_id):
        return fn_id in self.available_actions

    def summary(self):
        """Compact one-line text for an LLM prompt."""
        def describe(units,limit=32):
            counts=Counter(u.name for u in units)
            entries=sorted(counts.items())
            text=", ".join("%s x%d" % (name,n) for name,n in entries[:limit]) or "(none)"
            if len(entries)>limit:text+=" [omitted %d types/%d units]" % (len(entries)-limit,sum(n for _,n in entries[limit:]))
            return text
        visible=self.on_screen(self.enemy_units)
        return ("loop=%d mins=%d gas=%d supply=%d/%d army=%d idle_workers=%d "
                "bases=%d visible_enemies=%d | %s | ENEMY_ON_SCREEN: %s"
                % (self.game_loop, self.minerals, self.vespene,
                   self.supply_used, self.supply_cap, self.army_count,
                   self.idle_workers, len(self.townhalls()),
                   len(visible), describe(self.my_units),describe(visible)))



def parse_world(obs) -> World:
    """Build a World from a pysc2 TimeStep.observation."""
    w = World()
    o = obs

    def scalar(key, index=0, default=0):
        try:
            v = o.get(key)
        except AttributeError:
            return default
        if v is None:
            return default
        arr = np.asarray(v).ravel()
        if arr.size <= index:
            return default
        return int(arr[index])

    w.game_loop = scalar("game_loop")
    if o.get("player") is not None:
        w.minerals = scalar("player", 1)
        w.vespene = scalar("player", 2)
        w.supply_used = scalar("player", 3)
        w.supply_cap = scalar("player", 4)
        # NOTE: index 5 is food_army; real army_count is index 8
        w.army_count = scalar("player", 8)
        w.idle_workers = scalar("player", 7)

    if o.get("camera_position") is not None:
        c = np.asarray(o["camera_position"]).ravel()
        if c.size >= 2:
            w.camera = (int(c[0]), int(c[1]))
    if o.get("camera_size") is not None:
        c = np.asarray(o["camera_size"]).ravel()
        if c.size >= 2:
            w.camera_size = (int(c[0]), int(c[1]))

    if o.get("available_actions") is not None:
        w.available_actions = tuple(
            int(a) for a in np.asarray(o["available_actions"]).ravel())

    fu = o.get("feature_units")
    if fu is not None:
        for row in np.asarray(fu):
            if int(row[FU_TYPE]) == 0:
                continue
            u = Unit(
                type_id=int(row[FU_TYPE]),
                alliance=int(row[FU_ALLIANCE]),
                x=int(row[FU_X]), y=int(row[FU_Y]),
                health=int(row[FU_HEALTH]),
                selected=bool(int(row[FU_IS_SELECTED])),
                build_progress=int(row[FU_BUILD_PROGRESS]),
                minerals=int(row[FU_MINERALS]),
            )
            w.units.append(u)
            if u.type_id in U.MINERAL_TYPES:
                w.mineral_patches.append(u)
            elif u.type_id in U.GEYSER_TYPES:
                w.geysers.append(u)
            if u.is_self:
                w.my_units.append(u)
            elif u.is_enemy:
                w.enemy_units.append(u)

    fs = o.get("feature_screen")
    if fs is not None:
        try:
            w.buildable = np.asarray(
                fs[FS_BUILDABLE]).astype(np.int32)
        except Exception:
            w.buildable = None
    return w


class SkillResult:
    """Result of one skill tick. ok=False means 'could not act this tick'.

    waiting=True marks "the world is not ready yet" (a prerequisite building is
    still under construction, supply is full momentarily, ...). The policy must
    NOT count those toward its circuit breaker -- doing so got train_unit
    banned for 3 ticks while its Barracks was still being built, and it stayed
    blacklisted afterwards.
    """

    __slots__ = ("ok", "msg", "waiting")

    def __init__(self, ok, msg, waiting=False):
        self.ok = ok
        self.msg = msg
        self.waiting = waiting

    def __repr__(self):
        return "SkillResult(%s, %r%s)" % (
            self.ok, self.msg, ", waiting" if self.waiting else "")


class Skill:
    """Base class. One instance per running skill; act() called per step."""

    name = "skill"
    # consecutive "cannot act" count; the policy uses this to trip a
    # circuit breaker instead of retrying a broken skill forever.
    fail_count = 0

    def act(self, ctx, world):
        raise NotImplementedError

    # -- shared helpers --
    # VERIFIED (logs/coord3.log): feature_units x/y ARE screen pixels, i.e. the
    # same 0..64 space select_point/Attack_screen accept. No camera maths needed.
    SCREEN_W = 64
    SCREEN_H = 64

    @staticmethod
    def nearest(src, targets):
        if not targets:
            return None
        return min(targets, key=lambda t: math.hypot(src.x - t.x, src.y - t.y))

    @classmethod
    def on_screen(cls, wx, wy, margin=1):
        return (margin <= wx < cls.SCREEN_W - margin and
                margin <= wy < cls.SCREEN_H - margin)

    @classmethod
    def in_camera(cls, ctx, world, u):
        return cls.on_screen(u.x, u.y)

    @staticmethod
    def screen(ctx, world, wx, wy):
        """Kept for compatibility; feature_units coords are already screen."""
        return int(wx), int(wy)

    @staticmethod
    def noop():
        return actions.FUNCTIONS.no_op()


class NoOp(Skill):
    name = "no_op"

    def act(self, ctx, world):
        return SkillResult(True, "no_op")


class TrainUnit(Skill):
    """Train a unit from its production building."""
    name = "train_unit"

    def __init__(self, unit="marine"):
        self.unit = unit
        self._type = U.BY_NAME.get(unit.lower())
        self._picked = None

    def act(self, ctx, world):
        if self._type is None:
            return SkillResult(False, "unknown unit %r" % self.unit)
        fn_id = _TRAIN_FN.get(self._type)
        if fn_id is None:
            return SkillResult(False, "no train action for %s" % self.unit)
        want = _PRODUCERS.get(self._type, set())
        producers = world.producers(want)
        if not producers:
            # Is one still under construction? Then wait, do not give up.
            cooking = [u for u in world.my_units
                       if u.type_id in want and not u.done]
            if cooking:
                return SkillResult(
                    False, "%s is still under construction (%.0f%%), waiting"
                    % (cooking[0].name, cooking[0].build_progress),
                    waiting=True)
            return SkillResult(False, "no production building for %s" % self.unit)
        b = producers[0]
        # camera must be over the building before we can click it
        if not ctx.on_screen(b.x, b.y):
            return SkillResult(False, "production building off screen")
        # Track our own selection: feature_units' is_selected flag is NOT
        # reliable (logs/trace_build.log showed it always reading False, which
        # made this skill re-select forever and never submit the order).
        if self._picked is None:
            self._picked = (b.x, b.y)
            return ctx.select_point(world, b)
        if not world.can(fn_id):
            # Selected but the order is not offered yet (still building, or the
            # building lost the selection). Wait rather than get banned.
            self._picked = None
            return SkillResult(
                False, "train %s not offered yet (mins=%d supply=%d/%d)"
                % (self.unit, world.minerals, world.supply_used,
                   world.supply_cap), waiting=True)
        self._picked = None
        return _by_id(fn_id, [False])


class TrainWorker(TrainUnit):
    name = "train_worker"

    def __init__(self):
        super(TrainWorker, self).__init__(unit="scv")
        self._picked = None

    def act(self, ctx, world):
        # NOTE: the train action only becomes available AFTER the production
        # building is selected, so we must select first and only then check
        # availability. Checking the other way round made this always fail.
        for th, worker in ((U.COMMAND_CENTER, "scv"), (U.NEXUS, "probe"),
                           (U.HATCHERY, "drone")):
            ths = world.producers({th})
            if not ths:
                cooking = [u for u in world.my_units
                           if u.type_id == th and not u.done]
                if cooking:
                    return SkillResult(
                        False, "%s still under construction (%.0f%%), waiting"
                        % (cooking[0].name, cooking[0].build_progress),
                        waiting=True)
                continue
            b = ths[0]
            if not ctx.on_screen(b.x, b.y):
                return SkillResult(False, "town hall off screen")
            if self._picked is None:
                self._picked = (b.x, b.y)
                return ctx.select_point(world, b)
            fn_id = _TRAIN_FN.get(U.BY_NAME[worker])
            self._picked = None
            if not world.can(fn_id):
                # supply capped or still settling: wait, do not get banned
                return SkillResult(
                    False, "train %s not offered (supply %d/%d, mins %d)"
                    % (worker, world.supply_used, world.supply_cap,
                       world.minerals), waiting=True)
            return _by_id(fn_id, [False])
        return SkillResult(False, "no town hall to train workers from")


class BuildStructure(Skill):
    """Two-phase: select a worker, then place the structure."""
    name = "build_structure"

    _FN = {
        "supply_depot": actions.FUNCTIONS.Build_SupplyDepot_screen,
        "barracks": actions.FUNCTIONS.Build_Barracks_screen,
        "factory": actions.FUNCTIONS.Build_Factory_screen,
        "refinery": actions.FUNCTIONS.Build_Refinery_screen,
        "command_center": actions.FUNCTIONS.Build_CommandCenter_screen,
    }

    # candidate build offsets relative to the town hall, tried in order
    # Ordered by what has actually worked in measurement, not by guesswork.
    # logs/sel_verify.log built at (8,4); logs/distance_scan.log built at a far
    # tile; logs/ensure_test2.log built at (2,8). The old list started with
    # (4,2), which was measured NOT to work.
    OFFSETS = [(8, 4), (2, 8), (-2, 8), (8, -6), (6, 6), (-6, 6),
               (4, 2), (-4, 2), (4, -4), (-4, -4), (6, 0), (0, 6),
               (-8, 4), (-8, -6), (10, 2), (-10, 2)]

    # which unit type to count when verifying that a build actually started
    TARGETS = {
        "supply_depot": U.SUPPLY_DEPOT, "barracks": U.BARRACKS,
        "factory": U.FACTORY, "refinery": U.REFINERY,
        "command_center": U.COMMAND_CENTER,
    }

    # footprint in map tiles, used to check the whole area is buildable
    SIZES = {
        "supply_depot": 2, "barracks": 3, "factory": 3, "starport": 3,
        "engineering_bay": 3, "refinery": 3, "command_center": 5,
        "pylon": 2, "gateway": 3, "assimilator": 3,
    }

    def __init__(self, structure="supply_depot", offset=(4, 2)):
        self.structure = structure.lower()
        self.offset = offset
        self._fn = self._FN.get(self.structure)
        self._try = 0
        self._picked = None      # the worker we selected (see note below)
        self.size = self.SIZES.get(self.structure, 3)
        # Re-issuing a build order every tick cancels the previous one, so the
        # worker never gets to walk over and start. Wait it out instead.
        self._cooldown = 0
        # Tiles the game refused. Without this, best_build_tile kept
        # returning the SAME nearest tile every tick, so the skill
        # retried one impossible spot forever (logs/trace2.log).
        self._excluded = set()
        self._last_spot = None
        # Self-check: a build order gives us no immediate feedback, so after
        # issuing one we wait and then verify the unit count actually rose.
        # (spot, count_before, ticks_left)
        self._pending = None
        self._waiting = 0
        self._target_type = self.TARGETS.get(self.structure)

    def _tile_ok(self, bm, x, y):
        """True if a size x size footprint at (x,y) is fully buildable."""
        h, w = bm.shape
        n = self.size
        if x < 0 or y < 0 or x + n > w or y + n > h:
            return False
        return bool(bm[y:y + n, x:x + n].all())

    def _blocked(self, world, x, y):
        """True if (x,y)..(x+size,y+size) overlaps an existing structure.

        The buildable feature layer marks TERRAIN legality only -- it happily
        reports 1 on top of our own Command Center. logs/siting.log showed the
        first pick being the CC tile itself, which is why nothing got built.
        """
        n = self.size
        for u in world.my_units:
            if not (u.is_building or u.type_id in U.TOWNHALL_TYPES):
                continue
            b = 4 if u.type_id in U.TOWNHALL_TYPES else 3
            if (x < u.x + b and u.x < x + n and
                    y < u.y + b and u.y < y + n):
                return True
        return False

    def candidates(self, world, ref, limit=60, min_dist=5):
        """Ordered list of plausible tiles: measured offsets first, then the
        buildable mask.

        The offset path used to bypass _blocked() entirely, so a Barracks would
        happily retry the tile its own Supply Depot already occupied and fail
        (logs/chain_v3.log: 9 offsets, zero barracks, while the ability WAS
        offered). Everything now funnels through the same filters.
        """
        cx, cy = int(ref.x), int(ref.y)
        out = []
        seen = set()
        for off in self.OFFSETS:
            x, y = int(cx + off[0]), int(cy + off[1])
            if not (1 <= x < 64 - self.size and 1 <= y < 64 - self.size):
                continue
            if (x, y) in seen or (x, y) in self._excluded:
                continue
            if self._blocked(world, x, y):
                continue
            seen.add((x, y))
            out.append((x, y))
        bm = world.buildable
        if bm is not None:
            h, w = bm.shape
            extra = []
            for y in range(1, h - self.size):
                for x in range(1, w - self.size):
                    if (x, y) in seen or (x, y) in self._excluded:
                        continue
                    if not bm[y:y + self.size, x:x + self.size].all():
                        continue
                    if self._blocked(world, x, y):
                        continue
                    d = (x - cx) ** 2 + (y - cy) ** 2
                    if d < min_dist * min_dist:
                        continue
                    extra.append((d, x, y))
            extra.sort()
            for _d, x, y in extra[:limit]:
                seen.add((x, y))
                out.append((x, y))
        return out

    def best_build_tile(self, world, ref, min_dist=6, exclude=None):
        """Nearest legal tile to `ref` using the buildable feature layer.

        Earlier versions guessed fixed offsets and hoped; logs/sel_verify.log
        showed (8,4) worked while (4,2) did not, with nothing to distinguish
        them up front. The buildable mask answers the terrain question, and
        _blocked() removes tiles our own buildings already occupy.

        min_dist matters: logs/trace2.log showed this picking (27,26) against a
        Command Center at (27,28) -- 2 tiles away, terrain-legal but in practice
        not placeable next to a 5x5 building. Every verified success sat >= 5
        tiles out, hence the default.
        """
        bm = world.buildable
        if bm is None:
            return None
        skip = exclude or set()
        h, w = bm.shape
        cx, cy = int(ref.x), int(ref.y)
        best = None
        best_d = None
        min_sq = min_dist * min_dist
        for y in range(1, h - self.size):
            row_ok = bm[y:y + self.size]
            for x in range(1, w - self.size):
                if not row_ok[:, x:x + self.size].all():
                    continue
                if (x, y) in skip or (x, y) in skip:
                    continue
                if self._blocked(world, x, y):
                    continue
                d = (x - cx) ** 2 + (y - cy) ** 2
                if d < min_sq:
                    continue
                if best_d is None or d < best_d:
                    best_d, best = d, (x, y)
        return best

    def act(self, ctx, world):
        if self._fn is None:
            return SkillResult(False, "unsupported structure %r" % self.structure)

        # --- verify the previous attempt before doing anything else ---
        if self._pending is not None:
            spot, before, left = self._pending
            now = (len(world.my(self._target_type))
                   if self._target_type else 0)
            if now > before:
                self._pending = None          # it worked
                self._excluded.clear()
            elif left <= 0:
                self._pending = None
                if self._fn.id not in world.available_actions:
                    # The ability itself is not offered -> prerequisite or
                    # resources are missing (logs/tech_tree2.log: Barracks only
                    # appeared after a Supply Depot existed AND minerals ~545).
                    # Wait rather than burn a good tile.
                    self._waiting += 1
                else:
                    # Ability was offered but the tile was refused -> genuinely
                    # a bad spot, so blacklist it and move on.
                    self._excluded.add(spot)
                    self._try += 1
            else:
                # still walking over / under construction: wait, do not re-issue
                self._pending = (spot, before, left - 1)
                return FUNCTIONS.no_op()
        thr = world.townhalls()
        if not thr:
            return SkillResult(False, "no town hall for reference")
        t = thr[0]

        cands = self.candidates(world, t)
        if not cands:
            return SkillResult(False, "no candidate tile for %s" % self.structure)
        spot = cands[self._try % len(cands)]
        if not ctx.on_screen(spot[0], spot[1]):
            return SkillResult(False, "build spot off screen, trying next")

        workers = [u for u in world.workers()
                   if 0 <= u.x < 64 and 0 <= u.y < 64]
        if not workers:
            return SkillResult(False, "no on-screen worker")

        # Pick ONE worker and stick with it. Re-choosing "nearest to the town
        # hall" every tick rotated through a different SCV each time (workers
        # move constantly), so no single worker ever accumulated enough
        # uninterrupted time to place the structure -- see logs/trace2.log where
        # the select target changes every other tick.
        w = None
        if self._picked is not None:
            px, py = self._picked
            w = min(workers, key=lambda u: (u.x - px) ** 2 + (u.y - py) ** 2)
        if w is None:
            w = ctx.nearest_unit(t, workers)
        if self._picked is None:
            self._picked = (w.x, w.y)
            return ctx.select_point(world, w)
        # Do NOT gate on available_actions here. Measured both ways:
        #   - gating on it  -> skill parked in "waiting" and never built
        #     (logs/final_chain.log, logs/chain_fixed*.log: 400 ticks, 1535
        #      minerals, zero structures)
        #   - sending anyway -> Supply Depot built (logs/build_offsets.log)
        # The env runs ensure_available_actions=False precisely so the game can
        # be the judge. Availability is consulted later, in the self-check, to
        # tell "bad tile" apart from "prereq not met yet".
        self._picked = None
        self._last_spot = (spot[0], spot[1])
        before = (len(world.my(self._target_type))
                  if self._target_type else 0)
        self._pending = (spot, before, 14)   # verify within 14 ticks
        return self._fn(False, list(spot))

    @property
    def fail_count(self):
        """Keep the policy's circuit breaker away while we are legitimately
        waiting for a prerequisite (e.g. Barracks before Supply Depot)."""
        return 0 if getattr(self, "_waiting", 0) else getattr(
            self, "_fc", 0)

    @fail_count.setter
    def fail_count(self, v):
        self._fc = v

    def note_failure(self):
        """Called by the policy on a failed tick.

        Blacklist the tile we just tried and re-pick the worker. Independently
        measured successes always came from trying SEVERAL tiles
        (logs/sel_verify.log, logs/distance_scan.log); retrying one tile is what
        kept every attempt failing.
        """
        if self._last_spot:
            self._excluded.add(self._last_spot)
            self._last_spot = None
        self._picked = None


class Gather(Skill):
    """Saturate the mineral line with workers (needs continuous pumping)."""
    name = "gather"

    def __init__(self):
        self._selected = False
        # Re-picking the "nearest" patch every tick made workers constantly
        # retarget (42,42 -> 44,39 -> ...) and they never reached anything:
        # minerals stayed flat. The working sequence used ONE fixed patch.
        self._target = None

    def act(self, ctx, world):
        if not world.mineral_patches:
            return SkillResult(False, "no visible mineral patch")
        workers = world.workers()
        if not workers:
            return SkillResult(False, "no workers at all")
        base = world.townhalls() or workers

        if self._target is None:
            t = self.nearest(workers[0], world.mineral_patches)
            self._target = (int(t.x), int(t.y))
        tx, ty = self._target
        if not ctx.on_screen(tx, ty):
            # cached patch scrolled off screen; pick a fresh one
            t = self.nearest(workers[0], world.mineral_patches)
            self._target = (int(t.x), int(t.y))
            tx, ty = self._target
            if not ctx.on_screen(tx, ty):
                return SkillResult(False, "no mineral patch on screen")

        can_harvest = world.can(actions.FUNCTIONS.Harvest_Gather_screen.id)
        # DIAGNOSIS (logs/mine_diag.log): mining only pays off if we KEEP
        # issuing the gather command AND keep the target fixed.
        if self._selected and can_harvest:
            return FUNCTIONS.Harvest_Gather_screen(False, [tx, ty])

        # A/B test (logs/ab_test.log) showed a single-SCV select is the most
        # reliable way in: minerals went 50 -> 105 with it.
        w = self.nearest(base[0], workers)
        if not ctx.on_screen(w.x, w.y):
            return SkillResult(False, "worker off screen")
        self._selected = True
        return ctx.select_point(world, w)


class AttackNearest(Skill):
    name = "attack_nearest"

    def act(self, ctx, world):
        if not world.enemy_units:
            return SkillResult(False, "no visible enemy")
        target = world.enemy_units[0]
        if not ctx.on_screen(target.x, target.y):
            return SkillResult(False, "enemy off screen")
        fn_id = actions.FUNCTIONS.Attack_screen.id
        if not world.can(fn_id):
            if world.can(actions.FUNCTIONS.select_army.id):
                return actions.FUNCTIONS.select_army("select")
            return SkillResult(False, "cannot select army / attack")
        return FUNCTIONS.Attack_screen(False, [int(target.x), int(target.y)])


class MoveOut(Skill):
    name = "move_out"

    def __init__(self, x=None, y=None):
        self.x, self.y = x, y

    def act(self, ctx, world):
        fn_id = actions.FUNCTIONS.Move_screen.id
        if not world.can(fn_id):
            if world.can(actions.FUNCTIONS.select_army.id):
                return actions.FUNCTIONS.select_army("select")
            return SkillResult(False, "cannot move (no army selected)")
        tx = self.x if self.x is not None else world.camera[0] + 8
        ty = self.y if self.y is not None else world.camera[1]
        return FUNCTIONS.Move_screen(False, [int(tx), int(ty)])


class Expand(BuildStructure):
    """Build a Command Center at a distant mineral patch."""
    name = "expand"

    def __init__(self):
        super(Expand, self).__init__(structure="command_center")

    def act(self, ctx, world):
        thr = world.townhalls()
        if not thr:
            return SkillResult(False, "no town hall")
        home = thr[0]
        far = [p for p in world.mineral_patches
               if math.hypot(home.x - p.x, home.y - p.y) > 15]
        if not far:
            return SkillResult(False, "no distant mineral patch visible")
        site = self.nearest(home, far)
        spot = (site.x + 3, site.y + 3)
        if not ctx.on_screen(spot[0], spot[1]):
            return SkillResult(False, "expansion site off screen")
        workers = world.workers()
        if not workers:
            return SkillResult(False, "no worker")
        w = ctx.nearest_unit(home, workers)
        if not w.selected:
            return ctx.select_point(world, w)
        # same reason as BuildStructure: never gate on available_actions
        return FUNCTIONS.Build_CommandCenter_screen(
            False, [int(spot[0]), int(spot[1])])


_FUNCTION_BY_ID = {f.id: f for f in FUNCTIONS}


def _by_id(fn_id, *args):
    """Call a pysc2 function by numeric id, using its real signature."""
    return _FUNCTION_BY_ID[fn_id](*args)


class SkillContext:
    """Handed to skills; owns the low-level action helpers."""

    TARGET_MARGIN = 2   # tiles of slack around the camera centre

    def select_point(self, world, u, act="select"):
        # feature_units x/y ARE screen pixels (verified in logs/coord3.log)
        return FUNCTIONS.select_point(act, [int(u.x), int(u.y)])

    def select_all_of_type(self, world, u):
        """Select every unit of u's type that is on screen (e.g. all SCVs)."""
        return FUNCTIONS.select_point(
            "select_all_type", [int(u.x), int(u.y)])

    # -- camera steering ----------------------------------------------------
    # VERIFIED (logs/coord3.log): feature_units x/y ARE screen pixels, i.e. the
    # same 0..screen_dim space that select_point/Attack_screen take. So we do
    # NOT need any of the camera_position/minimap maths that misled us here.
    SCREEN_W = 64
    SCREEN_H = 64
    CAMERA_STEP = 4      # minimap units per nudge while hunting off-screen

    def on_screen(self, wx, wy, margin=1):
        """True if these screen-space coords are inside the clickable area."""
        return (margin <= wx < self.SCREEN_W - margin and
                margin <= wy < self.SCREEN_H - margin)

    def center_on(self, world, wx, wy):
        """Nudge the camera toward a target we cannot currently see.

        Note: move_camera takes MINIMAP coords, a different space from
        feature_units. We therefore only ever use this to sweep the map when a
        target is genuinely off-screen; it is a slow hunt, not a solve.
        """
        if self.on_screen(wx, wy):
            return None
        if actions.FUNCTIONS.move_camera.id not in world.available_actions:
            return None
        cw, ch = world.camera
        # step in the direction the camera needs to travel to reveal the point
        tx = cw + (self.CAMERA_STEP if wx > self.SCREEN_W else -self.CAMERA_STEP)
        ty = ch + (self.CAMERA_STEP if wy > self.SCREEN_H else -self.CAMERA_STEP)
        return FUNCTIONS.move_camera([max(0, int(tx)), max(0, int(ty))])

    def approach(self, world, wx, wy):
        """Return a move_camera action if needed, else None."""
        return self.center_on(world, wx, wy)

    @staticmethod
    def screen(ctx, world, wx, wy):
        return Skill.screen(ctx, world, wx, wy)

    @staticmethod
    def in_camera(ctx, world, u):
        return Skill.on_screen(u.x, u.y)

    @staticmethod
    def nearest_unit(src, units):
        return Skill.nearest(src, units)


class SkillSet:
    """Registry mapping skill NAMES -> Skill objects (fresh per invocation)."""

    def __init__(self):
        self.ctx = SkillContext()
        self._builders = {
            "no_op": lambda **kw: NoOp(),
            "train_unit": lambda **kw: TrainUnit(**kw),
            "train_worker": lambda **kw: TrainWorker(),
            "supply_depot": lambda **kw: BuildStructure("supply_depot"),
            "build_supply": lambda **kw: BuildStructure("supply_depot"),
            "barracks": lambda **kw: BuildStructure("barracks"),
            "build_structure": lambda structure="barracks", **kw:
                BuildStructure(structure),
            "gather": lambda **kw: Gather(),
            "attack_nearest": lambda **kw: AttackNearest(),
            "move_out": lambda **kw: MoveOut(**kw),
            "expand": lambda **kw: Expand(),
        }

    def names(self):
        return sorted(self._builders)

    def make(self, name, **kwargs):
        b = self._builders.get(name)
        if b is None:
            raise KeyError("unknown skill %r; available: %s"
                           % (name, ", ".join(self.names())))
        return b(**kwargs)


# --- static tables ----------------------------------------------------------
_PRODUCERS = {
    U.SCV: {U.COMMAND_CENTER},
    U.MARINE: {U.BARRACKS},
    U.MARAUDER: {U.BARRACKS},
    U.PROBE: {U.NEXUS},
    U.ZEALOT: {U.GATEWAY},
    U.STALKER: {U.GATEWAY},
    U.DRONE: {U.HATCHERY},
    U.ZERGLING: {U.HATCHERY},
}

_TRAIN_FN = {
    U.SCV: actions.FUNCTIONS.Train_SCV_quick.id,
    U.MARINE: actions.FUNCTIONS.Train_Marine_quick.id,
    U.MARAUDER: actions.FUNCTIONS.Train_Marauder_quick.id,
    U.PROBE: actions.FUNCTIONS.Train_Probe_quick.id,
    U.ZEALOT: actions.FUNCTIONS.Train_Zealot_quick.id,
    U.STALKER: actions.FUNCTIONS.Train_Stalker_quick.id,
    U.DRONE: actions.FUNCTIONS.Train_Drone_quick.id,
    U.ZERGLING: actions.FUNCTIONS.Train_Zergling_quick.id,
}
