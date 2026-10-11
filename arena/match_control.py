"""Match-level commands. Surrender is never a unit action or a composite step."""
from arena.composite_skills import PlanValidationError

SURRENDER_PROTOCOL = """
Match control: surrender is accepted on this backend ONLY while `control_policy.surrender_allowed`
is true; while it is false the request is rejected and `surrender_blockers` lists what still keeps
you in the game. To voluntarily concede YOUR side,
return exactly {"tasks":[{"skill":"surrender","args":{"reason":"specific reason based on your observations"}}]}.
A nonempty reason of at most 500 characters is required; count must be omitted or 1.
This must be the only task; do not define skills in the same reply. Surrender cannot be a
composite step and cannot target the other player. It ends the match and records a loss.
A temporary shortage, individual rejected action or model/network error alone does not imply defeat.
No script automatically chooses surrender. In bot matches the acknowledged concession is recorded as
a match-rule loss and replay is saved immediately BEFORE leaving; no post-leave engine result is
available from the departed client. With another API player, the opponent's engine result is checked.
"""

# A side that still owns combat units, or can still pay for something, is not defeated. These bounds
# only block the obvious no-way-back cases; whether a position is truly hopeless stays the
# commander's judgment. They exist because an early concession with a live army is not "reasonable
# behaviour" -- it is the model misreading the position.
SURRENDER_MIN_ECONOMY = 50      # below this, with no workers, nothing can be rebuilt
SURRENDER_MIN_MILITARY = 0      # any combat unit alive blocks surrender


def surrender_blockers(world):
    """Reasons surrender is mechanically unavailable now (empty list = allowed).

    Derived from the observed snapshot only: no recommendation, no hidden policy.
    """
    from arena.tech_tree import WORKERS

    def combat(unit):
        return (not unit.is_building and unit.type_id not in WORKERS
                and (unit.ground_dps > 0 or unit.air_dps > 0))

    alive = [u for u in world.my_units if combat(u)]
    if len(alive) > SURRENDER_MIN_MILITARY:
        return ['you still own %d combat unit(s); a side with an army is not defeated' % len(alive)]
    workers = [u for u in world.my_units if u.type_id in WORKERS]
    economy = world.minerals + world.vespene
    blockers = []
    if workers:
        blockers.append('you still have %d worker(s) producing income' % len(workers))
    if economy >= SURRENDER_MIN_ECONOMY:
        blockers.append('you still hold %d resources, enough to rebuild' % int(economy))
    if not blockers:
        # No army, no workers, and not enough left to pay for anything: the position is over.
        return []
    return blockers


def surrender_allowed(world):
    return not surrender_blockers(world)

def surrender_reason(plan):
    """Return None for ordinary plans; validate the whole surrender plan before side effects."""
    tasks=plan.get('tasks')
    if not isinstance(tasks,list) or not any(isinstance(t,dict) and t.get('skill')=='surrender' for t in tasks):return None
    issues=[]
    def bad(field,value,expected):issues.append(dict(field=field,received=value,expected=expected))
    if len(tasks)!=1:bad('tasks',tasks,'surrender must be the only task')
    if plan.get('skills',[])!=[]:bad('skills',plan.get('skills'),'no skill definitions in a surrender plan')
    for n,t in enumerate(tasks):
        if not isinstance(t,dict) or t.get('skill')!='surrender':continue
        path='tasks[%d]' % n
        for key in t.keys()-{'skill','args','count'}:bad(path+'.'+key,t[key],'remove unsupported field')
        count=t.get('count',1)
        if type(count) is not int or count!=1:bad(path+'.count',count,'integer 1')
        args=t.get('args')
        if not isinstance(args,dict):bad(path+'.args',args,'object with reason')
        else:
            for key in args.keys()-{'reason'}:bad(path+'.args.'+key,args[key],'only reason; actor is always your own player')
            reason=args.get('reason')
            if not isinstance(reason,str) or not reason.strip() or len(reason)>500:
                bad(path+'.args.reason',reason,'nonempty explanation of at most 500 characters')
    if issues:raise PlanValidationError(issues)
    return tasks[0]['args']['reason'].strip()
