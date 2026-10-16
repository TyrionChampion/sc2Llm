"""Zero-LLM native checks for direct blink, shield, chrono boost and transport."""
import argparse
import json
import math
import time
from pathlib import Path

from absl import flags
from s2clientprotocol import debug_pb2 as debug
from arena.raw_engine import RawArena
from arena.tasks import TaskQueue
from arena.native_abilities import report as ability_report
from verify_tactical_scenarios_live import Fixture


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',required=True)
    args=parser.parse_args();flags.FLAGS(['direct-native-abilities'])
    folder=Path('logs/native-abilities-'+args.label);folder.mkdir(exist_ok=False)
    result=dict(model_calls=0,debug_fixture=True,fast_build=False,game_speed='slower',cases={},passed=False)
    arena=queue=None;events=[]
    try:
        arena=RawArena(render=False,races=['protoss','terran'],game_speed='slower',seed=7)
        fixture=Fixture(arena);home=fixture.worlds[0].my(59)[0]
        arena.controllers[0].debug([debug.DebugCommand(game_state=debug.upgrade)])
        stalker=fixture.spawn(74,1,fixture.point((home.x+6,home.y+4)))[0]
        sentry=fixture.spawn(77,1,fixture.point((home.x+8,home.y+4)))[0]
        medivac=fixture.spawn(54,1,fixture.point((home.x+10,home.y+4)))[0]
        marine=fixture.spawn(48,1,medivac.pos)[0]
        fixture.structure_near(60,home)
        fixture.values([sentry,home],debug.DebugSetUnitValue.Energy,150)
        queue=TaskQueue(arena,0,lambda _:None,lambda kind,**data:events.append(dict(kind=kind,**data)))

        def cast(caster,ability,target,predicate):
            w=fixture.worlds[0]
            # Resolve the exact native name; do not assume an old catalogue alias.
            name=arena.tech_tree.ability_names[ability]
            published=ability_report(w,queue)
            available=next(row[-1] for row in published['actors']['rows'] if row[0]==caster)
            if not any(arena.tech_tree.canonical_ability(a)==arena.tech_tree.canonical_ability(ability) for a in available):
                raise AssertionError('native ability not published as ready: '+name)
            queue.enqueue([dict(skill='use_ability',args=dict(caster=caster,ability=name,target=target,timeout_s=30))],w.seconds,'native direct ability check')
            task=queue.tasks[-1];before=w;wall=time.monotonic();effect=False
            while fixture.worlds[0].seconds-before.seconds<32 and time.monotonic()-wall<85:
                w=fixture.worlds[0];fixture.worlds=arena.step([queue.tick(w,set()),[]])
                w=fixture.worlds[0];effect=effect or predicate(before,w)
                if task.status=='failed':raise AssertionError(task.reason)
                if effect and task.status=='completed':break
                time.sleep(.02)
            case=dict(ability=name,task_state=task.status,actual_effect_observed=effect,
                      dispatches=sum(e['kind']=='native_ability_order_issued' and e.get('task_id')==task.id for e in events),
                      game_s=fixture.worlds[0].seconds-before.seconds,passed=effect and task.status=='completed')
            result['cases'][name]=case
            if not case['passed'] or case['dispatches']!=1:raise AssertionError('native direct cast check failed: '+name)
            return fixture.worlds[0]

        goal=list(fixture.point((stalker.x-3,stalker.y)))
        cast(stalker.tag,1442,goal,lambda before,w:w.by_tag(stalker.tag) is not None
             and math.dist(w.by_tag(stalker.tag).pos,goal)<1.5
             and w.by_tag(stalker.tag).distance(before.by_tag(stalker.tag))>2)
        cast(sentry.tag,76,None,lambda before,w:w.by_tag(sentry.tag) is not None
             and w.by_tag(sentry.tag).energy<before.by_tag(sentry.tag).energy-60
             and bool(set(w.by_tag(sentry.tag).buffs)-set(before.by_tag(sentry.tag).buffs)))
        # Native training makes the chrono target meaningful; this is a debug
        # fixture command, not a model strategy or hidden production fallback.
        from arena.raw_engine import command
        arena.controllers[0].debug([debug.DebugCommand(game_state=debug.all_resources)])
        fixture.advance(.25,[[command(1006,[home.tag])],[]])
        if 1006 not in fixture.worlds[0].by_tag(home.tag).orders:
            raise AssertionError('native Nexus Probe training not observed before chrono boost')
        cast(home.tag,3755,home.tag,lambda before,w:w.by_tag(home.tag) is not None
             and w.by_tag(home.tag).energy<before.by_tag(home.tag).energy-40
             and 1006 in w.by_tag(home.tag).orders
             and bool(set(w.by_tag(home.tag).buffs)-set(before.by_tag(home.tag).buffs)))
        cast(medivac.tag,394,marine.tag,lambda before,w:w.by_tag(medivac.tag) is not None
             and marine.tag in w.by_tag(medivac.tag).passengers)
        available=arena.available_abilities(0,[medivac.tag])[medivac.tag]
        unload=next(a for a in available if a in arena.tech_tree.ability_names
                    and 'unload_all' in arena.tech_tree.ability_names[a])
        target=list(fixture.point((medivac.x+2,medivac.y)))
        cast(medivac.tag,unload,target,lambda before,w:w.by_tag(medivac.tag) is not None
             and marine.tag not in w.by_tag(medivac.tag).passengers and w.by_tag(marine.tag) is not None)
        result.update(passed=arena.action_errors==0,engine_action_errors=arena.action_errors,events=events)
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:
        result.update(error=type(error).__name__+': '+str(error),events=events)
        if arena:result['engine_action_errors']=arena.action_errors
    finally:
        if queue:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in result.items() if k!='events'},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
