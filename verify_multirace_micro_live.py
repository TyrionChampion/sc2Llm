"""Zero-model, debug-fixture native SC2 checks for three-race combat micro.

Each run creates new evidence, source snapshots and a replay; never changes match
ledgers. This is executor validation, not a model match or a win-rate claim.
"""
import hashlib,json,math,tempfile,time,traceback
from pathlib import Path
from absl import flags
from s2clientprotocol import debug_pb2 as debug
from arena.linux_step import LinuxStepArena
from arena.combat_micro import CombatMicro
from arena.raw_engine import ATTACK,MOVE,command
from arena.tasks import TaskQueue
from go_duel_series import source_snapshot
from verify_tactical_scenarios_live import Fixture

ROOT=Path(__file__).resolve().parent
class StepFixture(Fixture):
    def advance(self,game_s=.25,actions=None):
        start=self.worlds[0].seconds;deadline=time.monotonic()+15
        while self.worlds[0].seconds-start<game_s:
            if time.monotonic()>deadline:raise TimeoutError('bounded native fixture advance')
            self.worlds=self.arena.step(actions or [[],[]]);actions=None
        return self.worlds[0]

def main():
    if not flags.FLAGS.is_parsed():flags.FLAGS(['multirace-micro-native'])
    folder=Path(tempfile.mkdtemp(prefix='multirace-micro-',dir=ROOT/'logs')).resolve()
    sources=source_snapshot(folder);sources[Path(__file__).name]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (folder/'source'/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    history={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.glob('logs/**/budget.json')}
    result=dict(model_calls=0,debug_fixture=True,realtime=False,cases={},passed=False,source_sha256=sources)
    arena=None;f=None;spawned=set()
    def save():
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    try:
        arena=LinuxStepArena(races=['zerg','protoss'],seed=7)
        result['engine']=arena.version;f=StepFixture(arena)
        from arena.combat_spells import catalogue_audit
        (folder/'ability-coverage.json').write_text(json.dumps(catalogue_audit(arena.tech_tree),ensure_ascii=False,indent=2))
        home=next(u for u in f.worlds[0].my_units if u.type_id==86)
        # Spawn orientation is randomized. Require an open, same-height patch so
        # normal fog does not hide a fixture target behind a cliff/ramp.
        grid=f.grid;candidates=[]
        for y in range(6,grid.h-9):
            for x in range(6,grid.w-14):
                distance=math.dist((x+.5,y+.5),home.pos)
                if not 16<=distance<=55:continue
                patch=grid.pathing[y-5:y+9,x-5:x+14]
                heights=grid.height[y-5:y+9,x-5:x+14]
                if patch.all() and (heights==grid.height[y,x]).all():
                    candidates.append((abs(distance-25),x,y))
        if not candidates:raise AssertionError('no open same-height fixture patch')
        _,x,y=min(candidates);anchor=(x+.5,y+.5)
        result['anchor']=anchor
        arena.controllers[0].debug([debug.DebugCommand(game_state=debug.upgrade)])
        f.advance()
        def spawn(kind,owner,dx=0,dy=0,energy=None,health=None):
            u=f.spawn(kind,owner,f.point((anchor[0]+dx,anchor[1]+dy)))[0];spawned.add(u.tag)
            if energy is not None:f.values([u],debug.DebugSetUnitValue.Energy,energy)
            if health is not None:f.values([u],debug.DebugSetUnitValue.Life,health)
            return f.worlds[owner-1].by_tag(u.tag)
        def clear():
            live=[u.tag for w in f.worlds for u in w.my_units if u.tag in spawned]
            if live:
                arena.controllers[0].debug([debug.DebugCommand(kill_unit=debug.DebugKillUnit(tag=live))]);f.advance(20)
            spawned.clear()
        def run(name,kind,aid,setup,predicate,withdraw=None):
            clear();actor=spawn(kind,1,energy=200 if kind not in (688,502,498) else None)
            targets=setup();w=f.worlds[0];actor=w.by_tag(actor.tag)
            baseline={u.tag:u for u in w.units};events=[];micro=CombatMicro(arena,0,lambda k,**kw:events.append(dict(kind=k,**kw)))
            allowed=arena.available_abilities(0,[actor.tag]).get(actor.tag,set())
            if not arena.tech_tree.ability_available(aid,allowed):raise AssertionError(name+' initial native ability unavailable: '+str(sorted(allowed)))
            start=w.seconds;wall=time.monotonic();first=True;effect=False;sent=[];withdrew=False;form_back=False
            while f.worlds[0].seconds-start<15 and time.monotonic()-wall<60:
                w=f.worlds[0]
                batch=[command(ATTACK,[actor.tag],pos=(anchor[0]+12,anchor[1]))] if first else []
                ctx=[dict(source='task_queue',skill='order_units',task_id=1)] if first else [];first=False
                if withdraw and effect and not withdrew:
                    batch=[command(MOVE,[actor.tag],pos=(anchor[0]-5,anchor[1]))]
                    ctx=[dict(source='task_queue',skill='order_units',task_id=1)];withdrew=True
                actions,contexts=micro.apply(w,batch,ctx)
                sent.extend(dict(ability=a.action_raw.unit_command.ability_id,tags=list(a.action_raw.unit_command.unit_tags),game_s=w.seconds) for a in actions)
                arena.register_action_context(0,[(a.SerializeToString(),c) for a,c in zip(actions,contexts)])
                # Opponent fixture units hold position, preserving normal player-local sight.
                enemies=[u.tag for u in f.worlds[1].my_units if u.tag in spawned]
                f.worlds=arena.step([actions,[command(18,enemies)] if enemies else []])
                current=f.worlds[0];effect|=bool(predicate(baseline,current,actor.tag,targets))
                observed=current.by_tag(actor.tag)
                form_back=bool(withdrew and observed and observed.type_id==withdraw)
                receipt=any(e['kind'] in ('combat_micro_acknowledged','combat_micro_confirmed') and e.get('action')==name for e in events)
                if effect and receipt and (not withdraw or form_back):break
            case=dict(passed=effect and receipt and (not withdraw or form_back),actual_effect_observed=effect,
                withdrawal_mobile_form_observed=form_back if withdraw else None,initial_native_available=sorted(allowed),
                game_s=f.worlds[0].seconds-start,actions=sent,events=events)
            result['cases'][name]=case;save();print('NATIVE_CASE',name,case['passed'],flush=True)
            if not case['passed']:raise AssertionError(name+' missing native effect/receipt')
        def cluster(kind=48,front=False,**kw):
            units=[]
            if front:units.append(spawn(kind,2,5,0,**kw))
            for i in range(3):units.append(spawn(kind,2,7+(i%2),i//2,**kw))
            return [u.tag for u in units]
        run('corrosive_bile',688,2338,lambda:cluster(33),lambda b,w,a,t:any(e[0]==11 and e[1]==1 for e in w.effects))
        run('lurker_burrow',502,2108,lambda:cluster(48),lambda b,w,a,t:w.by_tag(a) and w.by_tag(a).type_id==503,withdraw=502)
        run('psi_storm',75,1036,lambda:cluster(48),lambda b,w,a,t:any(e[0]==1 and e[1]==1 for e in w.effects))
        run('feedback',75,140,lambda:[spawn(126,2,7,energy=150).tag],lambda b,w,a,t:any(w.by_tag(tag) and w.by_tag(tag).energy<b[tag].energy-40 for tag in t))
        def shield_setup():
            spawn(48,1,1,2);return cluster(48)
        run('guardian_shield',77,76,shield_setup,lambda b,w,a,t:bool(w.by_tag(a) and 18 in w.by_tag(a).buffs))
        run('force_field',77,1526,lambda:cluster(48,front=True),lambda b,w,a,t:any(u.type_id==135 and u.tag not in b for u in w.units))
        run('fungal_growth',111,74,lambda:cluster(48),lambda b,w,a,t:any(w.by_tag(tag) and 17 in w.by_tag(tag).buffs for tag in t))
        run('abduct',499,2067,lambda:[spawn(33,2,7).tag],lambda b,w,a,t:any(w.by_tag(tag) and w.by_tag(tag).distance(b[tag])>2 for tag in t))
        run('interference_matrix',56,3747,lambda:[spawn(33,2,7).tag],lambda b,w,a,t:any(w.by_tag(tag) and set(w.by_tag(tag).buffs)-set(b[tag].buffs) for tag in t))
        def heal_setup():return [spawn(48,1,2,health=10).tag]
        run('heal',54,386,heal_setup,lambda b,w,a,t:any(w.by_tag(tag) and w.by_tag(tag).health>b[tag].health+3 for tag in t))
        # Exercise the actual Commander -> standing defence -> micro pipeline,
        # not just a synthetic attack context handed to CombatMicro.
        clear();guard_base=spawn(86,1,0,4);caster=spawn(75,1,energy=200)
        cluster(48)
        from arena.commander import Commander
        class Idle:
            compact_battle=True;soft_timeout=12;timeout=60
            def ready_for_request(self):return False
        guard=Commander(arena,0,Idle(),opening=False,combat_micro=True,feedback_dir=folder/'defense-feedback')
        start=f.worlds[0].seconds;wall=time.monotonic();effect=False;dispatched=False
        try:
            guard.tasks.enqueue([dict(skill='defend_base',args=dict(base=list(guard_base.pos),
                units=['high_templar'],count=1,stance='hold',reason='原生场景验证施法单位守备'))],start)
            while f.worlds[0].seconds-start<10 and time.monotonic()-wall<60:
                actions=guard.tick(f.worlds[0])
                dispatched|=any(a.action_raw.unit_command.ability_id==1036 for a in actions)
                f.worlds=arena.step([actions,[]]);effect|=any(e[0]==1 and e[1]==1 for e in f.worlds[0].effects)
                if effect and guard.combat_micro.counts['combat_micro_acknowledged']:break
            result['cases']['standing_defense_storm']=dict(passed=effect and dispatched,
                actual_storm_effect_observed=effect,storm_dispatched=dispatched,
                assigned_tags=sorted(guard.tasks.tasks[0].program.tags),micro=guard.combat_micro.public())
        finally:guard.stop()
        save()
        if not result['cases']['standing_defense_storm']['passed']:raise AssertionError('standing defence micro integration')
        clear()
        base=spawn(86,1,0,4);queen=spawn(126,1,3,4,energy=75)
        events=[];queue=TaskQueue(arena,0,lambda _:None,lambda k,**kw:events.append(dict(kind=k,**kw)))
        start=f.worlds[0].seconds;queue.enqueue([dict(skill='inject_larva',args=dict(base=str(base.tag)))],start)
        wall=time.monotonic()
        while f.worlds[0].seconds-start<15 and time.monotonic()-wall<60:
            w=f.worlds[0];actions=queue.tick(w,set());f.worlds=arena.step([actions,[]])
            if queue.tasks[-1].status in ('completed','failed'):break
        w=f.worlds[0];injected=w.by_tag(base.tag);original=w.by_tag(home.tag)
        case=dict(passed=queue.tasks[-1].status=='completed' and injected is not None and 11 in injected.buffs
            and original is not None and 11 not in original.buffs,selected_base_tag=base.tag,original_home_tag=home.tag,
            task_state=queue.tasks[-1].status,events=events)
        result['cases']['exact_base_tag_injection']=case;queue.close();save()
        if not case['passed']:raise AssertionError('exact base tag injection')
        replay=folder/'control.SC2Replay';arena.save_replay(replay)
        result.update(replay=str(replay),replay_sha256=hashlib.sha256(replay.read_bytes()).hexdigest(),
            engine_action_errors=arena.action_errors,passed=all(c['passed'] for c in result['cases'].values()) and arena.action_errors==0)
    except Exception as error:result.update(error=type(error).__name__+': '+str(error),traceback=traceback.format_exc())
    finally:
        if arena:arena.close()
        result.update(source_unchanged=all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in sources.items()),
            historical_unchanged=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in history.items()),cleanup_complete=arena is None or arena.closed)
        save();print('NATIVE_REPORT',folder,flush=True)
        print(json.dumps({k:v for k,v in result.items() if k not in ('cases','source_sha256','traceback')},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
