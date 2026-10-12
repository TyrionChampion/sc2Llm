"""A portable handoff retains accounting and cannot overwrite another session's work."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from tools.restore_bot_campaign import restore


class HandoffTests(unittest.TestCase):
    def fixture(self,folder,name='logs/autonomous-bot/example/campaign.json'):
        state=dict(budget=dict(limit=26,attempted=5,by_role={'battle':4,'review':1}),
            matches=[dict(number=1,report='/old/repo/logs/autonomous-bot/example/match-01-hard/match.json')])
        raw=json.dumps(state).encode();archive=folder/'archive.zip'
        with zipfile.ZipFile(archive,'w') as z:z.writestr(name,raw)
        manifest=dict(source_root='/old/repo',archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            files={name:dict(sha256=hashlib.sha256(raw).hexdigest())},
            campaigns={'example':dict(budget=state['budget'],numbered_matches=1)})
        path=folder/'manifest.json';path.write_text(json.dumps(manifest))
        return archive,path,state

    def test_relocates_paths_and_keeps_counters_exactly(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);archive,manifest,old=self.fixture(folder);root=folder/'new'
            restore(archive,manifest,root)
            state=json.loads((root/'logs/autonomous-bot/example/campaign.json').read_text())
            self.assertEqual(state['budget'],old['budget'])
            self.assertTrue(state['matches'][0]['report'].startswith(str(root)))

    def test_refuses_existing_campaign_without_changing_it(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);archive,manifest,_=self.fixture(folder);root=folder/'new'
            target=root/'logs/autonomous-bot/example';target.mkdir(parents=True)
            marker=target/'campaign.json';marker.write_text('newer battles')
            with self.assertRaises(FileExistsError):restore(archive,manifest,root)
            self.assertEqual(marker.read_text(),'newer battles')

    def test_verify_only_does_not_restore_files(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);archive,manifest,_=self.fixture(folder);root=folder/'new'
            self.assertTrue(restore(archive,manifest,root,verify_only=True)['verified'])
            self.assertFalse(root.exists())

    def test_rejects_escaping_archive_path_before_writing(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);archive,manifest,_=self.fixture(folder,'logs/autonomous-bot/example/../../outside/campaign.json')
            with self.assertRaisesRegex(ValueError,'Unsafe'):restore(archive,manifest,folder/'new')
            self.assertFalse((folder/'new').exists())

    def test_rejects_bad_checksum_before_writing(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);archive,manifest,_=self.fixture(folder);archive.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'checksum'):restore(archive,manifest,folder/'new')
            self.assertFalse((folder/'new').exists())


if __name__=='__main__':unittest.main()
