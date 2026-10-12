"""Restore the reviewed, credential-free handoff without overwriting later battles."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def restore(archive, manifest_path, root, *, verify_only=False):
    manifest = json.loads(Path(manifest_path).read_text())
    archive = Path(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest['archive_sha256']:
        raise ValueError('Handoff archive checksum mismatch')
    root = Path(root).resolve()
    files = manifest['files']
    campaigns = manifest['campaigns']
    payloads = {}
    with zipfile.ZipFile(archive) as source:
        names = source.namelist()
        if len(names) != len(set(names)) or set(names) != set(files):
            raise ValueError('Archive inventory mismatch')
        if len(names) > 500 or sum(i.file_size for i in source.infolist()) > 20*1024*1024:
            raise ValueError('Handoff archive exceeds size limit')
        for item in source.infolist():
            path = PurePosixPath(item.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in item.filename
                    or len(path.parts) < 4 or path.parts[:2] != ('logs', 'autonomous-bot')
                    or path.parts[2] not in campaigns
                    or path.name not in {'campaign.json','strategy.json','winning-strategy.json',
                        'match.json','review.json','budget.json','summary.json','report.md','match.SC2Replay'}
                    or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError('Unsafe handoff entry: '+item.filename)
            if item.file_size > 5*1024*1024:
                raise ValueError('Handoff file exceeds size limit')
            data = source.read(item)
            if hashlib.sha256(data).hexdigest() != files[item.filename]['sha256']:
                raise ValueError('Handoff file checksum mismatch: '+item.filename)
            if item.filename.endswith('.json'):
                value = json.loads(data)
                def relocate(node):
                    if isinstance(node, str):
                        return node.replace(manifest['source_root']+'/', str(root)+'/')
                    if isinstance(node, list):return [relocate(x) for x in node]
                    if isinstance(node, dict):return {k:relocate(v) for k,v in node.items()}
                    return node
                data = (json.dumps(relocate(value),ensure_ascii=False,indent=2)+'\n').encode()
            payloads[item.filename] = data
    for name, expected in campaigns.items():
        state = json.loads(payloads['logs/autonomous-bot/'+name+'/campaign.json'])
        budget = state['budget']
        if (budget != expected['budget'] or len(state['matches']) != expected['numbered_matches']
                or sum(budget['by_role'].values()) != budget['attempted']
                or not 0 <= budget['attempted'] <= budget['limit']):
            raise ValueError('Handoff ledger mismatch: '+name)
        if not verify_only and (root/'logs/autonomous-bot'/name).exists():
            raise FileExistsError('Existing campaign retained; do not overwrite newer battles: '+name)
    if not verify_only:
        # All paths, hashes, budgets and destination conflicts are checked before writing.
        for name, data in payloads.items():
            target = root/name
            if not target.resolve().is_relative_to(root):
                raise ValueError('Destination escapes workspace')
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as stream:stream.write(data)
    return dict(verified=True,restored=not verify_only,files=len(payloads),
                campaigns={name:entry['budget'] for name,entry in campaigns.items()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--archive',type=Path,default=ROOT/'handoff/bot-campaigns.zip')
    parser.add_argument('--manifest',type=Path,default=ROOT/'handoff/manifest.json')
    parser.add_argument('--verify-only',action='store_true')
    args = parser.parse_args()
    print(json.dumps(restore(args.archive,args.manifest,args.root,verify_only=args.verify_only)))


if __name__=='__main__':main()
