"""Explicit LLM orbital orders; instant abilities confirmed from player-local observations."""
import math
from arena.raw_engine import command

ABILITIES = {
    "scan": (399, "ScannerSweep"),
    "call_mule": (171, "CalldownMULE"),
    "supply_drop": (255, "SupplyDrop"),
}
LANDMARKS = ("home", "expansion", "enemy_start", "nearest_enemy")
SUPPLY_BUFF = 25


def specification(tree, race, skill, args):
    if race != "terran":
        raise ValueError("orbital abilities require Terran")
    allowed = {"target"} if skill == "scan" else {"base"}
    if set(args) - allowed:
        raise ValueError(skill + " allowed args: " + str(sorted(allowed)))
    key = "target" if skill == "scan" else "base"
    target = args.get(key, "home")
    if skill == "scan" and isinstance(target, (list, tuple)):
        # Scan also accepts an explicit [x, y]: the commander may have a concrete place to
        # look at (a remembered fight, a likely expansion) and no landmark names it.
        if len(target) != 2 or any(not isinstance(v, (int, float)) or isinstance(v, bool)
                                   or not math.isfinite(v) for v in target):
            raise ValueError(key + " must be one of " + str(LANDMARKS) + " or a finite [x, y] pair")
        target = [float(target[0]), float(target[1])]
    else:
        choices = LANDMARKS if skill == "scan" else ("home", "expansion")
        if target not in choices:
            raise ValueError(key + " must be one of " + str(choices) + " or a finite [x, y] pair")
    ability, link = ABILITIES[skill]
    if ability not in tree.abilities or tree.abilities[ability].link_name != link:
        raise ValueError("engine orbital ability identity mismatch: " + link)
    return dict(kind="orbital", name=skill, ability=ability, target=target)


class OrbitalRun:
    # These instant abilities may coexist with SCV training; they never cancel its order.
    # Reserve the caster between send and confirmation to avoid double spending.
    def __init__(self, spec, args):
        self.spec = spec
        self.args = dict(args)
        self.tags = set()
        self.pending = None
        self.next_at = 0

    def close(self):
        self.tags.clear()

    def summary(self):
        return dict(kind="orbital", ability=self.spec["name"], args=self.args,
                    pending=self.pending, unit_tags=sorted(self.tags))

    def explicit_point(self, q, target):
        """Snap an explicit scan coordinate to a walkable cell centre when a map is available.

        Unlike rally targets this never rejects the request: a scan is an observation, so a
        coordinate over impassable terrain still reveals the area around it. The snap only
        avoids aiming the sweep at a cliff edge by rounding error.
        """
        x, y = target[0], target[1]
        grid = getattr(q.backend, "grid", None)
        if grid is None:
            infos = getattr(q.backend, "map_info_by_player", None)
            if infos is not None:
                try:
                    from arena.rally import GroundMap
                    grid = GroundMap(infos[q.index].start_raw)
                except (ValueError, AttributeError, TypeError, IndexError):
                    grid = None
        if grid is None:
            return (x, y)
        cx, cy = grid.nearest((x, y))
        return (cx + .5, cy + .5)

    def anchor(self, w, q):
        name = self.spec["target"]
        home = q.backend.homes[q.index]
        if isinstance(name, (list, tuple)):
            return self.explicit_point(q, name)
        if name == "home":
            return home
        if name == "enemy_start":
            return q.backend.enemy_start(q.index)
        if name == "nearest_enemy":
            return min(w.enemy_units, key=lambda u: u.distance(home)).pos if w.enemy_units else None
        bases = [u for u in w.my_units if u.type_id in (18,130,132) and u.done
                 and not u.flying and u.distance(home) > 15]
        return min(bases, key=lambda u: u.distance(home)).pos if bases else None

    @staticmethod
    def covered(w, pos):
        return any(e[0] == 6 and e[1] == 1 and math.dist((e[2],e[3]),pos) < 2
                   for e in w.effects)

    def tick(self, w, q, t, used, bank, out):
        try:
            self.advance(w, q, t, used, out)
        except Exception as error:
            reason = type(error).__name__ + ": " + str(error)
            q.event("orbital_failed", task_id=t.id, skill=t.skill, error=reason,
                    operation=self.summary())
            q.update(t, "failed", reason)
        return bank

    def advance(self, w, q, t, used, out):
        name = self.spec["name"]
        if self.pending:
            p = self.pending
            errors = [(tag,a,code) for tag,a,code in w.errors
                      if tag == p["caster"] and a == self.spec["ability"]]
            if errors:
                from s2clientprotocol import error_pb2
                raise RuntimeError("engine rejected orbital ability: " +
                                   str([(tag,a,error_pb2.ActionResult.Name(code)) for tag,a,code in errors]))
            caster = w.by_tag(p["caster"])
            spent = caster is not None and caster.energy <= p["energy"] - 40
            evidence = None
            if name == "scan" and self.covered(w, p["pos"]):
                evidence = dict(effect_id=6, pos=p["pos"])
            elif name == "call_mule":
                new = [u for u in w.my(268) if u.tag not in p["before_mules"]
                       and u.distance(p["pos"]) < 6]
                if new:
                    evidence = dict(mule_tags=[u.tag for u in new],
                                    mining_confirmed=False, note="spawn observed; income is not yet proven")
            elif name == "supply_drop":
                depot = w.by_tag(p["target"])
                if depot and depot.alliance == 1 and SUPPLY_BUFF in depot.buffs:
                    evidence = dict(depot_tag=depot.tag, buff_id=SUPPLY_BUFF,
                                    supply_before=p["supply"], supply_now=w.supply_cap)
            if spent and evidence:
                q.event("orbital_confirmed", task_id=t.id, skill=t.skill,
                        energy_before=p["energy"], energy_now=caster.energy, **evidence)
                q.update(t, "completed", "observed energy spent and " + str(evidence))
                return
            if caster is None:
                raise RuntimeError("orbital caster lost before effect confirmation")
            if w.seconds - p["sent"] > 8:
                raise TimeoutError("orbital effect/energy not confirmed in 8 game seconds; no automatic recast")
            return
        deadline = 30 if name == "scan" else 120
        if w.seconds - t.created > deadline:
            raise TimeoutError("orbital request expired; fresh LLM decision required")
        if w.seconds < self.next_at:
            return
        self.next_at = w.seconds + .5
        pos = self.anchor(w, q)
        if pos is None:
            q.update(t, "waiting", "requested landmark unavailable; no target fallback")
            return
        if name == "scan" and self.covered(w, pos):
            q.event("orbital_already_covered", task_id=t.id, skill=t.skill, pos=pos, energy_spent=0)
            q.update(t, "completed", "existing owned scanner sweep covers target; no recast")
            return
        blocked = q.defense_tags | {
            tag for task in q.tasks if task.active and task is not t and task.program
            and not ((getattr(task.program, "spec", None) or {}).get("kind") == "production")
            for tag in task.program.tags}
        actors = [u for u in w.my(132) if u.done and not u.flying and u.energy >= 50
                  and u.tag not in blocked]
        if not actors:
            q.update(t, "waiting", "need completed grounded OrbitalCommand with 50 energy")
            return
        available = q.backend.available_abilities(q.index, [u.tag for u in actors])
        actors = [u for u in actors if q.backend.tech_tree.ability_available(
            self.spec["ability"], available.get(u.tag, set()))]
        if not actors:
            q.update(t, "waiting", "orbital ability unavailable in engine query")
            return
        caster = min(actors, key=lambda u: u.distance(pos))
        target = None
        if name == "call_mule":
            patches = [u for u in w.units if u.mineral and u.mineral_contents > 0
                       and u.distance(pos) < 13]
            if not patches:
                q.update(t, "waiting", "no observed nonempty mineral patch at requested base")
                return
            target_unit = max(patches, key=lambda u: u.mineral_contents)
            target, pos = target_unit.tag, target_unit.pos
        elif name == "supply_drop":
            reserved_targets = {task.program.pending["target"] for task in q.tasks
                                if task.active and task is not t
                                and isinstance(task.program, OrbitalRun) and task.program.pending
                                and task.program.spec["name"] == "supply_drop"}
            depots = [u for u in w.my_units if u.type_id in (19,47) and u.done
                      and u.tag not in reserved_targets
                      and SUPPLY_BUFF not in u.buffs and u.distance(pos) < 25]
            if not depots or w.supply_cap >= 200:
                q.update(t, "waiting", "need completed unboosted depot near base and supply cap <200")
                return
            target_unit = min(depots, key=lambda u: u.distance(pos))
            target, pos = target_unit.tag, target_unit.pos
        if not q.issue(t, command(self.spec["ability"], [caster.tag],
                                  pos=pos if target is None else None, target=target), out):
            return
        self.tags = {caster.tag}
        used.add(caster.tag)
        self.pending = dict(caster=caster.tag, target=target, pos=pos, energy=caster.energy,
                            sent=w.seconds, before_mules=[u.tag for u in w.my(268)], supply=w.supply_cap)
        q.event("orbital_order_issued", task_id=t.id, skill=t.skill, caster=caster.tag,
                ability_id=self.spec["ability"], target_tag=target, target_pos=pos,
                energy_before=caster.energy, execution="sent; effect not yet confirmed")
        q.update(t, "running", "orbital ability issued; awaiting energy and observed effect")
