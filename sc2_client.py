"""Realtime SC2 desktop spectator. Raw AI control is independent of the camera.

./run_sc2.sh .venv311/bin/python sc2_client.py Acropolis mock
Arrows/WASD/mouse edge: pan. Space: hold AI orders (game keeps running).
F: toggle stats. Esc: exit. P2 provider defaults to P1; ARENA_P2_PROVIDER
can select a separate provider. Real LLMs use the existing provider key config.
"""
import os
import time
import numpy as np
import pygame
from absl import app
from arena.raw_engine import RawArena, camera
from arena.commander import Commander, ScriptedPlanner
from arena.llm import LLMClient,PROVIDERS

WIN_W,WIN_H=1024,768
GAME_RES=(1024,768)
SHOT_AFTER=float(os.environ.get('ARENA_SHOT','0') or 0)
SHOT_PATH=os.environ.get('ARENA_SHOT_PATH','logs/client_shot.png')
FG=(210,219,230)


def pick_provider(argv):
    name=(argv[2] if len(argv)>2 else os.environ.get('ARENA_PROVIDER','deepseek')).strip().lower()
    if name not in PROVIDERS: raise ValueError('unknown provider: '+name)
    if name!='mock' and not PROVIDERS[name].configured:
        raise ValueError('missing key for %s; explicitly select mock to run without a model' % name)
    return name


def make_client(name):
    if name=='mock':
        print('[planner] explicit scripted mock baseline, no API calls',flush=True)
        return ScriptedPlanner(float(os.environ.get('ARENA_DELAY','0')))
    if name not in PROVIDERS or not PROVIDERS[name].configured:
        raise ValueError('unknown/unconfigured provider '+name)
    return LLMClient(name)


def build_env(map_name): return RawArena(map_name,render=True)


def draw_text(screen,font,text,x,y,color=FG):
    screen.blit(font.render(text,True,(0,0,0)),(x+1,y+1))
    screen.blit(font.render(text,True,color),(x,y))


def main(argv):
    map_name=argv[1] if len(argv)>1 else 'Acropolis'
    names=[pick_provider(argv),os.environ.get('ARENA_P2_PROVIDER',pick_provider(argv))]
    clients=[make_client(name) for name in names]
    pygame.init(); commanders=[]
    try:
        screen=pygame.display.set_mode((WIN_W,WIN_H))
        pygame.display.set_caption('SC2 Arena | REALTIME | independent camera')
        font=pygame.font.Font(None,19); cap=pygame.time.Clock()
        with build_env(map_name) as arena:
            worlds=arena.initial
            commanders=[Commander(arena,i,c) for i,c in enumerate(clients)]
            running=True; held=False; stats=True
            started=time.monotonic(); last_report=started; last_pan=0; last_frame=started
            while running and not arena.ended:
                for ev in pygame.event.get():
                    if ev.type==pygame.QUIT: running=False
                    elif ev.type==pygame.KEYDOWN:
                        if ev.key in (pygame.K_ESCAPE,pygame.K_q): running=False
                        elif ev.key==pygame.K_SPACE: held=not held
                        elif ev.key==pygame.K_f: stats=not stats
                if not running: break
                now=time.monotonic(); dt=min(.2,now-last_frame); last_frame=now
                keys=pygame.key.get_pressed()
                dx=int(keys[pygame.K_RIGHT] or keys[pygame.K_d])-int(keys[pygame.K_LEFT] or keys[pygame.K_a])
                dy=int(keys[pygame.K_UP] or keys[pygame.K_w])-int(keys[pygame.K_DOWN] or keys[pygame.K_s])
                if pygame.mouse.get_focused():
                    mx,my=pygame.mouse.get_pos()
                    dx+=int(mx>WIN_W-24)-int(mx<24)
                    dy+=int(my<24)-int(my>WIN_H-24)
                batches=[[] if held else c.tick(w) for c,w in zip(commanders,worlds)]
                if (dx or dy) and now-last_pan>=.05:
                    x,y=worlds[0].camera
                    target=(max(1,min(arena.size[0]-1,x+dx*30*dt)),
                            max(1,min(arena.size[1]-1,y+dy*30*dt)))
                    # Camera and unit commands coexist in one player's action batch.
                    batches[0].append(camera(target)); last_pan=now
                worlds=arena.step(batches)
                rgb=arena.rgb()
                if rgb.shape!=(WIN_H,WIN_W,3): raise RuntimeError('unexpected RGB dimensions: %r' % (rgb.shape,))
                arr=np.asarray(rgb).astype(np.uint8)
                surf=pygame.image.frombuffer(arr,GAME_RES,'RGB')
                screen.blit(surf,(0,0))
                timing=arena.clock.report()
                draw_text(screen,font,'REALTIME | %.1f fps | game %.1fs / wall %.1fs = %.2fx | %s' %
                    (timing['fps'],timing['game_s'],timing['wall_s'],timing['game_wall_ratio'],
                     'AI HELD - GAME RUNNING' if held else 'AI active'),8,6,
                    (255,100,80) if timing['speed_check']=='DRIFT' else FG)
                if stats:
                    y=30
                    for i,(c,w) in enumerate(zip(commanders,worlds)):
                        s=c.stats()
                        draw_text(screen,font,'P%d %s mins=%d supply=%d/%d queue=%d fail=%d err=%d %s' %
                            (i+1,names[i],w.minerals,w.supply_used,w.supply_cap,len(s['active_tasks']),s['failed'],s['errors'],
                             'thinking' if s['thinking'] else ''),8,y)
                        y+=20
                        for task in s['active_tasks'][:3]:
                            draw_text(screen,font,'  #%d %s [%s]' % (task['id'],task['skill'],task['state']),8,y)
                            y+=18
                        if c.log: draw_text(screen,font,c.log[-1][:125],8,y,(150,190,160)); y+=22
                draw_text(screen,font,'WASD/arrows/edge: camera | SPACE: hold AI (game continues) | F: stats | ESC: exit',8,WIN_H-22)
                pygame.display.flip()
                if now-last_report>=10:
                    print('[timing]',timing,flush=True); last_report=now
                if SHOT_AFTER and now-started>=SHOT_AFTER:
                    os.makedirs(os.path.dirname(SHOT_PATH) or '.',exist_ok=True)
                    pygame.image.save(screen,SHOT_PATH)
                    print('[screenshot]',SHOT_PATH,flush=True); running=False
                cap.tick(60)
            print('[match]',arena.result() if arena.ended else 'user exit / screenshot limit',flush=True)
            print('[timing-final]',arena.clock.report(),flush=True)
    finally:
        for c in commanders: c.stop()
        pygame.quit()

if __name__=='__main__': app.run(main)
