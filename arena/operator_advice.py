"""Player-local durable UI messages, consumed only by scheduled planner requests."""
import json
import math
from pathlib import Path
import time
import uuid

from arena.lifecycle import write_json_atomic

MAX_TEXT = 1000
MAX_PENDING = 32
MAX_BATCH_CHARS = 1800
PROTOCOL = ('operator_advice.messages 是用户发给本方的建议，按顺序阅读，在本轮决策中考虑。'
    '用户的战况描述属于用户报告，不是引擎观测；结合当前己方观测、工具契约和预算决定如何调整。'
    '有建议时，在 reason 中简要回应采纳方式或未采纳的原因；仍返回原有 tasks/reason JSON。'
    '收到建议不代表已采纳、已执行或执行成功。')


def mailbox_path(journal):
    return Path(journal).resolve().with_suffix('.advice')


def read_status(directory):
    return json.loads((Path(directory)/'status.json').read_text())


def submit_advice(directory, text, player=None, message_id=None):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('请输入给模型的建议')
    text = text.strip()
    if len(text) > MAX_TEXT:
        raise ValueError('每条建议最多%d字' % MAX_TEXT)
    directory = Path(directory)
    status = read_status(directory)
    if player is not None and status['player']!=player:
        raise ValueError('消息接收玩家不匹配')
    if not status['accepting'] or time.time()-status['updated_at'] > 15:
        raise ValueError(status.get('reason') or '指挥进程未更新，暂不能发送')
    pending = sum(m['state'] in ('queued', 'sent', 'retry') for m in status['messages'])
    if pending+len(list((directory/'inbox').glob('*.json'))) >= MAX_PENDING:
        raise ValueError('待处理建议已满，请等待模型下一轮回复')
    if message_id is not None and (not isinstance(message_id, str) or len(message_id) != 32
                                  or any(c not in '0123456789abcdef' for c in message_id)):
        raise ValueError('建议 ID 无效')
    message = dict(id=message_id or uuid.uuid4().hex, text=text, submitted_at=time.time())
    write_json_atomic(directory/'inbox'/(message['id']+'.json'), message)
    return message


def public_decisions(journal, limit=20):
    """Public answers only, never queued prompts, raw transport or hidden reasoning."""
    rows = []
    journal = Path(journal)
    with journal.open('rb') as stream:
        offset = max(0, journal.stat().st_size - 256000)
        stream.seek(offset)
        if offset: stream.readline()
        for line in stream:
            try: row = json.loads(line)
            except (ValueError, UnicodeDecodeError): continue
            if row.get('record') != 'request_finished': continue
            try: plan = json.loads(row.get('response') or '{}')
            except (ValueError, TypeError): plan = {}
            if not isinstance(plan, dict): plan = {}
            rows.append(dict(request_id=row.get('request_id'), reason=plan.get('reason', ''),
                tasks=plan.get('tasks', []), error=row.get('error'), latency_s=row.get('latency_s')))
    return rows[-limit:]


class OperatorAdvice:
    def __init__(self, journal, player, audit):
        self.directory = mailbox_path(journal)
        (self.directory/'inbox').mkdir(parents=True)
        self.player, self.audit = player, audit
        self.messages = []
        self.accepting, self.reason = True, ''
        self.last_poll = self.last_save = float('-inf')
        self.save()

    def save(self):
        # Keep every pending message; terminal history is also in the journal.
        pending = [m for m in self.messages if m['state'] in ('queued','sent','retry')]
        terminal = [m for m in self.messages if m not in pending][-32:]
        self.messages = sorted(pending+terminal, key=lambda m:(m['submitted_at'],m['id']))
        write_json_atomic(self.directory/'status.json', self.public())
        self.last_save = time.monotonic()

    def public(self):
        return dict(player=self.player, mailbox=str(self.directory), accepting=self.accepting,
                    reason=self.reason, updated_at=time.time(), max_text=MAX_TEXT,
                    messages=[dict(m) for m in self.messages])

    def poll(self, accepting=True, reason=''):
        now=time.monotonic()
        changed=(accepting,reason)!=(self.accepting,self.reason)
        self.accepting,self.reason=accepting,reason
        if now-self.last_poll < .25 and not changed:return
        self.last_poll=now
        paths=list((self.directory/'inbox').glob('*.json'))
        incoming=[]
        for path in paths[:MAX_PENDING*2]:
            try:
                if path.stat().st_size > 8192:raise ValueError('消息过大')
                m=json.loads(path.read_text())
                if (not isinstance(m,dict) or m.get('id')!=path.stem or len(path.stem)!=32
                        or any(c not in '0123456789abcdef' for c in path.stem)
                        or not isinstance(m.get('text'),str) or not m['text'].strip()
                        or len(m['text'])>MAX_TEXT or type(m.get('submitted_at')) not in (int,float)
                        or not math.isfinite(m['submitted_at'])):
                    raise ValueError('消息格式无效')
                incoming.append((path,{key:m[key] for key in ('id','text','submitted_at')}))
            except (OSError,ValueError,TypeError) as e:
                self.audit(dict(record='operator_advice_rejected',player=self.player,error=str(e)))
                path.unlink(missing_ok=True)
        for path,m in sorted(incoming,key=lambda pair:(pair[1]['submitted_at'],pair[1]['id'])):
            if any(x['id']==m['id'] for x in self.messages):continue
            m=dict(m,state='queued' if accepting else 'closed',request_id=None)
            if sum(x['state'] in ('queued','sent','retry') for x in self.messages)>=MAX_PENDING:
                m['state']='rejected'
            self.messages.append(m)
            self.audit(dict(record='operator_advice_queued',player=self.player,message=dict(m)))
        if paths or changed or now-self.last_save>=5:self.save()
        for path,m in incoming:path.unlink(missing_ok=True)

    def batch(self):
        rows=[];size=0
        for m in self.messages:
            if m['state'] not in ('queued','retry'):continue
            row={k:m[k] for k in ('id','text','submitted_at')}
            length=len(json.dumps(row,ensure_ascii=False,separators=(',',':')))
            if rows and size+length>MAX_BATCH_CHARS:break
            rows.append(row);size+=length
        return dict(messages=rows, source='user advice to this player; not engine observations',
                    instruction=PROTOCOL,
                    deferred_count=sum(m['state'] in ('queued','retry') for m in self.messages)-len(rows))

    def sent(self, ids, request_id):
        for m in self.messages:
            if m['id'] in ids:
                m.update(state='sent',request_id=request_id)
        self.audit(dict(record='operator_advice_sent',player=self.player,ids=ids,request_id=request_id))
        self.save()

    def finish(self, ids, request_id, returned):
        for m in self.messages:
            if m['id'] in ids:
                m.update(state='received' if returned else 'retry',request_id=request_id)
        if ids:
            self.audit(dict(record='operator_advice_received' if returned else 'operator_advice_retry',
                player=self.player,ids=ids,request_id=request_id,
                meaning='API returned; adoption/execution not inferred' if returned else 'Retained for next scheduled request'))
            self.save()

    def close(self):
        self.poll(False,'对局已停止，没有下一轮决策')
        for m in self.messages:
            if m['state'] in ('queued','sent','retry'):m['state']='closed'
        self.save()
