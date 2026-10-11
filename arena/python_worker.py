"""Trusted wire runner, copied into an isolated Python process; never import user code here in host."""
import importlib.util
import json
import resource
import sys
import traceback

# Applied before loading the submitted module. Parent additionally enforces wall time/output.
for kind, limit in ((resource.RLIMIT_AS, 256*1024*1024), (resource.RLIMIT_CPU, 60),
                    (resource.RLIMIT_FSIZE, 1024*1024), (resource.RLIMIT_NOFILE, 32),
                    (resource.RLIMIT_NPROC, 16), (resource.RLIMIT_CORE, 0)):
    resource.setrlimit(kind, (limit, limit))
wire = sys.stdout
sys.stdout = sys.stderr  # Ordinary print() becomes bounded diagnostics, never the wire.
def send(value):
    wire.write(json.dumps(value, allow_nan=False, separators=(",", ":"))+"\n")
    wire.flush()
try:
    spec = importlib.util.spec_from_file_location("tactic", "/skill/tactic.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "tick", None)):
        raise TypeError("skill must export tick(ctx, state)")
    send({"ready": True})
    for line in sys.stdin:
        message = json.loads(line)
        result = module.tick(message["ctx"], message["state"])
        send({"result": result})
except BaseException as error:
    send({"error": type(error).__name__+": "+str(error), "traceback": traceback.format_exc()[-12000:]})
