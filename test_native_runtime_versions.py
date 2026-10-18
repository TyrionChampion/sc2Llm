"""Use the active installed build; never silently launch a cached old executable."""
from pathlib import Path
import tempfile
import unittest
from arena.native_runtime import installation_version,game_executable


class NativeVersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def manifest(self,rows):
        (self.root/'.build.info').write_text('Branch!STRING:0|Active!DEC:1|Version!STRING:0\n'+rows)

    def executable(self,build):
        path=self.root/'Versions'/('Base%d'%build)/'SC2_x64.exe'
        path.parent.mkdir(parents=True);path.write_bytes(b'fixture');return path

    def test_active_manifest_selects_current_build_despite_retained_old_or_newer_directories(self):
        self.manifest('cn|1|5.0.17.98370\ncn|0|5.0.16.97579\n')
        self.executable(97579);expected=self.executable(98370);self.executable(99999)
        self.assertEqual(installation_version(self.root),'5.0.17.98370')
        self.assertEqual(game_executable(self.root),expected)

    def test_missing_active_executable_does_not_fall_back_to_old_build(self):
        self.manifest('cn|1|5.0.17.98370\n');self.executable(97579)
        with self.assertRaisesRegex(RuntimeError,'sync_native_runtime'):game_executable(self.root)

    def test_ambiguous_inactive_or_malformed_manifest_is_rejected(self):
        for rows in ('cn|0|5.0.17.98370\n','cn|1|5.0.17.98370\nus|1|5.0.16.97579\n','cn|1|invalid\n'):
            with self.subTest(rows=rows):
                self.manifest(rows)
                with self.assertRaisesRegex(RuntimeError,'unambiguous'):installation_version(self.root)


if __name__=='__main__':unittest.main()
