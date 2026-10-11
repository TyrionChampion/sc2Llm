"""Rebuild the Acropolis speed-only derivative using an explicit local StormLib.
Run via ./run_sc2.sh .venv311/bin/python -m arena.build_speed_map --storm-lib /path/libstorm.so.
No downloads, original-map edits, game launches or model calls.
"""
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import mpyq
from arena.game_speed import ROOT

SPEED_LINES = """    MeleeInitOptions();
    GameSetSpeedLocked(false);
    GameSetSpeedValueMinimum(c_gameSpeedSlower);
    GameSetSpeedValue(c_gameSpeedNormal);
    GameSetSpeedLocked(true);"""

def build(library, speed='normal'):
    if speed not in ('normal','slower'):raise ValueError('supported derivatives: normal, slower')
    label={'normal':'Normal','slower':'Slower'}[speed]
    lib=C.CDLL(str(Path(library).resolve()))
    signatures={
        "SFileOpenArchive":[C.c_char_p,C.c_uint32,C.c_uint32,C.POINTER(C.c_void_p)],
        "SFileOpenFileEx":[C.c_void_p,C.c_char_p,C.c_uint32,C.POINTER(C.c_void_p)],
        "SFileGetFileSize":[C.c_void_p,C.POINTER(C.c_uint32)],
        "SFileReadFile":[C.c_void_p,C.c_void_p,C.c_uint32,C.POINTER(C.c_uint32),C.c_void_p],
        "SFileCloseFile":[C.c_void_p],"SFileCloseArchive":[C.c_void_p],
        "SFileAddFileEx":[C.c_void_p,C.c_char_p,C.c_char_p,C.c_uint32,C.c_uint32,C.c_uint32],
    }
    for name,args in signatures.items():
        fn=getattr(lib,name);fn.argtypes=args
        fn.restype=C.c_uint32 if name=="SFileGetFileSize" else C.c_bool
    source=ROOT/"engine/StarCraftII/Maps/Ladder2019Season3/AcropolisLE.SC2Map"
    dest=ROOT/("engine/StarCraftII/Maps/Arena/Acropolis"+label+".SC2Map")
    dest.parent.mkdir(exist_ok=True)
    def read(archive,name):
        handle=C.c_void_p();file=C.c_void_p()
        if not lib.SFileOpenArchive(str(archive).encode(),0,0x100,C.byref(handle)):
            raise RuntimeError("StormLib could not open "+str(archive))
        try:
            if not lib.SFileOpenFileEx(handle,name,0,C.byref(file)):
                raise RuntimeError("StormLib missing entry "+str(name))
            try:
                size=lib.SFileGetFileSize(file,None)
                if size==0xFFFFFFFF:raise RuntimeError("StormLib file size failed")
                buf=C.create_string_buffer(size);count=C.c_uint32()
                if not lib.SFileReadFile(file,buf,size,C.byref(count),None) or count.value!=size:
                    raise RuntimeError("StormLib incomplete read "+str(name))
                return buf.raw
            finally:lib.SFileCloseFile(file)
        finally:lib.SFileCloseArchive(handle)
    original=read(source,b"MapScript.galaxy").decode().replace("\r\n","\n")
    marker="    MeleeInitOptions();"
    if original.count(marker)!=1 or "GameSetSpeed" in original:
        raise ValueError("unexpected source initialization; manual review required")
    script=original.replace(marker,SPEED_LINES.replace('GameSetSpeedValue(c_gameSpeedNormal)', 'GameSetSpeedValue(c_gameSpeed'+label+')'))
    with tempfile.TemporaryDirectory(prefix=speed+"-map-",dir=dest.parent) as tmp:
        temporary=Path(tmp)/dest.name;galaxy=Path(tmp)/"MapScript.galaxy"
        shutil.copy2(source,temporary);galaxy.write_text(script)
        handle=C.c_void_p()
        if not lib.SFileOpenArchive(str(temporary).encode(),0,0,C.byref(handle)):
            raise RuntimeError("StormLib failed to open writable derivative")
        try:
            if not lib.SFileAddFileEx(handle,str(galaxy).encode(),b"MapScript.galaxy",0x80000200,2,2):
                raise RuntimeError("StormLib failed to replace speed script")
        finally:
            if not lib.SFileCloseArchive(handle):raise RuntimeError("StormLib failed to close derivative")
        names=mpyq.MPQArchive(str(source)).files
        changed=[n.decode() for n in names if read(source,n)!=read(temporary,n)]
        if changed!=["MapScript.galaxy"]:raise RuntimeError("unexpected map changes: "+str(changed))
        temporary.replace(dest)
    metadata=dict(source=str(source.relative_to(ROOT)),
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        map_sha256=hashlib.sha256(dest.read_bytes()).hexdigest(),changed_entries=changed,
        method="StormLib; unlocked, minimum Slower, speed "+label+", locked at initialization",
        game_speed=speed,
        validation="Rebuilt derivative requires native realtime loop-rate validation before claiming support")
    dest.with_suffix(".json").write_text(json.dumps(metadata,indent=2))
    dest.with_suffix(".galaxy").write_text(script)
    print(json.dumps(metadata,indent=2))

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storm-lib",required=True)
    parser.add_argument("--speed",choices=["normal","slower"],default="normal")
    args=parser.parse_args()
    build(args.storm_lib,args.speed)
