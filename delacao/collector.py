"""Coletor de metadados de janela: espinha dorsal da captura (ver ADR-0001)."""

import json
import re
import subprocess
import sys
import time

from . import config, db


def _call_patterns():
    raw = db.setting("call_patterns")
    pats = json.loads(raw) if raw else config.DEFAULT_CALL_PATTERNS
    return [re.compile(p) for p in pats]


class WindowsBackend:
    def __init__(self):
        import ctypes
        import ctypes.wintypes as wt

        self.ct = ctypes
        self.wt = wt
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32

    def _text(self, hwnd):
        n = self.user32.GetWindowTextLengthW(hwnd)
        if not n:
            return ""
        buf = self.ct.create_unicode_buffer(n + 1)
        self.user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def active_window(self):
        hwnd = self.user32.GetForegroundWindow()
        title = self._text(hwnd)
        pid = self.wt.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, self.ct.byref(pid))
        app = ""
        hproc = self.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if hproc:
            buf = self.ct.create_unicode_buffer(260)
            size = self.wt.DWORD(260)
            if self.kernel32.QueryFullProcessImageNameW(hproc, 0, buf, self.ct.byref(size)):
                app = buf.value.rsplit("\\", 1)[-1].removesuffix(".exe")
            self.kernel32.CloseHandle(hproc)
        return app, title

    def idle_ms(self):
        class LASTINPUTINFO(self.ct.Structure):
            _fields_ = [("cbSize", self.wt.UINT), ("dwTime", self.wt.DWORD)]

        li = LASTINPUTINFO()
        li.cbSize = self.ct.sizeof(li)
        self.user32.GetLastInputInfo(self.ct.byref(li))
        return max(0, self.kernel32.GetTickCount() - li.dwTime)

    def all_titles(self):
        titles = []
        proc = self.ct.WINFUNCTYPE(self.wt.BOOL, self.wt.HWND, self.wt.LPARAM)

        def cb(hwnd, _):
            if self.user32.IsWindowVisible(hwnd):
                t = self._text(hwnd)
                if t:
                    titles.append(t)
            return True

        self.user32.EnumWindows(proc(cb), 0)
        return titles


class X11Backend:
    def __init__(self):
        from Xlib import X, display
        from Xlib.ext import screensaver  # noqa: F401 (registra a extensão)

        self.X = X
        self.d = display.Display()
        self.root = self.d.screen().root
        self.NET_ACTIVE = self.d.intern_atom("_NET_ACTIVE_WINDOW")
        self.NET_NAME = self.d.intern_atom("_NET_WM_NAME")
        self.NET_LIST = self.d.intern_atom("_NET_CLIENT_LIST")
        self.WM_NAME = self.d.intern_atom("WM_NAME")
        self.UTF8 = self.d.intern_atom("UTF8_STRING")

    def _title(self, w):
        try:
            p = w.get_full_property(self.NET_NAME, self.UTF8) or w.get_full_property(
                self.WM_NAME, self.X.AnyPropertyType
            )
            if p is None:
                return ""
            v = p.value
            return v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
        except Exception:
            return ""

    def active_window(self):
        p = self.root.get_full_property(self.NET_ACTIVE, self.X.AnyPropertyType)
        if not p or not p.value or not p.value[0]:
            return "", ""
        w = self.d.create_resource_object("window", p.value[0])
        app = ""
        try:
            c = w.get_wm_class()
            app = c[1] if c else ""
        except Exception:
            pass
        return app, self._title(w)

    def idle_ms(self):
        try:
            return self.root.screensaver_query_info().idle
        except Exception:
            try:
                return int(subprocess.check_output(["xprintidle"], timeout=2))
            except Exception:
                return 0

    def all_titles(self):
        out = []
        p = self.root.get_full_property(self.NET_LIST, self.X.AnyPropertyType)
        if p:
            for wid in p.value:
                t = self._title(self.d.create_resource_object("window", wid))
                if t:
                    out.append(t)
        return out


def make_backend():
    if sys.platform == "win32":
        return WindowsBackend()
    return X11Backend()


def read_sample(backend, patterns):
    app, title = backend.active_window()
    idle_ms = backend.idle_ms()
    call_title = None
    for t in backend.all_titles():
        if any(p.search(t) for p in patterns):
            call_title = t
            break
    return app, title, idle_ms, call_title


def run():
    backend = None
    while True:
        try:
            if backend is None:
                backend = make_backend()
            app, title, idle_ms, call_title = read_sample(backend, _call_patterns())
            db.ex(
                "INSERT INTO samples(ts, app, title, idle_ms, in_call, call_title) "
                "VALUES(?,?,?,?,?,?)",
                (int(time.time()), app, title or "", int(idle_ms),
                 1 if call_title else 0, call_title),
            )
        except Exception as e:
            print(f"collector: {e}", file=sys.stderr)
            backend = None  # reconecta (ex.: X server reiniciou)
        time.sleep(config.POLL_S)
