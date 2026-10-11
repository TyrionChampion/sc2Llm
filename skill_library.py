"""Inspect or seed validated per-model persistent skills; never starts a game."""
import argparse,json
from pathlib import Path
from arena.persistent_skills import PersistentSkills
from arena.skill_lab import SkillLab
from arena.python_skills import PythonCatalog

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root');subs=parser.add_subparsers(dest='command',required=True)
    seed=subs.add_parser('seed');seed.add_argument('--from-run',required=True)
    show=subs.add_parser('show');show.add_argument('--model',required=True)
    args=parser.parse_args();library=PersistentSkills(args.root)
    if args.command=='show':print(json.dumps(library.latest(args.model),ensure_ascii=False,indent=2));return
    source=Path(args.from_run).resolve();record=json.loads((source/'run.json').read_text())
    for i,report in enumerate(record['preparation']):
        model=record['models'][i]
        if report['model']!=model:raise ValueError('Source author mismatch')
        current=library.latest(model)
        if current:
            if current['manifest']['skills']!=report['skills']:raise ValueError('A different latest version already exists; refusing implicit rollback for '+model)
            print('[skill-library] already stored; keeping existing latest for '+model);continue
        lab=SkillLab(source/f'p{i+1}/lab');catalog=PythonCatalog(report['frozen_directory'])
        library.publish(model,lab,catalog,report,expected=None,origin={'kind':'import_validated_preparation','run':str(source),'player':i+1})

if __name__=='__main__':main()
