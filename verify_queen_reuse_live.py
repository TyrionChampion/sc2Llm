"""Zero-model sequential injections at two bases using one Queen, without recasts."""
import argparse,hashlib,importlib.util,json,time
from pathlib import Path
from absl import flags
from s2clientprotocol import debug_pb2 as debug
from arena.raw_engine import RawArena
from arena.tasks import TaskQueue
import arena.queen as queen_module
from verify_tactical_scenarios_live import Fixture


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--label',required=True)
    p.add_argument('--baseline-source',type=Path,help='Local archived Queen runner for a before-fix control')
    args=p.parse_args();flags.FLAGS(['queen-reuse-control'])
    source=args.baseline_source or Path(queen_module.__file__)
    if args.baseline_source:
        spec=importlib.util.spec_from_file_location('baseline_queen',source)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        queen_module.InjectLarvaRun=module.InjectLarvaRun
    folder=Path('logs/inject-reuse-'+args.label);folder.mkdir(exist_ok=False)
    result=dict(model_calls=0,debug_fixture=True,fast_build=False,passed=False,events=[],samples=[],
        runner_source=str(source),runner_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    arena=queue=None
    try:
        arena=RawArena(render=False,races=['zerg','terran'],game_speed='slower',seed=7)
        f=Fixture(arena);home=f.worlds[0].my(86)[0]
        orient=lambda pos:pos if home.x>88 else (176-pos[0],172-pos[1])
        expansion=f.spawn(86,1,orient((117.5,60.5)))[0]
        queen=f.spawn(126,1,orient((140.5,29.5)))[0]
        f.values([queen],debug.DebugSetUnitValue.Energy,70)
        queue=TaskQueue(arena,0,lambda _:None,lambda kind,**kw:result['events'].append(
            dict(kind=kind,**kw)|dict(observed_game_s=getattr(queue,'clock',None))))
        queue.enqueue([dict(skill='inject_larva',args=dict(base=base,timeout_s=90))
            for base in ('home','expansion')],f.worlds[0].seconds,'explicit two-base injection control')
        began=f.worlds[0].seconds;wall=time.monotonic();sample_at=0
        while f.worlds[0].seconds-began<93 and time.monotonic()-wall<235:
            w=f.worlds[0]
            if w.seconds>=sample_at:
                actor=w.by_tag(queen.tag)
                result['samples'].append(dict(game_s=w.seconds,queen=dict(pos=list(actor.pos),energy=actor.energy,
                    orders=list(actor.orders),order_targets=list(actor.order_targets)),
                    home_buff=11 in w.by_tag(home.tag).buffs,expansion_buff=11 in w.by_tag(expansion.tag).buffs))
                sample_at=w.seconds+.25
            f.worlds=arena.step([queue.tick(w,set()),[]]);time.sleep(.02)
            if any(t.status=='failed' for t in queue.tasks):break
            if all(t.status=='completed' for t in queue.tasks):break
        confirmed=[e for e in result['events'] if e['kind']=='inject_larva_confirmed']
        issued=[e for e in result['events'] if e['kind']=='inject_larva_order_issued']
        result.update(states=[t.status for t in queue.tasks],reasons=[t.reason for t in queue.tasks],
            confirmations=confirmed,dispatches=len(issued),engine_action_errors=arena.action_errors,
            game_s=f.worlds[0].seconds-began,
            passed=(all(t.status=='completed' for t in queue.tasks) and len(issued)==len(confirmed)==2
                and {e['base_tag'] for e in confirmed}=={home.tag,expansion.tag}
                and all(e['queen_tag']==queen.tag for e in confirmed) and arena.action_errors==0))
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:result['error']=type(error).__name__+': '+str(error)
    finally:
        if queue:queue.close()
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k not in ('events','samples')},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
