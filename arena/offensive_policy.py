"""Model-owned offensive commitments; reminders never issue game commands."""
from collections import deque
from copy import deepcopy
import threading


PROTOCOL = '''主动施压：无紧迫防守威胁时比较袭矿、空投、压矿和主力推进；未知情报可用可撤回的小队试探。
自选小队/目标/撤退点，按用户当前建议与风险偏好调整。勿反复提高成军门槛，等待须有条件与期限。
顶层offensive_plan={action:"attack|harass|scout|prepare|defend|regroup",objective:"目标",squad:"小队",
launch_condition:"出击条件",abort_condition:"停止条件/撤退点",review_after_s:1..180,revision_reason:"维持/修改依据"}。
objective/squad各≤160字，其余文本≤240字，均非空。不修订可省略或null，旧期限保留。仅意图，实际操作须tasks。
读offensive_policy.commitment与unresolved_wait：连续等待的最早期限不因修订延后，到期重新评估；可按新威胁继续防守。
打赢局部交战后评估追击、拆矿、切增援或撤回。分矿被毁不证明主矿被毁；enemy_start仅出生点；失去视野不等于死亡。
可见敌军不是全军，清空/胜利须观测证据。改派先释放旧任务归属。200人口不能靠补给再增上限；矿衰退评估新矿预算。
期限只提醒，不自动调兵、选目标或增加请求。'''

ACTIONS = ('attack', 'harass', 'scout', 'prepare', 'defend', 'regroup')
TEXT_LIMITS = dict(objective=160, squad=160, launch_condition=240,
                   abort_condition=240, revision_reason=240)


def plan_schema():
    properties = dict(action=dict(type='string', enum=list(ACTIONS)))
    properties.update({k:dict(type='string', minLength=1, maxLength=n)
                       for k,n in TEXT_LIMITS.items()})
    properties['review_after_s'] = dict(type='number', minimum=1, maximum=180)
    return dict(anyOf=[dict(type='null'), dict(type='object', properties=properties,
                required=list(properties), additionalProperties=False)])


def validate_plan(value):
    if value is None:
        return
    fields = {'action', 'review_after_s', *TEXT_LIMITS}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('offensive_plan requires exactly action, objective, squad, launch_condition, abort_condition, review_after_s, revision_reason')
    if value['action'] not in ACTIONS:
        raise ValueError('offensive_plan.action must be '+', '.join(ACTIONS))
    for key,limit in TEXT_LIMITS.items():
        text = value[key]
        if not isinstance(text, str) or not text.strip() or len(text) > limit:
            raise ValueError('offensive_plan.%s requires 1..%d characters' % (key,limit))
    seconds = value['review_after_s']
    if type(seconds) not in (int, float) or not 1 <= seconds <= 180:
        raise ValueError('offensive_plan.review_after_s must be finite, 1..180 game seconds')


def attack_requested(tasks):
    return any(t.get('skill') in ('attack_nearest', 'move_out', 'raid', 'drop_raid')
               or (t.get('skill') == 'order_units' and t.get('args', {}).get('mode') == 'attack_move')
               for t in tasks if isinstance(t, dict))


class OffensiveMemory:
    def __init__(self):
        self.commitment = None
        self.history = deque(maxlen=3)
        self.revisions = 0
        self.wait_since = self.wait_review = None
        self.last_attack_request = None
        self.lock = threading.RLock()

    def accept(self, plan, game_s, request_id):
        """Call only after the entire plan has passed validation and enqueue."""
        validate_plan(plan.get('offensive_plan'))
        with self.lock:
            self._accept(plan,game_s,request_id)

    def _accept(self, plan, game_s, request_id):
        value = plan.get('offensive_plan')
        attack = attack_requested(plan.get('tasks') or [])
        if attack:
            self.last_attack_request = game_s
            self.wait_since = self.wait_review = None
        if value is None:
            return
        if self.commitment is not None:
            self.history.append(deepcopy(self.commitment))
        self.revisions += 1
        self.commitment = dict(plan=deepcopy(value), accepted_game_s=game_s,
            review_at_game_s=game_s+value['review_after_s'], request_id=request_id,
            attack_order_in_reply=attack)
        if not attack and value['action'] in ('prepare', 'scout', 'defend', 'regroup'):
            if self.wait_since is None:
                self.wait_since = game_s
                self.wait_review = game_s+value['review_after_s']
            else:
                self.wait_review = min(self.wait_review, game_s+value['review_after_s'])

    def public(self, game_s):
        with self.lock:
            return self._public(game_s)

    def _public(self, game_s):
        current = deepcopy(self.commitment)
        if current is not None:
            current['review_due'] = game_s >= current['review_at_game_s']
        waiting = None if self.wait_since is None else dict(
            since_game_s=self.wait_since, elapsed_game_s=max(0,game_s-self.wait_since),
            first_review_at_game_s=self.wait_review, review_due=game_s>=self.wait_review)
        return dict(enabled=True, commitment=current, recent=deepcopy(list(self.history)),
            revisions=self.revisions, unresolved_wait=waiting,
            last_attack_request_game_s=self.last_attack_request,
            meaning='Model intentions and accepted task requests only; not dispatch, arrival, battle success or victory. Deadlines only remind the model; no automatic orders or requests.')
