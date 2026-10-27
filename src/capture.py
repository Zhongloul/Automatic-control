import ctypes
import threading
import time
from ctypes import wintypes

import numpy as np

BI_RGB = 0
DIB_RGB_COLORS = 0
SRCCOPY = 0x00CC0020


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("bmiHeader", _BitmapInfoHeader), ("bmiColors", wintypes.DWORD * 3)]


class GdiCapture:
    """GDI screen grab without CAPTUREBLT (avoids cursor flicker)."""

    def __init__(self):
        self._ready = False

    def _init_api(self):
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self._user32.GetDC.argtypes = [wintypes.HWND]
        self._user32.GetDC.restype = wintypes.HDC
        self._user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        self._user32.ReleaseDC.restype = ctypes.c_int
        self._gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        self._gdi32.CreateCompatibleDC.restype = wintypes.HDC
        self._gdi32.CreateDIBSection.argtypes = [
            wintypes.HDC,
            ctypes.POINTER(_BitmapInfo),
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p),
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        self._gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        self._gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        self._gdi32.SelectObject.restype = wintypes.HGDIOBJ
        self._gdi32.BitBlt.argtypes = [
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.DWORD,
        ]
        self._gdi32.BitBlt.restype = wintypes.BOOL
        self._gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        self._gdi32.DeleteObject.restype = wintypes.BOOL
        self._gdi32.DeleteDC.argtypes = [wintypes.HDC]
        self._gdi32.DeleteDC.restype = wintypes.BOOL
        self._ready = True

    def grab(self, region):
        if not self._ready:
            self._init_api()
        left, top, right, bottom = (int(value) for value in region)
        width = right - left
        height = bottom - top
        if width < 1 or height < 1:
            return None
        screen = self._user32.GetDC(None)
        if not screen:
            return None
        mem = self._gdi32.CreateCompatibleDC(screen)
        bitmap = None
        old = None
        try:
            if not mem:
                return None
            info = _BitmapInfo()
            info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
            info.bmiHeader.biWidth = width
            info.bmiHeader.biHeight = -height
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            info.bmiHeader.biCompression = BI_RGB
            bits = ctypes.c_void_p()
            bitmap = self._gdi32.CreateDIBSection(
                screen, ctypes.byref(info), DIB_RGB_COLORS, ctypes.byref(bits), None, 0
            )
            if not bitmap or not bits.value:
                return None
            old = self._gdi32.SelectObject(mem, bitmap)
            if not self._gdi32.BitBlt(mem, 0, 0, width, height, screen, left, top, SRCCOPY):
                return None
            buffer = (ctypes.c_ubyte * (width * height * 4)).from_address(bits.value)
            frame = np.frombuffer(buffer, dtype=np.uint8).reshape(height, width, 4)
            return frame[:, :, :3].copy()
        finally:
            if old:
                self._gdi32.SelectObject(mem, old)
            if bitmap:
                self._gdi32.DeleteObject(bitmap)
            if mem:
                self._gdi32.DeleteDC(mem)
            self._user32.ReleaseDC(None, screen)

    def close(self):
        self._ready = False


class FrameSource:
    def __init__(self, backend="auto"):
        self.backend = None
        self._dxcam = None
        self._sct = None
        self._gdi = None
        candidates = ["dxcam", "gdi", "mss"] if backend in ("auto", "", None) else [backend]
        errors = []
        for name in candidates:
            try:
                self._init_backend(name)
                self.backend = name
                break
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        if self.backend is None:
            raise RuntimeError("无法初始化截屏后端 -> " + "; ".join(errors))

    def _init_backend(self, name):
        if name == "dxcam":
            import dxcam

            camera = dxcam.create(output_color="BGR")
            if camera is None:
                raise RuntimeError("dxcam.create 返回 None")
            self._dxcam = camera
            return
        if name == "gdi":
            self._gdi = GdiCapture()
            return
        if name == "mss":
            import mss

            self._sct = mss.mss()
            return
        raise RuntimeError(f"未知截屏后端: {name}")

    def grab(self, region):
        if not region:
            return None
        left, top, right, bottom = (int(value) for value in region)
        if right - left < 4 or bottom - top < 4:
            return None
        try:
            if self.backend == "dxcam":
                frame = self._dxcam.grab(region=(left, top, right, bottom))
                return frame
            if self.backend == "gdi":
                return self._gdi.grab((left, top, right, bottom))
            raw = self._sct.grab(
                {"left": left, "top": top, "width": right - left, "height": bottom - top}
            )
            return np.array(raw)[:, :, :3].copy()
        except Exception:
            return None

    def close(self):
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = None
        if self._gdi is not None:
            self._gdi.close()
            self._gdi = None


class CaptureThread:
    def __init__(self, source, region_provider, fps=45):
        self.source = source
        self.region_provider = region_provider
        self.interval = 1.0 / max(1, int(fps))
        self.fps_actual = 0.0
        self.last_time = 0.0
        self._frame = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="capture", daemon=True)
        self._thread.start()

    def _run(self):
        counter = 0
        started = time.perf_counter()
        while not self._stop.is_set():
            begin = time.perf_counter()
            region = self.region_provider()
            if region:
                frame = self.source.grab(region)
                if frame is not None:
                    with self._lock:
                        self._frame = frame
                        self.last_time = time.perf_counter()
            elapsed = time.perf_counter() - begin
            time.sleep(max(0.001, self.interval - elapsed))
            counter += 1
            if counter >= 30:
                now = time.perf_counter()
                self.fps_actual = counter / max(1e-6, now - started)
                counter = 0
                started = now

    def latest(self):
        with self._lock:
            return self._frame

    def age(self):
        with self._lock:
            if self._frame is None:
                return float("inf")
            return time.perf_counter() - self.last_time

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
