"""Zero-model native Queen birth, explicit defence and bounded command confirmation."""
import argparse,json,time
from pathlib import Path
from absl import flags
from s2clientprotocol import debug_pb2 as debug
from arena.raw_engine import RawArena
from arena.tasks import TaskQueue
from verify_tactical_scenarios_live import Fixture


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--label',required=True)
    p.add_argument('--skill',choices=('defend_base','order_units'),default='defend_base')
    args=p.parse_args();flags.FLAGS(['queen-defence-control'])
    folder=Path('logs/queen-defense-'+args.label);folder.mkdir(exist_ok=False)
    result=dict(model_calls=0,debug_fixture=True,fast_build=False,skill=args.skill,passed=False,events=[])
    arena=queue=None
    try:
        arena=RawArena(render=False,races=['zerg','terran'],game_speed='slower',seed=7)
        f=Fixture(arena);home=f.worlds[0].my(86)[0];f.structure_near(89,home)
        # An observed enemy stays within the explicitly ordered base's radius.
        point=f.point((home.x+(18 if home.x<88 else -18),home.y))
        target=f.spawn(48,2,point)[0]
        # Fixture-only owned observer provides local sight; replay fog stays on.
        f.spawn(82,1,(point[0],point[1]+3))
        # The fixture observer itself costs one supply; retain room for a Queen.
        f.spawn(106,1,(home.x,home.y+2))
        if f.worlds[0].by_tag(target.tag) is None:raise AssertionError('fixture enemy not visible to player1')
        arena.controllers[0].debug([debug.DebugCommand(game_state=debug.all_resources)])
        f.advance(.25)
        w=f.worlds[0]
        result['foundation']=dict(supply_used=w.supply_used,supply_cap=w.supply_cap,
            minerals=w.minerals,target_visible=w.by_tag(target.tag) is not None)
        if w.supply_cap-w.supply_used<2 or w.minerals<150:
            raise AssertionError('fixture lacks observed Queen resources/supply')
        queue=TaskQueue(arena,0,lambda _:None,
            lambda kind,**kw:result['events'].append(dict(kind=kind,**kw)))
        mission=(dict(skill='defend_base',args=dict(base='home',units=['Queen'],count=1,
                stance='hold',timeout_s=120,reason='explicit newborn Queen defence control'))
            if args.skill=='defend_base' else dict(skill='order_units',args=dict(unit='queen',count=1,
                mode='attack_move',target=list(point),timeout_s=120,reason='explicit newborn Queen movement control')))
        queue.enqueue([dict(skill='train_unit',args=dict(unit='queen')),mission],
            f.worlds[0].seconds,'zero-model native birth control')
        start=f.worlds[0].seconds;wall=time.monotonic();first=None;damaged=False;travelled=False
        dispatches=[]
        while f.worlds[0].seconds-start<100 and time.monotonic()-wall<245:
            w=f.worlds[0];queens=w.my(126)
            if queens and first is None:
                queen=queens[0];first=dict(tag=queen.tag,pos=list(queen.pos),game_s=w.seconds,
                    progress=queen.progress,ready=sorted(arena.available_abilities(0,[queen.tag]).get(queen.tag,set())))
            out=queue.tick(w,set())
            for action,ctx in zip(out,queue.action_contexts):
                if ctx.get('source')=='defense_guard' or ctx.get('task_id')==queue.tasks[1].id:
                    dispatches.append(dict(game_s=w.seconds,loop=w.game_loop,
                        tags=list(action.action_raw.unit_command.unit_tags)))
            f.worlds=arena.step([out,[]]);w=f.worlds[0]
            victim=w.by_tag(target.tag)
            damaged=damaged or victim is not None and victim.health<target.health or target.tag in w.dead_units
            if first:
                queen=w.by_tag(first['tag'])
                travelled=travelled or queen is not None and queen.distance(first['pos'])>2
            if damaged and travelled and queue.tasks[0].status=='completed':break
            for task in queue.tasks:
                if task.status=='failed':raise AssertionError(task.reason)
            time.sleep(.02)
        rejected=[e for e in result['events'] if e['kind']=='defense_command_rejected']
        result.update(first_queen=first,actual_target_damaged=damaged,actual_queen_moved=travelled,
            training_state=queue.tasks[0].status,mission_state=queue.tasks[1].status,
            mission=queue.tasks[1].program.summary(),
            dispatches=dispatches,defense_rejections=len(rejected),engine_action_errors=arena.action_errors,
            game_s=f.worlds[0].seconds-start,
            passed=bool(first) and damaged and travelled and queue.tasks[0].status=='completed'
                and not rejected and arena.action_errors==0)
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:result['error']=type(error).__name__+': '+str(error)
    finally:
        if queue:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k not in ('events','dispatches')},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
