"""Probe: with NO /dev/dri present, can Mesa's software EGL actually create a
pbuffer + GL context and render real pixels?

eglInitialize() succeeding is not enough -- SC2 needs a working context and
finite buffer. This decides whether an `-eglpath` shim that redirects SC2's
EGL_PLATFORM_DEVICE_EXT request to SURFACELESS/X11 is worth building.
"""
import ctypes
import os

EGL_NONE = 0x3038
EGL_NO_SURFACE = 0
EGL_NO_CONTEXT = 0
EGL_SURFACE_TYPE = 0x3033
EGL_PBUFFER_BIT = 0x0001
EGL_RENDERABLE_TYPE = 0x3040
EGL_OPENGL_BIT = 0x0008
EGL_RED_SIZE, EGL_GREEN_SIZE, EGL_BLUE_SIZE, EGL_ALPHA_SIZE = 0x3024, 0x3023, 0x3022, 0x3021
EGL_WIDTH, EGL_HEIGHT = 0x3057, 0x3056
EGL_CONTEXT_CLIENT_VERSION = 0x3098
EGL_OPENGL_API = 0x30A2

GL_COLOR_BUFFER_BIT = 0x4000
GL_RGBA, GL_UNSIGNED_BYTE = 0x1908, 0x1401
GL_VENDOR, GL_RENDERER, GL_VERSION = 0x1F00, 0x1F01, 0x1F02

W, H = 256, 192


def cstr(p):
    return None if not p else ctypes.cast(p, ctypes.c_char_p).value.decode()


def run(platform):
    print("\n======== EGL_PLATFORM=%s ========" % platform)
    os.environ["EGL_PLATFORM"] = platform

    egl = ctypes.CDLL("libEGL.so.1")
    egl.eglGetDisplay.restype = ctypes.c_void_p
    egl.eglGetDisplay.argtypes = [ctypes.c_void_p]
    egl.eglGetError.restype = ctypes.c_int
    egl.eglInitialize.argtypes = [ctypes.c_void_p,
                                  ctypes.POINTER(ctypes.c_int),
                                  ctypes.POINTER(ctypes.c_int)]
    egl.eglInitialize.restype = ctypes.c_int
    egl.eglChooseConfig.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
                                    ctypes.c_void_p, ctypes.c_int,
                                    ctypes.POINTER(ctypes.c_int)]
    egl.eglChooseConfig.restype = ctypes.c_int
    egl.eglCreatePbufferSurface.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                            ctypes.POINTER(ctypes.c_int)]
    egl.eglCreatePbufferSurface.restype = ctypes.c_void_p
    egl.eglBindAPI.restype = ctypes.c_int
    egl.eglBindAPI.argtypes = [ctypes.c_uint]
    egl.eglCreateContext.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    egl.eglCreateContext.restype = ctypes.c_void_p
    egl.eglMakeCurrent.argtypes = [ctypes.c_void_p] * 4
    egl.eglMakeCurrent.restype = ctypes.c_int

    dpy = egl.eglGetDisplay(ctypes.c_void_p(0))
    if not dpy:
        print("  eglGetDisplay -> NO_DISPLAY (0x%x)" % egl.eglGetError())
        return False
    maj, mnr = ctypes.c_int(), ctypes.c_int()
    if not egl.eglInitialize(ctypes.c_void_p(dpy), ctypes.byref(maj), ctypes.byref(mnr)):
        print("  eglInitialize FAILED (0x%x)" % egl.eglGetError())
        return False
    print("  eglInitialize OK (EGL %d.%d)" % (maj.value, mnr.value))

    cfg_attrs = (ctypes.c_int * 15)(
        EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
        EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT,
        EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8,
        EGL_NONE)
    cfg = ctypes.c_void_p()
    n = ctypes.c_int()
    if not egl.eglChooseConfig(ctypes.c_void_p(dpy), cfg_attrs,
                               ctypes.byref(cfg), 1, ctypes.byref(n)) or n.value < 1:
        print("  eglChooseConfig FAILED (0x%x), n=%d" % (egl.eglGetError(), n.value))
        return False
    print("  eglChooseConfig OK (%d configs)" % n.value)

    pb_attrs = (ctypes.c_int * 5)(EGL_WIDTH, W, EGL_HEIGHT, H, EGL_NONE)
    surf = egl.eglCreatePbufferSurface(ctypes.c_void_p(dpy), cfg, pb_attrs)
    if not surf:
        print("  eglCreatePbufferSurface FAILED (0x%x)" % egl.eglGetError())
        return False
    print("  eglCreatePbufferSurface OK (%dx%d)" % (W, H))

    if not egl.eglBindAPI(EGL_OPENGL_API):
        print("  eglBindAPI(OPENGL) FAILED (0x%x)" % egl.eglGetError())
        return False
    ctx_attrs = (ctypes.c_int * 3)(EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE)
    ctx = egl.eglCreateContext(ctypes.c_void_p(dpy), cfg, None, ctx_attrs)
    if not ctx:
        print("  eglCreateContext FAILED (0x%x)" % egl.eglGetError())
        return False
    print("  eglCreateContext OK")

    if not egl.eglMakeCurrent(ctypes.c_void_p(dpy), ctypes.c_void_p(surf),
                              ctypes.c_void_p(surf), ctypes.c_void_p(ctx)):
        print("  eglMakeCurrent FAILED (0x%x)" % egl.eglGetError())
        return False
    print("  eglMakeCurrent OK")

    gl = ctypes.CDLL("libGL.so.1")
    gl.glGetString.restype = ctypes.c_char_p
    gl.glGetString.argtypes = [ctypes.c_uint]
    gl.glClearColor.argtypes = [ctypes.c_float] * 4
    gl.glClear.argtypes = [ctypes.c_uint]
    gl.glReadPixels.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
                                ctypes.c_void_p]
    print("  GL_VENDOR   = %s" % cstr(gl.glGetString(GL_VENDOR)))
    print("  GL_RENDERER = %s" % cstr(gl.glGetString(GL_RENDERER)))
    print("  GL_VERSION  = %s" % cstr(gl.glGetString(GL_VERSION)))

    gl.glClearColor(1.0, 0.0, 0.0, 1.0)
    gl.glClear(GL_COLOR_BUFFER_BIT)
    buf = (ctypes.c_ubyte * (W * H * 4))()
    gl.glReadPixels(0, 0, W, H, GL_RGBA, GL_UNSIGNED_BYTE, buf)
    px = list(buf[:8])
    nonzero = sum(1 for i in range(0, W * H * 4, 4) if buf[i] or buf[i + 1] or buf[i + 2])
    print("  glReadPixels first px (RGBA) = %s" % px)
    print("  non-black pixels: %d / %d" % (nonzero, W * H))
    ok = nonzero > 0
    print("  => RENDER %s" % ("WORKS" if ok else "BLANK/FAILED"))
    return ok


if __name__ == "__main__":
    results = {}
    for plat in ("x11", "surfaceless", "device"):
        try:
            results[plat] = run(plat)
        except Exception as e:
            print("  EXCEPTION: %s: %s" % (type(e).__name__, e))
            results[plat] = False
    print("\n======== SUMMARY ========")
    for k, v in results.items():
        print("  %-12s %s" % (k, "RENDER OK" if v else "no"))
    raise SystemExit(0 if any(results.values()) else 1)
