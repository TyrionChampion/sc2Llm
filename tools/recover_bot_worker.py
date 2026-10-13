"""Adopt one completed, cleaned-up worker after stopping its orchestrator; no API calls."""
import argparse
import json
from pathlib import Path


def recover(folder, number):
    folder=Path(folder).resolve();path=folder/'campaign.json'
    state=json.loads(path.read_text());matches=state['matches']
    config=json.loads((folder/('worker-%02d.json'%number)).read_text())
    match=Path(config['folder']).resolve()
    if not match.is_relative_to(folder):raise ValueError('Worker folder escapes campaign')
    report_path=match/'match.json';report=json.loads(report_path.read_text())
    if number<=len(matches):
        if Path(matches[number-1]['report']).resolve()!=report_path:raise ValueError('Conflicting completed match')
        return state
    if number!=len(matches)+1 or report['number']!=number:raise ValueError('Nonsequential worker')
    if report['model']!=state['model'] or not report.get('cleanup_complete'):
        raise ValueError('Model mismatch or incomplete worker cleanup')
    previous=config['budget'];usage=json.loads((match/'budget.json').read_text())
    if previous!=state['budget'] or usage['limit']!=previous['limit']:
        raise ValueError('Campaign changed while worker ran; refuse to overwrite counters')
    attempts=report['model_attempts']
    expected=dict(previous['by_role']);expected['battle']=expected.get('battle',0)+attempts
    if (usage['by_role']!=expected or usage['attempted']!=previous['attempted']+attempts
            or sum(usage['by_role'].values())!=usage['attempted']
            or not 0<=usage['attempted']<=usage['limit']):
        raise ValueError('Worker accounting mismatch')
    matches.append(dict(number=number,difficulty=report['difficulty'],report=str(report_path),
                        model=report['model'],outcome=report['outcome'],termination=report['termination'],
                        model_calls=report['model_calls'],result_source=report['result_source']))
    state['budget']=usage
    state.setdefault('worker_recoveries',[]).append(dict(number=number,previous_attempted=previous['attempted'],
        restored_attempted=usage['attempted'],source='completed worker report and budget; no history reset'))
    state['state']='paused_for_review';state['stop_reason']='completed_worker_requires_review'
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(state,ensure_ascii=False,indent=2));temporary.replace(path)
    return state


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('campaign',type=Path);parser.add_argument('--number',type=int,required=True)
    args=parser.parse_args();state=recover(args.campaign,args.number)
    print(json.dumps(dict(matches=len(state['matches']),budget=state['budget'],state=state['state'])))
