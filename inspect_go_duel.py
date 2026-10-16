"""Read-only duel audit from raw request results, feedback and saved observations."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def inspect(folder):
    folder=Path(folder)
    match=json.loads((folder/'match.json').read_text())
    result={k:match.get(k) for k in ('number','status','races','outcomes','result_source',
        'termination','wall_s','engine_action_errors','cleanup_complete','budget')}
    result['players']=[]
    for i in (1,2):
        own=folder/('p'+str(i));errors=[];returned=Counter();attempts=0;worker_hashes=Counter()
        for path in sorted((own/'planner').glob('request-*/request.json')):
            attempts+=1
            response=path.with_name('result.json')
            if not response.exists():continue
            try:data=json.loads(response.read_text())
            except json.JSONDecodeError:continue
            if data.get('worker_source_sha256'):worker_hashes[data['worker_source_sha256']]+=1
            if data.get('error'):errors.append(dict(request=path.parent.name,error=data['error'],
                error_kind=data.get('error_kind'),response_stage=data.get('response_stage'),
                public_json_error=data.get('public_json_error')))
            else:returned[(data.get('metadata') or {}).get('returned_model')]+=1
        events=[];latencies=[]
        for path in sorted((own/'feedback').glob('*.jsonl')):
            for line in path.read_text().splitlines():
                # A live writer may have an unfinished final line.
                try:row=json.loads(line)
                except json.JSONDecodeError:continue
                if row.get('record')=='feedback_event':events.append(row['event'])
                elif row.get('record')=='request_finished':
                    latencies.append(dict(request=row['request_id'],returned=row['api_returned'],
                        latency_s=row['latency_s'],error=row.get('error')))
        side=next((p for p in match.get('players',[]) if p['player']==i),{})
        world=side.get('final_world') or (side.get('timeline') or [{}])[-1].get('world',{})
        result['players'].append(dict(player=i,attempts=attempts,returned_models=dict(returned),
            worker_source_hashes=dict(worker_hashes),
            request_errors=errors,finished_requests=latencies,world=world,
            event_counts=dict(Counter(e['kind'] for e in events)),
            execution_issues=[e for e in events if e['kind'] in (
                'plan_rejected','plan_expired','engine_action_rejected','macro_failed','macro_retry',
                'native_ability_failed','native_building_cancel_failed','native_queue_cancel_failed','native_morph_cancel_failed',
                'inject_larva_failed','orbital_failed','spatial_mission_error','model_request_failed')
                or e['kind']=='task_status' and e.get('state')=='failed'],
            native_abilities=[e for e in events if e['kind'].startswith('native_ability_')]))
    manifest=match.get('code_sha256') or match.get('source_sha256') or match.get('source_hashes')
    if manifest is not None:
        result['source_snapshot_matches']=all((folder/'source'/name).is_file()
            and hashlib.sha256((folder/'source'/name).read_bytes()).hexdigest()==digest
            for name,digest in manifest.items())
    result['scope']='Observer audit; not model input. Dispatch/acknowledgement does not prove a full tactical effect or victory.'
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('match',type=Path)
    args=parser.parse_args()
    print(json.dumps(inspect(args.match),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
