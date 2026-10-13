"""Small Unicode/IME-aware editor for the native decision window."""
import pygame
import time
from arena.operator_advice import MAX_TEXT


class ChatInput:
    def __init__(self):
        self.text='';self.cursor=0;self.composition='';self.selected=False;self.error='';self.ime_commit_until=0

    def insert(self,text):
        text=text.replace('\r\n','\n').replace('\r','\n').replace('\x00','')
        current='' if self.selected else self.text
        cursor=0 if self.selected else self.cursor
        if len(current)+len(text)>MAX_TEXT:
            self.error='每条建议最多%d字'%MAX_TEXT;return
        self.text=current[:cursor]+text+current[cursor:]
        self.cursor=cursor+len(text);self.selected=False;self.composition='';self.error=''

    def clear(self):
        self.text='';self.cursor=0;self.composition='';self.selected=False;self.error=''

    def handle(self,event):
        """Return True only for a send keystroke outside active IME composition."""
        if event.type==pygame.TEXTEDITING:
            self.composition=event.text
        elif event.type==pygame.TEXTINPUT:
            if self.composition:self.ime_commit_until=time.monotonic()+.15
            self.insert(event.text)
        elif event.type==pygame.KEYDOWN:
            mod=getattr(event,'mod',0)
            if event.key in (pygame.K_RETURN,pygame.K_KP_ENTER):
                if self.composition or time.monotonic()<self.ime_commit_until:return False
                if mod & pygame.KMOD_SHIFT:self.insert('\n')
                else:return True
            elif self.composition:return False
            elif event.key==pygame.K_a and mod & pygame.KMOD_CTRL:self.selected=True
            elif event.key==pygame.K_v and mod & pygame.KMOD_CTRL:
                try:
                    if not pygame.scrap.get_init():pygame.scrap.init()
                    value=pygame.scrap.get(pygame.SCRAP_TEXT)
                    if value:self.insert(value.decode('utf-8').rstrip('\x00'))
                except (pygame.error,UnicodeError):self.error='无法粘贴剪贴板文字'
            elif event.key in (pygame.K_BACKSPACE,pygame.K_DELETE):
                if self.selected:self.clear()
                elif event.key==pygame.K_BACKSPACE and self.cursor:
                    self.text=self.text[:self.cursor-1]+self.text[self.cursor:];self.cursor-=1
                elif event.key==pygame.K_DELETE:self.text=self.text[:self.cursor]+self.text[self.cursor+1:]
            elif event.key==pygame.K_LEFT:self.cursor=max(0,self.cursor-1);self.selected=False
            elif event.key==pygame.K_RIGHT:self.cursor=min(len(self.text),self.cursor+1);self.selected=False
            elif event.key==pygame.K_HOME:self.cursor=0;self.selected=False
            elif event.key==pygame.K_END:self.cursor=len(self.text);self.selected=False
        return False
