"""Chinese IME and per-panel submit through the actual native window, zero model calls."""
import os
os.environ['SDL_VIDEODRIVER']='dummy';os.environ['SDL_AUDIODRIVER']='dummy'
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import pygame
from arena.chat_input import ChatInput
from arena.operator_advice import OperatorAdvice
from arena.lifecycle import write_json_atomic
from llm_output_window import Window,Feed

class EditorTests(unittest.TestCase):
 def key(self,key,mod=0):return pygame.event.Event(pygame.KEYDOWN,key=key,mod=mod)
 def test_ime_enter_commits_without_accidental_send(self):
  e=ChatInput();e.handle(pygame.event.Event(pygame.TEXTEDITING,text='先守二矿',start=0,length=4))
  self.assertFalse(e.handle(self.key(pygame.K_RETURN)));self.assertEqual(e.text,'')
  with patch('arena.chat_input.time.monotonic',return_value=1):
   e.handle(pygame.event.Event(pygame.TEXTINPUT,text='先守二矿'));self.assertFalse(e.handle(self.key(pygame.K_RETURN)))
  with patch('arena.chat_input.time.monotonic',return_value=2):self.assertTrue(e.handle(self.key(pygame.K_RETURN)))
  self.assertEqual(e.text,'先守二矿')
 def test_unicode_editing_selection_and_shift_enter(self):
  e=ChatInput();e.insert('先防守');e.handle(self.key(pygame.K_LEFT));e.insert('二矿');self.assertEqual(e.text,'先防二矿守')
  e.handle(self.key(pygame.K_END));e.handle(self.key(pygame.K_RETURN,pygame.KMOD_SHIFT));e.insert('再反击');self.assertTrue(e.text.endswith('\n再反击'))
  e.handle(self.key(pygame.K_a,pygame.KMOD_CTRL));e.insert('新建议');self.assertEqual(e.text,'新建议')
 def test_oversized_paste_not_silently_truncated(self):
  e=ChatInput();e.insert('已有');e.insert('甲'*1001);self.assertEqual(e.text,'已有');self.assertTrue(e.error)

class WindowTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);root=Path(self.tmp.name)
  self.journals=[root/'p1.jsonl',root/'p2.jsonl'];self.boxes=[]
  for i,p in enumerate(self.journals):
   p.touch();self.boxes.append(OperatorAdvice(p,i+1,lambda row:None))
  write_json_atomic(root/'match.json',dict(outcome='running',game_speed='slower',identities=[{'model':'本地验收（未调用模型）'}]*2,
   samples=[dict(worlds=[{'game_s':250}],timing={},commanders=[{'feedback':{'journal':str(p)}} for p in self.journals])]))
  self.window=Window(root);self.addCleanup(pygame.quit);self.window.draw()
 def test_send_button_path_only_targets_the_selected_player(self):
  w=self.window;w.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN,button=1,pos=w.input_rects[1].center))
  w.handle_event(pygame.event.Event(pygame.TEXTINPUT,text='守二矿，再考虑反击'))
  w.handle_event(pygame.event.Event(pygame.KEYDOWN,key=pygame.K_RETURN,mod=0))
  self.boxes[0].poll();self.boxes[1].poll()
  self.assertEqual(self.boxes[0].batch()['messages'],[]);self.assertEqual(self.boxes[1].batch()['messages'][0]['text'],'守二矿，再考虑反击')
  self.assertEqual(w.inputs[1].text,'');w.draw()
 def test_click_send_and_close_do_not_stop_commander(self):
  w=self.window;w.inputs[0].insert('先侦察')
  rect=next(rect for rect,key in w.buttons if key==('send',0))
  w.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN,button=1,pos=rect.center))
  self.boxes[0].poll();self.assertEqual(self.boxes[0].batch()['messages'][0]['text'],'先侦察')
  self.assertFalse(w.handle_event(pygame.event.Event(pygame.QUIT)))
  self.assertTrue(self.boxes[0].public()['accepting'])
 def test_failed_send_keeps_draft_and_old_reports_render(self):
  w=self.window;self.boxes[0].close();w.inputs[0].insert('别丢掉这条');w.send_advice(0)
  self.assertEqual(w.inputs[0].text,'别丢掉这条');self.assertTrue(w.chat_notices[0])
  w.data['samples'][0]['commanders']=w.data['samples'][0]['commanders'][:1];w.draw()
 def test_message_history_and_received_state_are_not_adoption(self):
  f=Feed();f.consume(dict(record='operator_advice_queued',player=1,message=dict(id='a',text='防守',state='queued')))
  f.consume(dict(record='operator_advice_received',ids=['a'],request_id=2))
  self.assertIn('采纳／执行情况见回复',f.entries[0]['status']);self.assertFalse(f.entries[0]['error'])
if __name__=='__main__':unittest.main()
