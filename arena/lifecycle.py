"""Durable match status and idempotent signal-driven shutdown."""
import json
import os
from pathlib import Path
import signal
import tempfile
import time


def write_json_atomic(path, value):
    path = Path(path)
    text = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ShutdownSignals:
    """The first signal interrupts play; later signals cannot interrupt cleanup."""
    def __init__(self):
        self.previous = {}
        self.requested = False
        self.cleaning = False
        self.state = dict(events=[],omitted_events=0,sender="not recorded by Python signal handler")

    def install(self):
        for number in (signal.SIGTERM, signal.SIGINT):
            self.previous[number] = signal.signal(number, self.receive)

    def receive(self, number, frame):
        if len(self.state["events"]) < 16:
            self.state["events"].append(dict(signal=signal.Signals(number).name,
                number=number,received_at=time.time(),during_cleanup=self.cleaning))
        else:
            self.state["omitted_events"] += 1
        first = not self.requested
        self.requested = True
        if first and not self.cleaning:
            raise KeyboardInterrupt("stop signal: " + signal.Signals(number).name)

    def restore(self):
        for number, handler in self.previous.items():
            signal.signal(number, handler)


def read_match_status(run, now=None):
    """Return a display copy; never claim stale observations prove a live game."""
    run = Path(run)
    path = run / "match.json"
    data = json.loads(path.read_text())
    if not isinstance(data,dict):
        raise ValueError("match.json is not an object")
    notice = ""
    launch = run / "run.json"
    if launch.exists():
        meta = json.loads(launch.read_text())
        if not isinstance(meta,dict):
            raise ValueError("run.json is not an object")
        terminal = {"interrupted":"interrupted","failed":"error","finished":"ended_unknown"}
        if data.get("outcome") in ("starting","running") and meta.get("status") in terminal:
            data["reported_outcome"] = data.get("outcome")
            data["outcome"] = terminal[meta["status"]]
            notice = "启动器已记录对局结束，游戏报告未完成收尾：" + str(meta.get("error") or meta["status"])
    age = (time.time() if now is None else now) - path.stat().st_mtime
    if data.get("outcome") == "running" and age > 25:
        data["reported_outcome"] = "running"
        data["outcome"] = "stale"
        notice = "对局状态已 %d 秒未更新，当前运行情况未知，请检查控制进程。"%age
    if data.get("outcome") in ("interrupted","error") and not notice:
        notice = str(data.get("error") or "对局已停止，请查看运行日志。")
    return data, notice


def match_status_label(outcome):
    labels={"running":"实时对局","starting":"正在启动","interrupted":"对局已停止",
            "error":"对局异常停止","stale":"状态失联（未确认仍在运行）",
            "ended_unknown":"对局已结束（结果待核实）","time limit":"对局已结束（时间上限）"}
    if isinstance(outcome,dict):
        results={"Victory":"胜","Defeat":"负","Tie":"平"}
        return "对局已结束："+" · ".join("P"+str(player)+" "+results.get(result,str(result))
                                     for player,result in outcome.items())
    return labels.get(str(outcome),"对局已结束："+str(outcome))
