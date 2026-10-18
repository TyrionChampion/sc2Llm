"""Refresh the isolated native game from the user's installed Battle.net game.

Source assets are read only. Publish the active manifest after data/executables
are copied, and refuse to synchronize while a native match owns the lock.
"""
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from arena.native_runtime import GAME, RUNTIME, SOURCE, installation_version, game_executable
from arena.lifecycle import write_json_atomic


def main():
    RUNTIME.mkdir(exist_ok=True)
    with (RUNTIME/'native-match.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        source_manifest=(SOURCE/'.build.info').read_bytes()
        version=installation_version(SOURCE)
        target=RUNTIME/'data-copy'
        if target.resolve()==(SOURCE/'SC2Data').resolve():raise RuntimeError('Runtime data must be a separate writable copy')
        target.mkdir(exist_ok=True);GAME.mkdir(exist_ok=True)
        print('Synchronizing isolated native SC2 to '+version,flush=True)
        subprocess.run(['rsync','-a','--stats','--exclude=ecache/','--exclude=s2c/',
                        str(SOURCE/'SC2Data')+'/',str(target)+'/'],check=True)
        for name in ('Versions','Support64','Interfaces'):
            (GAME/name).mkdir(exist_ok=True)
            subprocess.run(['rsync','-a',str(SOURCE/name)+'/',str(GAME/name)+'/'],check=True)
        if (SOURCE/'.build.info').read_bytes()!=source_manifest:
            raise RuntimeError('Source game changed during synchronization; active manifest was not published')
        for name in ('.product.db','.build.info'):
            temporary=GAME/(name+'.sync-tmp')
            shutil.copy2(SOURCE/name,temporary);temporary.replace(GAME/name)
        game_data=GAME/'SC2Data'
        if not game_data.exists():game_data.symlink_to(target,target_is_directory=True)
        if game_data.resolve()!=target.resolve():raise RuntimeError('Game SC2Data does not resolve to isolated data copy')
        executable=game_executable()
        result=dict(version=version,source=str(SOURCE),runtime=str(GAME),
                    synced_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
                    executable=str(executable),executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
                    manifest_sha256=hashlib.sha256(source_manifest).hexdigest())
        write_json_atomic(RUNTIME/'runtime-sync.json',result)
        print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
