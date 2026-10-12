"""Export only selected public game records, strategies and replays; no runtime state."""
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from arena.autonomous_bot import BOT_RULES, INITIAL_STRATEGY
from arena.autonomous_review import REVIEW_RULES

CAMPAIGNS = ('20261012-093031-xhnbux9u','20261011-191309-d7e114rf')
ALLOWED = {'campaign.json','strategy.json','winning-strategy.json','match.json',
           'review.json','budget.json','summary.json','report.md','match.SC2Replay'}


def main():
    output = ROOT/'handoff';output.mkdir(exist_ok=True)
    manifest = dict(source_root=str(ROOT),files={},campaigns={},
        scope='Saved game reports, reviews, strategies, budgets and native replays; no authentication, service logs, raw API sessions or databases.')
    with zipfile.ZipFile(output/'bot-campaigns.zip','w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name in CAMPAIGNS:
            folder = ROOT/'logs/autonomous-bot'/name
            state = json.loads((folder/'campaign.json').read_text())
            manifest['campaigns'][name] = dict(budget=state['budget'],numbered_matches=len(state['matches']),
                goal_reached=state.get('goal_reached',False),retry_retained=bool(state.get('retry')))
            for path in sorted(folder.rglob('*')):
                rel = path.relative_to(folder)
                if not path.is_file() or path.name not in ALLOWED:
                    continue
                # Never walk service request/state folders into the export inventory.
                if len(rel.parts)>2 or (len(rel.parts)==2 and not re.fullmatch(r'(?:match|retry)-\d+-\w+',rel.parts[0])):
                    continue
                if path.is_symlink():raise ValueError('Symlink in selected handoff data')
                data = path.read_bytes()
                if path.suffix in ('.json','.md'):
                    if re.search(rb'(?:sk-[A-Za-z0-9_-]{20,}|eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|-----BEGIN [A-Z ]*PRIVATE KEY-----)',data):
                        raise ValueError('Credential-shaped text in selected file: '+str(rel))
                target = path.relative_to(ROOT).as_posix()
                archive.writestr(target,data)
                manifest['files'][target] = dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
    manifest['archive_sha256'] = hashlib.sha256((output/'bot-campaigns.zip').read_bytes()).hexdigest()
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    strategy = json.loads((ROOT/'logs/autonomous-bot'/CAMPAIGNS[0]/'strategy.json').read_text())
    (output/'prompts.json').write_text(json.dumps(dict(initial_strategy=INITIAL_STRATEGY,
        battle_rules=BOT_RULES,review_rules=REVIEW_RULES,latest_strategy=strategy),ensure_ascii=False,indent=2)+'\n')
    for number in (20,21,22):
        source = ROOT/'logs/autonomous-bot'/CAMPAIGNS[0]/f'match-{number:02d}-hard/review.json'
        (output/f'review-{number:02d}.json').write_bytes(source.read_bytes())
    print(json.dumps(dict(files=len(manifest['files']),archive_bytes=(output/'bot-campaigns.zip').stat().st_size,
                         campaigns=manifest['campaigns'])))


if __name__=='__main__':main()
