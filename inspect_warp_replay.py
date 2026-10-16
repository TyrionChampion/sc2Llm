"""Player-local native replay command evidence; zero model calls/orders."""
import argparse,json,math
from pathlib import Path
from absl import flags
from pysc2 import run_configs
from s2clientprotocol import sc2api_pb2 as pb
from s2clientprotocol import query_pb2 as query


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--replay',type=Path,required=True)
    p.add_argument('--player',type=int,choices=(1,2),default=1)
    p.add_argument('--terrain-point',type=float,nargs=2,help='Audit static walkability around a requested coordinate')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--from-loop',type=int,default=12700)
    p.add_argument('--to-loop',type=int,default=13700)
    p.add_argument('--step',type=int,default=4)
    p.add_argument('--actor-tags',type=int,nargs='+',default=[],help='Additional owned actors whose native queues to inspect')
    p.add_argument('--query-abilities',action='store_true',help='Include native ready abilities for selected owned actors')
    p.add_argument('--action-abilities',type=int,nargs='+',default=[1413,1414,1415])
    p.add_argument('--actions-for-actors',action='store_true',help='Restrict recorded commands to selected actor tags')
    p.add_argument('--target-point',type=float,nargs=2,help='Query native ground reachability for selected owned actors to this point')
    p.add_argument('--product-types',type=int,nargs='+',default=[73,74,77],help='Owned product types for birth/incomplete-product evidence')
    args=p.parse_args();flags.FLAGS(['inspect-warp-replay'])
    if args.output.exists():raise ValueError('Do not overwrite replay evidence')
    if not 0<=args.from_loop<args.to_loop or not 1<=args.step<=16:p.error('invalid replay interval')
    report=dict(replay=str(args.replay),player=args.player,disable_fog=False,model_calls=0,
                mode='offline recorded replay; no match result or budget changes',
                from_loop=args.from_loop,to_loop=args.to_loop,samples=[],births=[],actions=[])
    try:
        rc=run_configs.get(version='4.10.0')
        data=args.replay.read_bytes()
        with rc.start(want_rgb=False) as c:
            info=c.replay_info(data)
            report['engine_version']=info.game_version;report['map_path']=info.local_map_path
            c.start_replay(pb.RequestStartReplay(replay_data=data,
                map_data=rc.map_data(info.local_map_path),observed_player_id=args.player,
                disable_fog=False,options=pb.InterfaceOptions(raw=True,score=True)))
            structures={u.unit_id for u in c.data_raw().units if 8 in u.attributes} if args.target_point else set()
            if args.terrain_point:
                from arena.rally import GroundMap
                grid=GroundMap(c.game_info().start_raw)
                candidates=[(x+.5,y+.5) for y in range(grid.h) for x in range(grid.w) if grid.free(x,y)]
                closest=min(candidates,key=lambda point:math.dist(point,args.terrain_point))
                report['terrain_point']=dict(requested=args.terrain_point,grid_size=[grid.w,grid.h],
                    closest_walkable_point=closest,distance=math.dist(closest,args.terrain_point),
                    scope='Static terrain only; not dynamic reachability, enemies or a selected retreat target')
            if args.from_loop:c.step(args.from_loop)
            known=None
            while True:
                response=c.observe();o=response.observation
                units=[u for u in o.raw_data.units if u.alliance==1]
                now={u.tag for u in units}
                for u in units:
                    if known is not None and u.tag not in known and u.unit_type in args.product_types:
                        report['births'].append(dict(loop=o.game_loop,tag=u.tag,type_id=u.unit_type,
                            pos=[u.pos.x,u.pos.y],progress=u.build_progress))
                known=now if known is None else known|now
                gates=[u for u in units if u.unit_type==133]
                row=dict(loop=o.game_loop,minerals=o.player_common.minerals,gas=o.player_common.vespene,
                    observed_dead_tags=list(o.raw_data.event.dead_units),
                    supply=[o.player_common.food_used,o.player_common.food_cap],
                    gates=[dict(tag=u.tag,pos=[u.pos.x,u.pos.y],powered=u.is_powered,
                        orders=[dict(ability=q.ability_id,progress=q.progress) for q in u.orders],buffs=list(u.buff_ids)) for u in gates],
                    products=[dict(tag=u.tag,type_id=u.unit_type,pos=[u.pos.x,u.pos.y],progress=u.build_progress)
                        for u in units if u.unit_type in args.product_types and u.build_progress<1],
                    actors=[dict(tag=u.tag,type_id=u.unit_type,pos=[u.pos.x,u.pos.y],progress=u.build_progress,
                        radius=u.radius,energy=u.energy,buffs=list(u.buff_ids),
                        orders=[dict(ability=q.ability_id,progress=q.progress,
                            target_tag=q.target_unit_tag or None,
                            target_pos=[q.target_world_space_pos.x,q.target_world_space_pos.y]
                                if q.HasField('target_world_space_pos') else None) for q in u.orders])
                        for u in units if u.tag in args.actor_tags])
                if args.query_abilities:
                    selected=[u for u in units if u.tag in args.actor_tags]
                    ready=c.query(query.RequestQuery(abilities=[query.RequestQueryAvailableAbilities(unit_tag=u.tag)
                        for u in selected])).abilities if selected else []
                    if len(ready)!=len(selected):raise ValueError('incomplete replay ability query response')
                    row['native_abilities']=[dict(tag=u.tag,ready=sorted(a.ability_id for a in r.abilities))
                        for u,r in zip(selected,ready)]
                if args.target_point:
                    selected=[u for u in units if u.tag in args.actor_tags]
                    distances=c.query(query.RequestQuery(pathing=[query.RequestQueryPathing(
                        unit_tag=u.tag,end_pos=dict(x=args.target_point[0],y=args.target_point[1])) for u in selected])).pathing if selected else []
                    row['native_paths']=[dict(tag=u.tag,target=args.target_point,distance=d.distance)
                        for u,d in zip(selected,distances)]
                    controls=[u for u in units if u.unit_type not in structures and not u.is_flying
                        and u.tag not in args.actor_tags and u.unit_type==45][:2]
                    distances=c.query(query.RequestQuery(pathing=[query.RequestQueryPathing(
                        unit_tag=u.tag,end_pos=dict(x=args.target_point[0],y=args.target_point[1])) for u in controls])).pathing if controls else []
                    row['path_controls']=[dict(tag=u.tag,type_id=u.unit_type,pos=[u.pos.x,u.pos.y],
                        target=args.target_point,distance=d.distance) for u,d in zip(controls,distances)]
                    row['own_nearby_buildings']=[dict(tag=u.tag,type_id=u.unit_type,pos=[u.pos.x,u.pos.y],radius=u.radius,
                        progress=u.build_progress) for u in units if u.unit_type in structures and any(
                        math.dist([u.pos.x,u.pos.y],[a.pos.x,a.pos.y])<=14 for a in selected)]
                report['samples'].append(row)
                for a in response.actions:
                    if a.HasField('action_raw') and a.action_raw.HasField('unit_command'):
                        cmd=a.action_raw.unit_command
                        if (cmd.ability_id in args.action_abilities and args.from_loop<=a.game_loop<=args.to_loop
                                and (not args.actions_for_actors or set(cmd.unit_tags)&set(args.actor_tags))):
                            report['actions'].append(dict(loop=a.game_loop,ability=cmd.ability_id,
                                tags=list(cmd.unit_tags),target_tag=cmd.target_unit_tag or None,
                                pos=[cmd.target_world_space_pos.x,cmd.target_world_space_pos.y]
                                    if cmd.HasField('target_world_space_pos') else None))
                if o.game_loop>=args.to_loop or response.player_result:break
                c.step(args.step)
        report['state']='complete'
    except Exception as e:
        report.update(state='failed',error=type(e).__name__+': '+str(e))
    finally:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in report.items() if k not in ('samples','actions','births')},ensure_ascii=False),flush=True)
    return 0 if report['state']=='complete' else 1


if __name__=='__main__':raise SystemExit(main())
