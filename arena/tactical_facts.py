"""Player-local geometry facts. No suggested goals, hidden-unit query or issued action."""
import math


def ground_grid(backend, index):
    grid = getattr(backend, 'grid', None)
    if grid is not None:
        return grid
    cache = getattr(backend, '_tactical_grids', None)
    if cache is None:
        cache = backend._tactical_grids = {}
    if index not in cache:
        try:
            from arena.rally import GroundMap
            cache[index] = GroundMap(backend.map_info_by_player[index].start_raw)
        except (ValueError, AttributeError, TypeError, IndexError):
            cache[index] = None
    return cache[index]


def point_rally_routes(backend, world, index, producer):
    targets = [p[:2] for p in producer.rally_targets if not p[2]]
    if not targets:
        return []
    grid = ground_grid(backend, index)
    rows = []
    for target in targets:
        row = dict(target=[round(v, 1) for v in target],
            threat_scope='currently observed ground weapons only; fog not cleared')
        if grid is None:
            rows.append(dict(row, state='unavailable', reason='Static ground topology absent'))
            continue
        try:
            route = grid.route(producer.pos, target)
        except ValueError:
            rows.append(dict(row, state='unavailable', reason='No static ground route'))
            continue
        threats = []
        for enemy in world.enemy_units:
            if enemy.ground_dps <= 0 or enemy.hallucination:
                continue
            distance = min(math.dist((x+.5, y+.5), enemy.pos) for x, y in route)
            radius = enemy.attack_range+enemy.radius+1
            if distance <= radius:
                threats.append(dict(pos=[round(enemy.x, 1), round(enemy.y, 1)],
                    weapon_range=enemy.attack_range, clearance_radius=round(radius, 2),
                    minimum_route_distance=round(distance, 2)))
        rows.append(dict(row, state='observed', static_path_tiles=len(route),
            source_target_distance=round(producer.distance(target), 1),
            observed_fire_zone_overlap=bool(threats), threat_count=len(threats),
            threat_samples=sorted(threats,key=lambda t:t['minimum_route_distance'])[:3]))
    return rows


def combat_contacts(world, grid, damage):
    if grid is None:
        return []
    damaged = {d['tag'] for d in damage}
    own = sorted((u for u in world.my_units if not u.flying and not u.is_building and u.ground_dps>0),
                 key=lambda u:(u.tag not in damaged, u.tag))[:24]
    rows = []
    for u in own:
        x,y=map(int,u.pos)
        if not (0<=x<grid.w and 0<=y<grid.h):continue
        height=int(grid.height[y,x])
        for enemy in world.enemy_units:
            if enemy.flying or enemy.ground_dps<=0 or enemy.hallucination:continue
            a,b=map(int,enemy.pos)
            distance=u.distance(enemy)
            if distance>max(u.attack_range,enemy.attack_range)+8 or not (0<=a<grid.w and 0<=b<grid.h):continue
            visibility=(int(world.visibility[b,a]) if world.visibility is not None
                        and b<world.visibility.shape[0] and a<world.visibility.shape[1] else None)
            rows.append(dict(own_tag=u.tag,own_pos=[round(u.x,1),round(u.y,1)],own_height=height,
                own_attack_range=u.attack_range,own_recently_damaged=u.tag in damaged,
                own_targeting_contact=u.engaged_target==enemy.tag,
                enemy_pos=[round(enemy.x,1),round(enemy.y,1)],enemy_height=int(grid.height[b,a]),
                higher_than_own=int(grid.height[b,a])>height+4,enemy_position_visibility=visibility,
                distance=round(distance,1),within_own_weapon_range=distance<=u.attack_range+u.radius+enemy.radius,
                source='current own raw contact; terrain visibility is a separate observation'))
    return sorted(rows,key=lambda r:(not r['own_recently_damaged'],not r['higher_than_own'],r['distance']))[:12]
