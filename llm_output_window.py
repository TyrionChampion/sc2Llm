"""Native Chinese viewer with next-round advice below each player's decisions.

./run_sc2.sh .venv311/bin/python llm_output_window.py --run opencode-runtime/match-...
Closing this window never stops the game. No model calls or reasoning traces.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import pygame
from arena.lifecycle import read_match_status, match_status_label
from arena.chat_input import ChatInput
from arena.operator_advice import mailbox_path, read_status, submit_advice, MAX_TEXT

BG='#0d1420';PANEL='#151f2e';CARD='#1c2a3c';TEXT='#e6edf7';MUTED='#9babc1'
COLORS=['#72c9ff','#c6a1ff'];RED='#ff9498';GREEN='#85dfb4'
ERRORS={'plan_rejected','plan_expired','model_response_invalid','model_request_failed',
        'python_skill_failed','python_repair_failed','python_development_failed','engine_action_rejected','surrender_failed',
        'model_credentials_failed'}
LABELS={'plan_rejected':'计划被拒绝','plan_expired':'计划已过期','model_response_invalid':'回复格式错误',
        'model_request_failed':'模型请求失败',
        'model_credentials_failed':'账户级失败：余额/密钥/权限，已停止请求该模型',
        'python_skill_failed':'Python 技能失败',
        'python_development_failed':'现场技能开发失败','python_repair_failed':'技能修复失败','engine_action_rejected':'引擎拒绝动作','surrender_failed':'投降请求失败'}
ACCEPTED={'task_enqueued','task_coalesced','plan_wait','surrender_requested'}


def game_time(value):
    value=max(0,int(value or 0));return '%02d:%02d'%(value//60,value%60)


class Feed:
    def __init__(self):
        self.path=None;self.offset=0;self.entries=[];self.requests={};self.plans={};self.pending=None;self.error='';self.advice_entries={}
    def poll(self,path):
        if self.path is None:self.path=Path(path)
        if self.path!=Path(path):raise ValueError('Player journal changed unexpectedly')
        try:
            with self.path.open('rb') as stream:
                if self.path.stat().st_size<self.offset:raise ValueError('Journal truncated; restart viewer to reload')
                stream.seek(self.offset)
                while True:
                    line=stream.readline()
                    if not line or not line.endswith(b'\n'):break
                    self.offset+=len(line)
                    try:self.consume(json.loads(line))
                    except (ValueError,KeyError,TypeError) as error:
                        self.entries.append(dict(title='日志解析错误',body=str(error),raw=line.decode(errors='replace'),error=True))
            self.error=''
        except (OSError,ValueError) as error:self.error='无法读取日志：'+str(error)
    def consume(self,row):
        kind=row.get('record');rid=row.get('request_id')
        if kind=='operator_advice_queued':
            m=row['message'];entry=dict(title='你给 P%d 的建议'%row['player'],body=m['text'],
                raw=json.dumps(m,ensure_ascii=False),error=m['state'] in ('closed','rejected'),
                status={'closed':'模型已停止，未确认送达','rejected':'建议队列已满'}.get(m['state'],'等待后续决策读取'))
            self.entries.append(entry);self.advice_entries[m['id']]=entry
        elif kind in ('operator_advice_sent','operator_advice_received','operator_advice_retry'):
            label={'operator_advice_sent':'已加入第%s轮，等待回复'%rid,
                'operator_advice_received':'模型已收到；采纳／执行情况见回复',
                'operator_advice_retry':'请求失败，建议保留等待下一轮'}[kind]
            for mid in row['ids']:
                if mid in self.advice_entries:self.advice_entries[mid]['status']=label
        elif kind=='tactical_request_finished':
            error=row.get('error');raw=row.get('response');body=''
            try:
                plan=json.loads(raw) if raw else {}
                body=str(plan.get('reason',''))
                if 'orders' in plan:
                    labels={'defend':'增援防守','attack':'进攻','hold':'固守','withdraw':'撤退'}
                    body+='\n'+('\n'.join(str(o.get('squad','?'))+'：'+labels.get(o.get('mode'),str(o.get('mode')))+' → '+str(o.get('target'))+'；'+str(o.get('reason','')) for o in plan['orders']) or '保留已有命令')
                else:body+='\n战术：'+str(plan.get('mode','无'))+' · 目标 '+str(plan.get('objective','无'))
            except (ValueError,TypeError,AttributeError) as issue:error=error or str(issue)
            if error:body+='\n错误：'+str(error)
            self.entries.append(dict(title='战术指挥 #%s · %s · %.1f 秒'%(rid,game_time(row.get('game_s')),row.get('latency_s',0)),
                body=body,raw=raw or str(error),error=bool(error),status='请求失败' if error else '已回复，等待战况复核'))
        elif kind=='defense_request_finished':
            error=row.get('error');raw=row.get('response');body=''
            try:
                plan=json.loads(raw) if raw else {}
                body=plan.get('reason','')+'\n防守指令：'+str(plan.get('mode','无'))+' · 基地 '+str(plan.get('base_tag','?'))
            except (ValueError,TypeError,AttributeError) as issue:error=error or str(issue)
            if error:body+='\n错误：'+str(error)
            self.entries.append(dict(title='防守副官 #%s · %s · %.1f 秒'%(rid,game_time(row.get('game_s')),row.get('latency_s',0)),
                body=body,raw=raw or str(error),error=bool(error),status='请求失败' if error else '已回复，等待当前战况复核'))
        elif kind=='request_queued':
            self.requests[rid]=row.get('prompt',{}).get('world',{}).get('game_s',0);self.pending=rid
        elif kind=='request_finished':
            if self.pending==rid:self.pending=None
            raw=row.get('response');error=row.get('error');body='';tasks=[]
            if raw is not None:
                try:
                    plan=json.loads(raw)
                    if not isinstance(plan,dict):raise ValueError('回复不是 JSON 对象')
                    body=plan.get('reason') or '（本条回复没有决策说明）'
                    if not isinstance(body,str):body=json.dumps(body,ensure_ascii=False)
                    tasks=plan.get('tasks',[])
                except (ValueError,TypeError) as issue:body='回复无法解析：'+str(issue);error=error or str(issue)
            if error:body+='\n错误：'+str(error)
            if isinstance(tasks,list):
                commands=[str(t.get('skill','?'))+(' ×'+str(t['count']) if t.get('count',1)!=1 else '') for t in tasks if isinstance(t,dict)]
                body+='\n指令：'+(' · '.join(commands) if commands else '等待，保留已有任务')
            else:body+='\n指令字段格式错误：'+str(tasks)
            entry=dict(title='#%s  %s  ·  %.1f 秒'%(rid,game_time(self.requests.get(rid)),row.get('latency_s',0)),
                       body=body,raw=raw or str(error),error=bool(error),status='请求失败' if error else '已回复，待执行器确认')
            self.entries.append(entry);self.plans[rid]=entry
        elif kind=='feedback_event':
            event=row['event'];name=event.get('kind');rid=event.get('request_id')
            if name in ('tactical_plan','tactical_state','tactical_plan_rejected','tactical_plan_expired','tactical_base_alert','tactical_base_alert_cleared','tactical_orders_issued','tactical_arrived','tactical_order_failed','tactical_squad_adjusted'):
                failed=name in ('tactical_plan_rejected','tactical_plan_expired','tactical_order_failed')
                self.entries.append(dict(title=game_time(event.get('game_loop',0)/22.4)+' · 战术执行',
                    body=str(event.get('reason') or '')+('\n错误：'+str(event['error']) if event.get('error') else ''),raw=json.dumps(event,ensure_ascii=False),
                    error=failed,status='未执行该回复' if failed else '指令已接收，效果以实际观察为准'))
            if name in ACCEPTED and rid in self.plans:self.plans[rid]['status']='计划已接收（不代表任务完成）'
            if name in ERRORS:
                if rid in self.plans:self.plans[rid].update(status=LABELS[name],error=True)
                self.entries.append(dict(title=game_time(event.get('game_loop',0)/22.4)+'  ·  '+LABELS[name],
                    body=str(event.get('error') or event.get('reason') or event.get('result_name') or event),
                    raw=json.dumps(event,ensure_ascii=False,indent=2),error=True,status='错误已记录'))
            elif name in ('spatial_mission_started','scout_contact','spatial_arrival','spatial_mission_report','spatial_mission_error','spatial_target_resolved','spatial_cancel_stop_requested'):
                labels={'spatial_mission_started':'模型派遣任务已开始','scout_contact':'侦察发现敌人，等待模型决定',
                    'spatial_arrival':'已实际到达指定地点','spatial_mission_report':'行动结果已反馈模型',
                    'spatial_mission_error':'行动失败，完整错误已记录','spatial_target_resolved':'目标已解析到可通行格',
                    'spatial_cancel_stop_requested':'模型已取消任务，停止命令已请求'}
                self.entries.append(dict(title=game_time(event.get('game_loop',0)/22.4)+' · 侦察／调兵',
                    body=event.get('error') or event.get('reason') or json.dumps(event.get('contacts',event.get('observation',event.get('report',event))),ensure_ascii=False),
                    raw=json.dumps(event,ensure_ascii=False,indent=2),
                    error=name=='spatial_mission_error' or event.get('state')=='failed',status=labels[name]))
            elif name=='all_army_coverage':
                counts=event.get('counts',{})
                blocked=counts.get('failed',0)+counts.get('stalled',0)
                self.entries.append(dict(title=game_time(event.get('game_loop',0)/22.4)+' · 全军进攻',
                    body='已下令 %d/%d，观察到出发 %d，受阻或停滞 %d，其他任务占用或无法调动 %d。'%(
                        event.get('dispatched',0),event.get('total',0),event.get('departed',0),blocked,counts.get('excluded',0)),
                    raw=json.dumps(event,ensure_ascii=False),error=bool(blocked),status='按实际位置核对，已下令不等于已出发'))
            elif name in ('mineral_transfer_dispatched','mineral_transfer_arrived','mineral_transfer_unconfirmed'):
                label={'mineral_transfer_dispatched':'已派遣富余矿工，等待到达',
                       'mineral_transfer_arrived':'观察到矿工到达矿点并持有采集指令',
                       'mineral_transfer_unconfirmed':'转移未确认，已释放分配预留'}[name]
                body=('%d人 → 基地%s'%(len(event.get('workers',[])),event.get('destination'))
                      if name=='mineral_transfer_dispatched' else '工人%s → 基地%s'%(event.get('worker'),event.get('destination')))
                self.entries.append(dict(title=game_time(event.get('game_loop',0)/22.4)+' · 矿工调配',body=body,
                    raw=json.dumps(event,ensure_ascii=False),error=name=='mineral_transfer_unconfirmed',status=label))
            elif name in ('macro_plan_set','economy_reservation','economy_milestone'):
                states={'not_requested':'尚未设置预算','saving':'正在为扩张存钱',
                    'expired':'预算预留到期，已释放','base_goal_started':'基地数量达到开工目标',
                    'expansion_order_issued':'扩张命令已发出，等待建成',
                    'missing_expand_task':'尚未派遣扩张任务，未预留资源',
                    'released_by_model':'模型解除资源预留','unsupported_ambiguous_recipe':'配方造价不唯一，无法预留'}
                if name=='macro_plan_set':
                    plan=event['plan']
                    body='开局：'+{'economic':'经济扩张','pressure':'限时压制','defend':'防守','custom':'自定'}.get(plan['opening'],plan['opening'])
                    body+='；目标：%d基地 / %d工人。%s'%(plan['target_bases'],plan['worker_target'],plan['reason'])
                    status='模型已设置运营目标'
                elif name=='economy_reservation':
                    body=states.get(event.get('state'),str(event.get('state')))
                    if event.get('state')=='saving':body+='：%s矿 / %s气'%(event['minerals'],event['gas'])
                    status='扩张预算'
                else:
                    body={'second_base_first_observed':'观察到第二座基地开工或已存在',
                          'second_base_completed_first_observed':'观察到第二座基地建成'}.get(event.get('milestone'),str(event.get('milestone')))
                    status='实际运营进度'
                self.entries.append(dict(title=game_time(event.get('game_loop',0)/22.4)+' · 运营',
                    body=body,raw=json.dumps(event,ensure_ascii=False,indent=2),error=False,status=status))
            elif isinstance(name,str) and name.startswith('orbital_'):
                failed=name=='orbital_failed'
                ability={'scan':'雷达扫描','call_mule':'呼叫矿骡','supply_drop':'额外补给'}.get(event.get('skill'),'轨道能力')
                self.entries.append(dict(title=game_time(event.get('game_loop',0)/22.4)+' · '+ability,
                    body=event.get('error') or json.dumps(event,ensure_ascii=False),
                    raw=json.dumps(event,ensure_ascii=False,indent=2),error=failed,status={
                        'orbital_order_issued':'已发出，等待实际效果',
                        'orbital_confirmed':'能量与效果已确认',
                        'orbital_already_covered':'已有扫描覆盖，未重复耗能',
                        'orbital_failed':'失败已反馈模型'}.get(name,'轨道能力反馈')))
            elif isinstance(name,str) and name.startswith('combat_micro_'):
                failed=name in ('combat_micro_failed','combat_micro_unavailable','combat_micro_unconfirmed')
                action={'stim':'兴奋剂','siege':'坦克架起','unsiege':'坦克收起'}.get(event.get('action'),event.get('action','战斗能力'))
                self.entries.append(dict(title=game_time(event.get('game_loop',0)/22.4)+' · 战斗执行',
                    body=event.get('reason') or action+' · 单位 '+str(event.get('unit_tag','?')),
                    raw=json.dumps(event,ensure_ascii=False,indent=2),error=failed,status={
                        'combat_micro_requested':'已生成命令，等待引擎与实际效果',
                        'combat_micro_confirmed':'已观察到实际 buff／形态变化',
                        'combat_micro_failed':'动作未确认成功','combat_micro_unavailable':'技能当前不可用',
                        'combat_micro_unconfirmed':'控制权或单位状态改变，结果未确认'}.get(name,'战斗执行反馈')))
            elif isinstance(name,str) and name.startswith('defense_'):
                failed=name in ('defense_request_failed','defense_plan_expired','defense_order_blocked')
                self.entries.append(dict(title=game_time(event.get('game_loop',0)/22.4)+' · 防守协调',
                    body=event.get('reason') or event.get('error') or event.get('handling') or json.dumps(event,ensure_ascii=False),
                    raw=json.dumps(event,ensure_ascii=False,indent=2),error=failed,status={
                        'defense_alert':'基地警报：守备立即响应','defense_plan_accepted':'防守计划已接收（不代表成功）',
                        'defense_released':'警报解除，交还部队','defense_task_paused':'原战术任务暂停',
                        'defense_no_units':'基地告警：暂无可分配部队','defense_task_resumed':'原战术任务恢复','defense_request_failed':'副官请求失败，守备仍运行',
                        'defense_plan_expired':'副官回复过期，未执行'}.get(name,'防守执行反馈')))
            elif name in ('python_development_started','python_development_ready','python_development_skipped'):
                label={'python_development_started':'后台编写与测试','python_development_ready':'通过隔离验收，等待模型派遣','python_development_skipped':'现场开发未继续'}[name]
                self.entries.append(dict(title=game_time(event.get('game_loop',0)/22.4)+' · 现场技能开发',
                    body=str(event.get('skill',''))+'：'+str(event.get('goal') or event.get('error') or event.get('handling') or event.get('reason') or label),
                    raw=json.dumps(event,ensure_ascii=False,indent=2),error=False,status=label))
            elif name in ('python_repair_started','python_skill_revision_ready','python_repair_skipped'):
                self.entries.append(dict(title=game_time(event.get('game_loop',0)/22.4)+'  ·  技能维护',body=json.dumps(event,ensure_ascii=False),
                    raw=json.dumps(event,ensure_ascii=False,indent=2),error=False,status='后台修复状态'))


class Window:
    def __init__(self,run):
        self.run=Path(run).resolve();self.feeds=[Feed(),Feed()];self.data=None;self.notice='等待对局数据…';self.offsets=[0,0]
        self.raw=False;self.only_errors=False;self.follow=True;self.buttons=[];self.last_poll=0
        self.inputs=[ChatInput(),ChatInput()];self.focus=None;self.input_rects={}
        self.chats=[[],[]];self.chat_status=[{},{}];self.chat_notices=['','']
        pygame.init();self.screen=pygame.display.set_mode((1320,960),pygame.RESIZABLE)
        pygame.key.stop_text_input()
        pygame.display.set_caption('星际 AI 对战 · 双方输出')
        result=subprocess.run(['fc-match',':lang=zh-cn','-f','%{file}'],capture_output=True,text=True,check=True)
        candidates=['/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
                    '/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc',result.stdout.strip()]
        for path in candidates:
            try:
                font=pygame.font.Font(path,20)
                if (all(x is not None for x in font.metrics('双方决策输出中文'))
                        and pygame.image.tostring(font.render('中',True,'white'),'RGBA') !=
                            pygame.image.tostring(font.render('文',True,'white'),'RGBA')):break
            except (pygame.error,OSError):pass
        else:raise RuntimeError('未找到支持中文的字体')
        self.fonts={n:pygame.font.Font(path,n) for n in (16,18,20,24,28)}
        print('[output window] Chinese font='+path+'; run='+str(self.run),flush=True)
    def poll(self):
        now=time.monotonic()
        if now-self.last_poll<.5:return
        self.last_poll=now
        try:
            data,self.notice=read_match_status(self.run);self.data=data
            sample=(data.get('samples') or [{}])[-1]
            for feed,c in zip(self.feeds,sample.get('commanders',[])):feed.poll(c['feedback']['journal'])
        except FileNotFoundError:self.notice='等待原生游戏启动，尚未产生对局数据…'
        except json.JSONDecodeError:self.notice='状态文件正在更新，暂时保留上次画面…'
        except (OSError,KeyError,TypeError,ValueError) as error:self.notice='读取对局状态失败：'+str(error)
        for i,feed in enumerate(self.feeds):
            if feed.path:
                try:
                    self.chat_status[i]=read_status(mailbox_path(feed.path))
                    known={m['id']:m for m in self.chat_status[i]['messages']}
                    local={m['id']:m for m in self.chats[i]}
                    local.update(known)
                    self.chats[i]=sorted(local.values(),key=lambda m:(m['submitted_at'],m['id']))[-32:]
                    if self.chats[i] and self.chats[i][-1]['state']!='queued':self.chat_notices[i]=''
                except (OSError,ValueError,KeyError,TypeError):
                    self.chat_status[i]={}
        if self.follow:self.offsets=[0,0]

    def send_advice(self,i):
        if self.inputs[i].composition:return
        try:
            if not self.feeds[i].path:raise ValueError('等待该玩家的指挥进程启动')
            m=submit_advice(mailbox_path(self.feeds[i].path),self.inputs[i].text,player=i+1)
            self.chats[i].append(dict(m,state='queued',request_id=None))
            self.inputs[i].clear();self.chat_notices[i]='已排队，将在下一轮决策发送'
        except (OSError,ValueError,KeyError) as e:self.chat_notices[i]=str(e)

    def focus_input(self,i):
        if self.focus is not None:self.inputs[self.focus].composition=''
        self.focus=i
        if i is None:pygame.key.stop_text_input()
        else:
            pygame.key.start_text_input()
            pygame.key.set_text_input_rect(self.input_rects[i])

    def draw_chat(self,i,x,top,width):
        editor=self.inputs[i];status=self.chat_status[i];color=COLORS[i]
        self.text('给 P%d 的建议 · 下一轮决策读取'%(i+1),x+16,top+5,color,18)
        labels={'queued':'等待下一轮','sent':'已加入决策，等待回复','retry':'请求失败，下一轮重试',
                'received':'模型已收到，采纳情况见上方决策','closed':'未确认送达，模型已停止','rejected':'建议队列已满'}
        message=self.chats[i][-1] if self.chats[i] else None
        if message:
            self.text(labels.get(message['state'],message['state']),x+16,top+32,GREEN if message['state']=='received' else MUTED,16)
            lines=self.wrap('你：'+message['text'],width-40,16)
            for n,line in enumerate(lines[:2]):self.text(line,x+16,top+55+n*20,size=16)
            reply=self.feeds[i].plans.get(message.get('request_id')) if message['state']=='received' else None
            if reply:
                for n,line in enumerate(self.wrap('模型：'+reply['body'].split('\n')[0],width-40,16)[:2]):
                    self.text(line,x+16,top+100+n*20,RED if reply['error'] else GREEN,16)
        else:self.text('输入战术建议或问题，模型会在下一轮决策说明中回应。',x+16,top+40,MUTED,16)
        rect=pygame.Rect(x+16,top+150,width-132,64);self.input_rects[i]=rect
        pygame.draw.rect(self.screen,CARD,rect,border_radius=7)
        pygame.draw.rect(self.screen,color if self.focus==i else '#34465e',rect,width=1,border_radius=7)
        self.screen.set_clip(rect.inflate(-10,-8))
        display=editor.text[:editor.cursor]+editor.composition+editor.text[editor.cursor:]
        if not display:self.text('例如：先守住二矿，暂缓进攻',rect.x+8,rect.y+7,MUTED,16)
        lines=self.wrap(display,rect.w-16,16)
        before=self.wrap(editor.text[:editor.cursor]+editor.composition,rect.w-16,16)
        if display.endswith('\n'):lines.append('')
        if (editor.text[:editor.cursor]+editor.composition).endswith('\n'):before.append('')
        start=max(0,len(before)-2)
        if editor.selected:pygame.draw.rect(self.screen,'#304c70',rect.inflate(-12,-12))
        for n,line in enumerate(lines[start:start+3]):self.text(line,rect.x+8,rect.y+6+n*21,size=16)
        if self.focus==i:
            cy=rect.y+6+(len(before)-1-start)*21
            cx=rect.x+8+self.fonts[16].size(before[-1])[0]
            pygame.draw.line(self.screen,color,(cx,cy),(cx,cy+18))
            pygame.key.set_text_input_rect(pygame.Rect(cx,cy,1,21))
        self.screen.set_clip(None)
        enabled=bool(status.get('accepting') and time.time()-status.get('updated_at',0)<=15)
        self.button('发送',x+width-104,top+164,88,('send',i),enabled)
        tip=editor.error or self.chat_notices[i] or status.get('reason') or ('Enter发送 · Shift+Enter换行 · Ctrl+V粘贴' if enabled else '等待该玩家的指挥进程更新')
        self.text(self.wrap(tip,width-100,16)[0],x+16,top+223,MUTED,16)
        self.text('%d/%d'%(len(editor.text),MAX_TEXT),x+width-90,top+223,MUTED,16)
    def text(self,text,x,y,color=TEXT,size=18):
        surf=self.fonts[size].render(str(text),True,color);self.screen.blit(surf,(x,y))
    def wrap(self,value,width,size=18):
        font=self.fonts[size];lines=[]
        for paragraph in str(value).splitlines() or ['']:
            line=''
            for char in paragraph:
                if line and font.size(line+char)[0]>width:lines.append(line);line=''
                line+=char
            lines.append(line)
        return lines
    def button(self,label,x,y,width,key,on=False):
        rect=pygame.Rect(x,y,width,34);pygame.draw.rect(self.screen,'#2c4663' if on else CARD,rect,border_radius=7)
        self.text(label,x+12,y+5,size=16);self.buttons.append((rect,key))
    def draw(self):
        self.poll();w,h=self.screen.get_size();self.screen.fill(BG);self.buttons=[]
        self.text('双方决策输出',24,18,size=28)
        data=self.data or {};samples=data.get('samples') or [];sample=samples[-1] if samples else {};timing=sample.get('timing',{})
        outcome=data.get('outcome','starting');status=match_status_label(outcome)
        if data.get('realtime') is False:status+=' · 非实时'+('（等待模型）' if timing.get('paused_for_llm') else '（步进中）')
        speed_label={'slower':'最慢速度','normal':'普通速度','faster':'更快速度'}.get(data.get('game_speed'))
        if speed_label:status+=' · '+speed_label
        self.text(status+'  ·  游戏 '+game_time((sample.get('worlds') or [{}])[0].get('game_s'))+'  ·  %.3f×'%timing.get('game_wall_ratio',0),265,29,MUTED,size=16)
        self.button('正文 / 原始 JSON',w-400,20,168,'raw',self.raw)
        self.button('只看错误',w-222,20,98,'errors',self.only_errors)
        self.button('回到最新',w-114,20,90,'latest',self.follow)
        self.text('鼠标移到任一栏滚轮查看历史；关闭此窗口不会停止游戏。',24,66,MUTED,size=16)
        self.text(self.notice or '显示模型公开回复与执行反馈；技能名称保留原文。',24,92,RED if self.notice else MUTED,size=16)
        gap=16;pw=(w-48-gap)//2;top=128;bottom=h-34
        for i,feed in enumerate(self.feeds):
            x=24+i*(pw+gap);color=COLORS[i];area=pygame.Rect(x,top,pw,bottom-top)
            pygame.draw.rect(self.screen,PANEL,area,border_radius=12)
            identities=data.get('identities') or []
            identity=identities[i] if i<len(identities) else {};model=identity.get('model','等待模型')
            self.text('P%d  '%(i+1)+model,x+16,top+13,color,24)
            commanders=sample.get('commanders') or [];c=commanders[i] if i<len(commanders) else {}
            thinking='正在生成回复' if feed.pending is not None and outcome=='running' else '等待下一次决策' if outcome=='running' else '历史记录'
            self.text(thinking+'   ·   已接收 %d   错误 %d'%(c.get('decisions',0),c.get('errors',0)),x+16,top+52,MUTED,16)
            d=c.get('defense_adjutant',{})
            tactics=c.get('army_tactics',{})
            coverage=tactics.get('all_army',{})
            if coverage.get('enabled'):
                counts=coverage.get('counts',{})
                self.text('全军：已发 %d/%d · 出发 %d · 受阻 %d · 占用 %d'%(
                    coverage.get('dispatched',0),coverage.get('total',0),coverage.get('departed',0),
                    counts.get('failed',0)+counts.get('stalled',0),counts.get('excluded',0)),
                    x+16,top+78,MUTED,16)
            elif tactics.get('enabled'):
                label={'assemble':'集结','attack':'进攻','hold':'固守','withdraw':'撤退'}.get(tactics.get('mode'),'模型调兵' if tactics.get('kind')=='independent_battle_llm' else '待命')
                self.text('战术指挥：'+label+(' · 等待回复' if tactics.get('thinking') else '')+
                          ' · 部队 %d · 每批 %d · 回复 %d'%(len(tactics.get('unit_tags',[])),tactics.get('min_group',6),tactics.get('accepted',0)),
                          x+16,top+78,MUTED,16)
            if d.get('enabled'):
                self.text('防守副官：'+('等待回复' if d.get('thinking') else '守备中' if d.get('active') else '待命')+
                          ' · 分配 %d · 回复 %d · 错误 %d'%(len(d.get('unit_tags',[])),d.get('accepted',0),d.get('errors',0)),
                          x+16,top+78,MUTED,16)
            if feed.error:self.text(feed.error[:60],x+16,top+78,RED,16)
            chat_top=bottom-254
            view=pygame.Rect(x+8,top+104,pw-16,max(1,chat_top-top-115));self.screen.set_clip(view)
            entries=[e for e in reversed(feed.entries) if not self.only_errors or e['error']]
            y=view.y-self.offsets[i]
            if not entries:self.text('暂无错误记录' if self.only_errors else '等待模型的第一条回复…',x+18,view.y+15,MUTED)
            for entry in entries:
                layout_key=(self.raw,pw)
                if entry.get('_layout_key')!=layout_key:
                    entry['_lines']=self.wrap(entry['raw'] if self.raw else entry['body'],pw-52);entry['_layout_key']=layout_key
                lines=entry['_lines']
                card_h=78+len(lines)*27
                if y+card_h>=view.top and y<=view.bottom:
                    pygame.draw.rect(self.screen,CARD,pygame.Rect(x+12,y,pw-24,card_h-10),border_radius=9)
                    self.text(entry['title'],x+24,y+10,color,16)
                    self.text(entry.get('status',''),x+24,y+35,RED if entry['error'] else GREEN,16)
                    for n,line in enumerate(lines):
                        yy=y+62+n*27
                        if view.top-30<yy<view.bottom:self.text(line,x+24,yy,RED if entry['error'] else TEXT)
                y+=card_h
            self.screen.set_clip(None)
            total=y-view.y+self.offsets[i];self.offsets[i]=min(self.offsets[i],max(0,total-view.h))
            if total>view.h:
                bar=max(24,view.h*view.h/total);by=view.y+(view.h-bar)*self.offsets[i]/max(1,total-view.h)
                pygame.draw.rect(self.screen,color,pygame.Rect(x+pw-7,by,3,bar),border_radius=2)
            self.draw_chat(i,x,chat_top,pw)
        self.text(self.run.name+'  ·  决策与建议状态每 0.5 秒更新；关闭窗口不停止对局',24,h-26,MUTED,16)
        pygame.display.flip()
    def handle_event(self,event):
        if event.type==pygame.QUIT:return False
        elif event.type==pygame.VIDEORESIZE:self.screen=pygame.display.set_mode((max(1000,event.w),max(720,event.h)),pygame.RESIZABLE)
        elif event.type in (pygame.TEXTINPUT,pygame.TEXTEDITING,pygame.KEYDOWN) and self.focus is not None:
            if self.inputs[self.focus].handle(event):self.send_advice(self.focus)
        elif event.type==pygame.MOUSEWHEEL:
            i=int(pygame.mouse.get_pos()[0]>=self.screen.get_width()/2)
            self.offsets[i]=max(0,self.offsets[i]-event.y*90);self.follow=False
        elif event.type==pygame.MOUSEBUTTONDOWN and event.button==1:
            selected=next((i for i,rect in self.input_rects.items() if rect.collidepoint(event.pos)),None)
            if selected is not None:self.focus_input(selected)
            for rect,key in self.buttons:
                if rect.collidepoint(event.pos):
                    if isinstance(key,tuple):self.send_advice(key[1])
                    elif key=='raw':self.raw=not self.raw;self.offsets=[0,0]
                    elif key=='errors':self.only_errors=not self.only_errors;self.offsets=[0,0]
                    else:self.follow=True;self.offsets=[0,0]
        return True

    def loop(self,snapshot=None):
        clock=pygame.time.Clock();running=True
        while running:
            for event in pygame.event.get():
                if not self.handle_event(event):running=False;break
            self.draw()
            if snapshot:
                pygame.image.save(self.screen,str(snapshot));print('[output window] snapshot='+str(snapshot),flush=True);break
            clock.tick(12)
        pygame.quit()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',required=True);parser.add_argument('--snapshot')
    args=parser.parse_args();Window(args.run).loop(args.snapshot)

if __name__=='__main__':main()
