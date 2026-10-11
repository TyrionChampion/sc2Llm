"""Real-engine smoke for the browser viewer's producer and JSON/JPEG state."""
import io
import json
import os
import threading
import time
from absl import flags
from PIL import Image
import live_viewer as viewer

if __name__=='__main__':
    flags.FLAGS(['verify_viewer'])
    os.environ['ARENA_PROVIDER']='mock'; os.environ['ARENA_P2_PROVIDER']='mock'
    os.environ['ARENA_DELAY']='0'
    thread=threading.Thread(target=viewer.game_loop)
    thread.start()
    try:
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            with viewer._lock:
                state=dict(viewer._state)
            if state['error']: raise AssertionError(state['error'])
            if state['frame_id']>=100: break
            time.sleep(.2)
        else: raise AssertionError('viewer did not produce 100 real frames')
        assert Image.open(io.BytesIO(state['jpeg'])).size==(1024,768)
        assert len(state['players'])==2
        assert state['timing']['game_s']>0
        json.dumps({k:v for k,v in state.items() if k!='jpeg'})
        print('VIEWER_OK',json.dumps({'frames':state['frame_id'],'timing':state['timing']}),flush=True)
    finally:
        viewer._stop.set(); thread.join(timeout=10)
        assert not thread.is_alive(),'viewer failed to shut down'
