"""Isolated native SC2 experiment; USB assets are mounted read-only."""
import os, pathlib, re, shutil, subprocess, sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
RUNTIME=ROOT/"native-runtime"
SOURCE=pathlib.Path(os.environ.get("ARENA_SC2_SOURCE", "/run/media/tyrion/7E75-BB43/StarCraft II")).expanduser()
STEAM=pathlib.Path(os.environ.get("ARENA_STEAM_PATH", str(pathlib.Path.home()/".local/share/Steam"))).expanduser()
PROTON=pathlib.Path(os.environ.get("ARENA_PROTON_PATH", str(STEAM/"compatibilitytools.d/GE-Proton11-7-x86_64"))).expanduser()
GAME=RUNTIME/"StarCraft II"

def installation_version(root=None):
    root=GAME if root is None else pathlib.Path(root)
    lines=(root/".build.info").read_text().splitlines()
    if not lines:raise RuntimeError("Empty native SC2 build manifest")
    fields=[field.split('!',1)[0] for field in lines[0].split('|')]
    rows=[dict(zip(fields,line.split('|'))) for line in lines[1:] if line.strip()]
    versions={row.get('Version','') for row in rows if row.get('Active')=='1'}
    if len(versions)!=1 or not re.fullmatch(r'\d+\.\d+\.\d+\.\d+',next(iter(versions),'')):
        raise RuntimeError("Native SC2 requires one unambiguous active version in .build.info")
    return versions.pop()

def game_executable(root=None):
    root=GAME if root is None else pathlib.Path(root)
    build=int(installation_version(root).rsplit('.',1)[1])
    executable=root/"Versions"/('Base%d'%build)/"SC2_x64.exe"
    if not executable.is_file():
        raise RuntimeError("Active native SC2 executable missing; run tools/sync_native_runtime.py: "+str(executable))
    return executable

def prepare():
    for d in (RUNTIME,GAME,RUNTIME/"compat-ge",RUNTIME/"logs",RUNTIME/"tmp",RUNTIME/"cache",RUNTIME/"config"):
        d.mkdir(parents=True,exist_ok=True)
    if not (RUNTIME/"data-copy/data").is_dir():
        raise RuntimeError("Native SC2 requires the local SC2Data copy in native-runtime/data-copy; no read-only/source fallback")
    # Executables/metadata and writable game data live in the project; USB stays read-only.
    for name in ("Versions","Support64","Interfaces"):
        if not (GAME/name).exists(): shutil.copytree(SOURCE/name,GAME/name)
    for name in (".build.info",".product.db"):
        if not (GAME/name).exists(): shutil.copy2(SOURCE/name,GAME/name)
    if (SOURCE/".build.info").is_file() and installation_version()!=installation_version(SOURCE):
        raise RuntimeError("Native SC2 snapshot is older/different than the Battle.net installation; run tools/sync_native_runtime.py")
    game_executable()  # Never fall back to a different installed Base directory.
    for name,target in (("SC2Data",RUNTIME/"data-copy"),("Maps",ROOT/"engine/StarCraftII/Maps")):
        if not (GAME/name).exists(): (GAME/name).symlink_to(target,target_is_directory=True)
    # Proton takes a lock next to its launcher; keep it out of the installed tool.
    shadow=RUNTIME/"proton-ge"
    shadow.mkdir(exist_ok=True)
    for p in PROTON.iterdir():
        dest=shadow/p.name
        if dest.exists(): continue
        if p.is_file(): shutil.copy2(p,dest)
        elif p.name in ("files","protonfixes"): dest.symlink_to(p,target_is_directory=True)
    return shadow/"proton"

def launch(args):
    launcher=prepare()
    env=os.environ.copy()
    # Do not expose model credentials to the Windows game.
    for key in list(env):
        if "API_KEY" in key.upper() or key.startswith("ARENA_"): env.pop(key,None)
    for key in ("LD_LIBRARY_PATH","PYSC2_OSMESA","SC2PATH"): env.pop(key,None)
    env.update(STEAM_COMPAT_DATA_PATH=str(RUNTIME/"compat-ge"),
        STEAM_COMPAT_CLIENT_INSTALL_PATH=str(STEAM),
        PROTONFIXES_DISABLE="1",SteamAppId="0",SteamGameId="0",PROTON_LOG="1",PROTON_LOG_DIR=str(RUNTIME/"logs"),
        DXVK_HUD="fps",DXVK_LOG_LEVEL="info",DXVK_LOG_PATH=str(RUNTIME/"logs"),
        XDG_CACHE_HOME=str(RUNTIME/"cache"),XDG_CONFIG_HOME=str(RUNTIME/"config"),
        TMPDIR=str(RUNTIME/"tmp"))
    # Native SC2 currently uses D3D9. Also cover DXGI if its renderer changes.
    # These are presentation settings, independent of realtime simulation/API polling.
    display_config="d3d9.presentInterval=1;d3d9.maxFrameRate=60;dxgi.syncInterval=1;dxgi.maxFrameRate=60"
    inherited_config=env.get("DXVK_CONFIG", "").strip()
    env["DXVK_CONFIG"]=(inherited_config.rstrip(";")+";" if inherited_config else "")+display_config
    print("NATIVE_DISPLAY vsync=on max_fps=60 applies_on_launch; DXVK overrides: "+display_config,flush=True)
    command=["bwrap","--die-with-parent","--unshare-pid","--ro-bind","/","/","--dev-bind","/dev","/dev",
        "--proc","/proc","--bind",str(RUNTIME),str(RUNTIME),"--bind","/tmp","/tmp",
        "--chdir",str(GAME/"Support64"),str(launcher),"run"]+args
    print("NATIVE_LAUNCH",command,flush=True)
    return subprocess.call(command,env=env)

if __name__=="__main__":
    if sys.argv[1:]==["--init"]:
        sys.exit(launch(["wineboot","-u"]))
    sys.exit(launch(sys.argv[1:]))
