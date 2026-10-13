"""Player-local sightings sampled every observation, independent of model latency."""
from collections import Counter, deque
from arena.raw_engine import NAMES
from arena.production_lifecycle import PRODUCTION_INTERMEDIATES,REMOVAL_NOTE

# A pending alert carries a full contact snapshot (own_unit_death_observed adds every
# nearby sighting), so an unbounded backlog dominates the battle prompt: measured on the
# 2026-10-10 match, 32 pending alerts were 56,776 of 127,005 payload characters (45%).
# Newest alerts are the actionable ones; older ones collapse into a counted summary.
MAX_PENDING_ALERTS = 8
POSITION_DECIMALS = 1


def compact_positions(value):
    """Round raw world coordinates inside a nested alert record.

    The observation carries full float precision (67.277587890625); one decimal is the same
    tactical information at a fraction of the characters.
    """
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == 'pos' and isinstance(item, (list, tuple)) and len(item) == 2:
                result[key] = [round(item[0], POSITION_DECIMALS), round(item[1], POSITION_DECIMALS)]
            else:
                result[key] = compact_positions(item)
        return result
    if isinstance(value, list):
        return [compact_positions(item) for item in value]
    return value


class Intelligence:
    def __init__(self, emit):
        self.emit = emit
        self.enemies = {}
        self.own = {}
        self.alerts = deque(maxlen=32)
        self.revision = 0
        self.acknowledged = 0
        self.dropped_alerts = 0
        self.evicted_contacts = 0
        self.last_loop = -1

    def alert(self, kind, w, **details):
        self.revision += 1
        if len(self.alerts) == self.alerts.maxlen and self.alerts[0]['revision'] > self.acknowledged:
            self.dropped_alerts += 1
        row = dict(revision=self.revision, kind=kind, game_s=round(w.seconds, 2), **details)
        self.alerts.append(row)
        self.emit('intelligence_event', intel_event=row)

    def update(self, w):
        if w.game_loop <= self.last_loop:
            return False
        self.last_loop = w.game_loop
        previous_revision = self.revision
        visible = {u.tag for u in w.enemy_units}
        fresh = []
        for u in w.enemy_units:
            previous = self.enemies.get(u.tag)
            if previous is None or previous['type_id'] != u.type_id:
                fresh.append(dict(tag=u.tag, unit=NAMES.get(u.type_id, str(u.type_id)), pos=list(u.pos)))
            self.enemies[u.tag] = dict(tag=u.tag, type_id=u.type_id,
                unit=NAMES.get(u.type_id, str(u.type_id)), building=u.is_building,
                pos=list(u.pos), last_seen_game_s=w.seconds, visible=True)
        for tag, row in self.enemies.items():
            row['visible'] = tag in visible
        if fresh:
            self.alert('enemy_sighted', w, contacts=fresh)
        for tag in w.dead_units:
            if tag in self.enemies:
                lost = self.enemies.pop(tag)
                self.alert('enemy_production_intermediate_removed' if lost['type_id'] in PRODUCTION_INTERMEDIATES
                    else 'enemy_death_observed', w, contact=lost,
                    attribution=REMOVAL_NOTE if lost['type_id'] in PRODUCTION_INTERMEDIATES else 'Native death event; killer not established.')
            if tag in self.own:
                lost = self.own.pop(tag)
                recent = [dict(r) for r in self.enemies.values()
                          if w.seconds-r['last_seen_game_s'] <= 8
                          and (r['pos'][0]-lost['pos'][0])**2+(r['pos'][1]-lost['pos'][1])**2 <= 18**2]
                self.alert('own_production_intermediate_removed' if lost.get('type_id') in PRODUCTION_INTERMEDIATES
                    else 'own_unit_death_observed', w, unit=lost,
                           nearby_recent_contacts=recent,
                           attribution=REMOVAL_NOTE if lost.get('type_id') in PRODUCTION_INTERMEDIATES else
                               'Nearby shared-vision sightings; not proof this unit alone revealed them or an enemy kill.')
        # Absence alone never means death (fog, transports and refineries).
        for u in w.my_units:
            self.own[u.tag] = dict(tag=u.tag,type_id=u.type_id,unit=NAMES.get(u.type_id, str(u.type_id)), pos=list(u.pos))
        if len(self.enemies) > 2048:
            oldest = sorted(self.enemies, key=lambda t:self.enemies[t]['last_seen_game_s'])
            evicted = oldest[:len(self.enemies)-2048]
            for tag in evicted:
                del self.enemies[tag]
            self.evicted_contacts += len(evicted)
            self.alert('contact_memory_capacity', w, evicted_tags=evicted)
        return self.revision != previous_revision

    def acknowledge(self, revision):
        self.acknowledged = max(self.acknowledged, revision)

    def public(self, w):
        # Group nearby contacts to keep battle prompts bounded. Each tag counts once.
        groups = {}
        for row in self.enemies.values():
            age = max(0, w.seconds-row['last_seen_game_s'])
            state = 'visible' if row['visible'] else ('last_seen' if row['building'] or age <= 180 else 'old_unconfirmed')
            key = (row['unit'], state, int(row['pos'][0]//12), int(row['pos'][1]//12))
            if key not in groups:
                groups[key] = dict(unit=row['unit'], state=state, count=0,
                    area_center=[key[2]*12+6, key[3]*12+6], age_min_s=round(age, 1), age_max_s=round(age, 1))
            g = groups[key];g['count'] += 1
            g['age_min_s'] = min(g['age_min_s'], round(age, 1))
            g['age_max_s'] = max(g['age_max_s'], round(age, 1))
        values = sorted(groups.values(), key=lambda g:(g['state']!='visible', g['age_min_s']))
        alerts = []
        for row in self.alerts:
            if row['revision'] <= self.acknowledged:continue
            alert = compact_positions(dict(row))
            for field in ('contacts','nearby_recent_contacts'):
                contacts = alert.get(field,[])
                if len(contacts)>12:
                    alert[field] = contacts[:12]
                    alert[field+'_summary'] = dict(total=len(contacts),
                        counts_by_type=dict(Counter(r['unit'] for r in contacts)),
                        omitted_details=len(contacts)-12,full_record='intelligence_observed journal')
            alerts.append(alert)
        # Bound the pending backlog: keep the newest actionable alerts and count the rest, so a
        # delivery failure or a long fight cannot inflate every later prompt without limit.
        pending = alerts[-MAX_PENDING_ALERTS:]
        omitted = alerts[:-MAX_PENDING_ALERTS]
        omitted_summary = None
        if omitted:
            omitted_summary = dict(total=len(omitted),
                counts_by_kind=dict(Counter(a['kind'] for a in omitted)),
                newest_omitted_game_s=max(a.get('game_s') or 0 for a in omitted),
                full_record='intelligence_observed journal',
                note='older pending alerts collapsed: newest kept, older counted only')
        return dict(revision=self.revision, observation_game_s=round(w.seconds, 2),
            contacts=values[:96], omitted_contact_groups=max(0,len(values)-96),
            pending_alerts=pending, pending_alerts_omitted=omitted_summary,
            older_unacknowledged_alerts_in_journal=self.dropped_alerts,
            evicted_contacts=self.evicted_contacts,
            meaning='Last-seen positions are historical, not current targets or confirmed surviving army. '
                    'Sightings survive scout death. No enemy information outside this player observation.')
