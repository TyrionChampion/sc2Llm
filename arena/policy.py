"""Policy: turn LLM decisions into skill executions.

Key design point
----------------
The game runs ~37 steps/second but an LLM call takes seconds. Calling the LLM
every step would be absurd (10k+ calls per match). So decisions are made on a
slow cadence (every N game steps) and the chosen skill is then *pumped* every
step in between. That is exactly how the SKILL layer is meant to be used.
"""
import json
import re
import threading
import time

from arena.core import SkillSet
from arena.context import EngagementLog, DecisionMemory

SYSTEM_PROMPT = (
    "You are a StarCraft II commander playing Terran. You decide at a high "
    "level; a separate executor performs the clicking.\n"
    "Reply with ONE JSON object and nothing else:\n"
    '  {"skill": "<skill_name>", "args": {<optional>}, "reason": "<short>"}\n'
    "Choose the single most useful next objective. Rules of thumb:\n"
    "- Keep workers mining early; build a Supply Depot before supply caps out.\n"
    "- Get a Barracks before trying to make army units.\n"
    "- Only attack when you actually have army."
)


def build_user_prompt(world, skills, history=None, engagements=None, memory=None):
    lines = ["AVAILABLE SKILLS"]
    for n in sorted(skills.names()):
        lines.append("  - %s" % n)
    lines += ["", "ARG HINTS",
        '  train_unit: {"unit": "marine"}',
        '  move_out:   {"x": <tile>, "y": <tile>}  (optional)',
        "", "OBSERVATION RULES",
        "Enemy counts in this legacy screen interface cover the camera view only. Camera changes and disappearance do not prove deaths.",
        "History records model intentions, not execution success. Keep current observations authoritative.",
        "", "CURRENT STATE", world.summary(), "", "ENGAGEMENT LOG",
        json.dumps(engagements or {'windows':[],'status':'no observation log supplied'},ensure_ascii=False),
        "", "STRATEGY MILESTONES",
        json.dumps((memory or {}).get('milestones',[]),ensure_ascii=False), "", "RECENT DECISIONS"]
    if history:
        for h in history[-8:]:
            text=str(h);lines.append("  - "+text[:240]+(" [truncated]" if len(text)>240 else ""))
    lines += ["", "Respond with the JSON object now."]
    return "\n".join(lines)


_JSON_RE = re.compile(r"\{.*\}", re.S)


def parse_decision(text):
    """Pull a decision dict out of possibly-chatty model output."""
    if not text:
        return None, "empty response"
    m = _JSON_RE.search(text)
    if not m:
        return None, "no JSON object found in: %r" % text[:200]
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return None, "bad JSON (%s): %r" % (e, m.group(0)[:200])
    if not isinstance(d, dict) or "skill" not in d:
        return None, "JSON lacks a 'skill' field: %r" % str(d)[:200]
    return d, None


class Policy:
    """Slow LLM decisions + fast skill pumping.

    tick(world) -> (action, info)
        action: a pysc2 action, or None if it could not act this step
        info:   dict with what happened (for logging / the UI)
    """

    def __init__(self, client, decide_every=24, history_len=8, log=None,
                 fail_limit=3):
        self.client = client
        self.fail_limit = fail_limit
        self.broken_skills = set()
        self.decide_every = decide_every
        self.skills = SkillSet()
        self.skill = None
        self.skill_name = None
        self.skill_args = {}
        self.steps_since_decision = 10 ** 9   # force an early decision
        self.history = []
        self.context_lock=threading.RLock()
        self.engagements=EngagementLog(lambda kind,**data:self._log(kind+" "+json.dumps(data,ensure_ascii=False)),mode="screen")
        self.decision_memory=DecisionMemory()
        self.history_len = history_len
        self.log = log or []
        self.decisions = 0
        self.errors = 0
        self.total_llm_latency = 0.0

    # -- internals ----------------------------------------------------------
    def _log(self, msg):
        stamp = time.strftime("%H:%M:%S")
        entry = "[%s] %s" % (stamp, msg)
        self.log.append(entry)
        if len(self.log) > 200:
            del self.log[:-200]
        return entry

    def _prompt(self,world):
        with self.context_lock:
            return build_user_prompt(world,self.skills,list(self.history),self.engagements.public(),self.decision_memory.public())

    def _record_decision(self,world,name,args,reason):
        with self.context_lock:
            self.history.append("%s %s (%s)" % (name,args or "",reason))
            del self.history[:-self.history_len]
            self.decision_memory.add(world.game_loop,dict(skill=name,reason=reason))

    def _decide(self, world):
        user = self._prompt(world)
        t0 = time.time()
        text = self.client.chat(SYSTEM_PROMPT, user)
        dt = time.time() - t0
        self.total_llm_latency += dt
        self.decisions += 1

        d, err = parse_decision(text)
        if d is None:
            self.errors += 1
            self._log("LLM parse FAILED: %s" % err)
            return None
        name = str(d.get("skill", "")).strip()
        args = d.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        if name not in self.skills.names():
            self.errors += 1
            self._log("LLM chose unknown skill %r" % name)
            return None
        if name in self.broken_skills:
            self.errors += 1
            self._log("LLM re-chose broken skill %r (ignored)" % name)
            return None
        try:
            self.skill = self.skills.make(name, **args)
        except TypeError as e:
            self.errors += 1
            self._log("skill %s bad args %s: %s" % (name, args, e))
            return None
        self.skill_name = name
        self.skill_args = args
        reason = str(d.get("reason", ""))[:80]
        self._log("LLM(%.1fs) -> %s%s  # %s"
                  % (dt, name, (" %s" % args) if args else "", reason))
        self._record_decision(world,name,args,reason)
        return name

    # -- public -------------------------------------------------------------
    def tick(self, world):
        """Advance one game step. Returns (action_or_None, info)."""
        self.engagements.update(world)
        if self.skill is None or self.steps_since_decision >= self.decide_every:
            self.steps_since_decision = 0
            if self._decide(world) is None and self.skill is None:
                # nothing usable at all: fall back so the match keeps moving
                self.skill = self.skills.make("gather")
                self.skill_name = "gather(fallback)"
                self._log("[explicit fallback] no valid initial decision; gathering until next plan")
        self.steps_since_decision += 1

        try:
            res = self.skill.act(self.skills.ctx, world)
        except Exception as e:  # noqa: BLE001 - never kill the match silently
            self.errors += 1
            self._log("skill %s raised %s: %s"
                      % (self.skill_name, type(e).__name__, e))
            return None, {"skill": self.skill_name, "error": str(e)}

        if hasattr(res, "ok"):
            # "waiting" means the world is not ready (e.g. the Barracks is still
            # being built). That is not a malfunction, so it must not count
            # toward the breaker.
            if getattr(res, "waiting", False):
                return None, {"skill": self.skill_name, "msg": res.msg,
                              "ok": False, "waiting": True}
            # A skill that cannot act is normal for a tick or two (e.g. waiting
            # on supply). But a skill failing forever means it is BROKEN, and
            # retrying it silently is how the match ends up with an AI that
            # stands still while its bank grows. Trip a breaker instead.
            failed = getattr(self.skill, "fail_count", 0) + 1
            self.skill.fail_count = failed
            # let skills that can self-heal try a different approach
            note = getattr(self.skill, "note_failure", None)
            if callable(note):
                try:
                    note()
                except Exception:
                    pass
            if failed >= self.fail_limit:
                self._log("CIRCUIT BREAK: %s failed %d times (%s); "
                          "abandoning it"
                          % (self.skill_name, failed, res.msg))
                self.broken_skills.add(self.skill_name)
                self.skill = self.skills.make("gather")
                self.skill_name = "gather(after-break)"
                self.skill.fail_count = 0
                # force a fresh decision soon so it can pick a different plan
                self.steps_since_decision = self.decide_every
                return None, {"skill": self.skill_name, "msg": res.msg,
                              "ok": False, "circuit_break": True}
            return None, {"skill": self.skill_name, "msg": res.msg,
                          "ok": False}
        self.skill.fail_count = 0
        return res, {"skill": self.skill_name, "ok": True}

    def stats(self):
        return {
            "decisions": self.decisions,
            "llm_errors": self.errors,
            "avg_llm_latency": round(
                self.total_llm_latency / self.decisions, 2)
            if self.decisions else 0.0,
            "provider_calls": self.client.calls,
            "provider": self.client.p.name,
            "model": self.client.model,
            "current_skill": self.skill_name,
            "broken_skills": sorted(self.broken_skills),
        }


class AsyncPolicy(Policy):
    """Policy whose LLM calls never block the game loop.

    The problem this solves
    -----------------------
    A synchronous Policy calls the LLM inside tick(), so the match freezes for
    seconds on every decision -- the whole simulator stalls waiting on a
    network round trip.

    Here the game loop only ever reads a cached decision. A worker thread does
    the slow part and drops the result into a slot; the main thread picks it up
    on a later step. Consequences:
      - the game runs at full speed the entire time
      - the commander plays with a stale world view (that is the honest
        trade-off of thinking while the world moves on)

    Usage:
        pol = AsyncPolicy(client)
        pol.update_world(world)      # main thread, cheap
        action, info = pol.tick(world)
        ...
        pol.stop()
    """

    def __init__(self, client, decide_every=24, min_interval=0.0,
                 history_len=8, log=None, fail_limit=3):
        super(AsyncPolicy, self).__init__(
            client, decide_every=decide_every, history_len=history_len,
            log=log, fail_limit=fail_limit)
        self._wlock = threading.Lock()
        self._world = None
        self._world_loop = -1
        self._pending = False
        self._slot = None            # (skill_name, args, reason)
        self._think_started = 0.0
        self._last_finish = 0.0
        self._min_interval = min_interval
        self._stop = threading.Event()
        self._worker = None

    # -- main thread --------------------------------------------------------
    def update_world(self, world):
        with self._wlock:
            self._world = world
            self._world_loop = world.game_loop

    def _decide(self, world):
        """NEVER call the model here -- this runs on the game loop's thread.

        BUG THIS FIXES: AsyncPolicy used to delegate to Policy.tick(), whose
        body calls self._decide() synchronously. The worker thread was doing the
        real work, but the main thread ALSO blocked on a second call every
        decide_every steps. Measured cost: with ARENA_DELAY=3 the loop advanced
        18 steps per 4s instead of 110, i.e. the "async" policy was still fully
        blocked (fps 2.4 vs 24.2). Returning None here keeps the main loop free;
        the worker thread owns all decisions and drops them into self._slot.
        """
        return None

    def tick(self, world):
        """Never blocks. Applies a finished decision if one is waiting."""
        self.update_world(world)
        self._apply_ready()
        if self.skill is None:
            self.steps_since_decision = 10 ** 9
            self._apply_fallback()
        return super(AsyncPolicy, self).tick(world)

    # -- worker thread ------------------------------------------------------
    def start(self):
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            time.sleep(0.15)
            if self._pending:
                continue
            now = time.time()
            if now - self._last_finish < self._min_interval:
                continue
            with self._wlock:
                w = self._world
            if w is None:
                continue
            # Decide when the skill is stale or we have not chosen one yet.
            if self.skill is not None and \
                    self.steps_since_decision < self.decide_every:
                continue
            self._pending = True
            self._think_started = now
            try:
                self._think(w)
            except Exception as e:  # noqa: BLE001 - report, keep playing
                self.errors += 1
                self._log("worker error %s: %s" % (type(e).__name__, e))
            finally:
                self._pending = False
                self._last_finish = time.time()

    def _think(self, world):
        """Runs in the worker thread; only touches the slot and the log."""
        user = self._prompt(world)
        t0 = time.time()
        text = self.client.chat(SYSTEM_PROMPT, user)
        dt = time.time() - t0
        self.total_llm_latency += dt
        self.decisions += 1
        d, err = parse_decision(text)
        if d is None:
            self.errors += 1
            self._log("LLM parse FAILED: %s" % err)
            return
        name = str(d.get("skill", "")).strip()
        args = d.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        if name not in self.skills.names():
            self.errors += 1
            self._log("LLM chose unknown skill %r" % name)
            return
        reason = str(d.get("reason", ""))[:80]
        self._log("LLM(%.1fs) -> %s%s  # %s"
                  % (dt, name, (" %s" % args) if args else "", reason))
        self._record_decision(world,name,args,reason)
        self._slot = (name, args, reason)

    # -- main thread, apply -------------------------------------------------
    def _apply_ready(self):
        slot, self._slot = self._slot, None
        if slot is None:
            return
        name, args, _reason = slot
        try:
            self.skill = self.skills.make(name, **args)
        except TypeError as e:
            self.errors += 1
            self._log("skill %s bad args %s: %s" % (name, args, e))
            return
        self.skill_name = name
        self.skill_args = args
        self.steps_since_decision = 0

    def _apply_fallback(self):
        try:
            self.skill = self.skills.make("gather")
            self.skill_name = "gather(fallback)"
            self._log("[explicit fallback] waiting for initial async plan; gathering")
        except Exception as error:
            self._log("[fallback FAILED] gather: %s: %s" % (type(error).__name__, error))
            self.skill = None

    def thinking(self):
        """True while a request is in flight (for the UI)."""
        return self._pending

    def stats(self):
        s = super(AsyncPolicy, self).stats()
        s["thinking"] = self._pending
        s["mode"] = "async (non-blocking)"
        return s
