"""Replay recorded wake timestamps through the commander scheduler, without API calls.

This is a counterfactual scheduling check, not a simulation of model decisions,
SC2 outcomes, defensive effectiveness, or wall-clock performance.
"""
import json
from pathlib import Path
from arena.commander import Commander

EVIDENCE = Path(__file__).parent/'testdata/ai_duel_latency/events.json'


def replay(events, end_game_s, interval=20, step_loops=8):
    scheduler=Commander.__new__(Commander)
    scheduler.interval=interval
    scheduler.last_request=float('-inf')
    scheduler.next_request_game_s=float('inf')
    pending=iter(sorted(events,key=lambda e:e['game_s']))
    event=next(pending,None);requests=[]
    # Assume replies complete in the same paused game frame; wall latency is
    # deliberately absent. The input events remain those from the original run.
    for loop in range(0,int(end_game_s*22.4)+1,step_loops):
        now=loop/22.4
        while event and event['game_s']<=now:
            scheduler._wake_request(event['game_s'],
                delay=3 if event['kind'] in ('plan_rejected','plan_expired') else 2,
                urgent=event['kind']=='tactical_base_alert')
            event=next(pending,None)
        if scheduler._request_due(now):
            requests.append(now)
            scheduler.last_request=now;scheduler.next_request_game_s=float('inf')
    return requests


def report():
    evidence=json.loads(EVIDENCE.read_text());players={}
    for side,data in evidence['players'].items():
        end=data['requests'][-1]
        scheduled=replay(data['events'],end)
        original=[s for s in data['requests'] if s>=480]
        combat=[s for s in scheduled if s>=480]
        players[side]=dict(source=data['source'],
            recorded_requests_after_480=len(original),replayed_requests_after_480=len(combat),
            replayed_min_gap_after_480=round(min(b-a for a,b in zip(combat,combat[1:])),3),
            recorded_mean_gap_after_480=round((original[-1]-original[0])/(len(original)-1),3))
    return dict(meaning=__doc__.strip(),players=players)


if __name__=='__main__':print(json.dumps(report(),ensure_ascii=False,indent=2))
