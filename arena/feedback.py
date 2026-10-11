"""Durable feedback delivery and player-local action evidence. No cause guessing."""
import json
from pathlib import Path
import tempfile
import threading
from s2clientprotocol import error_pb2
from arena.raw_engine import NAMES


class FeedbackJournal:
    def __init__(self, player, directory="logs/feedback"):
        Path(directory).mkdir(parents=True, exist_ok=True)
        f = tempfile.NamedTemporaryFile(mode="w", prefix=f"p{player}-", suffix=".jsonl",
                                        dir=directory, delete=False)
        self.path = str(Path(f.name).resolve()); f.close()
        self.lock = threading.Lock()
        self.pending = []; self.next_id = 1; self.delivered = 0
        self.requests = 0

    def audit(self, record):
        with self.lock, open(self.path, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()

    def add(self, kind, game_loop, **details):
        event = dict(id=self.next_id, kind=kind, game_loop=game_loop, **details)
        self.audit(dict(record="feedback_event", event=event))
        self.next_id += 1; self.pending.append(event)
        return event

    def batch(self, max_events=24, max_chars=24000, prioritize_failures=False):
        events = []; size = 0
        pending=self.pending
        if prioritize_failures and pending:
            def urgent(e):
                return any(word in e['kind'] for word in ('failed','invalid','rejected','expired')) or e.get('state')=='failed'
            # Keep one oldest event to avoid starving routine feedback. Errors stay complete.
            pending=pending[:1]+[e for e in pending[1:] if urgent(e)]+[e for e in pending[1:] if not urgent(e)]
        for event in pending:
            length = len(json.dumps(event, ensure_ascii=False))
            if events and len(events)>=max_events:break
            if events and size+length>max_chars:
                # Do not let a giant failure sit behind one routine event every turn.
                # Send that first failure whole; the visible oversized flag explains the exception.
                if not (prioritize_failures and len(events)==1 and urgent(event) and not urgent(events[0])):break
            events.append(event); size += length
        return dict(events=events, pending_total=len(self.pending),
                    order="oldest then failures then remaining" if prioritize_failures else "oldest first",
                    oversized_event=bool(size>max_chars),
                    deferred_count=len(self.pending)-len(events),
                    delivery_rule="Retained until a model API call returns successfully; this does not prove model understanding.")

    def acknowledge(self, ids, request_id):
        ids = set(ids)
        self.audit(dict(record="feedback_delivered", request_id=request_id, feedback_ids=sorted(ids)))
        self.delivered += sum(e["id"] in ids for e in self.pending)
        self.pending = [e for e in self.pending if e["id"] not in ids]

    def stats(self):
        return dict(pending=len(self.pending), delivered=self.delivered,
                    total=self.next_id-1, journal=self.path)


def entity_evidence(w, tag):
    if tag is None: return None
    u = w.by_tag(tag)
    if u is None:
        return dict(tag=tag, status="confirmed_dead" if tag in w.dead_units else "absent_from_player_observation")
    return dict(tag=tag, status="observed", unit=NAMES.get(u.type_id, str(u.type_id)),
                alliance=u.alliance, health=u.health, position=list(u.pos), orders=list(u.orders))


def describe_rejections(before, after, batch, bindings, response_failures=(), sent_at=None):
    """Match exact response actions first; observation-only ambiguity stays explicit."""
    result = []
    for tag, ability, code in after.errors:
        exact = [n for n, c in response_failures if c == code and
                 batch[n].action_raw.unit_command.ability_id == ability and
                 tag in batch[n].action_raw.unit_command.unit_tags]
        candidates = exact or [n for n, a in enumerate(batch)
                               if a.action_raw.unit_command.ability_id == ability and
                               tag in a.action_raw.unit_command.unit_tags]
        actions = []
        for n in candidates:
            raw = batch[n].action_raw.unit_command
            binding = bindings[n] if n < len(bindings) else None
            context = (binding[1] if binding and binding[0] == batch[n].SerializeToString()
                       else {"source":"unregistered_action", "task_id":None})
            target = raw.target_unit_tag if raw.HasField("target_unit_tag") else None
            position = ([raw.target_world_space_pos.x, raw.target_world_space_pos.y]
                        if raw.HasField("target_world_space_pos") else None)
            actions.append(dict(batch_index=n, ability_id=ability, actor_tags=list(raw.unit_tags),
                                target_tag=target, target_position=position, queue_command=raw.queue_command, context=context,
                                target_before=entity_evidence(before, target),
                                target_after=entity_evidence(after, target)))
        result.append(dict(kind="engine_action_rejected", unit_tag=tag, ability_id=ability,
            result_code=code, result_name=error_pb2.ActionResult.Name(code),
            origin="action_response" if exact else "observation_action_error",
            association="exact response action" if exact else "candidate by unit/ability only; not proven",
            result_scope="action-level result; per-unit outcome unavailable" if exact else "unit-level observation error",
            observation_before_loop=before.game_loop, observation_after_loop=after.game_loop,
            batch_sent_unix_s=sent_at,
            actor_before=entity_evidence(before, tag), actor_after=entity_evidence(after, tag),
            action_match="unique" if len(actions)==1 else ("ambiguous" if actions else "unmatched; may refer to an earlier batch"),
            actions=actions, cause="unknown: engine supplied an error code, not an explanation",
            handling="Command rejected. Script continues from fresh observations; this event alone does not confirm recovery."))
    return tuple(result)
