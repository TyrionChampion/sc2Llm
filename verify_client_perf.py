"""Compatibility entry point for the realtime offscreen regression."""
from absl import app
from verify_realtime import main
if __name__ == '__main__':
    print('[verification] replaced screen-action check with realtime raw regression', flush=True)
    app.run(main)
