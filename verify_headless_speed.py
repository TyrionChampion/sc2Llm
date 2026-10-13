"""Check real Linux engine speed using normal observations; no model/debug calls."""
import argparse
import json
import time
from pathlib import Path
from absl import flags
from arena.raw_engine import RawArena


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--speed',choices=['slower','normal','faster'],default='slower')
    parser.add_argument('--seconds',type=float,default=20)
    parser.add_argument('--output',default='logs/headless_speed_probe.json')
    args=parser.parse_args()
    if args.seconds<10:parser.error('at least 10 wall seconds are needed')
    flags.FLAGS(['headless_speed_probe'])
    with RawArena(render=False,difficulty='hard',races=['terran'],bot_race='terran',
                  game_speed=args.speed) as arena:
        started=time.monotonic()
        while time.monotonic()-started<args.seconds:
            arena.step([[]]);time.sleep(.03)
        timing=arena.clock.report()
        report=dict(mode='runtime validation, not a campaign match',model_calls=0,debug_used=False,
                    game_speed=args.speed,realtime=True,engine=arena.controllers[0].ping().game_version,
                    timing=timing,validated=timing['speed_check']=='ok')
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,indent=2))
    print(json.dumps(report))
    if not report['validated']:raise SystemExit(1)


if __name__=='__main__':main()
