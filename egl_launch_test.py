"""Try launching SC2 with a given EGL_PLATFORM and capture its GL log lines.

Usage: python egl_launch_test.py <platform>
"""
import os
import sys
import time

from absl import app
from pysc2.run_configs import platforms


def main(argv):
    del argv
    plat = os.environ.get("TEST_EGL_PLATFORM", "")
    if plat:
        os.environ["EGL_PLATFORM"] = plat
    print("EGL_PLATFORM=%r" % os.environ.get("EGL_PLATFORM"), flush=True)
    print("SC2PATH=%s" % os.environ.get("SC2PATH"), flush=True)

    rc = platforms.Linux()
    proc = rc.start(want_rgb=True, connect=False, timeout_seconds=30)
    print("launched pid=%s" % proc.pid, flush=True)
    time.sleep(15)
    print("--- process stdout/err captured above ---", flush=True)
    proc.close()
    print("closed", flush=True)


if __name__ == "__main__":
    app.run(main)
