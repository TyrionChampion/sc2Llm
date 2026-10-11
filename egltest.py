"""EGL probe: ask Mesa directly whether it can create a usable EGL display.

SC2's log only says "Devices tried: 0" which is ambiguous. This script tries
every plausible EGL platform/explicit-device combination and prints the raw
EGL error code, so we learn WHICH path works (if any) instead of guessing.
"""
import ctypes
import ctypes.util
import os

EGL_DEFAULT_DISPLAY = 0
EGL_NO_DISPLAY = 0
# EGL platform enums
PLATFORMS = {
    "EGL_PLATFORM_GBM_KHR": 0x31D7,
    "EGL_PLATFORM_SURFACELESS_MESA": 0x31DD,
    "EGL_PLATFORM_DEVICE_EXT": 0x313F,
}


def load_egl():
    path = ctypes.util.find_library("EGL") or "libEGL.so.1"
    print("libEGL ->", path)
    egl = ctypes.CDLL(path)
    egl.eglGetDisplay.restype = ctypes.c_void_p
    egl.eglGetDisplay.argtypes = [ctypes.c_void_p]
    egl.eglGetError.restype = ctypes.c_int
    egl.eglInitialize.argtypes = [ctypes.c_void_p,
                                  ctypes.POINTER(ctypes.c_int),
                                  ctypes.POINTER(ctypes.c_int)]
    egl.eglInitialize.restype = ctypes.c_int
    if hasattr(egl, "eglGetPlatformDisplayEXT"):
        egl.eglGetPlatformDisplayEXT.restype = ctypes.c_void_p
        egl.eglGetPlatformDisplayEXT.argtypes = [
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
    if hasattr(egl, "eglQueryDevicesEXT"):
        egl.eglQueryDevicesEXT.restype = ctypes.c_int
        egl.eglQueryDevicesEXT.argtypes = [
            ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    return egl


def try_display(egl, label, display):
    print("\n--- %s ---" % label)
    if not display:
        print("   eglGetDisplay returned NO_DISPLAY (0x%x)" % egl.eglGetError())
        return False
    major = ctypes.c_int()
    minor = ctypes.c_int()
    ok = egl.eglInitialize(ctypes.c_void_p(display),
                           ctypes.byref(major), ctypes.byref(minor))
    if ok:
        print("   ✅ INIT OK  EGL %d.%d" % (major.value, minor.value))
        return True
    print("   ❌ eglInitialize failed, eglGetError = 0x%x" % egl.eglGetError())
    return False


def main():
    egl = load_egl()

    print("\n=== Devices via eglQueryDevicesEXT ===")
    if hasattr(egl, "eglQueryDevicesEXT"):
        n = ctypes.c_int(0)
        if egl.eglQueryDevicesEXT(0, None, ctypes.byref(n)):
            print("   device count =", n.value)
            if n.value:
                arr = (ctypes.c_void_p * n.value)()
                egl.eglQueryDevicesEXT(n.value, arr, ctypes.byref(n))
                for i in range(n.value):
                    print("   device[%d] = 0x%x" % (i, arr[i] or 0))
        else:
            print("   query failed, err 0x%x" % egl.eglGetError())
    else:
        print("   eglQueryDevicesEXT not exported")

    print("\n=== eglGetDisplay(EGL_DEFAULT_DISPLAY) ===")
    got = False
    got |= try_display(egl, "EGL_DEFAULT_DISPLAY (0)",
                       egl.eglGetDisplay(ctypes.c_void_p(EGL_DEFAULT_DISPLAY)))

    print("\n=== eglGetDisplay(EGL_DRI2 native) via env EGL_PLATFORM ===")
    for env_val, name in [("surfaceless", "SURFACELESS_MESA"),
                          ("gbm", "GBM_KHR"),
                          ("device", "DEVICE_EXT"),
                          ("x11", "X11")]:
        os.environ["EGL_PLATFORM"] = env_val
        got |= try_display(
            egl, "EGL_PLATFORM=%s (%s)" % (env_val, name),
            egl.eglGetDisplay(ctypes.c_void_p(EGL_DEFAULT_DISPLAY)))
    os.environ.pop("EGL_PLATFORM", None)

    print("\n=== eglGetPlatformDisplayEXT with explicit enums ===")
    if hasattr(egl, "eglGetPlatformDisplayEXT"):
        for name, enum in PLATFORMS.items():
            got |= try_display(
                egl, "%s (0x%x)" % (name, enum),
                egl.eglGetPlatformDisplayEXT(enum, None, None))
    else:
        print("   eglGetPlatformDisplayEXT not exported")

    print("\n=== VERDICT ===")
    print("any EGL display usable:", got)
    print("\n=== referenced device nodes ===")
    for p in ("/dev/dri/renderD128", "/dev/dri/card1", "/dev/dri/card0"):
        try:
            st = os.stat(p)
            print("   %s exists (mode %o)" % (p, st.st_mode & 0o777))
        except OSError as e:
            print("   %s -> %s" % (p, e))
    return 0 if got else 1


if __name__ == "__main__":
    raise SystemExit(main())
